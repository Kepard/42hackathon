"""Final-text controls, useful guidance, and questions cannot block emergency pause."""

import asyncio
import json
import uuid
from collections import deque
from types import SimpleNamespace

import httpx
import pytest
from holospeed.app import create_app
from holospeed.config import Settings
from holospeed.providers import ProviderError
from holospeed.voice import VoiceAnswerer, VoiceControl, intent, validate_command

RUN = "20261108T104247-2170a4ce"
SESSION = "45463597-20b7-4af7-b3b3-f5fb778203ab"


def command(text="pause", **changes):
    return {
        "command_id": str(uuid.uuid4()),
        "text": text,
        "run_id": RUN,
        "session_id": SESSION,
        "utterance_id": str(uuid.uuid4()),
        **changes,
    }


class Engine:
    def __init__(self):
        self.state = {
            "run_id": RUN,
            "status": "running",
            "objective": "Mine four iron ore",
            "events": [],
        }
        self.events = []
        self.operator_guidance = deque(maxlen=6)
        self.recorder = SimpleNamespace(closed=False)
        self.task = None
        self.pauses = []
        self.starts = 0
        self.finishes = 0
        self.paused_game = 0
        self.arms = 0
        self.timings = []
        self.bridge = self

    def log(self, kind, data):
        self.events.append({"type": kind, "data": data})

    def broadcast(self):
        pass

    def snapshot(self):
        return self.state | {"events": list(self.events)}

    def require_run(self, expected_run_id):
        if expected_run_id != self.state["run_id"]:
            raise ValueError("The run changed")

    async def pause(self, reason, *, expected_run_id, pause_world):
        self.require_run(expected_run_id)
        self.pauses.append(reason)
        self.state["status"] = "paused"
        return await self.pause_game() if pause_world else False

    async def pause_game(self):
        self.paused_game += 1
        return True

    async def finish(self, *, expected_run_id, pause_world):
        self.require_run(expected_run_id)
        self.finishes += 1
        self.recorder.closed = True
        self.state["status"] = "idle"
        if pause_world:
            await self.pause_game()

    async def start(self, *, expected_run_id, arm, resume_only):
        self.require_run(expected_run_id)
        assert resume_only and not self.recorder.closed
        if arm:
            await self.arm()
        self.starts += 1
        self.state["status"] = "running"

    async def arm(self):
        self.arms += 1

    def timing(self, *values):
        self.timings.append(values)


class Answerer:
    journal = None

    def __init__(self):
        self.calls = []
        self.release = asyncio.Event()
        self.entered = asyncio.Event()
        self.failure = False

    async def answer(self, text, evidence):
        self.calls.append((text, evidence))
        self.entered.set()
        await self.release.wait()
        if self.failure:
            raise ProviderError("service unavailable")
        return (
            "I am mining iron for the bucket and ignition tool.",
            42.0,
            {"prompt_tokens": 12, "completion_tokens": 5},
        )

    async def aclose(self):
        pass


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Pause", "pause"),
        ("Can you please pause the game now?", "pause"),
        ("STOP MINING!", "pause"),
        ("end the run please", "stop"),
        ("Could you resume gameplay?", "resume"),
        ("don't pause", "guidance"),
        ("Do not stop the run", "guidance"),
        ("Pause after crafting", "guidance"),
        ("Why did you pause?", "question"),
        ("What are you doing?", "question"),
        ("Gather more wood", "guidance"),
        ("the sign says pause", "guidance"),
    ],
)
def test_control_intent_is_conservative_and_negation_never_controls(text, expected):
    assert intent(text) == expected


async def test_pause_is_immediate_idempotent_and_freezes_local_world():
    engine = Engine()
    answerer = Answerer()
    voice = VoiceControl(engine, Settings(mode="live"), answerer=answerer)
    value = command("Please pause the game")
    result = await voice.apply(value)
    assert result == {"intent": "pause", "reply": "Gameplay paused; agent controls released."}
    assert await voice.apply(value) == result
    assert await voice.apply(value | {"command_id": str(uuid.uuid4())}) == result
    assert len(engine.pauses) == 1 and engine.paused_game == 1 and not answerer.calls
    with pytest.raises(ValueError, match="identity"):
        await voice.apply(value | {"text": "resume"})
    await voice.close()


async def test_stale_run_and_malformed_text_never_mutate_controls():
    engine = Engine()
    voice = VoiceControl(engine, Settings(), answerer=Answerer())
    for changes in (
        {"run_id": None},
        {"text": ""},
        {"text": "x" * 1001},
        {"text": "bad\x00text"},
        {"session_id": "wss://api.gladia.io/private"},
        {"utterance_id": "../secret"},
        {"command_id": "not-uuid"},
    ):
        with pytest.raises(ValueError):
            await voice.apply(command(**changes))
    with pytest.raises(ValueError):
        validate_command(command() | {"api_key": "secret"})
    assert not engine.pauses and not engine.events
    await voice.close()


async def test_guidance_records_operator_preference_without_dispatching_a_skill():
    engine = Engine()
    voice = VoiceControl(engine, Settings(holo_api_key="hk-private"), answerer=Answerer())
    result = await voice.apply(command("Gather wood before going further hk-private"))
    assert result["intent"] == "guidance" and uuid.UUID(result["guidance_id"])
    assert (
        len(engine.operator_guidance) == 1
        and "hk-private" not in engine.operator_guidance[0]["text"]
    )
    assert not engine.pauses and engine.starts == 0
    assert [x["type"] for x in engine.events] == ["voice_received", "voice_guidance", "voice_reply"]
    await voice.close()


async def test_no_active_run_or_unsupported_requests_do_not_claim_a_queued_action():
    engine = Engine()
    voice = VoiceControl(engine, Settings(), answerer=Answerer())
    result = await voice.apply(command("Teleport to the nether"))
    assert "cannot" in result["reply"] and not engine.operator_guidance
    engine.recorder.closed = True
    result = await voice.apply(command("Gather wood"))
    assert "Start a recorded run" in result["reply"] and not engine.operator_guidance
    result = await voice.apply(command("Why are you idle?"))
    assert "no active recording" in result["reply"] and voice.question_task is None
    result = await voice.apply(command("Resume"))
    assert "no paused recording" in result["reply"] and engine.starts == 0
    await voice.close()


async def test_question_runs_in_background_and_cannot_hold_up_pause():
    engine = Engine()
    answerer = Answerer()
    voice = VoiceControl(engine, Settings(mode="live"), answerer=answerer)
    asked = command("Why are you mining iron?")
    result = await voice.apply(asked)
    assert result["intent"] == "question" and "reviewing" in result["reply"]
    await answerer.entered.wait()
    paused = await asyncio.wait_for(voice.apply(command("pause")), 0.2)
    assert paused["intent"] == "pause" and engine.paused_game == 1
    assert not voice.question_task.done()
    answerer.release.set()
    await voice.question_task
    answers = [
        e["data"]
        for e in engine.events
        if e["type"] == "voice_reply" and e["data"]["command_id"] == asked["command_id"]
    ]
    assert answers[-1]["provider"] == "holo" and answers[-1]["pending"] is False
    assert answers[-1]["latency_ms"] == 42 and len(engine.timings) == 1
    assert "Current run status: paused" in answers[-1]["reply"]
    assert answerer.calls[0][1]["objective"] == "Mine four iron ore"
    await voice.close()


async def test_late_question_answer_never_relabels_a_new_run():
    engine = Engine()
    answerer = Answerer()
    voice = VoiceControl(engine, Settings(), answerer=answerer)
    await voice.apply(command("What now?"))
    await answerer.entered.wait()
    engine.state["run_id"] = "20261108T110000-deadbeef"
    before = len(engine.events)
    answerer.release.set()
    await voice.question_task
    assert len(engine.events) == before and not engine.timings
    await voice.close()


async def test_question_service_failure_is_an_explicit_local_fallback():
    engine = Engine()
    answerer = Answerer()
    answerer.failure = True
    answerer.release.set()
    voice = VoiceControl(engine, Settings(), answerer=answerer)
    await voice.apply(command("What now?"))
    await voice.question_task
    result = engine.events[-1]["data"]
    assert (
        result["provider"] == "local"
        and "unavailable" in result["reply"]
        and "Mine four iron ore" in result["reply"]
    )
    await voice.close()


async def test_stop_archives_but_resume_does_not_silently_create_a_new_run():
    engine = Engine()
    voice = VoiceControl(engine, Settings(mode="live"), answerer=Answerer())
    await voice.apply(command("resume"))
    assert engine.starts == 1 and engine.arms == 1
    await voice.apply(command("end the run"))
    assert engine.finishes == 1 and engine.paused_game == 1
    await voice.apply(command("resume"))
    assert engine.starts == 1
    await voice.close()


@pytest.mark.parametrize("provider_usage", [{"completion_tokens": 8}, None])
async def test_holo_answer_uses_grounded_text_only_and_records_real_payload(provider_usage):
    calls = []
    journal = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "none" and body["max_tokens"] == 300
        assert "recorded_evidence" in body["messages"][1]["content"]
        assert "tools" not in body and request.headers["Authorization"] == "Bearer hk-dummy"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Recorded inventory contains four raw iron."}}],
                "usage": provider_usage,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        answerer = VoiceAnswerer(Settings(holo_api_key="hk-dummy"), client)
        answerer.journal = lambda k, d: journal.append((k, d))
        reply, latency, usage = await answerer.answer(
            "What do I have?", {"inventory": {"minecraft:raw_iron": 4}}
        )
    assert reply.startswith("Recorded") and latency >= 0 and usage == (provider_usage or {})
    assert [k for k, _ in journal] == ["model_request", "model_response"]
    assert "hk-dummy" not in json.dumps(journal)


async def test_local_voice_endpoint_is_bounded_and_rejects_cross_origin(tmp_path):
    app = create_app(Settings(runs_dir=tmp_path))
    engine = app.state.engine
    # No start or real game/provider call is required for this validation boundary.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        result = await client.post("/api/voice", json=command("gather wood", run_id=None))
        assert (
            result.status_code == 200 and "Start a recorded run" in result.json()["data"]["reply"]
        )
        assert (
            await client.post(
                "/api/voice", json=command(), headers={"Origin": "https://evil.example"}
            )
        ).status_code == 403
        assert (await client.post("/api/voice", content=b"x" * 8193)).status_code == 400
        assert (await client.post("/api/voice", json=command())).status_code == 409
    await app.state.voice.close()
    await engine.close()


async def test_finished_recording_discards_late_answer_and_metrics():
    engine = Engine()
    answerer = Answerer()
    voice = VoiceControl(engine, Settings(), answerer=answerer)
    await voice.apply(command("Why iron?"))
    await answerer.entered.wait()
    engine.recorder.closed = True
    before = len(engine.events)
    answerer.release.set()
    await voice.question_task
    assert len(engine.events) == before and not engine.timings
    await voice.close()


async def test_voice_stop_cancels_pending_question_without_waiting_for_it():
    engine = Engine()
    answerer = Answerer()
    voice = VoiceControl(engine, Settings(), answerer=answerer)
    await voice.apply(command("Why iron?"))
    await answerer.entered.wait()
    result = await asyncio.wait_for(voice.apply(command("End the run")), 0.2)
    assert result["intent"] == "stop" and engine.finishes == 1
    await asyncio.sleep(0)
    assert voice.question_task.done() and not engine.timings
    await voice.close()
