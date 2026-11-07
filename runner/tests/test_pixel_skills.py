import asyncio
import base64
import io
import json
import math

import pytest
from PIL import Image

from mc_observatory.holo import HoloAgent, HoloConfig, PRIMER
from mc_observatory.holo_tools import InvalidToolArgs, ToolExecutor, parse_calls, tool_schemas
from mc_observatory.vision import center_crop, changes
from test_holo import FakeAPI, PixelClient, PNG, call, response


def png(color, size=(854, 480)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def parsed(name, **args):
    return parse_calls({"tool_calls": [call(name, **args)]}, "skills")[0]


def test_pixel_transforms_and_errors():
    a, b = png("black"), png("white")
    assert changes(a, a) == {"frame_change": 0, "center_change": 0, "center_changed_fraction": 0}
    assert changes(a, b) == {"frame_change": 1, "center_change": 1, "center_changed_fraction": 1}
    assert Image.open(io.BytesIO(center_crop(a))).size == (320, 240)
    assert Image.open(io.BytesIO(center_crop(png("black", (20, 10))))).size == (40, 20)
    with pytest.raises(ValueError, match="dimensions"):
        changes(a, PNG)
    with pytest.raises(OSError):
        center_crop(b"not a picture")


@pytest.mark.parametrize("name,args", [
    ("move", {"direction": "forward", "seconds": 1, "jump": 1}),
    ("move", {"direction": "forward", "seconds": 1, "sprint": True, "sneak": True}),
    ("aim_at", {"x": 1001, "y": 500}), ("set_pitch", {"deg": -91}),
    ("mine_up", {"count": True}), ("mine_up", {"count": 0}),
    ("attack_until_broken", {"max_s": 0}), ("update_notes", {"text": "x" * 4001})])
def test_skill_validation(name, args):
    with pytest.raises(InvalidToolArgs):
        parsed(name, **args)


def test_disclosed_skills_and_pure_raw_vocabulary():
    assert all("screenshot-only" in t["function"]["description"] for t in tool_schemas("skills"))
    assert all("frame_change" not in t["function"]["description"] for t in tool_schemas("raw"))
    with pytest.raises(InvalidToolArgs):
        parse_calls({"tool_calls": [call("set_pitch", deg=0)]}, "raw")
    assert parsed("move", direction="forward", seconds=0)["arguments"] == {"direction": "forward", "seconds": 0, "intent": "Observe the world."}


@pytest.mark.parametrize("flags,codes", [({"jump": True, "sprint": True}, [87, 341, 32]), ({"sneak": True}, [87, 340]), ({}, [87])])
async def test_move_holds_concurrently_then_releases(flags, codes):
    c = PixelClient()
    ex = ToolExecutor(c, 854, 480)
    ex.begin_step(PNG)
    await ex.execute(parsed("move", direction="forward", seconds=0, **flags), "skills")
    keys = [event for event in c.events if isinstance(event, tuple) and event[0] == "key"]
    assert [k[1] for k in keys] == codes + list(reversed(codes))
    assert [k[2]["pressed"] for k in keys] == [True] * len(codes) + [False] * len(codes)
    assert not ex.held_keys
    assert not ex.results[-1]["stuck"]
    await ex.execute(parsed("move", direction="forward", seconds=0, **flags), "skills")
    assert ex.results[-1]["stuck"] and ex.results[-1]["frame_change"] == 0


@pytest.mark.parametrize("x,y", [(500, 500), (1000, 0), (0, 1000)])
async def test_aim_fov_geometry(x, y):
    c = PixelClient()
    ex = ToolExecutor(c, 854, 480)
    ex.begin_step(PNG)
    await ex.execute(parsed("aim_at", x=x, y=y), "skills")
    action = ex.actions[0]
    f = 240 / math.tan(math.radians(35))
    px, py = (x / 1000 - .5) * 853, (y / 1000 - .5) * 479
    assert action["dx"] * .15 == pytest.approx(math.degrees(math.atan(px / f)))
    assert action["dy"] * .15 == pytest.approx(math.degrees(math.atan(py / math.hypot(f, px))))


@pytest.mark.parametrize("degrees", [-90, 0, 90])
async def test_set_pitch_clamps_without_reading_state(degrees):
    ex = ToolExecutor(PixelClient(), 854, 480)
    ex.begin_step(PNG)
    await ex.execute(parsed("set_pitch", deg=degrees), "skills")
    assert [a["dy"] for a in ex.actions] == [1200, (degrees - 90) / .15]


@pytest.mark.parametrize("changed", [False, True])
async def test_attack_observes_without_early_release_or_semantic_claims(monkeypatch, changed):
    clock = [0.0]
    monkeypatch.setattr("mc_observatory.holo_tools.time", type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])}))
    class Client(PixelClient):
        async def screenshot(self):
            return png("white" if changed else "black")
    class Executor(ToolExecutor):
        async def primitive(self, name, **args):
            if name == "wait":
                clock[0] += args["ms"] / 1000
            else:
                await super().primitive(name, **args)
    ex = Executor(Client(), 854, 480)
    ex.begin_step(png("black"))
    result = await ex.execute(parsed("attack_until_broken", max_s=1.4), "skills")
    assert result["outcome"] == ("scene_changed" if changed else "no_change")
    assert result["scene_changed"] is changed
    assert result["block_gone"] is None and result["item_collected"] is None
    assert clock[0] == pytest.approx(1.4)
    assert len(ex.frames) == 5
    assert ex.actions[0] == {"action": "mouse", "button": 0, "pressed": True}
    assert ex.actions[-1] == {"action": "mouse", "button": 0, "pressed": False}
    assert not ex.held_buttons


@pytest.mark.parametrize("tier", ["raw", "skills"])
async def test_observation_barrier_discards_dependent_calls_and_explains_history(tmp_path, tier):
    client = PixelClient()
    tools = ([call("update_notes", text="Observe after aiming"), call("aim_at", x=500, y=500),
              call("move", direction="forward", seconds=0), call("attack", seconds=0)] if tier == "skills"
             else [call("mouse_move", dx=0, dy=0), call("key_press", key="w", ms=0)])
    api = FakeAPI(client, response(tools))
    agent = HoloAgent(HoloConfig(max_steps=2, tier=tier), api)
    records = [r async for r in agent.steps(client, "get log", tmp_path)]
    assert len(records[0]["raw_tool_calls"]) == len(tools)
    assert [c["name"] for c in records[0]["tool_calls"]] == (["update_notes", "aim_at"] if tier == "skills" else ["mouse_move", "key_press"])
    assert [c["function"]["name"] for c in records[0]["skipped_tool_calls"]] == (["move", "attack"] if tier == "skills" else [])
    assert any(e[0] == "key" for e in client.events if isinstance(e, tuple)) is (tier == "raw")
    history = json.loads(api.payloads[1]["messages"][1]["content"][0]["text"].split(": ", 1)[1])
    assert history[0]["actions"] == records[0]["tool_calls"]
    assert history[0]["skipped_tool_calls"] == records[0]["skipped_tool_calls"]


async def test_observation_barrier_empty_completion_does_not_invent_actions(tmp_path):
    agent = HoloAgent(HoloConfig(max_steps=1), FakeAPI(PixelClient(), response([])))
    records = [r async for r in agent.steps(PixelClient(), "get log", tmp_path)]
    assert records[0]["tool_calls"] == records[0]["skipped_tool_calls"] == records[0]["executed_actions"] == []


@pytest.mark.parametrize("tail", [[{"function": {"name": "attack", "arguments": "{"}}], [None], [{"function": {"name": "unknown", "arguments": {}}}]])
async def test_observation_barrier_ignores_malformed_discarded_calls(tmp_path, tail):
    client = PixelClient()
    tools = [call("update_notes", text="Observe first"), call("aim_at", x=500, y=500), *tail]
    api = FakeAPI(client, response(tools))
    agent = HoloAgent(HoloConfig(max_steps=1), api)
    record = [r async for r in agent.steps(client, "get log", tmp_path)][0]
    assert not record["errors"]
    assert [c["name"] for c in record["tool_calls"]] == ["update_notes", "aim_at"]
    assert record["skipped_tool_calls"] == tail
    assert any(a["action"] == "mouse" for a in record["executed_actions"])


async def test_attack_screenshot_error_releases_mouse():
    class Client(PixelClient):
        async def screenshot(self):
            raise OSError("capture failed")
    class Executor(ToolExecutor):
        async def primitive(self, name, **args):
            if name != "wait":
                await super().primitive(name, **args)
    ex = Executor(Client(), 854, 480)
    ex.begin_step(PNG)
    with pytest.raises(OSError, match="capture failed"):
        await ex.execute(parsed("attack_until_broken", max_s=1), "skills")
    assert not ex.held_buttons
    assert ex.actions[-1] == {"action": "mouse", "button": 0, "pressed": False}


async def test_attack_final_sample_timeout_checks_released_settled_frame(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("mc_observatory.holo_tools.time", type("Clock", (), {"monotonic": staticmethod(lambda: clock[0])}))
    async def timeout(capture, seconds):
        capture.close()
        raise asyncio.TimeoutError
    monkeypatch.setattr("mc_observatory.holo_tools.asyncio.wait_for", timeout)
    class Client(PixelClient):
        async def screenshot(self):
            assert not ex.held_buttons
            return png("white")
    class Executor(ToolExecutor):
        async def primitive(self, kind, **args):
            if kind == "wait":
                clock[0] += args["ms"] / 1000
            else:
                await super().primitive(kind, **args)
    ex = Executor(Client(), 854, 480)
    ex.begin_step(png("black"))
    result = await ex.execute(parsed("attack_until_broken", max_s=.35), "skills")
    assert result["outcome"] == "scene_changed" and result["scene_changed"]
    assert result["block_gone"] is None and result["item_collected"] is None
    assert len(ex.frames) == 1
    assert ex.actions[-1] == {"action": "mouse", "button": 0, "pressed": False}


@pytest.mark.parametrize("name,args", [("move", {"direction": "forward", "seconds": 1, "jump": True}), ("attack_until_broken", {"max_s": 1})])
async def test_action_error_releases_controls(name, args):
    class Executor(ToolExecutor):
        async def primitive(self, method, **values):
            if method == "wait":
                raise RuntimeError("input failed")
            await super().primitive(method, **values)
    ex = Executor(PixelClient(), 854, 480)
    ex.begin_step(PNG)
    with pytest.raises(RuntimeError, match="input failed"):
        await ex.execute(parsed(name, **args), "skills")
    assert not ex.held_keys and not ex.held_buttons


async def test_mine_up_count_and_notes(monkeypatch):
    ex = ToolExecutor(PixelClient(), 854, 480)
    ex.begin_step(PNG)
    attempts = []
    async def attack(seconds):
        attempts.append(seconds)
        return "no_change"
    monkeypatch.setattr(ex, "attack_until_broken", attack)
    result = await ex.execute(parsed("mine_up", count=3), "skills")
    assert attempts == [4, 4, 4] and result["outcome"] == ["no_change"] * 3
    assert ex.actions[:2] == [{"action": "mouse", "dx": 0, "dy": 1200}, {"action": "mouse", "dx": 0, "dy": -1200}]
    for text in ("I saw a trunk; try jumping.", ""):
        await ex.execute(parsed("update_notes", text=text), "skills")
        assert ex.notes == text


@pytest.mark.parametrize("tier", ["raw", "skills"])
async def test_three_frames_crop_notes_and_pixel_boundary(tmp_path, tier):
    c = PixelClient()
    api = FakeAPI(c, response([call("update_notes", text="MY OBSERVATION")]) if tier == "skills" else response([call("wait", ms=0)]))
    agent = HoloAgent(HoloConfig(max_steps=4, tier=tier), api)
    records = [r async for r in agent.steps(c, "get log", tmp_path)]
    assert len(records[-1]["image_refs"]) == 3
    assert len(records[-1]["input_image_refs"]) == 4
    content = api.payloads[-1]["messages"][1]["content"]
    image_parts = [p for p in content if p["type"] == "image_url"]
    refs = records[-1]["input_image_refs"]
    assert len(refs) == len(image_parts)
    for ref, part in zip(refs, image_parts):
        assert (tmp_path / ref).read_bytes() == base64.b64decode(part["image_url"]["url"].split(",")[1])
    text = json.dumps(content)
    assert "not game-state feedback" not in text
    assert ("MY OBSERVATION" in text) == (tier == "skills")
    assert bool(records[-1]["tool_results"]) == (tier == "skills")
    assert ("center_change" in text) == (tier == "skills")
    assert len(PRIMER.split()) < 550


async def test_model_input_hook_only_announces_api_payloads(tmp_path):
    announcements = []
    class Client(PixelClient):
        async def model_input(self, step, content, refs):
            announcements.append((step, content, refs))
            self.events.append("model_input")
    c = Client()
    api = FakeAPI(c)
    agent = HoloAgent(HoloConfig(max_steps=2), api)
    records = [r async for r in agent.steps(c, "observe", tmp_path)]
    assert c.events.count("screenshot") == 4
    assert c.events.count("model_input") == c.events.count("api") == 2
    for idx, (_, content, refs) in enumerate(announcements):
        assert content == api.payloads[idx]["messages"][1]["content"]
        assert refs == records[idx]["input_image_refs"]
    assert records[0]["feedback_image_refs"][0] not in announcements[0][2]
    assert records[0]["feedback_image_refs"][0] in announcements[1][2]
