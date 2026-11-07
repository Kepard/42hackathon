from types import SimpleNamespace

import pytest

from mc_observatory.holo import HoloAgent, HoloConfig
from mc_observatory.holo_tools import ToolExecutor, tool_schemas, parse_calls, observation_prefix
from mc_observatory.jev import JevVision
from mc_observatory.jev_eval import PRICE_PER_INPUT_TOKEN, RESERVE_PER_CALL
from test_holo import PixelClient, PNG, call, FakeAPI, response

pytest.importorskip("typesafe_sdk")


class FakeJev:
    def __init__(self, p=.9, box=(.4, .3, .6, .7), error=None):
        self.p, self.box, self.error = p, box, error
        self.inputs = []

    async def system_one(self, **kwargs):
        self.inputs.append(kwargs)
        assert set(kwargs["state"]) == {"screenshot"}
        assert "next_move" not in kwargs["questions"]
        assert "crosshair_on_log" not in kwargs["questions"]
        if self.error:
            raise self.error
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=100),
            nouls={"visible": SimpleNamespace(noul=self.p)},
            boxes={"target": SimpleNamespace(box=SimpleNamespace(left=self.box[0], top=self.box[1], right=self.box[2], bottom=self.box[3]))})


def executor(fake):
    ex = ToolExecutor(PixelClient(), 640, 480, JevVision(fake))
    ex.begin_step(PNG)
    return ex


@pytest.mark.parametrize("p,box,outline,expected", [
    (.1, (.4,.3,.6,.7), True, "not_visible"),
    (.9, (.4,.3,.6,.7), True, "in_reach"),
    (.9, (.7,.3,.9,.7), True, "timeout"),
    (.9, (.4,.3,.6,.7), False, "timeout"),
])
async def test_seek_stop_and_outline_must_be_inside_box(monkeypatch, p, box, outline, expected):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _: outline)
    ex = executor(FakeJev(p, box))
    assert await ex.seek("oak log trunk", .01) == expected
    assert not ex.held_keys and not ex.held_buttons
    assert ex.jev.calls[0]["cost_usd"] == 100 * PRICE_PER_INPUT_TOKEN
    assert len(ex.frames) > 0 and ex.frames[0] == PNG


async def test_seek_proportional_aim_is_clamped_and_does_not_walk_off_target(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _: False)
    ex = executor(FakeJev(box=(.8,.8,1,1)))
    await ex.seek("iron ore (orange-tan spots)", .01)
    mouse = next(a for a in ex.actions if a["action"] == "mouse")
    assert mouse["dx"] * .15 == 12
    assert mouse["dy"] * .15 == 8
    assert not any(a["action"] == "key" for a in ex.actions)


async def test_unchanged_walking_stops_stuck(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _: False)
    ex = executor(FakeJev())
    assert await ex.seek("stone", 1.8) == "stuck"
    assert not ex.held_keys


async def test_error_stops_without_walking_and_logs_unknown_cost(monkeypatch):
    ex = executor(FakeJev(error=RuntimeError("no service")))
    assert await ex.seek("lava", 1) == "vision_error"
    assert ex.jev.calls[0]["cost_usd"] == RESERVE_PER_CALL
    assert ex.jev.calls[0]["error"] == "RuntimeError"
    assert not ex.held_keys


@pytest.mark.parametrize("p,box,outline,error,expected", [
    (.1, (.4,.3,.6,.7), False, None, "not_visible"),
    (.9, (.4,.3,.6,.7), True, None, "in_reach"),
    (.9, (.8,.8,1,1), False, None, "timeout"),
    (.9, (.4,.3,.6,.7), False, RuntimeError("failed"), "vision_error"),
])
async def test_seek_always_captures_settled_feedback(monkeypatch, p, box, outline, error, expected):
    from test_pixel_skills import png
    dark, light = png("black", (640,480)), png("white", (640,480))
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _: outline)

    class Client(PixelClient):
        captures = 0
        async def screenshot(self):
            self.captures += 1
            return dark if self.captures == 1 else light

    client, fake = Client(), FakeJev(p, box, error)
    ex = ToolExecutor(client, 640,480, JevVision(fake))
    ex.begin_step(dark)
    result = await ex.execute({"name": "seek", "arguments": {"target": "log", "max_s": .01}}, "skills")
    assert result["outcome"] == expected
    assert client.captures == len(fake.inputs) + 1
    assert ex.current_frame == ex.frames[-1] == light
    assert result["scene_changed"]
    assert not ex.held_keys and not ex.held_buttons


async def test_mining_requires_existing_outline_not_jev_confidence(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _: False)
    fake = FakeJev()
    ex = executor(fake)
    assert await ex.attack_until_broken(.35) == "not_in_reach"
    assert not fake.inputs and not ex.held_buttons


@pytest.mark.parametrize("overhead_outline", [False, True])
async def test_mine_up_checks_outline_in_fresh_overhead_view(monkeypatch, overhead_outline):
    from test_pixel_skills import png
    dark, light = png("black", (640,480)), png("white", (640,480))
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda frame: frame == light)

    class Client(PixelClient):
        async def screenshot(self):
            return light if overhead_outline else dark

    ex = ToolExecutor(Client(), 640,480, JevVision(FakeJev()))
    ex.begin_step(dark if overhead_outline else light)
    ex.attack_until_broken = lambda _: ToolExecutor.attack_until_broken(ex, 0)
    result = await ex.execute({"name": "mine_up", "arguments": {"count": 1}}, "skills")
    assert result["outcome"] == (["no_change"] if overhead_outline else ["not_in_reach"])
    assert any(a.get("button") == 0 and a.get("pressed") for a in ex.actions) is overhead_outline


async def test_collect_advice_is_not_pickup_proof():
    ex = executor(FakeJev())
    result = await ex.execute({"name": "collect", "arguments": {"max_s": .01,"item": "oak log"}}, "skills")
    assert result["item_collected"] is None
    assert result["hotbar_item_advisory"]["noul"] == .9
    assert result["hotbar_item_advisory"]["not_collection_proof"]
    assert ex.jev.calls[-1]["kind"] == "hotbar"


def test_opt_in_schemas_raw_unchanged_and_barrier():
    assert tool_schemas("raw", "jev") == tool_schemas("raw")
    assert "seek" not in [t["function"]["name"] for t in tool_schemas("skills")]
    assert "seek" in [t["function"]["name"] for t in tool_schemas("skills", "jev")]
    c = call("seek", target="gravel")
    assert parse_calls({"tool_calls": [c]}, "skills", "jev")[0]["name"] == "seek"
    assert observation_prefix([c,call("attack",seconds=4)], "jev") == ([c],[call("attack",seconds=4)])
    with pytest.raises(ValueError):
        HoloConfig(tier="raw", reflex="jev")


async def test_agent_logs_jev_inputs_and_aggregate_cost_and_low_setting(tmp_path):
    client, fake = PixelClient(), FakeJev(p=.1)
    api = FakeAPI(client,response([call("seek",target="log",max_s=.2)]))
    agent = HoloAgent(HoloConfig(reflex="jev", reasoning_effort="low", max_steps=1), api, jev_client=fake)
    records = [r async for r in agent.steps(client,"log",tmp_path)]
    r = records[0]
    assert r["cost_usd"] == pytest.approx(r["holo_cost_usd"] + r["jev_cost_usd"])
    assert r["jev_cost_usd"] == 100 * PRICE_PER_INPUT_TOKEN
    assert len(r["jev_calls"]) == 1
    assert (tmp_path / r["jev_calls"][0]["screenshot_ref"]).read_bytes() == PNG
    assert api.payloads[0]["reasoning_effort"] == "low"
    assert r["tool_results"][0]["outcome"] == "not_visible"
