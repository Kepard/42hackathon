"""Fresh-world launch: preserved saves, verified state, cancellation, and crash receipts."""

import asyncio
import json
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from holospeed.bridge import BridgeError, GameBridge
from holospeed.config import Settings
from holospeed.demo_launch import DemoLauncher, file_hashes, prepare_template, verify_template
from holospeed.game_state import GameState
from nbt import nbt
from test_skills import fixture

SEED = "-7131204740862824481"


def saved_world(path, *, items=False):
    path.mkdir()
    root = nbt.NBTFile()
    data = nbt.TAG_Compound(name="Data")
    root.tags.append(data)
    data.tags.extend(
        [
            nbt.TAG_Int(name="DataVersion", value=3955),
            nbt.TAG_Int(name="GameType", value=0),
            nbt.TAG_Byte(name="allowCommands", value=0),
            nbt.TAG_Long(name="Time", value=1234),
        ]
    )
    generation = nbt.TAG_Compound(name="WorldGenSettings")
    generation.tags.extend(
        [
            nbt.TAG_Long(name="seed", value=int(SEED)),
            nbt.TAG_Byte(name="bonus_chest", value=0),
        ]
    )
    data.tags.append(generation)
    player = nbt.TAG_Compound(name="Player")
    inventory = nbt.TAG_List(name="Inventory", type=nbt.TAG_Compound)
    if items:
        inventory.tags.append(nbt.TAG_Compound())
    player.tags.extend(
        [
            inventory,
            nbt.TAG_List(name="EnderItems", type=nbt.TAG_Compound),
            nbt.TAG_String(name="Dimension", value="minecraft:overworld"),
            nbt.TAG_Float(name="Health", value=20),
            nbt.TAG_Int(name="foodLevel", value=20),
        ]
    )
    position = nbt.TAG_List(name="Pos", type=nbt.TAG_Double)
    position.tags.extend(nbt.TAG_Double(value=value) for value in (-6.5, 65.0, -6.5))
    player.tags.append(position)
    abilities = nbt.TAG_Compound(name="abilities")
    abilities.tags.extend(
        nbt.TAG_Byte(name=key, value=0) for key in ("instabuild", "flying", "invulnerable")
    )
    player.tags.append(abilities)
    data.tags.append(player)
    root.write_file(filename=str(path / "level.dat"))
    (path / "session.lock").write_bytes(b"lock")
    (path / "region").mkdir()
    (path / "region" / "r.0.0.mca").write_bytes(b"unchanged region data")
    return path


@pytest.fixture
def prepared(tmp_path):
    source = saved_world(tmp_path / "source")
    template = tmp_path / "template"
    manifest = prepare_template(source, template, SEED)
    saves = tmp_path / "saves"
    saves.mkdir()
    return source, template, saves, manifest


def test_template_preserves_all_world_bytes_and_never_overwrites(prepared):
    source, template, _, manifest = prepared
    assert verify_template(template, SEED) == manifest
    assert file_hashes(source) == file_hashes(template / "world")
    assert not (template / "world" / "session.lock").exists()
    assert manifest["world"]["position"] == [-6.5, 65.0, -6.5]
    with pytest.raises(ValueError, match="new template"):
        prepare_template(source, template, SEED)
    (template / "world" / "region" / "r.0.0.mca").write_bytes(b"changed")
    with pytest.raises(ValueError, match="files changed"):
        verify_template(template, SEED)


def test_template_rejects_nonempty_wrong_seed_and_symlinks(tmp_path, prepared):
    source, template, _, _ = prepared
    nonempty = saved_world(tmp_path / "nonempty", items=True)
    with pytest.raises(ValueError, match="empty"):
        prepare_template(nonempty, tmp_path / "bad", SEED)
    with pytest.raises(ValueError, match="seed"):
        verify_template(template, "42")
    (source / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symbolic"):
        prepare_template(source, tmp_path / "symlink", SEED)


def test_template_refuses_source_locked_by_minecraft_process(tmp_path, prepared):
    source, _, _, _ = prepared
    script = (
        "import fcntl,sys; stream=open(sys.argv[1],'r+b'); "
        "fcntl.lockf(stream,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read(1)"
    )
    with subprocess.Popen(
        [sys.executable, "-c", script, str(source / "session.lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout.readline().strip() == "locked"
            with pytest.raises(ValueError, match="Save and unload"):
                prepare_template(source, tmp_path / "active", SEED)
            assert not (tmp_path / "active").exists()
        finally:
            child.communicate("x", timeout=3)


class Bridge:
    connected = True

    def __init__(self):
        self.calls = []
        self.loading = asyncio.Event()
        self.release = asyncio.Event()
        self.bad = None

    async def unload_world(self):
        self.calls.append("unload")

    async def load_world(self, name):
        self.calls.append("load")
        self.loading.set()
        await self.release.wait()
        value = {
            "seed": SEED,
            "dimension": "minecraft:overworld",
            "game_mode": "survival",
            "health": 20,
            "inventory_empty": True,
            "controls_armed": False,
            "save_name": name,
        }
        if self.bad:
            value.update(self.bad)
        return value

    async def game_state(self):
        self.calls.append("state")
        value = fixture()
        value["sampled_at_ms"] = int(time.time() * 1000)
        return GameState.model_validate(value)

    async def cancel_world(self):
        self.calls.append("cancel")

    async def stop(self, reason):
        self.calls.append("stop")


class Engine:
    def __init__(self):
        self.bridge = Bridge()
        self.state = {"run_id": "20261108T070000-deadbeef", "elapsed_ms": 6000}
        self.stages = []
        self.started = 0
        self.recorder = SimpleNamespace(save_game_state=lambda value: None)

    def broadcast(self):
        self.stages.append(self.state["launch"]["stage"])

    async def finish(self, *, launch_id, expected_run_id=None):
        self.launcher.interrupt("finish", launch_id=launch_id)
        if expected_run_id:
            assert expected_run_id == self.state["run_id"]
            self.state["status"] = "idle"

    async def start(self, *, launch_id, arm):
        self.launcher.guard_start(launch_id)
        assert arm
        self.started += 1
        self.state["status"] = "running"
        self.state["run_id"] = f"20261108T080000-{self.started:08x}"
        self.state["elapsed_ms"] = 0

    def log(self, kind, data):
        self.logged = (kind, data)


def launcher(tmp_path, prepared):
    _, template, saves, _ = prepared
    engine = Engine()
    settings = Settings(
        mode="live",
        runs_dir=tmp_path / "runs",
        demo_template_dir=template,
        minecraft_saves_dir=saves,
        declared_seed=SEED,
    )
    return DemoLauncher(engine, settings), engine


async def finish_task(value):
    await asyncio.wait_for(asyncio.shield(value.task), 3)


async def test_launch_returns_while_loading_and_starts_only_after_verified_state(
    tmp_path, prepared
):
    value, engine = launcher(tmp_path, prepared)
    identifier = str(uuid.uuid4())
    accepted = await value.begin(identifier)
    assert accepted["status"] == "preparing" and accepted["run_id"] is None
    await asyncio.wait_for(engine.bridge.loading.wait(), 3)
    assert engine.started == 0 and engine.state["elapsed_ms"] == 6000
    with pytest.raises(ValueError, match="already preparing"):
        await value.begin(str(uuid.uuid4()))
    assert (await value.begin(identifier))["id"] == identifier
    engine.bridge.release.set()
    await finish_task(value)
    assert engine.bridge.calls == ["unload", "load", "state"]
    assert engine.stages == ["saving", "copying", "loading", "verifying", "starting", "running"]
    assert value.record["run_id"] == engine.state["run_id"]
    assert engine.started == 1 and engine.state["elapsed_ms"] == 0
    assert engine.logged[0] == "launch_verified"
    assert (await value.begin(identifier))["status"] == "running"
    assert engine.started == 1
    # A genuinely new command retains the earlier clone and gets a new folder.
    await value.begin(str(uuid.uuid4()))
    await finish_task(value)
    assert len(list(prepared[2].iterdir())) == 2
    assert file_hashes(prepared[0]) == prepared[3]["files"]


@pytest.mark.parametrize("before_task", [True, False])
async def test_cancel_never_late_starts_and_releases_cross_process_lock(
    tmp_path, prepared, before_task
):
    value, engine = launcher(tmp_path, prepared)
    await value.begin(str(uuid.uuid4()))
    if not before_task:
        await asyncio.wait_for(engine.bridge.loading.wait(), 3)
    value.interrupt("Operator paused")
    engine.bridge.release.set()
    await finish_task(value)
    assert engine.started == 0 and value.record["status"] == "cancelled"
    assert "cancel" in engine.bridge.calls
    assert value.owner_fd is None
    await value.begin(str(uuid.uuid4()))
    await finish_task(value)
    assert engine.started == 1


@pytest.mark.parametrize(
    "bad",
    [{"seed": "42"}, {"inventory_empty": False}, {"save_name": "wrong"}, {"controls_armed": True}],
)
async def test_bad_loaded_world_fails_without_start_or_private_path_leak(tmp_path, prepared, bad):
    value, engine = launcher(tmp_path, prepared)
    engine.bridge.bad = bad
    engine.bridge.release.set()
    await value.begin(str(uuid.uuid4()))
    await finish_task(value)
    assert engine.started == 0 and value.record["status"] == "failed"
    assert engine.bridge.calls[-2:] == ["stop", "cancel"]
    assert str(tmp_path) not in json.dumps(value.snapshot())
    assert "error_class" not in value.snapshot()


async def test_restart_marks_interrupted_without_replaying_world_calls(tmp_path, prepared):
    value, engine = launcher(tmp_path, prepared)
    engine.bridge.release.set()
    await value.begin(str(uuid.uuid4()))
    await finish_task(value)
    restarted = DemoLauncher(Engine(), value.settings)
    assert restarted.record["status"] == "interrupted"
    assert not restarted.engine.bridge.calls
    assert restarted.engine.started == 0


async def test_copy_cancel_waits_for_worker_and_never_loads(tmp_path, prepared):
    value, engine = launcher(tmp_path, prepared)
    copying = threading.Event()

    def blocked_copy(identifier):
        copying.set()
        assert value.abort.wait(2)
        raise OSError("cancelled worker")

    value._copy = blocked_copy
    await value.begin(str(uuid.uuid4()))
    assert await asyncio.to_thread(copying.wait, 2)
    value.interrupt("Stop")
    await finish_task(value)
    assert engine.started == 0
    assert engine.bridge.calls == ["unload", "cancel"]
    assert value.owner_fd is None


async def test_post_start_recording_failure_stops_the_started_agent(tmp_path, prepared):
    value, engine = launcher(tmp_path, prepared)
    engine.bridge.release.set()

    def fail_write(state):
        raise OSError("disk full")

    engine.recorder.save_game_state = fail_write
    await value.begin(str(uuid.uuid4()))
    await finish_task(value)
    assert engine.started == 1
    assert engine.state["status"] == "idle"
    assert value.record["status"] == "failed"
    assert engine.bridge.calls[-2:] == ["stop", "cancel"]


async def test_operator_pause_cancels_arm_without_waiting_for_engine_lock(tmp_path, prepared):
    from test_state_runtime import engine_with_fakes

    bridge = Bridge()
    bridge.release.set()
    arming = asyncio.Event()

    async def arm():
        arming.set()
        await asyncio.Future()

    bridge.arm = arm
    engine, _, _ = await engine_with_fakes(tmp_path / "runs", bridge=bridge, declared_seed=SEED)
    engine.settings.demo_template_dir = prepared[1]
    engine.settings.minecraft_saves_dir = prepared[2]
    value = DemoLauncher(engine, engine.settings)
    try:
        await value.begin(str(uuid.uuid4()))
        await asyncio.wait_for(arming.wait(), 3)
        await asyncio.wait_for(engine.pause("F8 pressed"), 1)
        await finish_task(value)
        assert value.record["status"] == "cancelled"
        assert engine.task is None and engine.recorder is None
        assert "cancel" in bridge.calls
    finally:
        await engine.close()


def test_http_new_run_validates_body_and_advertises_capability(tmp_path):
    from fastapi.testclient import TestClient
    from holospeed.app import create_app

    with TestClient(create_app(Settings(runs_dir=tmp_path))) as client:
        assert client.get("/api/state").json()["launch"] is None
        for body in (
            {},
            {"command_id": "../bad"},
            {"command_id": []},
            {"command_id": str(uuid.uuid4()), "arm": True},
        ):
            assert client.post("/api/new-run", json=body).status_code == 409
        result = client.post("/api/new-run", json={"command_id": str(uuid.uuid4())})
        assert result.status_code == 409
        assert "live Minecraft" in result.json()["detail"]
        assert client.post("/api/new-run", content="x" * 513).status_code == 400


async def test_bridge_validates_world_ack_and_save_name():
    bridge = GameBridge()

    async def request(message, timeout):
        return {
            "type": "world_ack",
            "operation": "load",
            "status": "ready",
            "controls_armed": False,
            "save_name": "other",
        }

    bridge.request = request
    with pytest.raises(ValueError):
        await bridge.load_world("../escape")
    with pytest.raises(BridgeError):
        await bridge.load_world("holospeed-demo-abc")


async def test_hosted_command_reaches_real_new_run_route_and_automatically_starts_after_load(
    tmp_path, prepared, monkeypatch
):
    from holospeed.app import create_app
    from test_broadcast_commands import NOW, Peer, command, consumer

    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: NOW)
    settings = Settings(
        mode="live",
        runs_dir=tmp_path / "runs",
        holo_api_key="unused-test-key",
        demo_template_dir=prepared[1],
        minecraft_saves_dir=prepared[2],
        declared_seed=SEED,
    )
    app = create_app(settings)
    engine = app.state.engine
    bridge = Bridge()
    engine.bridge = bridge

    async def arm():
        bridge.calls.append("arm")

    async def run():
        await asyncio.Future()

    bridge.arm = arm
    engine.run = run
    peer = Peer(command("start_new"))
    local = httpx.ASGITransport(app=app)

    async def route(request):
        if request.url.host == "127.0.0.1":
            peer.requests.append(request)
            return await local.handle_async_request(request)
        return await peer.handle(request)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(route)) as client:
            publisher, _ = consumer(client, settings.runs_dir)
            await publisher.poll_once()
            assert peer.results[-1]["status"] == "completed"
            assert peer.results[-1]["run_id"] is None
            await asyncio.wait_for(bridge.loading.wait(), 3)
            assert "arm" not in bridge.calls
            assert engine.recorder is None
            bridge.release.set()
            await finish_task(app.state.launcher)
            assert engine.state["status"] == "running"
            assert engine.state["launch"]["status"] == "running"
            assert engine.state["launch"]["run_id"] == engine.recorder.id
            assert bridge.calls.index("state") < bridge.calls.index("arm")
            await publisher.poll_once()
            assert [request.url.path for request in peer.mutations] == ["/api/new-run"]
    finally:
        await engine.close()
        await app.state.launcher.close()
