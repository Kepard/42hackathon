import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import ImageDraw

from mc_observatory.holo import HoloAgent, HoloConfig
from mc_observatory.holo_tools import ToolExecutor, tool_schemas, parse_calls, observation_prefix
from mc_observatory.jev import JevVision
from mc_observatory.jev_eval import PRICE_PER_INPUT_TOKEN, RESERVE_PER_CALL
from mc_observatory.vision import image
from test_holo import PixelClient, PNG, call, FakeAPI, response

pytest.importorskip("typesafe_sdk")


@pytest.fixture(autouse=True)
def isolate_hold_loop_from_close_approach(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(ToolExecutor, "approach_close", AsyncMock(return_value=True))


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
    (.9, (.3,.3,.7,.7), True, "in_reach"),
    (.9, (.7,.3,.9,.7), True, "timeout"),
    (.9, (.4,.3,.6,.7), False, "timeout"),
])
async def test_seek_stop_and_outline_must_be_inside_box(monkeypatch, p, box, outline, expected):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: outline)
    ex = executor(FakeJev(p, box))
    assert await ex.seek("oak log trunk", .01) == expected
    assert not ex.held_keys and not ex.held_buttons
    assert ex.jev.calls[0]["cost_usd"] == 100 * PRICE_PER_INPUT_TOKEN
    assert len(ex.frames) > 0 and ex.frames[0] == PNG


async def test_seek_proportional_aim_is_clamped_and_does_not_walk_off_target(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: False)
    ex = executor(FakeJev(box=(.8,.8,1,1)))
    await ex.seek("iron ore (orange-tan spots)", .01)
    mouse = next(a for a in ex.actions if a["action"] == "mouse")
    assert mouse["dx"] * .15 == 12
    assert mouse["dy"] * .15 == 8
    assert not any(a["action"] == "key" for a in ex.actions)


@pytest.mark.parametrize("box", [(.46,.4,.66,.6), (.3,.3,.7,.7)])
async def test_seek_aligns_before_walking_and_does_not_press_into_close_face(monkeypatch, box):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: False)
    ex = executor(FakeJev(box=box))
    assert await ex.seek("oak log trunk", .01) == ("close_unconfirmed" if box == (.3,.3,.7,.7) else "timeout")
    assert not ex.jev.calls[0]["walking"]
    assert not any(a["action"] == "key" and a.get("pressed") for a in ex.actions)


@pytest.mark.parametrize("width,height", [(854,480), (2560,1440)])
@pytest.mark.parametrize("box,walking", [((.44,.4,.56,.6), True), ((.3,.3,.7,.7), False)])
async def test_seek_standoff_scales_with_vertical_fov_resolution(monkeypatch, width, height, box, walking):
    from test_pixel_skills import png
    frame = png("black", (width,height))
    class Client(PixelClient):
        async def screenshot(self):
            return frame
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: False)
    ex = ToolExecutor(Client(), width,height, JevVision(FakeJev(box=box)))
    ex.begin_step(frame)
    await ex.seek("oak log trunk", .01)
    assert ex.jev.calls[0]["walking"] is walking
    assert any(a["action"] == "key" and a.get("pressed") for a in ex.actions) is walking


async def test_unchanged_walking_stops_stuck(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: False)
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
    (.9, (.3,.3,.7,.7), True, None, "in_reach"),
    (.9, (.8,.8,1,1), False, None, "timeout"),
    (.9, (.4,.3,.6,.7), False, RuntimeError("failed"), "vision_error"),
])
async def test_seek_always_captures_settled_feedback(monkeypatch, p, box, outline, error, expected):
    from test_pixel_skills import png
    dark, light = png("black", (640,480)), png("white", (640,480))
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: outline)

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
    assert client.captures == len(fake.inputs) + (2 if expected == "in_reach" else 1)
    assert ex.current_frame == ex.frames[-1] == light
    assert result["scene_changed"]
    assert not ex.held_keys and not ex.held_buttons


async def test_seek_does_not_report_transient_outline_as_settled_reach(monkeypatch):
    from test_pixel_skills import png
    first, settled = png("black", (640,480)), png("white", (640,480))
    class Client(PixelClient):
        captures = 0
        async def screenshot(self):
            self.captures += 1
            return first if self.captures == 1 else settled
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda frame, *args: frame == first)
    ex = ToolExecutor(Client(), 640,480, JevVision(FakeJev(box=(.3,.3,.7,.7))))
    ex.begin_step(first)
    assert await ex.seek("oak log trunk", .01) == "timeout"
    assert ex.jev.calls[0]["reach_unconfirmed"]
    assert ex.current_frame == settled
    assert not ex.held_keys and not ex.held_buttons


async def test_seek_confirms_reachable_off_center_face_without_steering(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: True)
    ex = executor(FakeJev(box=(.49,.3,.79,.7)))
    assert await ex.seek("oak log trunk", .01) == "in_reach"
    assert ex.jev.calls[0]["decision"] == "in_reach"
    assert not any(a["action"] in ("mouse", "key") for a in ex.actions)


async def test_failed_mining_pixels_prevent_reusing_the_same_false_reach_cue(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: True)
    ex = executor(FakeJev())
    assert await ex.attack_until_broken(.35) == "no_change"
    assert ex.failed_mining_frame == PNG
    ex.begin_step(PNG)
    assert await ex.seek("oak log trunk", .01) == "timeout"
    assert ex.jev.calls[0]["walking"]
    assert any(a["action"] == "key" and a.get("pressed") for a in ex.actions)


async def test_failed_mining_at_close_face_reports_stuck_with_recovery(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: True)
    ex = executor(FakeJev(box=(.3,.3,.7,.7)))
    assert await ex.attack_until_broken(.35) == "no_change"
    ex.begin_step(PNG)
    result = await ex.execute({"name": "seek", "arguments": {"target": "oak log trunk", "max_s": .01}}, "skills")
    assert result["outcome"] == "stuck" and result["stuck"]
    assert "reposition" in result["next_action"]
    assert ex.jev.calls[0]["reason"] == "unchanged_mining_target_too_close"
    assert not any(a["action"] in ("mouse", "key") for a in ex.actions)


async def test_different_off_center_target_can_steer_after_failed_mining(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: True)
    ex = executor(FakeJev(box=(.4,.3,.9,.7)))
    assert await ex.attack_until_broken(.35) == "no_change"
    ex.begin_step(PNG)
    result = await ex.execute({"name": "seek", "arguments": {"target": "stone", "max_s": .01}}, "skills")
    assert result["outcome"] == "timeout" and not result["stuck"]
    assert ex.jev.calls[0]["decision"] == "steer"
    assert ex.jev.calls[0]["yaw_deg"] > 0
    assert not ex.held_keys and not ex.held_buttons


async def test_seek_rejects_outline_rectangle_outside_jev_target():
    from pathlib import Path
    frame = (Path(__file__).parent / "fixtures" / "outline_distant_background.png").read_bytes()
    class Client(PixelClient):
        async def screenshot(self):
            return frame
    ex = ToolExecutor(Client(), 854,480, JevVision(FakeJev(box=(.47,.46,.54,.58))))
    ex.begin_step(frame)
    assert await ex.seek("oak log trunk", .15) == "timeout"
    assert not ex.held_keys and not ex.held_buttons


@pytest.mark.parametrize("use_jev", [False, True])
async def test_attack_footer_change_has_consistent_scene_feedback_but_no_target_progress(monkeypatch, use_jev):
    from test_pixel_skills import png
    before = png("black", (640,480))
    footer = image(before)
    footer.paste((255,255,255), (0,400,640,430))
    buffer = io.BytesIO()
    footer.save(buffer, format="PNG")
    class Client(PixelClient):
        async def screenshot(self):
            return buffer.getvalue()
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: True)
    ex = ToolExecutor(Client(), 640,480, JevVision(FakeJev()) if use_jev else None)
    ex.begin_step(before)
    result = await ex.execute({"name": "attack_until_broken", "arguments": {"max_s": .35}}, "skills")
    assert result["outcome"] == ("no_change" if use_jev else "scene_changed")
    assert result["scene_changed"] is (not use_jev)
    assert result["center_change"] == 0
    if use_jev:
        assert result["mining_progress_pixel_cue"] is False
        assert result["next_action"] == "call seek(<target>) first"


@pytest.mark.parametrize("mode", ["cracks", "unchanged", "hud", "capture_timeout"])
async def test_real_bark_probe_continues_only_on_target_progress(monkeypatch, mode):
    before = (Path(__file__).parent / "fixtures" / "mining_bark_probe.png").read_bytes()
    broken = (Path(__file__).parent / "fixtures" / "mining_bark_broken.png").read_bytes()
    cracks = image(before)
    ImageDraw.Draw(cracks).line((400,215,430,250,410,265), fill="black", width=3)
    hud = image(before)
    hud.paste((255,255,255), (0,400,200,430))
    def encode(im):
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()
    clock = [0.]
    monkeypatch.setattr("mc_observatory.holo_tools.time", SimpleNamespace(monotonic=lambda: clock[0]))
    class Client(PixelClient):
        count = 0
        async def screenshot(self):
            self.count += 1
            if mode == "capture_timeout" and self.count == 2:
                raise asyncio.TimeoutError
            if clock[0] >= 3.3 and mode == "cracks":
                return broken
            if clock[0] >= .6:
                return encode(cracks) if mode == "cracks" else encode(hud) if mode == "hud" else before
            return before
    class Executor(ToolExecutor):
        async def primitive(self, kind, **args):
            if kind == "wait":
                clock[0] += args["ms"] / 1000
                self.actions.append({"action": kind, **args})
            else:
                await super().primitive(kind, **args)
    fake = FakeJev()
    ex = Executor(Client(),854,480,JevVision(fake))
    ex.begin_step(before)
    result = await ex.execute({"name": "attack_until_broken", "arguments": {"max_s": 4}}, "skills")
    assert result["mining_probes"][0]["used"]
    assert result["mining_probes"][0]["succeeded"] is (mode == "cracks")
    assert .6 <= clock[0] <= 4 if mode == "cracks" else clock[0] == pytest.approx(.4 if mode == "capture_timeout" else .6)
    assert not fake.inputs and not ex.held_buttons
    if mode == "cracks":
        assert result["outcome"] == "scene_changed"
    else:
        assert result["outcome"] == "not_in_reach"
        assert result["next_action"] == "call seek(<target>) first"


@pytest.mark.parametrize("seconds", [.35, .6, 4.])
@pytest.mark.parametrize("broke", [False, True])
async def test_probe_respects_max_s_and_releases_on_immediate_break(monkeypatch, seconds, broke):
    from test_pixel_skills import png
    before, after = png("green",(640,480)), png("white",(640,480))
    clock = [0.]
    monkeypatch.setattr("mc_observatory.holo_tools.time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: False)
    class Client(PixelClient):
        async def screenshot(self):
            return after if broke and clock[0] > 0 else before
    class Executor(ToolExecutor):
        async def primitive(self, kind, **args):
            if kind == "wait":
                clock[0] += args["ms"] / 1000
            else:
                await super().primitive(kind, **args)
    ex = Executor(Client(),640,480,JevVision(FakeJev()))
    result = await ex.execute({"name": "attack_until_broken", "arguments": {"max_s": seconds}}, "skills")
    held_s = clock[0] - (.35 if broke else 0)
    assert held_s == pytest.approx(min(.4 if broke else .6, seconds - .1))
    assert held_s <= seconds and not ex.held_buttons
    assert result["outcome"] == ("scene_changed" if broke else "not_in_reach")
    assert result["mining_probes"][0]["succeeded"] is broke
    assert result["mining_probes"][0]["broke_pixel_cue"] is broke


async def test_absent_outline_probes_then_releases_without_progress_or_jev_decision(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: False)
    fake = FakeJev()
    ex = executor(fake)
    assert await ex.attack_until_broken(.35) == "not_in_reach"
    assert not fake.inputs and not ex.held_buttons


@pytest.mark.parametrize("actual_reachable", [False, True])
async def test_mining_checks_fresh_pixels_after_model_latency(monkeypatch, actual_reachable):
    from test_pixel_skills import png
    dark, light = png("black", (640,480)), png("white", (640,480))
    class Client(PixelClient):
        async def screenshot(self):
            return light if actual_reachable else dark
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda frame, *args: frame == light)
    ex = ToolExecutor(Client(), 640,480, JevVision(FakeJev()))
    ex.begin_step(dark if actual_reachable else light)
    assert await ex.attack_until_broken(0) == ("no_change" if actual_reachable else "not_in_reach")
    assert any(a.get("button") == 0 and a.get("pressed") for a in ex.actions) is actual_reachable
    assert ex.mining_probes[0]["used"] is (not actual_reachable)


@pytest.mark.parametrize("overhead_outline", [False, True])
async def test_mine_up_uses_fresh_overhead_view_without_outline_gate(monkeypatch, overhead_outline):
    from unittest.mock import AsyncMock
    from test_pixel_skills import png
    dark, light = png("black", (640,480)), png("white", (640,480))
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda frame, *args: frame == light)
    monkeypatch.setattr("mc_observatory.holo_tools.asyncio.sleep", AsyncMock())

    class Client(PixelClient):
        async def screenshot(self):
            return light if overhead_outline else dark

    ex = ToolExecutor(Client(), 640,480, JevVision(FakeJev()))
    ex.begin_step(dark if overhead_outline else light)
    result = await ex.execute({"name": "mine_up", "arguments": {"count": 1}}, "skills")
    assert result["outcome"] == ["no_change"]
    assert all(a["frame_change"]==0 for a in result["vertical_mining"])
    assert any(a.get("button") == 0 and a.get("pressed") for a in ex.actions)
    assert result.get("mining_probes",[]) == []


async def test_collect_advice_is_not_pickup_proof():
    ex = executor(FakeJev())
    result = await ex.execute({"name": "collect", "arguments": {"max_s": .01,"item": "oak log"}}, "skills")
    assert result["item_collected"] is None
    assert result["hotbar_item_advisory"]["noul"] == .9
    assert result["hotbar_item_advisory"]["not_collection_proof"]
    assert ex.jev.calls[-1]["kind"] == "hotbar"


async def test_seek_boxes_one_block_face_not_the_entire_trunk():
    fake = FakeJev()
    await JevVision(fake).ask(PNG, "oak log trunk")
    instruction = fake.inputs[0]["questions"]["target"].instructions
    assert "ONE individual exposed oak log trunk cube face" in instruction
    assert "not box an entire multi-block trunk" in instruction
    assert "crosshair height" in instruction


def test_opt_in_schemas_raw_unchanged_and_barrier():
    assert tool_schemas("raw", "jev") == tool_schemas("raw")
    assert "seek" not in [t["function"]["name"] for t in tool_schemas("skills")]
    assert "seek" in [t["function"]["name"] for t in tool_schemas("skills", "jev")]
    c = call("seek", target="gravel")
    assert parse_calls({"tool_calls": [c]}, "skills", "jev")[0]["name"] == "seek"
    assert observation_prefix([c,call("attack",seconds=4)], "jev") == ([c],[call("attack",seconds=4)])
    with pytest.raises(ValueError):
        HoloConfig(tier="raw", reflex="jev")


def test_jev_has_no_legacy_block_approach_and_prompt_uses_seek():
    names = {t["function"]["name"] for t in tool_schemas("skills", "jev")}
    assert not names & {"move", "approach_until_outline", "aim_at", "mine", "attack"}
    assert {"seek", "reposition", "attack_until_broken"} <= names
    text = HoloAgent(HoloConfig(reflex="jev"), object()).messages([(0, PNG)])[0]["content"]
    assert "seek is the ONLY" in text or "Seek is the ONLY" in text
    assert "mine(max_s=4)" not in text
    assert "approach ->" not in text
    assert "aim_at" not in text and "look->move" not in text
    assert "trunk width >=85px" not in text
    assert "mining checks close size before any outline/progress probe" in text
    with pytest.raises(ValueError):
        parse_calls({"tool_calls": [call("reposition", direction="forward", seconds=.1)]}, "skills", "jev")


async def test_not_in_reach_tells_model_to_seek_first(monkeypatch):
    monkeypatch.setattr("mc_observatory.holo_tools.outline", lambda _, *args: False)
    ex = executor(FakeJev())
    r = await ex.execute({"name": "attack_until_broken", "arguments": {"max_s": .35}}, "skills")
    assert r["outcome"] == "not_in_reach"
    assert r["next_action"] == "call seek(<target>) first"


@pytest.mark.parametrize("name,reflex", [("move", "none"), ("reposition", "jev")])
def test_all_movement_tools_reject_conflicting_modifiers(name, reflex):
    with pytest.raises(ValueError, match="sprint and sneak"):
        parse_calls({"tool_calls": [call(name, direction="left", seconds=.1, sprint=True, sneak=True)]}, "skills", reflex)
    assert parse_calls({"tool_calls": [call(name, direction="left", seconds=.1, sprint=True)]}, "skills", reflex)


@pytest.mark.parametrize("name", ["move", "reposition"])
async def test_blocked_movement_reports_stuck_and_changed_view_clears_it(name):
    from test_pixel_skills import png
    ex = executor(FakeJev())
    c = {"name": name, "arguments": {"direction": "left", "seconds": 0}}
    assert not (await ex.execute(c, "skills"))["stuck"]
    assert (await ex.execute(c, "skills"))["stuck"]
    ex.current_frame = png("white", (640,480))
    assert not (await ex.execute(c, "skills"))["stuck"]
    assert ex.unchanged_moves == 0


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
