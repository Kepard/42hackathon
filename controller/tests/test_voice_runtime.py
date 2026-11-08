"""Race real engine controls against queued final utterances, without game inputs."""

import asyncio

import pytest
from holospeed.config import Settings
from holospeed.engine import AgentEngine
from holospeed.recording import RunRecorder
from holospeed.voice import VoiceControl
from test_runtime import FakeBridge
from test_voice import Answerer, command


class ControlBridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.arms = 0
        self.world_pauses = 0
        self.block_stop = False
        self.stop_entered = asyncio.Event()
        self.stop_release = asyncio.Event()

    async def arm(self):
        self.arms += 1

    async def pause_game(self):
        self.world_pauses += 1
        return True

    async def stop(self, reason):
        await super().stop(reason)
        if self.block_stop:
            self.block_stop = False
            self.stop_entered.set()
            await self.stop_release.wait()


def recording_engine(tmp_path):
    bridge = ControlBridge()
    settings = Settings(mode="live", runs_dir=tmp_path, holo_api_key="unused-test-key")
    engine = AgentEngine(settings, bridge)
    engine.recorder = RunRecorder(tmp_path, "live", {"test": True})
    engine.state.update(run_id=engine.recorder.id, status="paused")
    voice = VoiceControl(engine, settings, answerer=Answerer())
    return engine, bridge, voice


@pytest.mark.parametrize("text", ["pause", "end the run", "resume"])
async def test_queued_voice_control_rechecks_run_inside_engine_lock(tmp_path, text):
    engine, bridge, voice = recording_engine(tmp_path)
    try:
        original = engine.recorder
        async with engine.lock:
            pending = asyncio.create_task(voice.apply(command(text, run_id=original.id)))
            await asyncio.sleep(0)
            assert not pending.done()
            # A local New run/F9 transition owns the same lock before the queued
            # voice control acquires it. Its old run ID must never touch controls.
            engine.state["run_id"] = "20261108T235959-deadbeef"
        with pytest.raises(ValueError, match="run changed"):
            await pending
        assert bridge.stops == [] and bridge.world_pauses == 0 and bridge.arms == 0
        assert engine.recorder is original and not original.closed
        assert engine.task is None
    finally:
        await voice.close()
        await engine.close()


async def test_resume_waiting_behind_finish_cannot_create_a_new_recording(tmp_path):
    engine, bridge, voice = recording_engine(tmp_path)
    try:
        original = engine.recorder
        bridge.block_stop = True
        finishing = asyncio.create_task(engine.finish(expected_run_id=original.id))
        await asyncio.wait_for(bridge.stop_entered.wait(), timeout=1)
        resuming = asyncio.create_task(voice.apply(command("resume", run_id=original.id)))
        await asyncio.sleep(0)
        assert not resuming.done() and not original.closed
        bridge.stop_release.set()
        await finishing
        with pytest.raises(ValueError, match="recording ended"):
            await resuming
        assert engine.recorder is original and original.closed
        assert engine.state["run_id"] == original.id and engine.state["status"] == "idle"
        assert engine.task is None and bridge.arms == 0
        assert len(list(tmp_path.glob("*/summary.json"))) == 1
    finally:
        bridge.stop_release.set()
        await voice.close()
        await engine.close()
