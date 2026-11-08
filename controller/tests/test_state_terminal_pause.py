"""Terminal state-loop failures freeze the world without masking their cause."""

import asyncio
import json

import pytest
from holospeed import state_engine
from holospeed.bridge import BridgeError
from test_state_runtime import SkillBridge, engine_with_fakes, run_events


class TerminalBridge(SkillBridge):
    def __init__(self, *, pause_result=True, failed_read=True, wait_for_state=False):
        super().__init__()
        self.pause_result = pause_result
        self.failed_read = failed_read
        self.wait_for_state = wait_for_state
        self.state_entered = asyncio.Event()
        self.pause_cancelled = asyncio.Event()
        self.order = []

    async def game_state(self):
        self.state_entered.set()
        if self.wait_for_state:
            await asyncio.Event().wait()
        if self.failed_read:
            raise BridgeError("Original game-state failure")
        return await super().game_state()

    async def stop(self, reason):
        self.order.append("stop")
        await super().stop(reason)

    async def pause_game(self):
        self.order.append("pause")
        if self.pause_result == "wait":
            try:
                await asyncio.Event().wait()
            finally:
                self.pause_cancelled.set()
        if isinstance(self.pause_result, Exception):
            raise self.pause_result
        return self.pause_result


@pytest.mark.parametrize(
    "pause_result", [True, False, BridgeError("World cannot pause"), RuntimeError("Pause failed")]
)
async def test_terminal_failure_stops_then_pauses_without_replacing_original_error(
    tmp_path, pause_result
):
    engine, bridge, _ = await engine_with_fakes(tmp_path, TerminalBridge(pause_result=pause_result))
    try:
        await engine.start()
        # Cleanup must not reenter engine.pause(), which acquires this lock.
        async with engine.lock:
            await asyncio.wait_for(asyncio.shield(engine.task), timeout=1)
        assert bridge.order == ["stop", "pause"]
        assert engine.state["status"] == "error"
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert engine.state["metrics"]["errors"] == 1
        events = run_events(engine)
        errors = [event for event in events if event["type"] == "error"]
        assert len(errors) == 1 and errors[0]["data"]["message"] == "Original game-state failure"
        terminal = next(
            event
            for event in events
            if event["data"].get("name")
            in {"world_paused_after_terminal", "world_pause_unavailable"}
        )
        assert errors[0]["seq"] < terminal["seq"]
        assert terminal["data"]["after"] == "error"
        if isinstance(pause_result, Exception):
            assert terminal["data"]["class"] == type(pause_result).__name__
        else:
            assert terminal["data"]["paused"] is pause_result
        assert ("World paused." in engine.state["expected_effect"]) is (pause_result is True)
        await engine.close()
        summary = json.loads((engine.recorder.path / "summary.json").read_text())
        assert summary["status"] == "error" and summary["metrics"]["errors"] == 1
    finally:
        if not engine.recorder or not engine.recorder.closed:
            await engine.close()


@pytest.mark.parametrize("budget", ["time", "actions"])
async def test_exhausted_budget_uses_separate_pause_deadline_without_becoming_error(
    tmp_path, budget
):
    engine, bridge, _ = await engine_with_fakes(
        tmp_path,
        TerminalBridge(failed_read=False, wait_for_state=budget == "time"),
        max_run_seconds=0.03 if budget == "time" else 10,
        max_actions=1,
    )
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=2)
        assert bridge.order[-2:] == ["stop", "pause"]
        assert engine.state["status"] == "paused" and engine.state["metrics"]["errors"] == 0
        assert engine.state["keys"] == [] and engine.state["action"] is None
        events = run_events(engine)
        budget_event = next(e for e in events if e["data"].get("name") == "run_budget_reached")
        paused = next(e for e in events if e["data"].get("name") == "world_paused_after_terminal")
        assert budget_event["seq"] < paused["seq"]
        assert paused["data"]["after"] == "run_budget_reached" and paused["data"]["paused"] is True
        assert not any(event["type"] == "error" for event in events)
    finally:
        await engine.close()


async def test_missing_pause_ack_is_bounded_and_preserves_primary_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(state_engine, "TERMINAL_PAUSE_TIMEOUT_S", 0.02)
    engine, bridge, _ = await engine_with_fakes(tmp_path, TerminalBridge(pause_result="wait"))
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=1)
        assert bridge.pause_cancelled.is_set()
        assert engine.state["status"] == "error" and engine.state["metrics"]["errors"] == 1
        events = run_events(engine)
        unavailable = next(e for e in events if e["data"].get("name") == "world_pause_unavailable")
        assert unavailable["data"]["class"] == "TimeoutError"
        assert "timed out" in unavailable["data"]["detail"]
        assert any(e["data"].get("message") == "Original game-state failure" for e in events)
    finally:
        await engine.close()


async def test_launcher_cancellation_releases_controls_without_pausing_world(tmp_path):
    engine, bridge, _ = await engine_with_fakes(
        tmp_path, TerminalBridge(failed_read=False, wait_for_state=True)
    )
    try:
        await engine.start()
        await asyncio.wait_for(bridge.state_entered.wait(), timeout=1)
        async with engine.lock:
            engine.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(engine.task, timeout=1)
        assert bridge.order == ["stop"]
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert not any(
            e["data"].get("name") in {"world_paused_after_terminal", "world_pause_unavailable"}
            for e in run_events(engine)
        )
    finally:
        await engine.close()


async def test_release_failure_still_attempts_world_pause_and_keeps_original_error(tmp_path):
    class BrokenStopBridge(TerminalBridge):
        async def stop(self, reason):
            await super().stop(reason)
            if reason == "State-aware agent loop stopped":
                raise BridgeError("Release acknowledgment unavailable")

    engine, bridge, _ = await engine_with_fakes(tmp_path, BrokenStopBridge())
    try:
        await engine.start()
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=1)
        assert bridge.order == ["stop", "pause"]
        events = run_events(engine)
        assert [e["data"]["message"] for e in events if e["type"] == "error"] == [
            "Original game-state failure"
        ]
        assert any(e["data"].get("name") == "controls_release_unavailable" for e in events)
        assert engine.state["metrics"]["errors"] == 1
    finally:
        await engine.close()
