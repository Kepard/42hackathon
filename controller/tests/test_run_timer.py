"""Terminal result timing stays stable while diagnostic journals retain wall time."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from holospeed import recording
from holospeed.recording import RunRecorder, read_events
from test_state_runtime import SkillBridge, engine_with_fakes


@pytest.fixture
def clock(monkeypatch):
    value = [100.0]
    # Replace only the recorder's clock, never asyncio's monotonic clock.
    monkeypatch.setattr(recording, "time", SimpleNamespace(monotonic=lambda: value[0]))
    return value


def test_result_duration_excludes_post_terminal_idle_but_journal_preserves_it(tmp_path, clock):
    recorder = RunRecorder(tmp_path, "live", {})
    clock[0] = 127.634
    recorder.freeze_duration("error")
    clock[0] = 160.0
    diagnostic = recorder.log("event", {"name": "operator_inspected_failure"})
    recorder.checkpoint(status="error")
    assert diagnostic["elapsed_ms"] == 60_000
    assert recorder.elapsed_ms == 60_000
    assert recorder.duration_ms == 27_634
    assert json.loads((recorder.path / "summary.json").read_text())["duration_ms"] == 27_634
    clock[0] = 190.0
    recorder.finish("error")
    events = read_events(recorder.path / "events.jsonl")
    assert events[-1]["elapsed_ms"] == 90_000
    assert [event["elapsed_ms"] for event in events] == sorted(
        event["elapsed_ms"] for event in events
    )
    assert recorder.summary["duration_ms"] == 27_634
    assert len([event for event in events if event["data"].get("name") == "run_timer_frozen"]) == 1


@pytest.mark.parametrize("terminal", ["goal", "error", "budget"])
async def test_real_state_loop_freezes_hud_and_summary_before_terminal_idle(
    tmp_path, clock, terminal
):
    bridge = SkillBridge(behavior="false_completion" if terminal == "error" else "complete")
    if terminal == "goal":
        bridge.inventory = {"minecraft:stone_pickaxe": 1, "minecraft:stone_axe": 1}
    engine, _, _ = await engine_with_fakes(
        tmp_path, bridge=bridge, **({"max_actions": 0} if terminal == "budget" else {})
    )
    try:
        await engine.start()
        clock[0] = 127.634
        await asyncio.wait_for(asyncio.shield(engine.task), 4)
        assert engine.state["status"] == ("error" if terminal == "error" else "paused")
        assert engine.snapshot()["elapsed_ms"] == 27_634
        clock[0] = 160.0
        assert engine.recorder.elapsed_ms == 60_000
        assert engine.snapshot()["elapsed_ms"] == 27_634
        await engine.hud()
        assert bridge.messages[-1]["elapsed_ms"] == 27_634
        engine.checkpoint()
        assert engine.recorder.summary["duration_ms"] == 27_634
        await engine.finish()
        assert engine.recorder.summary["duration_ms"] == 27_634
    finally:
        await engine.close()


async def test_explicit_resume_includes_elapsed_pause_and_ordinary_pause_keeps_wall_clock(
    tmp_path, clock
):
    engine, _, _ = await engine_with_fakes(tmp_path)

    async def wait_for_operator():
        await asyncio.Future()

    engine.run = wait_for_operator
    try:
        await engine.start()
        identifier = engine.state["run_id"]
        clock[0] = 120.0
        engine.freeze_timer("error")
        await engine.pause()
        clock[0] = 160.0
        assert engine.snapshot()["elapsed_ms"] == 20_000
        await engine.start(resume_only=True)
        assert engine.state["run_id"] == identifier
        assert engine.snapshot()["elapsed_ms"] == 60_000
        await engine.pause()
        clock[0] = 170.0
        assert engine.snapshot()["elapsed_ms"] == 70_000
        assert any(
            event["data"].get("name") == "run_timer_resumed"
            for event in read_events(engine.recorder.path / "events.jsonl")
        )
    finally:
        await engine.close()
