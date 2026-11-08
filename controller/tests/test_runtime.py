"""Exercise real run orchestration and journals without Minecraft or model APIs."""

import asyncio
import base64
import hashlib
import io
import json
import time

import pytest
from holospeed.bridge import BridgeError, GameBridge
from holospeed.config import Settings
from holospeed.engine import AgentEngine
from holospeed.models import Action, Decision, ModelResult, Observation
from holospeed.providers import ProviderError, ReplanRequired
from holospeed.recording import RunRecorder, read_events
from PIL import Image


def frame(frame_id="frame-1"):
    stream = io.BytesIO()
    Image.new("RGB", (64, 36), "#438853").save(stream, format="PNG")
    return Observation(
        id="observation-1",
        frame_id=frame_id,
        tick=5,
        width=64,
        height=36,
        image_base64=base64.b64encode(stream.getvalue()).decode(),
    )


class Socket:
    """Minimal websocket peer that records wire messages but never invents acks."""

    def __init__(self):
        self.sent = []
        self.action_sent = asyncio.Event()
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def close(self, **kwargs):
        self.closed = True

    async def send_json(self, message):
        self.sent.append(message.copy())
        if message["type"] == "action":
            self.action_sent.set()


class FakeBridge:
    connected = True

    def __init__(self, observation=None):
        self.observation = observation or frame()
        self.executed = []
        self.stops = []
        self.messages = []

    async def observe(self):
        return self.observation

    async def execute(self, action, frame_id, ttl_ms):
        self.executed.append((action, frame_id, ttl_ms))
        return {"type": "ack", "id": "action-1", "status": "completed"}

    async def stop(self, reason):
        self.stops.append(reason)

    async def send(self, message):
        self.messages.append(message)


class WaitingProvider:
    """Expose cancellation while inference is in progress, including late results."""

    def __init__(self, suppress_cancellation=False):
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.suppress_cancellation = suppress_cancellation
        self.calls = 0

    async def decide(self, observation, context):
        self.calls += 1
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            if not self.suppress_cancellation:
                raise
        # A completed-goal claim also makes a broken loop terminate, so a failed
        # regression assertion reports a late action rather than hanging forever.
        return ModelResult(
            decision=Decision(
                objective="Inspect stone tools",
                strategy="Inspect visible inventory",
                expected_effect="No more movement",
                action=Action(keys=["forward"], duration_ticks=1),
                milestone="stone_tools",
                evidence="Synthetic test evidence",
            ),
            request={"frame_id": observation.frame_id},
            response={"test": True},
            latency_ms=2,
            model="fake-model",
        )

    async def aclose(self):
        pass


@pytest.fixture
def skip_retry_delays(monkeypatch):
    real_sleep = asyncio.sleep

    async def without_retry_delay(delay, *args, **kwargs):
        return await real_sleep(0 if delay == 0.5 else delay, *args, **kwargs)

    monkeypatch.setattr("holospeed.engine.asyncio.sleep", without_retry_delay)


async def test_actual_demo_flow_produces_complete_replay_and_summary(tmp_path, monkeypatch):
    """Use the real demo decisions, delays, images, executor, engine, and journal."""
    engine = AgentEngine(Settings(mode="demo", runs_dir=tmp_path), GameBridge())

    async def forbidden_api(*args, **kwargs):
        pytest.fail("Demo mode must never invoke a model provider")

    monkeypatch.setattr(engine.holo, "decide", forbidden_api)
    updates = asyncio.Queue(maxsize=1)
    engine.subscribers.add(updates)
    try:
        await engine.start()
        run_id = engine.state["run_id"]
        await asyncio.wait_for(asyncio.shield(engine.task), timeout=30)
        state = engine.snapshot()
        assert state["status"] == "paused"
        assert state["metrics"]["actions"] == 20
        assert state["metrics"]["observations"] == 20
        assert state["metrics"]["errors"] == 0
        assert state["keys"] == [] and state["action"] is None
        assert [m["id"] for m in state["milestones"]] == [
            "wood",
            "crafting_table",
            "wooden_pickaxe",
            "stone_tools",
        ]
        assert all(m["verification"] == "simulated" for m in state["milestones"])
        # Slow UI consumers get the newest snapshot, not an unbounded backlog.
        assert updates.qsize() == 1
        assert updates.get_nowait()["metrics"]["actions"] == 20
        assert all(event["type"] != "model_call" for event in state["events"])

        await engine.finish()
        run_path = tmp_path / run_id
        summary = json.loads((run_path / "summary.json").read_text())
        events = read_events(run_path / "events.jsonl")
        assert summary["status"] == "finished"
        assert summary["metrics"]["actions"] == 20
        assert summary["milestones"] == state["milestones"]
        assert summary["duration_ms"] > 0
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        assert [event["elapsed_ms"] for event in events] == sorted(
            event["elapsed_ms"] for event in events
        )
        assert len([event for event in events if event["type"] == "model_call"]) == 20
        assert len([event for event in events if event["type"] == "ack"]) == 20
        observations = [event for event in events if event["type"] == "observation"]
        assert len(observations) == 20
        observed_frames = {event["data"]["frame_id"] for event in observations}
        for event in observations:
            saved_frame = run_path / "frames" / event["data"]["frame_url"].rsplit("/", 1)[1]
            assert hashlib.sha256(saved_frame.read_bytes()).hexdigest() == event["data"]["sha256"]
        for event in events:
            if event["type"] in {"decision", "action", "milestone"}:
                assert event["data"]["frame_id"] in observed_frames
    finally:
        await engine.close()


@pytest.mark.parametrize("suppress_cancellation", [False, True])
async def test_pause_during_inference_never_executes_late_result(tmp_path, suppress_cancellation):
    bridge = FakeBridge()
    engine = AgentEngine(
        Settings(
            mode="live", gameplay_mode="visual", runs_dir=tmp_path, holo_api_key="test-unused"
        ),
        bridge,
    )
    await engine.holo.aclose()
    provider = WaitingProvider(suppress_cancellation=suppress_cancellation)
    engine.holo = provider
    try:
        await engine.start()
        await asyncio.wait_for(provider.entered.wait(), timeout=2)
        await asyncio.wait_for(engine.pause("Operator pause during inference"), timeout=2)
        assert provider.cancelled.is_set()
        provider.release.set()
        assert engine.task.done()
        assert engine.state["status"] == "paused"
        assert engine.state["keys"] == []
        assert bridge.executed == []
        assert engine.state["metrics"]["actions"] == 0
        assert "Operator pause during inference" in bridge.stops
        events = read_events(engine.recorder.path / "events.jsonl")
        assert not any(event["type"] == "action" for event in events)
    finally:
        provider.release.set()
        await engine.close()


@pytest.mark.parametrize("suppress_cancellation", [False, True])
async def test_run_deadline_cancels_inference_without_executing_late_result(
    tmp_path, suppress_cancellation
):
    bridge = FakeBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused",
            max_run_seconds=0.1,
        ),
        bridge,
    )
    await engine.holo.aclose()
    provider = WaitingProvider(suppress_cancellation=suppress_cancellation)
    engine.holo = provider
    try:
        await engine.start()
        await asyncio.wait_for(provider.entered.wait(), timeout=1)
        await asyncio.wait_for(engine.task, timeout=2)
        assert provider.cancelled.is_set()
        assert bridge.executed == []
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["metrics"]["errors"] == 0
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert bridge.stops[-1] == "Agent loop stopped"
        events = read_events(engine.recorder.path / "events.jsonl")
        assert not any(event["type"] in {"action", "ack", "error"} for event in events)
        assert any(
            event["type"] == "event" and event["data"].get("name") == "run_budget_reached"
            for event in events
        )
        summary = json.loads((engine.recorder.path / "summary.json").read_text())
        assert summary["status"] == "paused"
        assert summary["metrics"]["actions"] == 0
    finally:
        await engine.close()


async def test_run_deadline_cancels_execution_and_releases_inputs_without_completed_ack(
    tmp_path, monkeypatch
):
    class SlowBridge(FakeBridge):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def execute(self, action, frame_id, ttl_ms):
            self.executed.append((action, frame_id, ttl_ms))
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    bridge = SlowBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused",
            max_run_seconds=0.1,
        ),
        bridge,
    )

    async def decide(observation, context):
        return ModelResult(
            decision=Decision(
                objective="Approach a visible tree",
                strategy="Walk toward the trunk",
                expected_effect="Trunk becomes closer",
                action=Action(keys=["forward"], duration_ticks=10),
            ),
            request={"frame_id": observation.frame_id},
            response={"plan": True},
            latency_ms=1,
            model="fake-holo",
        )

    monkeypatch.setattr(engine.holo, "decide", decide)
    try:
        await engine.start()
        await asyncio.wait_for(bridge.entered.wait(), timeout=1)
        assert engine.state["keys"] == ["forward"]
        await asyncio.wait_for(engine.task, timeout=2)
        assert bridge.cancelled.is_set()
        assert len(bridge.executed) == 1
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["metrics"]["errors"] == 0
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert bridge.stops[-1] == "Agent loop stopped"
        events = read_events(engine.recorder.path / "events.jsonl")
        assert len([event for event in events if event["type"] == "action"]) == 1
        assert not any(event["type"] in {"ack", "error"} for event in events)
        assert any(
            event["type"] == "event" and event["data"].get("name") == "run_budget_reached"
            for event in events
        )
        summary = json.loads((engine.recorder.path / "summary.json").read_text())
        assert summary["status"] == "paused"
        assert summary["metrics"]["actions"] == 0
    finally:
        await engine.close()


async def test_holo_completion_claim_pauses_without_executing_an_action(tmp_path, monkeypatch):
    bridge = FakeBridge()
    engine = AgentEngine(
        Settings(mode="live", gameplay_mode="visual", runs_dir=tmp_path, holo_api_key="unused"),
        bridge,
    )
    calls = []

    async def completed_decision(observation, context):
        calls.append(observation.frame_id)
        return ModelResult(
            decision=Decision(
                objective="Review completed goal",
                strategy="The visible objective appears complete",
                expected_effect="Wait for operator review",
                # Completion must override even an otherwise valid movement.
                action=Action(keys=["forward"], duration_ticks=1),
                evidence="Synthetic visible completion evidence",
            ),
            request={"frame_id": observation.frame_id},
            response={"answer": "Synthetic completion"},
            latency_ms=8,
            usage={"input_tokens": 7, "output_tokens": 2},
            model="fake-holo",
            completed=True,
        )

    monkeypatch.setattr(engine.holo, "decide", completed_decision)
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=2)
        assert calls == ["frame-1"]
        assert engine.state["status"] == "paused"
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["metrics"]["input_tokens"] == 7
        assert engine.state["metrics"]["output_tokens"] == 2
        assert bridge.executed == []
        assert bridge.stops
        events = read_events(engine.recorder.path / "events.jsonl")
        completion = next(
            event for event in events if event["data"].get("name") == "model_completion_claim"
        )
        assert completion["data"]["frame_id"] == "frame-1"
        assert completion["data"]["detail"] == "Synthetic visible completion evidence"
        assert not any(event["type"] in {"action", "ack"} for event in events)
        summary = json.loads((engine.recorder.path / "summary.json").read_text())
        assert summary["status"] == "paused"
    finally:
        await engine.close()


async def test_close_preserves_terminal_error_in_summary_and_journal(tmp_path):
    class BrokenBridge(FakeBridge):
        async def observe(self):
            raise BridgeError("Test bridge disconnected before capture")

    bridge = BrokenBridge()
    engine = AgentEngine(
        Settings(mode="live", gameplay_mode="visual", runs_dir=tmp_path, holo_api_key="unused"),
        bridge,
    )
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=2)
        assert engine.state["status"] == "error"
        assert engine.state["metrics"]["errors"] == 1
        await engine.close()
        assert engine.recorder.closed
        summary = json.loads((engine.recorder.path / "summary.json").read_text())
        assert summary["status"] == "error"
        assert summary["metrics"]["errors"] == 1
        events = read_events(engine.recorder.path / "events.jsonl")
        assert any(event["type"] == "error" for event in events)
        assert events[-1]["type"] == "run_finished"
        assert events[-1]["data"]["status"] == "error"
        assert bridge.executed == []
    finally:
        if engine.recorder is None or not engine.recorder.closed:
            await engine.close()


@pytest.mark.parametrize("finalize", ["finish", "close"])
async def test_disconnect_pause_preserves_error_before_finalizing_run(tmp_path, finalize):
    class BrokenBridge(FakeBridge):
        async def observe(self):
            raise BridgeError("Capture failed before the bridge disconnected")

    bridge = BrokenBridge()
    engine = AgentEngine(
        Settings(mode="live", gameplay_mode="visual", runs_dir=tmp_path, holo_api_key="unused"),
        bridge,
    )
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=2)
        assert engine.state["status"] == "error"
        assert engine.state["metrics"]["errors"] == 1

        # The app pauses when the websocket disconnects, before shutdown or an
        # operator ends the run. That cleanup must retain the original failure.
        await engine.pause("Minecraft bridge disconnected")
        assert engine.state["status"] == "error"
        assert engine.state["keys"] == []
        assert engine.state["action"] is None
        assert "Minecraft bridge disconnected" in bridge.stops
        summary_path = engine.recorder.path / "summary.json"
        assert json.loads(summary_path.read_text())["status"] == "error"

        await getattr(engine, finalize)()
        assert engine.recorder.closed
        summary = json.loads(summary_path.read_text())
        assert summary["status"] == "error"
        assert summary["metrics"]["errors"] == 1
        events = read_events(engine.recorder.path / "events.jsonl")
        errors = [event for event in events if event["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["data"]["message"] == "Capture failed before the bridge disconnected"
        assert events[-1]["type"] == "run_finished"
        assert events[-1]["data"]["status"] == "error"
        assert bridge.executed == []
        assert not any(event["type"] in {"action", "ack"} for event in events)
    finally:
        await engine.close()

    # Finishing explicitly and then shutting down must not rewrite the archive.
    assert json.loads(summary_path.read_text())["status"] == "error"


async def test_holo_plan_executes_only_after_jev_validates_a_fresh_frame(tmp_path, monkeypatch):
    class SequentialBridge(FakeBridge):
        def __init__(self):
            super().__init__()
            self.observed = []

        async def observe(self):
            observation = frame(f"frame-{len(self.observed) + 1}")
            self.observed.append(observation.frame_id)
            return observation

    bridge = SequentialBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused-holo",
            jev_api_key="unused-jev",
            max_actions=1,
            validate_holo_plans=True,
        ),
        bridge,
    )
    holo_calls, jev_calls = [], []
    plan = Decision(
        objective="Approach the visible tree",
        strategy="Walk toward the unobstructed trunk",
        expected_effect="Trunk becomes closer",
        action=Action(keys=["forward"], duration_ticks=6),
    )
    validated = plan.model_copy(update={"action": Action(keys=["forward"], duration_ticks=2)})

    async def holo_decide(observation, context):
        holo_calls.append((observation.frame_id, json.loads(json.dumps(context))))
        return ModelResult(
            decision=plan,
            request={"frame_id": observation.frame_id},
            response={"plan": True},
            latency_ms=12.5,
            usage={"prompt_tokens": 13, "completion_tokens": 5},
            model="fake-holo",
        )

    async def jev_decide(observation, context):
        jev_calls.append((observation.frame_id, json.loads(json.dumps(context))))
        assert bridge.executed == []  # Holo's plan must not execute before validation.
        return ModelResult(
            decision=validated,
            request={"frame_id": observation.frame_id},
            response={"validated": True},
            latency_ms=4,
            usage={"input_tokens": 7, "output_tokens": 3},
            model="fake-jev",
        )

    monkeypatch.setattr(engine.holo, "decide", holo_decide)
    monkeypatch.setattr(engine.jev, "decide", jev_decide)
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=2)
        assert bridge.observed == ["frame-1", "frame-2"]
        assert len(holo_calls) == len(jev_calls) == 1
        assert holo_calls[0][0] == "frame-1"
        assert holo_calls[0][1]["recovery_guidance"]["advisory_only"] is True
        assert jev_calls[0][0] == "frame-2"
        validation_context = jev_calls[0][1]
        assert "recovery_guidance" not in validation_context
        assert validation_context["validate_unexecuted_plan"] is True
        assert validation_context["plan_source_frame_id"] == "frame-1"
        assert validation_context["validation_frame_id"] == "frame-2"
        assert validation_context["previous_decision"] == plan.model_dump()
        assert bridge.executed == [(validated.action, "frame-2", engine.settings.frame_ttl_ms)]
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 1
        assert engine.state["metrics"]["observations"] == 2
        assert engine.state["metrics"]["input_tokens"] == 20
        assert engine.state["metrics"]["output_tokens"] == 8
        assert engine.state["holo_latency_ms"] == 12.5
        assert engine.state["jev_latency_ms"] == 4
        assert engine.state["latency_history"] == {"holo": [12.5], "jev": [4]}
        events = read_events(engine.recorder.path / "events.jsonl")
        calls = [event["data"] for event in events if event["type"] == "model_call"]
        assert [(call["provider"], call["frame_id"]) for call in calls] == [
            ("holo", "frame-1"),
            ("jev", "frame-2"),
        ]
        assert calls[1]["plan_frame_id"] == "frame-1"
        assert (
            next(event for event in events if event["type"] == "plan")["data"]["frame_id"]
            == "frame-1"
        )
        for event in events:
            if event["type"] in {"decision", "action", "ack"}:
                assert event["data"]["frame_id"] == "frame-2"
    finally:
        await engine.close()


async def test_jev_rejection_prevents_all_actions_and_exhausts_recovery_budget(
    tmp_path, monkeypatch
):
    class SequentialBridge(FakeBridge):
        def __init__(self):
            super().__init__()
            self.observed = 0

        async def observe(self):
            self.observed += 1
            return frame(f"frame-{self.observed}")

    bridge = SequentialBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused-holo",
            jev_api_key="unused-jev",
            max_actions=1,
            validate_holo_plans=True,
            allow_exploration=False,
        ),
        bridge,
    )
    holo_frames, jev_frames = [], []
    plan = Decision(
        objective="Approach a tree",
        strategy="Find a safe approach",
        expected_effect="The tree becomes closer",
        action=Action(keys=["forward"], duration_ticks=2),
    )

    async def holo_decide(observation, context):
        holo_frames.append(observation.frame_id)
        if len(holo_frames) >= 3:
            assert context["objective"] == context["run_goal"]
            assert context["previous_subgoal_not_world_fact"] == plan.objective
            assert context["recovery_guidance"]["repeated_proposal"] is True
            assert all(item["physical_outcome"] == "unverified" for item in context["failures"])
        return ModelResult(
            decision=plan,
            request={"frame_id": observation.frame_id},
            response={"plan": True},
            latency_ms=10,
            model="fake-holo",
        )

    async def jev_decide(observation, context):
        assert context["validate_unexecuted_plan"] is True
        assert context["plan_source_frame_id"] != observation.frame_id
        jev_frames.append(observation.frame_id)
        raise ReplanRequired(
            "The fresh frame does not support this plan",
            provider="jev",
            latency_ms=3,
            request={"frame_id": observation.frame_id},
            response={"replan": True},
        )

    monkeypatch.setattr(engine.holo, "decide", holo_decide)
    monkeypatch.setattr(engine.jev, "decide", jev_decide)
    try:
        await engine.start()
        # Exercise the real bounded retry delays (seven 0.5-second intervals).
        await asyncio.wait_for(engine.task, timeout=8)
        assert holo_frames == [f"frame-{i}" for i in range(1, 16, 2)]
        assert jev_frames == [f"frame-{i}" for i in range(2, 17, 2)]
        assert bridge.executed == []
        assert engine.state["metrics"]["actions"] == 0
        assert engine.state["metrics"]["observations"] == 16
        assert engine.state["metrics"]["recoveries"] == 8
        assert engine.state["status"] == "error"
        assert engine.state["keys"] == [] and engine.state["action"] is None
        events = read_events(engine.recorder.path / "events.jsonl")
        assert not any(event["type"] in {"action", "ack"} for event in events)
        assert not any(event["type"] == "recovery_plan" for event in events)
        assert len([event for event in events if event["type"] == "recovery"]) == 8
        assert any(
            event["type"] == "error" and "Repeated model abstentions" in event["data"]["message"]
            for event in events
        )
        assert bridge.stops[-1] == "Agent loop stopped"
    finally:
        await engine.close()


async def test_repeated_rejections_require_jev_approval_before_one_shot_camera_scan(
    tmp_path, monkeypatch
):
    class SequentialBridge(FakeBridge):
        def __init__(self):
            super().__init__()
            self.observed = []

        async def observe(self):
            observation = frame(f"frame-{len(self.observed) + 1}")
            self.observed.append(observation.frame_id)
            return observation

    bridge = SequentialBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused-holo",
            jev_api_key="unused-jev",
            max_actions=1,
            validate_holo_plans=True,
            allow_exploration=True,
        ),
        bridge,
    )
    holo_frames, jev_calls = [], []
    blocked_plan = Decision(
        objective="Approach the trunk",
        strategy="Walk toward the visible tree",
        expected_effect="The tree becomes closer",
        action=Action(keys=["forward"], duration_ticks=10),
    )

    async def holo_decide(observation, context):
        holo_frames.append(observation.frame_id)
        return ModelResult(
            decision=blocked_plan,
            request={"frame_id": observation.frame_id},
            response={"plan": True},
            latency_ms=10,
            model="fake-holo",
        )

    async def jev_decide(observation, context):
        jev_calls.append((observation.frame_id, json.loads(json.dumps(context))))
        assert context["validate_unexecuted_plan"] is True
        assert bridge.executed == []
        proposal = Decision.model_validate(context["previous_decision"])
        if len(jev_calls) <= 2:
            assert proposal == blocked_plan
            raise ReplanRequired(
                "The proposed forward step is visibly blocked",
                provider="jev",
                latency_ms=3,
                request={"frame_id": observation.frame_id},
                response={"replan": True},
            )
        assert proposal.action == Action(keys=[], duration_ticks=1, yaw_delta=25, pitch_delta=15)
        return ModelResult(
            decision=proposal,
            request={"frame_id": observation.frame_id},
            response={"validated": True},
            latency_ms=4,
            model="fake-jev",
        )

    monkeypatch.setattr(engine.holo, "decide", holo_decide)
    monkeypatch.setattr(engine.jev, "decide", jev_decide)
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=5)
        assert holo_frames == ["frame-1", "frame-3"]
        assert [call[0] for call in jev_calls] == ["frame-2", "frame-4", "frame-5"]
        assert bridge.observed == [f"frame-{index}" for index in range(1, 6)]
        assert bridge.executed == [
            (
                Action(keys=[], duration_ticks=1, yaw_delta=25, pitch_delta=15),
                "frame-5",
                engine.settings.frame_ttl_ms,
            )
        ]
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 1
        assert engine.state["metrics"]["recoveries"] == 2
        assert engine.state["metrics"]["errors"] == 0
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert engine.previous is None  # A scan must force Holo to interpret the next view.
        events = read_events(engine.recorder.path / "events.jsonl")
        recovery_plans = [event for event in events if event["type"] == "recovery_plan"]
        assert len(recovery_plans) == 1
        assert recovery_plans[0]["data"]["origin"] == "local_camera_recovery"
        assert recovery_plans[0]["data"]["frame_id"] == "frame-5"
        assert recovery_plans[0]["data"]["attempt"] == 1
        assert len([event for event in events if event["type"] == "plan"]) == 2
        assert [event["data"]["frame_id"] for event in events if event["type"] == "action"] == [
            "frame-5"
        ]
        assert any(
            event["type"] == "event" and event["data"].get("name") == "run_budget_reached"
            for event in events
        )
    finally:
        await engine.close()


@pytest.mark.parametrize("validate_holo_plans", [False, True])
async def test_completed_noop_replans_with_holo_instead_of_jev_continuation(
    tmp_path, monkeypatch, validate_holo_plans
):
    bridge = FakeBridge()
    observations = []

    async def observe():
        observation = frame(f"frame-{len(observations) + 1}")
        observations.append(observation.frame_id)
        return observation

    monkeypatch.setattr(bridge, "observe", observe)
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused-holo",
            jev_api_key="unused-jev",
            max_actions=2,
            validate_holo_plans=validate_holo_plans,
        ),
        bridge,
    )
    holo_frames, jev_frames = [], []
    noop = Decision(
        objective="Inspect the current view",
        strategy="Observe before choosing a target",
        expected_effect="No physical change is expected",
        action=Action(keys=[], duration_ticks=1),
    )
    turn = noop.model_copy(update={"action": Action(keys=[], duration_ticks=1, yaw_delta=15)})

    async def holo_decide(observation, context):
        holo_frames.append(observation.frame_id)
        return ModelResult(
            decision=noop if len(holo_frames) == 1 else turn,
            request={"frame_id": observation.frame_id},
            response={"plan": True},
            latency_ms=1,
            model="fake-holo",
        )

    async def jev_decide(observation, context):
        # Both calls must validate a new Holo proposal. A no-op continuation
        # would call Jev without this flag and without a fresh Holo decision.
        assert validate_holo_plans
        assert context.get("validate_unexecuted_plan") is True
        assert context["plan_source_frame_id"] != observation.frame_id
        jev_frames.append(observation.frame_id)
        return ModelResult(
            decision=Decision.model_validate(context["previous_decision"]),
            request={"frame_id": observation.frame_id},
            response={"validated": True},
            latency_ms=1,
            model="fake-jev",
        )

    monkeypatch.setattr(engine.holo, "decide", holo_decide)
    monkeypatch.setattr(engine.jev, "decide", jev_decide)
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=2)
        assert holo_frames == (
            ["frame-1", "frame-3"] if validate_holo_plans else ["frame-1", "frame-2"]
        )
        assert jev_frames == (["frame-2", "frame-4"] if validate_holo_plans else [])
        assert [item[0] for item in bridge.executed] == [noop.action, turn.action]
        assert [item[1] for item in bridge.executed] == (
            ["frame-2", "frame-4"] if validate_holo_plans else ["frame-1", "frame-2"]
        )
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 2
        assert engine.state["metrics"]["recoveries"] == 0
        assert engine.state["metrics"]["errors"] == 0
    finally:
        await engine.close()


async def test_repeated_noops_record_unverified_feedback_and_stop_at_recovery_budget(
    tmp_path, monkeypatch
):
    bridge = FakeBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused-holo",
            jev_api_key="unused-jev",
            validate_holo_plans=True,
            max_actions=100,
            max_run_seconds=10,
        ),
        bridge,
    )
    calls = {"holo": 0, "jev": 0}
    noop = Decision(
        objective="Inspect the view",
        strategy="Wait without using any controls",
        expected_effect="No physical change is expected",
        action=Action(keys=[], duration_ticks=1),
    )

    async def holo_decide(observation, context):
        calls["holo"] += 1
        assert not context["recovery_guidance"]["repeated_proposal"]
        return ModelResult(
            decision=noop,
            request={},
            response={},
            latency_ms=1,
            model="fake-holo",
        )

    async def jev_decide(observation, context):
        calls["jev"] += 1
        assert context.get("validate_unexecuted_plan") is True
        assert Decision.model_validate(context["previous_decision"]).action == noop.action
        return ModelResult(
            decision=noop,
            request={},
            response={},
            latency_ms=1,
            model="fake-jev",
        )

    monkeypatch.setattr(engine.holo, "decide", holo_decide)
    monkeypatch.setattr(engine.jev, "decide", jev_decide)
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=4)
        assert engine.state["status"] == "error"
        assert engine.state["metrics"]["recoveries"] == 8
        assert engine.state["metrics"]["errors"] == 1
        assert 0 < len(bridge.executed) < engine.settings.max_actions
        assert calls["holo"] == calls["jev"]
        assert all(action == noop.action for action, _, _ in bridge.executed)
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert bridge.stops[-1] == "Agent loop stopped"
        events = read_events(engine.recorder.path / "events.jsonl")
        feedback = [event["data"] for event in events if event["type"] == "recovery"]
        assert len(feedback) == 8
        assert all(item["feedback_kind"] == "no_input_repeat" for item in feedback)
        assert all(item["physical_outcome"] == "unverified" for item in feedback)
        assert not any(event["type"] == "recovery_plan" for event in events)
        assert any(
            event["type"] == "error" and "Recovery budget exhausted" in event["data"]["message"]
            for event in events
        )
        memory = [json.loads(line) for line in (tmp_path / "memory.jsonl").read_text().splitlines()]
        assert all(item["feedback_kind"] == "no_input_repeat" for item in memory)
        assert all(item["physical_outcome"] == "unverified" for item in memory)
    finally:
        await engine.close()


async def test_provider_error_after_completed_action_does_not_blame_that_action(
    tmp_path, monkeypatch
):
    bridge = FakeBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused-holo",
            jev_api_key="unused-jev",
            validate_holo_plans=False,
        ),
        bridge,
    )
    calls = {"holo": 0, "jev": 0}
    plan = Decision(
        objective="Approach the tree",
        strategy="Take one short forward step",
        expected_effect="Nearby geometry shifts",
        action=Action(keys=["forward"], duration_ticks=2),
    )

    async def holo_decide(observation, context):
        calls["holo"] += 1
        return ModelResult(
            decision=plan,
            request={},
            response={},
            latency_ms=1,
            model="fake-holo",
            completed=calls["holo"] > 1,
        )

    async def jev_decide(observation, context):
        calls["jev"] += 1
        assert context["previous_decision"]["action"] == plan.action.model_dump()
        raise ProviderError(
            "Synthetic Jev HTTP 503 failure",
            provider="jev",
            latency_ms=1,
            request={},
            response={"status": 503},
        )

    monkeypatch.setattr(engine.holo, "decide", holo_decide)
    monkeypatch.setattr(engine.jev, "decide", jev_decide)
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=3)
        assert calls == {"holo": 2, "jev": 1}
        assert [item[0] for item in bridge.executed] == [plan.action]
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 1
        assert engine.state["metrics"]["recoveries"] == 1
        assert engine.state["metrics"]["errors"] == 1
        events = read_events(engine.recorder.path / "events.jsonl")
        feedback = [event["data"] for event in events if event["type"] == "recovery"]
        assert len(feedback) == 1
        assert feedback[0]["feedback_kind"] == "provider_error"
        assert feedback[0]["physical_outcome"] == "unverified"
        assert feedback[0]["previous_action"] is None
        assert feedback[0]["expected_effect"] is None
        assert engine.recent[0]["action"] == plan.action.model_dump()
        assert engine.recent[0]["ack"]["status"] == "completed"
        assert not any(event["type"] == "recovery_plan" for event in events)
    finally:
        await engine.close()


async def test_completed_movement_resets_consecutive_veto_budget_but_retains_total_recoveries(
    tmp_path, monkeypatch, skip_retry_delays
):
    bridge = FakeBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused-holo",
            jev_api_key="unused-jev",
            validate_holo_plans=True,
            allow_exploration=False,
            max_actions=2,
        ),
        bridge,
    )
    plan = Decision(
        objective="Approach the visible trunk",
        strategy="Use a short forward step after fresh visual validation",
        expected_effect="Nearby block edges shift",
        action=Action(keys=["forward"], duration_ticks=10),
    )
    outcomes = ["veto"] * 7 + ["accept"] + ["veto"] * 2 + ["accept"]
    jev_calls = []

    async def holo_decide(observation, context):
        return ModelResult(decision=plan, request={}, response={}, latency_ms=1, model="fake-holo")

    async def jev_decide(observation, context):
        outcome = outcomes[len(jev_calls)]
        jev_calls.append(outcome)
        if outcome == "veto":
            raise ReplanRequired(
                "Synthetic visible obstruction",
                provider="jev",
                latency_ms=1,
                request={},
                response={},
            )
        return ModelResult(decision=plan, request={}, response={}, latency_ms=1, model="fake-jev")

    monkeypatch.setattr(engine.holo, "decide", holo_decide)
    monkeypatch.setattr(engine.jev, "decide", jev_decide)
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=3)
        assert jev_calls == outcomes
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 2
        assert engine.state["metrics"]["recoveries"] == 9
        assert engine.state["metrics"]["errors"] == 0
        assert [action for action, _, _ in bridge.executed] == [plan.action, plan.action]
        events = read_events(engine.recorder.path / "events.jsonl")
        acks = [event for event in events if event["type"] == "ack"]
        recoveries = [event for event in events if event["type"] == "recovery"]
        assert len(acks) == 2 and all(event["data"]["status"] == "completed" for event in acks)
        assert sum(event["seq"] < acks[0]["seq"] for event in recoveries) == 7
        assert sum(acks[0]["seq"] < event["seq"] < acks[1]["seq"] for event in recoveries) == 2
        assert not any(event["type"] == "error" for event in events)
        assert any(
            event["type"] == "event" and event["data"].get("name") == "run_budget_reached"
            for event in events
        )
    finally:
        await engine.close()


async def test_camera_recovery_requires_new_consecutive_vetoes_after_completed_movement(
    tmp_path, monkeypatch, skip_retry_delays
):
    bridge = FakeBridge()
    engine = AgentEngine(
        Settings(
            mode="live",
            gameplay_mode="visual",
            runs_dir=tmp_path,
            holo_api_key="unused-holo",
            jev_api_key="unused-jev",
            validate_holo_plans=True,
            allow_exploration=True,
            max_actions=3,
        ),
        bridge,
    )
    plan = Decision(
        objective="Approach the visible trunk",
        strategy="Take one validated forward step",
        expected_effect="Nearby block edges shift",
        action=Action(keys=["forward"], duration_ticks=10),
    )
    outcomes = ["veto", "veto", "scan", "move", "veto", "veto", "scan"]
    jev_calls, holo_calls = [], []

    async def holo_decide(observation, context):
        holo_calls.append(context)
        return ModelResult(decision=plan, request={}, response={}, latency_ms=1, model="fake-holo")

    async def jev_decide(observation, context):
        outcome = outcomes[len(jev_calls)]
        jev_calls.append(outcome)
        proposal = Decision.model_validate(context["previous_decision"])
        if outcome == "scan":
            assert context["validate_unexecuted_plan"] is True
            assert proposal.action.keys == []
            assert proposal.action.yaw_delta or proposal.action.pitch_delta
        else:
            assert proposal.action == plan.action
        if outcome == "veto":
            raise ReplanRequired(
                "Synthetic visible obstruction",
                provider="jev",
                latency_ms=1,
                request={},
                response={},
            )
        return ModelResult(
            decision=proposal, request={}, response={}, latency_ms=1, model="fake-jev"
        )

    monkeypatch.setattr(engine.holo, "decide", holo_decide)
    monkeypatch.setattr(engine.jev, "decide", jev_decide)
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=3)
        assert jev_calls == outcomes
        assert len(holo_calls) == 4
        assert engine.state["status"] == "paused"
        assert engine.state["metrics"]["actions"] == 3
        assert engine.state["metrics"]["recoveries"] == 4
        assert engine.state["metrics"]["errors"] == 0
        assert bridge.executed[0][0].keys == []
        assert bridge.executed[1][0] == plan.action
        assert bridge.executed[2][0].keys == []
        events = read_events(engine.recorder.path / "events.jsonl")
        scans = [event for event in events if event["type"] == "recovery_plan"]
        acks = [event for event in events if event["type"] == "ack"]
        recoveries = [event for event in events if event["type"] == "recovery"]
        assert len(scans) == 2
        assert sum(event["seq"] < scans[0]["seq"] for event in recoveries) == 2
        # The two historical vetoes remain in memory, but a new scan waits for
        # two new rejections after the completed movement ACK.
        assert sum(acks[1]["seq"] < event["seq"] < scans[1]["seq"] for event in recoveries) == 2
        assert not any(acks[0]["seq"] < event["seq"] < acks[1]["seq"] for event in scans)
        assert bridge.stops[-1] == "Agent loop stopped"
    finally:
        await engine.close()


async def connected_bridge():
    bridge, socket = GameBridge(), Socket()
    assert await bridge.attach(socket)
    await bridge.receive({"type": "hello", "protocol": 1, "minecraft": "1.21.1"})
    bridge.frames["frame-1"] = time.monotonic()
    return bridge, socket


async def test_action_ack_timeout_releases_controls_and_discards_late_ack(monkeypatch):
    bridge, socket = await connected_bridge()
    request = bridge.request

    async def short_deadline(message, timeout):
        return await request(message, timeout=0.01)

    monkeypatch.setattr(bridge, "request", short_deadline)
    with pytest.raises(BridgeError, match="timed out"):
        await bridge.execute(Action(keys=["forward"], duration_ticks=1), "frame-1", 15_000)
    assert [message["type"] for message in socket.sent] == ["action", "stop"]
    assert bridge.pending == {}
    await bridge.receive({"type": "ack", "id": socket.sent[0]["id"], "status": "completed"})
    assert bridge.pending == {}
    await bridge.detach(socket)


@pytest.mark.parametrize("late_status", ["completed", "cancelled"])
async def test_cancelled_bridge_request_retains_wire_command_and_unmatched_ack(
    tmp_path, monkeypatch, late_status
):
    bridge, socket = await connected_bridge()
    recorder = RunRecorder(tmp_path, "live", {})
    bridge.journal = recorder.log
    send = socket.send_json

    async def send_after_journal(message):
        events = read_events(recorder.path / "events.jsonl")
        assert events[-1]["type"] == "bridge_command"
        assert events[-1]["data"] == message
        await send(message)

    monkeypatch.setattr(socket, "send_json", send_after_journal)
    task = asyncio.create_task(
        bridge.execute(Action(keys=["forward"], duration_ticks=10), "frame-1", 15_000)
    )
    try:
        await asyncio.wait_for(socket.action_sent.wait(), timeout=1)
        command = socket.sent[0]
        assert command["id"] in bridge.pending
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert bridge.pending == {}
        await bridge.stop("Controller cancelled the in-flight action")
        late_ack = {
            "type": "ack",
            "id": command["id"],
            "status": late_status,
            "start_tick": 5,
            "end_tick": 6,
        }
        await bridge.receive(late_ack)
        assert bridge.pending == {}
        events = read_events(recorder.path / "events.jsonl")
        commands = [event for event in events if event["type"] == "bridge_command"]
        acknowledgments = [event for event in events if event["type"] == "bridge_ack"]
        assert len(commands) == 2
        assert commands[0]["data"] == command
        assert commands[1]["data"]["type"] == "stop"
        assert len(acknowledgments) == 1
        assert acknowledgments[0]["data"] == late_ack
        assert commands[0]["seq"] < acknowledgments[0]["seq"]
    finally:
        if not task.done():
            task.cancel()
        await bridge.detach(socket)
        recorder.finish("interrupted")


async def test_disconnect_fails_pending_action_and_clears_frame_validity():
    bridge, socket = await connected_bridge()
    task = asyncio.create_task(bridge.execute(Action(duration_ticks=1), "frame-1", 15_000))
    await asyncio.wait_for(socket.action_sent.wait(), timeout=1)
    await bridge.detach(socket)
    with pytest.raises(BridgeError, match="disconnected"):
        await task
    assert not bridge.connected
    assert bridge.pending == {}
    assert bridge.frames == {}


async def test_expired_frame_cannot_send_an_action():
    bridge, socket = await connected_bridge()
    bridge.frames["frame-1"] = time.monotonic() - 5
    with pytest.raises(BridgeError, match="expired"):
        await bridge.execute(Action(keys=["attack"]), "frame-1", 100)
    assert socket.sent == []
    await bridge.detach(socket)


async def test_second_inflight_game_request_is_rejected():
    bridge, socket = await connected_bridge()
    task = asyncio.create_task(bridge.execute(Action(duration_ticks=1), "frame-1", 15_000))
    await asyncio.wait_for(socket.action_sent.wait(), timeout=1)
    with pytest.raises(BridgeError, match="one game request"):
        await bridge.observe()
    await bridge.receive({"type": "ack", "id": socket.sent[0]["id"], "status": "completed"})
    assert (await task)["status"] == "completed"
    assert bridge.pending == {}
    await bridge.detach(socket)


@pytest.mark.parametrize("corruption", ["invalid_base64", "not_an_image", "dimensions", "mime"])
def test_corrupted_frame_never_enters_replay(tmp_path, corruption):
    observation = frame()
    if corruption == "invalid_base64":
        observation.image_base64 = "%%%"
    elif corruption == "not_an_image":
        observation.image_base64 = base64.b64encode(b"not an image").decode()
    elif corruption == "dimensions":
        observation.width = 128
    else:
        observation.mime_type = "image/jpeg"
    recorder = RunRecorder(tmp_path, "live", {})
    try:
        with pytest.raises((ValueError, OSError)):
            recorder.save_observation(observation)
        assert list((recorder.path / "frames").iterdir()) == []
        assert not any(
            e["type"] == "observation" for e in read_events(recorder.path / "events.jsonl")
        )
    finally:
        recorder.finish("failed")


async def test_engine_rejects_corrupt_observation_before_inference_or_action(tmp_path):
    observation = frame()
    observation.image_base64 = base64.b64encode(b"bad frame").decode()
    bridge = FakeBridge(observation)
    engine = AgentEngine(
        Settings(mode="live", gameplay_mode="visual", runs_dir=tmp_path, holo_api_key="unused"),
        bridge,
    )
    await engine.holo.aclose()
    provider = WaitingProvider()
    engine.holo = provider
    try:
        await engine.start()
        await asyncio.wait_for(engine.task, timeout=2)
        assert engine.state["status"] == "error"
        assert engine.state["metrics"]["errors"] == 1
        assert provider.calls == 0
        assert bridge.executed == []
        assert bridge.stops
        assert engine.state["keys"] == []
        summary = json.loads((engine.recorder.path / "summary.json").read_text())
        assert summary["status"] == "error"
    finally:
        await engine.close()


def test_journal_redacts_nested_secrets_and_replays_after_partial_final_write(tmp_path):
    recorder = RunRecorder(tmp_path, "live", {"api_key": "private-configuration"})
    recorder.save_observation(frame())
    recorder.log(
        "model_call",
        {
            "request": {"Authorization": "Bearer private-header", "api_key": "private-key"},
            "response": {
                "items": [{"message": "provider echoed hk-test-secret and cog_test-token"}]
            },
            "token": "private-token",
        },
    )
    recorder.finish("finished", metrics={"actions": 1}, milestones=[{"id": "wood"}])
    path = recorder.path / "events.jsonl"
    original = read_events(path)
    with path.open("a") as stream:
        stream.write('{"seq":99,"type":"partial"')
    assert read_events(path) == original
    assert read_events(tmp_path / "missing.jsonl") == []
    combined = path.read_text() + (recorder.path / "summary.json").read_text()
    for secret in (
        "private-configuration",
        "private-header",
        "private-key",
        "private-token",
        "hk-test-secret",
        "cog_test-token",
    ):
        assert secret not in combined
    assert "[REDACTED]" in combined
    summary = json.loads((recorder.path / "summary.json").read_text())
    assert summary["metrics"] == {"actions": 1}
    assert summary["milestones"] == [{"id": "wood"}]
    frozen_elapsed = recorder.elapsed_ms
    assert recorder.elapsed_ms == frozen_elapsed
    recorder.finish("must-not-overwrite")
    assert json.loads((recorder.path / "summary.json").read_text())["status"] == "finished"
    with pytest.raises(RuntimeError, match="closed"):
        recorder.log("late", {})
