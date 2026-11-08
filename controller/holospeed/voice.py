"""Operator speech commands and grounded Holo4 answers, outside the game tick loop.

Gladia audio goes directly from the explicitly opened browser microphone to its
short-lived transcription session. This local API receives final text only.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime

from .providers import ProviderError, _Provider, _redact
from .recording import redact

RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}-[a-f0-9]{8}\Z")
UTTERANCE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
# Intentionally anchored: questions, negation, quotations, and delayed requests
# must not become an immediate control operation merely because they say pause.
POLITE = r"(?:(?:can|could|would|will) you )?(?:please )?"
SUFFIX = r"(?: now)?(?: please)?"
CONTROL_PHRASES = {
    "pause": re.compile(
        POLITE
        + r"(?:pause(?: (?:the )?(?:game|run|agent|gameplay))?|stop|stop (?:moving|mining|crafting|building)|hold (?:on|up)|wait)"
        + SUFFIX
    ),
    "stop": re.compile(POLITE + r"(?:end|stop|finish) (?:the |this )?run" + SUFFIX),
    "resume": re.compile(
        POLITE
        + r"(?:resume(?: (?:the )?(?:game|run|agent|gameplay))?|continue(?: (?:the )?(?:game|run))?|keep going)"
        + SUFFIX
    ),
}
QUESTION = re.compile(
    r"^(?:what|why|how|where|when|which|who|are you|do you|did you|is there|can you explain|could you explain|tell me|explain)\b"
)
UNSUPPORTED = re.compile(
    r"\b(?:teleport|spawn (?:me |an? |some )?(?:items?|diamonds?|iron|obsidian)|creative mode|give command|setblock)\b",
    re.IGNORECASE,
)


def canonical_uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def validate_command(value: object) -> dict:
    fields = {"command_id", "text", "run_id", "session_id", "utterance_id"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Voice commands require their exact text and run identity")
    if not canonical_uuid(value["command_id"]):
        raise ValueError("Invalid voice command identity")
    run_id = value["run_id"]
    if run_id is not None and (not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id)):
        raise ValueError("Invalid voice run identity")
    if value["session_id"] != "typed" and not canonical_uuid(value["session_id"]):
        raise ValueError("Invalid transcription session identity")
    if not isinstance(value["utterance_id"], str) or not UTTERANCE_ID.fullmatch(
        value["utterance_id"]
    ):
        raise ValueError("Invalid transcription utterance identity")
    text = value["text"]
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 1000
        or any(ord(char) < 32 and char not in "\n\t" for char in text)
    ):
        raise ValueError("Voice text must contain at most 1000 ordinary characters")
    return dict(value)


def intent(text: str) -> str:
    normalized = re.sub(r"[^\w\s']", " ", text.casefold())
    normalized = " ".join(normalized.split())
    for name in ("stop", "pause", "resume"):
        if CONTROL_PHRASES[name].fullmatch(normalized):
            return name
    return "question" if QUESTION.match(normalized) or text.rstrip().endswith("?") else "guidance"


class VoiceAnswerer(_Provider):
    name = "holo"

    async def answer(self, question: str, evidence: dict) -> tuple[str, float, dict]:
        body = {
            "model": self.settings.holo_model,
            "reasoning_effort": "none",
            "temperature": 0.2,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You explain HoloSpeed's Minecraft activity to its operator. Answer the spoken "
                        "question in at most two short sentences using only the supplied recorded evidence. "
                        "State uncertainty or stale evidence explicitly. Never claim you performed an "
                        "action: this response has no game-control tools. Available skills are normal "
                        "survival gathering, mining, crafting, smelting and portal construction. The "
                        "current objective remains authoritative unless the operator explicitly supplies "
                        "new guidance. Distinguish recorded progress from planned outcomes."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"question": question, "recorded_evidence": evidence}, ensure_ascii=False
                    ),
                },
            ],
        }
        request, response, latency = await self._post(
            self.settings.holo_base_url.rstrip("/") + "/chat/completions",
            self.settings.holo_api_key,
            body,
            self.settings.holo_model,
        )
        try:
            reply = response["choices"][0]["message"]["content"]
            if not isinstance(reply, str) or not reply.strip() or len(reply) > 2000:
                raise ValueError("Invalid voice answer")
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise self._invalid(
                "Holo4 returned an invalid spoken-question answer",
                request,
                response,
                latency,
                self.settings.holo_model,
            ) from error
        usage = response.get("usage")
        return reply.strip(), latency, usage if isinstance(usage, dict) else {}


class VoiceControl:
    def __init__(self, engine, settings, *, answerer=None):
        self.engine = engine
        self.settings = settings
        self.answerer = answerer or VoiceAnswerer(settings)
        self.lock = asyncio.Lock()
        self.receipts: OrderedDict[str, tuple[dict, dict]] = OrderedDict()
        self.utterances: OrderedDict[tuple, tuple[str, dict]] = OrderedDict()
        self.question_task: asyncio.Task | None = None
        self.closed = False

    def safe(self, text: str) -> str:
        return str(
            redact(
                _redact(
                    text,
                    (
                        self.settings.holo_api_key,
                        self.settings.jev_api_key,
                        self.settings.devin_api_key,
                        self.settings.bridge_token,
                    ),
                )
            )
        )[:2000]

    def event(self, kind: str, data: dict) -> None:
        self.engine.log(kind, data)
        self.engine.broadcast()

    def evidence(self) -> dict:
        snapshot = self.engine.snapshot()
        samples = [
            event for event in snapshot.get("events", []) if event.get("type") == "state_summary"
        ]
        latest = samples[-1] if samples else None
        return {
            "sampled_at": datetime.now(UTC).isoformat(),
            **{
                key: snapshot.get(key)
                for key in (
                    "run_id",
                    "status",
                    "objective",
                    "strategy",
                    "expected_effect",
                    "action",
                    "milestones",
                )
            },
            "latest_player_sample": latest,
            "evidence_note": "Recorded controller events; this is not a new visual observation or an unrecorded live state read.",
        }

    async def apply(self, raw: dict) -> dict:
        command = validate_command(raw)
        async with self.lock:
            if self.closed:
                raise ValueError("Voice control is shutting down")
            previous = self.receipts.get(command["command_id"])
            if previous:
                if previous[0] != command:
                    raise ValueError("Voice command identity already belongs to other text")
                return dict(previous[1])
            if command["run_id"] != self.engine.state.get("run_id"):
                raise ValueError("The run changed before this voice command arrived; speak again")
            utterance_key = (command["run_id"], command["session_id"], command["utterance_id"])
            repeated = self.utterances.get(utterance_key)
            if repeated:
                if repeated[0] != command["text"]:
                    raise ValueError("Transcription identity already belongs to other text")
                return dict(repeated[1])
            kind = intent(command["text"])
            text = self.safe(command["text"])
            self.event(
                "voice_received",
                {
                    "command_id": command["command_id"],
                    "text": text,
                    "session_id": command["session_id"],
                    "utterance_id": command["utterance_id"],
                    "source": "typed" if command["session_id"] == "typed" else "gladia",
                    "intent": kind,
                },
            )
            result = {"intent": kind, "reply": ""}
            if kind == "pause":
                paused = await self.engine.pause(
                    "Operator voice pause",
                    expected_run_id=command["run_id"],
                    pause_world=self.settings.mode == "live",
                )
                result["reply"] = (
                    "Gameplay paused; agent controls released."
                    if paused
                    else "Agent paused and controls released."
                )
            elif kind == "stop":
                if self.question_task and not self.question_task.done():
                    self.question_task.cancel()
                # Journal the acknowledgement before finish closes the run recorder.
                result["reply"] = "Run stopped and archived."
                self.event(
                    "voice_reply",
                    {
                        "command_id": command["command_id"],
                        "intent": "stop",
                        "reply": "Stop requested; the controller is archiving this run.",
                        "provider": "local",
                    },
                )
                await self.engine.finish(
                    expected_run_id=command["run_id"], pause_world=self.settings.mode == "live"
                )
            elif kind == "resume":
                if self.engine.recorder is None or self.engine.recorder.closed:
                    result["reply"] = (
                        "There is no paused recording to resume. Use New run to start a recording."
                    )
                else:
                    await self.engine.start(
                        expected_run_id=command["run_id"],
                        arm=self.settings.mode == "live",
                        resume_only=True,
                    )
                    result["reply"] = "Run resumed with fresh observations and prerequisite checks."
            elif kind == "question":
                if self.engine.recorder is None or self.engine.recorder.closed:
                    result["reply"] = self.safe(
                        f"There is no active recording. Recorded objective: {self.engine.state.get('objective') or 'not set'}."
                    )
                elif self.question_task and not self.question_task.done():
                    result["reply"] = (
                        "Holo4 is still answering the previous question. Pause and stop remain available."
                    )
                else:
                    result["reply"] = "Holo4 is reviewing the latest recorded game state."
                    evidence = self.evidence()
                    self.question_task = asyncio.create_task(
                        self._answer(command["command_id"], command["run_id"], text, evidence)
                    )
            elif self.engine.recorder is None or self.engine.recorder.closed:
                result["reply"] = (
                    "Start a recorded run before giving gameplay objectives. You can still ask questions."
                )
            elif UNSUPPORTED.search(text):
                result["reply"] = (
                    "I can guide gathering, mining, crafting, smelting and portal work using survival mechanics; I cannot spawn items or teleport."
                )
            else:
                identifier = str(uuid.uuid4())
                guidance = {
                    "id": identifier,
                    "text": text,
                    "received_at": datetime.now(UTC).isoformat(),
                }
                self.engine.operator_guidance.append(guidance)
                self.event("voice_guidance", {"command_id": command["command_id"], **guidance})
                result.update(
                    guidance_id=identifier,
                    reply="Guidance recorded for the next planning step, within the available survival skills.",
                )
            if kind != "stop":
                self.event(
                    "voice_reply",
                    {
                        "command_id": command["command_id"],
                        **result,
                        "provider": "local",
                        "pending": kind == "question"
                        and result["reply"].startswith("Holo4 is reviewing"),
                    },
                )
            self.receipts[command["command_id"]] = (command, result)
            self.utterances[utterance_key] = (command["text"], result)
            while len(self.receipts) > 512:
                self.receipts.popitem(last=False)
            while len(self.utterances) > 512:
                self.utterances.popitem(last=False)
            return dict(result)

    def active_run(self, run_id: str | None) -> bool:
        return (
            not self.closed
            and self.engine.state.get("run_id") == run_id
            and self.engine.recorder is not None
            and not self.engine.recorder.closed
        )

    async def _answer(self, command_id: str, run_id: str | None, text: str, evidence: dict) -> None:
        if not self.active_run(run_id):
            return
        request_id = str(uuid.uuid4())
        activity = {
            "provider": "holo",
            "request_id": request_id,
            "task": "Answer an operator question",
            "model": self.settings.holo_model,
            "input_source": "recorded_state",
            "decision_role": "voice_response",
        }
        self.answerer.journal = lambda kind, data: (
            self.engine.log(
                kind, {**data, "voice_command_id": command_id, "inference_request_id": request_id}
            )
            if self.active_run(run_id)
            else None
        )
        self.event("inference", {**activity, "phase": "thinking"})
        started = time.monotonic()
        provider = "holo"
        try:
            reply, latency, usage = await asyncio.wait_for(
                self.answerer.answer(text, evidence), timeout=8
            )
            if not self.active_run(run_id):
                return
            if self.engine.state.get("status") != evidence.get("status"):
                reply = f"From the recorded snapshot: {reply} Current run status: {self.engine.state.get('status')}."
            self.engine.timing("holo", latency, usage)
            self.event(
                "inference",
                {
                    **activity,
                    "phase": "complete",
                    "rationale": self.safe(reply),
                    "latency_ms": latency,
                },
            )
        except asyncio.CancelledError:
            if self.active_run(run_id):
                self.event(
                    "inference",
                    {
                        **activity,
                        "phase": "failed",
                        "reason": "Voice answer cancelled",
                        "cancelled": True,
                    },
                )
            raise
        except (TimeoutError, ProviderError):
            if not self.active_run(run_id):
                return
            provider = "local"
            latency = (time.monotonic() - started) * 1000
            self.event(
                "inference",
                {
                    **activity,
                    "phase": "failed",
                    "reason": "Voice answer unavailable; showing recorded objective",
                    "latency_ms": latency,
                },
            )
            reply = f"Holo4's answer is unavailable. Recorded status: {evidence.get('status')}; objective: {evidence.get('objective') or 'not set'}."
        self.event(
            "voice_reply",
            {
                "command_id": command_id,
                "intent": "question",
                "reply": self.safe(reply),
                "provider": provider,
                "latency_ms": latency,
                "pending": False,
            },
        )

    async def close(self) -> None:
        self.closed = True
        if self.question_task and not self.question_task.done():
            self.question_task.cancel()
            await asyncio.wait((self.question_task,), timeout=2)
        await self.answerer.aclose()
