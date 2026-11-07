import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mc_observatory.holo import RunController
from mc_observatory.server import ObservatoryService, create_app, leaderboard


class Client:
    def __init__(self): self.events = asyncio.Queue()
    async def connect(self): pass
    async def subscribe(self): pass
    async def close(self): pass
    async def call(self, *args, **kwargs): return {}


class Service(ObservatoryService):
    async def execute(self, *args): await asyncio.sleep(3600)


@pytest.fixture
def service(tmp_path):
    return Service(tmp_path, Client())


def test_run_and_confirmed_controls(service, monkeypatch):
    monkeypatch.setenv("HCOMPANY_API_KEY", "test-only")
    with TestClient(create_app(service)) as client:
        assert client.get("/api/runs").json() == []
        started = client.post("/api/runs", json={})
        assert started.status_code == 202
        run_id = started.json()["id"]
        assert client.post("/api/runs", json={}).status_code == 409
        for action in ("pause", "resume"):
            assert client.post("/api/control", json={"action": action, "source": "ui"}).status_code == 200
        assert client.post("/api/control", json={"action": "set_goal", "text": "Early goal"}).status_code == 409
        service.controller.goal = "Get a log"
        proposal = client.post("/api/control", json={"action": "set_goal", "text": "Build a table", "source": "voice"}).json()
        assert service.controller.goal != "Build a table"
        assert client.post("/api/control/confirm-goal", json={"token": "wrong"}).status_code == 409
        assert client.post("/api/control/confirm-goal", json={"token": proposal["confirmation_token"], "source": "voice"}).status_code == 200
        assert service.controller.goal == "Build a table"
        assert client.post("/api/control/confirm-goal", json={"token": proposal["confirmation_token"]}).status_code == 409
        detail = client.get(f"/api/runs/{run_id}").json()
        assert [v["action"] for v in detail["interventions"]] == ["pause", "resume", "propose_goal", "set_goal"]
        assert all(v["timestamp"] and v["source"] for v in detail["interventions"])
        assert len(service.controller.interventions) == 4
        assert client.get(f"/api/runs/{run_id}/screenshots/0").status_code == 404
        assert client.post("/api/control", json={"action": "stop", "source": "devin"}).status_code == 200
        assert service.controller.stopped


def test_empty_invalid_and_origin_requests(service, monkeypatch):
    monkeypatch.delenv("HCOMPANY_API_KEY", raising=False)
    with TestClient(create_app(service)) as client:
        assert client.get("/api/leaderboard").json() == []
        assert client.post("/api/control", json={"action": "pause"}).status_code == 409
        assert client.post("/api/runs", json={}).status_code == 503
        assert client.post("/api/runs", json={"max_steps": 0}).status_code == 422
        assert client.get("/api/runs/missing").status_code == 404
        assert client.get("/api/status", headers={"Origin": "http://untrusted.test"}).status_code == 403
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/live", headers={"Origin": "http://untrusted.test"}): pass
        with client.websocket_connect("/ws/live", headers={"Origin": "http://127.0.0.1:5173"}) as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_run_detail_handles_partial_jsonl_and_screenshot(service):
    path = service.root / "fixture-run"
    (path / "screenshots").mkdir(parents=True)
    (path / "summary.json").write_text(json.dumps({"score": 0, "success": False}))
    (path / "steps.jsonl").write_text('{"step":0}\n{"step":')
    (path / "screenshots/00000.png").write_bytes(b"test-png")
    with TestClient(create_app(service)) as client:
        assert client.get("/api/runs/fixture-run").json()["steps"] == [{"step": 0}]
        assert client.get("/api/runs/fixture-run/screenshots/0").content == b"test-png"
        assert client.get("/api/runs/fixture-run/screenshots/-1").status_code == 404


def test_leaderboard_aggregates_steps_and_keeps_groups_separate():
    def run(model, tier, success, cost):
        return {"summary": {"config": {"model": model, "tier": tier}, "task": "get_log", "score": 1/13 if success else 0,
                            "success": success, "total_cost_usd": cost, "milestones": {"log": 10} if success else {},
                            "failure_tags": [] if success else ["gui_stuck"]},
                "steps": [{"api_latency_ms": 100}, {"api_latency_ms": 300}]}
    result = leaderboard([run("a", "skills", True, .01), run("a", "skills", False, .03), run("a", "raw", False, None)])
    first = result[0]
    assert first["runs"] == 2 and first["success_rate"] == .5
    assert first["mean_score"] == pytest.approx(1/26)
    assert first["mean_latency_ms"] == 200 and first["p95_latency_ms"] == 300
    assert first["time_to_milestone"] == {"log": 10}
    assert first["cost_per_run"] == .02 and first["failure_counts"] == {"gui_stuck": 1}
    assert result[1]["cost_per_run"] is None


async def test_bounded_live_queue_and_intervention_event(service):
    queue = asyncio.Queue(maxsize=1)
    service.listeners.add(queue)
    service.publish("step", {"step": 0})
    service.publish("step", {"step": 1})
    assert queue.qsize() == 1 and (await queue.get())["data"]["step"] == 1


async def test_overlay_bounds_payload_and_command_error_is_not_disconnect(service):
    payloads = []
    async def call(command, **kwargs):
        payloads.append(kwargs["payload"])
        raise RuntimeError("command rejected")
    service.client.call = call
    service.connected = True
    await service.set_overlay(objective="x" * 20000, intent="\U0001f600" * 10000, action="a" * 20000)
    assert len(json.dumps(payloads[-1], ensure_ascii=True)) <= 7000
    assert service.connected
    assert len(service.overlay["objective"]) == 20000
    await service.set_overlay(objective="normal", intent="", action="")
    assert payloads[-1]["objective"] == "normal"


def test_startup_and_shutdown_interrupt_running_metadata(service, monkeypatch):
    path = service.root / "stale"
    path.mkdir()
    (path / "run.json").write_text(json.dumps({"status": "running"}))
    monkeypatch.setenv("HCOMPANY_API_KEY", "test-only")
    with TestClient(create_app(service)) as client:
        assert client.get("/api/runs/stale").json()["status"] == "interrupted"
        run_id = client.post("/api/runs", json={}).json()["id"]
    assert json.loads((service.root / run_id / "run.json").read_text())["status"] == "interrupted"


async def test_execute_cancel_persists_interrupted(tmp_path, monkeypatch):
    from mc_observatory.holo import HoloConfig
    from mc_observatory.server import StartRun
    started = asyncio.Event()
    async def run(*args):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr("mc_observatory.server.MinecraftRunner.run", run)
    service = ObservatoryService(tmp_path, Client())
    path = tmp_path / "cancelled"
    path.mkdir()
    (path / "run.json").write_text(json.dumps({"status": "running"}))
    service.controller = RunController()
    worker = asyncio.create_task(service.execute("cancelled", StartRun(), HoloConfig()))
    await started.wait()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError): await worker
    assert json.loads((path / "run.json").read_text())["status"] == "interrupted"
