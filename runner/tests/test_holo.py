import asyncio
import base64
import json
import io

from PIL import Image

import pytest

from mc_observatory.holo import HoloAgent, HoloConfig, RunController, usage_cost
from mc_observatory.holo_tools import InvalidToolArgs, ToolExecutor, parse_calls, tool_schemas
from mc_observatory.run import FailureDetector, MinecraftRunner, summarize
from mc_observatory.evaluator import LadderEvaluator


_buffer = io.BytesIO()
Image.new("RGB", (640, 480), (90, 100, 110)).save(_buffer, format="PNG")
PNG = _buffer.getvalue()


def call(name="wait", **args):
    return {"id": "call-1", "type": "function", "function": {"name": name, "arguments": json.dumps({"intent": "Observe the world.", **args})}}


def response(calls=None, **message):
    return {"choices": [{"message": {"tool_calls": calls if calls is not None else [call(seconds=0)], **message}}],
            "usage": {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 200}, "completion_tokens": 100,
                      "completion_tokens_details": {"reasoning_tokens": 30}}}


class PixelClient:
    def __init__(self):
        self.events = []

    async def pause(self): self.events.append("pause")
    async def resume(self): self.events.append("resume")
    async def screenshot(self): self.events.append("screenshot"); return PNG
    async def key(self, code, **args): self.events.append(("key", code, args))
    async def mouse(self, **args): self.events.append(("mouse", args))
    async def type_text(self, text): self.events.append(("type", text))
    async def state(self): raise AssertionError("Agent must not read state")
    async def call(self, command, **fields):
        assert command == "configure"
        self.settings = fields


class FakeAPI:
    def __init__(self, client, result=None, error=None):
        self.client, self.result, self.error = client, result or response(), error
        self.payloads = []

    async def complete(self, payload):
        self.client.events.append("api")
        self.payloads.append(payload)
        if self.error:
            raise self.error
        return self.result


def test_cost_math():
    tokens, cost = usage_cost(response()["usage"], "holo4-27b")
    assert tokens == {"in": 1000, "cached": 200, "out": 100, "reasoning": 30}
    assert cost == pytest.approx((800 * .40 + 200 * .04 + 100 * 3) / 1e6)
    assert usage_cost(response()["usage"], "holo4-35b-a3b")[1] == pytest.approx(.000446)
    assert usage_cost({}, "custom-id")[1] is None


@pytest.mark.parametrize("bad", [call("unknown"), call(seconds=-1), call(seconds=True), call(seconds=float("nan")),
                                  call(seconds=1, extra=1), {"function": {"name": "wait", "arguments": "{"}},
                                  {"function": {"name": "wait", "arguments": "null"}}, call("select_hotbar", n=1.2),
                                  {"function": {"name": "wait", "arguments": '{"seconds": 1}'}},
                                  {"function": {"name": "wait", "arguments": '{"seconds": 1, "intent": ""}'}}])
def test_malformed_calls(bad):
    with pytest.raises(InvalidToolArgs):
        parse_calls({"tool_calls": [bad]}, "skills")


def test_schema_and_sequential_parse():
    assert parse_calls({}, "raw") == []
    calls = parse_calls({"tool_calls": [call("key_down", key="w"), call("wait", ms=2)]}, "raw")
    assert [c["name"] for c in calls] == ["key_down", "wait"]
    for tier in ("raw", "skills"):
        for tool in tool_schemas(tier):
            assert "intent" in tool["function"]["parameters"]["required"]


@pytest.mark.parametrize("tier,name,args", [("raw", "key_press", {"key": "f3", "ms": 0}), ("skills", "toggle_debug", {})])
async def test_f3_is_pixel_only_keyboard_input(tier, name, args):
    client = PixelClient()
    executor = ToolExecutor(client, 640, 480)
    parsed = parse_calls({"tool_calls": [call(name, **args)]}, tier)
    await executor.execute(parsed[0], tier)
    assert executor.actions[0] == {"action": "key", "key": "f3", "pressed": True}
    assert executor.actions[-1] == {"action": "key", "key": "f3", "pressed": False}
    assert ("key", 292, {"pressed": True}) in client.events
    with pytest.raises(InvalidToolArgs):
        parse_calls({"tool_calls": [call("toggle_debug", read_text=True)]}, "skills")


def test_realtime_default_and_hud_prompt():
    from mc_observatory.holo import PRIMER
    from mc_observatory.server import StartRun
    assert HoloConfig().timing == StartRun().timing == "realtime"
    assert HoloConfig().history_images == 3
    assert "block selection outline" in PRIMER and "only from pixels" in PRIMER


async def test_freeze_order_pixels_only_and_history(tmp_path):
    client = PixelClient()
    api = FakeAPI(client, response(reasoning_content="I should observe."))
    agent = HoloAgent(HoloConfig(max_steps=2, timing="freeze"), api)
    records = []
    async for record in agent.steps(client, "Get a log", tmp_path):
        record["evaluator"] = {"secret": "never send score"}
        record["metrics"] = {"x": 1234567}
        records.append(record)
    assert client.events.index("pause") < client.events.index("api") < client.events.index("resume")
    assert client.events[-1] == "pause"
    assert records[0]["reasoning"] == "I should observe."
    assert records[0]["tokens"]["reasoning"] == 30
    payload_text = json.dumps(api.payloads)
    assert "never send score" not in payload_text and "1234567" not in payload_text
    for index, payload in enumerate(api.payloads):
        images = [part for part in payload["messages"][1]["content"] if part["type"] == "image_url"]
        assert len(images) == (2 if index == 0 else 4)
        assert base64.b64decode(images[0]["image_url"]["url"].split(",")[1]) == PNG
        crop = Image.open(io.BytesIO(base64.b64decode(images[-1]["image_url"]["url"].split(",")[1])))
        assert crop.size == (320, 240)
    assert (tmp_path / records[0]["screenshot_ref"]).read_bytes() == PNG


async def test_realtime_never_pauses_and_multiple_tools(tmp_path):
    client = PixelClient()
    api = FakeAPI(client, response([call("key_press", key="w", ms=0), call("mouse_move", dx=2, dy=3)]))
    agent = HoloAgent(HoloConfig(tier="raw", timing="realtime", max_steps=1), api)
    records = [r async for r in agent.steps(client, "Move", tmp_path)]
    assert "pause" not in client.events and "resume" not in client.events
    assert [a["action"] for a in records[0]["executed_actions"]] == ["key", "wait", "key", "mouse"]


@pytest.mark.parametrize("result,error,tag", [(response([]), None, "no_tool_call"),
                                             (response([call(seconds=-2)]), None, "invalid_tool_args"),
                                             (None, RuntimeError("unavailable"), "api_error")])
async def test_api_and_tool_failures(tmp_path, result, error, tag):
    client = PixelClient()
    agent = HoloAgent(HoloConfig(max_steps=1), FakeAPI(client, result, error))
    records = [r async for r in agent.steps(client, "Observe", tmp_path)]
    assert tag in records[0]["failure_tags"]
    assert "resume" not in client.events


async def test_timeout_releases_controls(tmp_path):
    client = PixelClient()
    api = FakeAPI(client, response([call("attack", seconds=10)]))
    agent = HoloAgent(HoloConfig(time_budget=.03, timing="freeze"), api)
    records = [r async for r in agent.steps(client, "Mine", tmp_path)]
    assert "timeout" in records[0]["failure_tags"]
    assert ("mouse", {"button": 0, "pressed": False}) in client.events
    assert client.events[-1] == "pause"


async def test_api_timeout_and_cost_budget(tmp_path):
    client = PixelClient()

    class SlowAPI:
        async def complete(self, payload): await asyncio.sleep(1)

    agent = HoloAgent(HoloConfig(api_timeout=.01), SlowAPI())
    records = [r async for r in agent.steps(client, "Observe", tmp_path / "timeout")]
    assert {"timeout", "api_error"} <= set(records[0]["failure_tags"])
    agent = HoloAgent(HoloConfig(max_cost_usd=.000001), FakeAPI(client))
    records = [r async for r in agent.steps(client, "Observe", tmp_path / "cost")]
    assert "budget_exhausted" in records[0]["failure_tags"]
    assert records[0]["executed_actions"] == []


async def test_macros_use_only_inputs_and_shift_coordinates():
    client = PixelClient()
    executor = ToolExecutor(client, 640, 480)
    calls = parse_calls({"tool_calls": [call("look", yaw_deg=15, pitch_deg=-15), call("shift_click_at", x=1000, y=1000)]}, "skills")
    for item in calls:
        await executor.execute(item, "skills")
    assert ("mouse", {"dx": 100, "dy": -100}) in client.events
    assert ("mouse", {"x": 639, "y": 479}) in client.events
    inputs = [event for event in client.events if isinstance(event, tuple)]
    assert inputs[1] == ("key", 340, {"pressed": True})
    assert inputs[-1] == ("key", 340, {"pressed": False})


async def test_controller_pause_stop_goal_and_events():
    controller = RunController()
    await controller.pause()
    task = asyncio.create_task(controller.checkpoint())
    await asyncio.sleep(0)
    assert not task.done()
    await controller.set_goal("Build a table")
    await controller.resume()
    assert await task and controller.goal == "Build a table"
    await controller.pause()
    task = asyncio.create_task(controller.checkpoint())
    await controller.stop()
    assert not await task
    await controller.emit({"step": 1})
    assert await controller.events.get() == {"step": 1}


def test_failure_detectors_and_summary():
    detector = FailureDetector(window=2)
    record = {"tool_calls": [{"name": "move", "arguments": {"direction": "forward", "seconds": 1, "intent": "Walk."}}],
              "executed_actions": [{"action": "key", "key": "w", "pressed": True}]}
    state = {"x": 1, "y": 2, "z": 3, "screen_open": True}
    detector.update(record, state, 0)
    detector.update(record, state, 0)
    tags = detector.update(record, {**state, "health": 0}, 0)
    assert set(tags) == {"gui_stuck", "stuck_position", "repeated_action_loop", "death"}
    assert "gui_stuck" not in detector.update(record, {**state, "x": 2}, 1)
    assert "stuck_position" not in detector.update(record, {**state, "x": 3}, 1)
    summary = summarize([], LadderEvaluator(), {}, False, 1, {"timeout"}, HoloConfig())
    assert summary["score"] == 0 and summary["failure_tags"] == ["timeout"]


@pytest.mark.parametrize("timing", ["freeze", "realtime"])
async def test_runner_scores_without_exposing_state_and_emits(tmp_path, timing):
    class ScoringClient(PixelClient):
        async def reset(self, *args):
            self.reset_args = args
            return {"world_id": "fresh"}
        async def state(self): return {"world_id": "fresh", "inventory": [{"id": "minecraft:oak_log", "count": 1}]}

    client = ScoringClient()
    api = FakeAPI(client)
    controller = RunController()
    agent = HoloAgent(HoloConfig(max_steps=2, timing=timing), api, controller)
    summary = await MinecraftRunner(agent, client, tmp_path, tracing=False).run("get_log")
    assert summary["success"] and summary["steps"] == 1 and summary["milestones"]["log"] >= 0
    assert (await controller.events.get())["evaluator"]["completed"] == ["log"]
    assert "minecraft:oak_log" not in json.dumps(api.payloads)
    assert (tmp_path / "summary.json").exists()
    initial = json.loads((tmp_path / "initial_state.json").read_text())
    assert client.reset_args == (7941, [], initial["task"]["spawn"])
    assert client.settings == {"auto_jump": True}
    assert initial["state"] == {"world_id": "fresh"}
    assert client.events[0] == ("pause" if timing == "freeze" else "resume")


@pytest.mark.parametrize("kwargs", [{"tier": "bad"}, {"history_images": 0}, {"max_steps": 0}, {"time_budget": -1},
                                    {"temperature": 3}, {"model": "other", "max_cost_usd": 1}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError): HoloConfig(**kwargs)


def test_agent_uses_environment_key_without_explicit_api(monkeypatch):
    monkeypatch.setenv("HCOMPANY_API_KEY", "test-only")
    assert HoloAgent().api.api_key == "test-only"
    monkeypatch.delenv("HCOMPANY_API_KEY")
    with pytest.raises(ValueError, match="HCOMPANY_API_KEY"):
        HoloAgent()


async def test_stop_during_setup_is_honored(tmp_path):
    controller = RunController()

    class ScoringClient(PixelClient):
        async def reset(self, *args):
            await controller.stop()
            return {}

    client = ScoringClient()
    api = FakeAPI(client)
    summary = await MinecraftRunner(HoloAgent(HoloConfig(), api, controller), client, tmp_path, tracing=False).run("get_log")
    assert summary["steps"] == 0 and not api.payloads


@pytest.mark.parametrize("calls,tag", [([], "no_tool_call"), ([call(seconds=-1)], "invalid_tool_args")])
async def test_cost_cap_applies_to_unusable_completions(tmp_path, calls, tag):
    client = PixelClient()
    api = FakeAPI(client, response(calls))
    agent = HoloAgent(HoloConfig(max_steps=5, max_cost_usd=.000001), api)
    rows = [r async for r in agent.steps(client, "Observe", tmp_path)]
    assert len(api.payloads) == len(rows) == 1
    assert {tag, "budget_exhausted"} <= set(rows[0]["failure_tags"])
    assert rows[0]["executed_actions"] == []


@pytest.mark.parametrize("elapsed,expected", [(119, True), (120, True), (150, False)])
async def test_runner_enforces_task_deadline(tmp_path, monkeypatch, elapsed, expected):
    from types import SimpleNamespace
    import mc_observatory.run as runner_module
    clock = [0]
    monkeypatch.setattr(runner_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Client(PixelClient):
        async def reset(self, *args): return {}
        async def state(self):
            clock[0] = elapsed
            return {"stats": {"crafted": {"minecraft:oak_planks": 4}}}

    client = Client()
    agent = HoloAgent(HoloConfig(max_steps=1), FakeAPI(client))
    summary = await MinecraftRunner(agent, client, tmp_path, tracing=False).run("craft_planks")
    assert summary["success"] is expected


@pytest.mark.parametrize("crafted", [True, False])
async def test_final_step_settles_lagging_stats(tmp_path, crafted):
    class Client(PixelClient):
        reads = 0
        async def reset(self, *args): return {}
        async def state(self):
            self.reads += 1
            return {"stats": {"crafted": {"minecraft:oak_planks": 4}}} if crafted and self.reads > 1 else {}

    client = Client()
    agent = HoloAgent(HoloConfig(max_steps=1, timing="freeze"), FakeAPI(client))
    summary = await MinecraftRunner(agent, client, tmp_path, tracing=False).run("craft_planks")
    assert summary["success"] is crafted and client.reads == 2
    assert json.loads((tmp_path / "steps.jsonl").read_text())["final_settle_ms"] == 600
    assert client.events[-1] == "pause"


async def test_failed_screenshot_has_no_stale_actions(tmp_path):
    class Client(PixelClient):
        reads = 0
        async def screenshot(self):
            self.reads += 1
            if self.reads > 1:
                raise ConnectionError("Screenshot unavailable")
            return PNG

    client = Client()
    api = FakeAPI(client, response([call("move", direction="forward", seconds=0)]))
    rows = [r async for r in HoloAgent(HoloConfig(max_steps=2), api).steps(client, "Move", tmp_path)]
    assert rows[0]["executed_actions"]
    assert rows[1]["executed_actions"] == []
    assert rows[1]["errors"]
