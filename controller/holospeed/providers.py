"""Bounded, single-request adapters for Holo4 planning and Jev continuation.

Neither adapter retries: a retried gameplay decision could act on an old frame.
Only JSON is parsed; model responses never become executable code.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections.abc import Callable
from typing import Any, get_args

import httpx
from pydantic import ValidationError

from .config import Settings
from .models import Action, Decision, Key, ModelResult, Observation


class ProviderError(RuntimeError):
    """A failed call, including credential-redacted evidence for the run journal."""

    def __init__(
        self,
        message: str,
        *,
        request: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        latency_ms: float = 0,
        provider: str = "",
        model: str = "",
    ) -> None:
        super().__init__(message)
        self.request = request or {}
        self.response = response or {}
        self.latency_ms = latency_ms
        self.provider = provider
        self.model = model


class ReplanRequired(ProviderError):
    """Jev abstained; release controls and obtain a fresh Holo4 plan."""


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if key.lower()
            in {
                "authorization",
                "api_key",
                "apikey",
                "token",
                "password",
                "secret",
            }
            else _redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
    return value


class _Provider:
    name = ""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=settings.model_timeout_s)
        self.journal: Callable[[str, dict[str, Any]], Any] | None = None
        self._secrets = (
            settings.holo_api_key,
            settings.jev_api_key,
            settings.devin_api_key,
            settings.bridge_token,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(
        self,
        url: str,
        api_key: str,
        body: dict[str, Any],
        model: str,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], float]:
        request = _redact(body, self._secrets)
        if not api_key:
            raise ProviderError(
                f"{self.name} API key is not configured",
                request=request,
                provider=self.name,
                model=model,
            )
        started = time.perf_counter()
        call_id = str(uuid.uuid4())
        if self.journal:
            self.journal(
                "model_request",
                {
                    "call_id": call_id,
                    "provider": self.name,
                    "model": model,
                    "request": request,
                },
            )
        try:
            response = await self._client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {api_key}", **(headers or {})},
                timeout=self.settings.model_timeout_s,
            )
        except asyncio.CancelledError:
            if self.journal:
                self.journal(
                    "model_cancelled",
                    {
                        "call_id": call_id,
                        "provider": self.name,
                        "model": model,
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    },
                )
            raise
        except httpx.HTTPError as exc:
            if self.journal:
                self.journal(
                    "model_error",
                    {
                        "call_id": call_id,
                        "provider": self.name,
                        "model": model,
                        "error": type(exc).__name__,
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    },
                )
            # HTTP exception text may include request URLs or provider-supplied secrets.
            raise ProviderError(
                f"{self.name} request failed ({type(exc).__name__})",
                request=request,
                latency_ms=(time.perf_counter() - started) * 1000,
                provider=self.name,
                model=model,
            ) from None
        latency = (time.perf_counter() - started) * 1000
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            payload = {"body": response.text}
        if not isinstance(payload, dict):
            payload = {"body": payload}
        payload = _redact(payload, self._secrets)
        if self.journal:
            self.journal(
                "model_response",
                {
                    "call_id": call_id,
                    "provider": self.name,
                    "model": payload.get("model", model),
                    "response": payload,
                    "http_status": response.status_code,
                    "latency_ms": latency,
                },
            )
        if not response.is_success:
            raise ProviderError(
                f"{self.name} returned HTTP {response.status_code}",
                request=request,
                response={"http_status": response.status_code, **payload},
                latency_ms=latency,
                provider=self.name,
                model=model,
            )
        return request, payload, latency

    def _invalid(
        self,
        message: str,
        request: dict[str, Any],
        response: dict[str, Any],
        latency: float,
        model: str,
        *,
        replan: bool = False,
    ) -> ProviderError:
        error = ReplanRequired if replan else ProviderError
        return error(
            message,
            request=request,
            response=response,
            latency_ms=latency,
            provider=self.name,
            model=model,
        )


_HOLO_SYSTEM = """You plan Minecraft survival from screenshots and ordinary player controls
only: no hidden state, commands, or cheats. Follow context.run_goal; objective is a subgoal.
Progress wood -> planks/sticks/table -> wooden pickaxe -> stone tools. Inspect the next
frame after acting, change failed approaches, prioritize survival. Craft through visible GUI.
Distinguish leafy foliage from solid log trunks (brown oak or white birch). If leaves block
the crosshair, a short attack to clear leaves is valid progress before aiming at the trunk.
expected_effect describes ONLY the next bounded action: moving approaches a tree, it does
not collect a log. Do not promise an entire objective from one movement or inventory click.
When context.allow_exploration is true and progress is uncertain, prefer a safe probe of
at most 10 ticks over a 20-tick movement. A leaf block at the crosshair: attack for 10 ticks
to clear it before walking into the foliage. Small look probes stay within yaw 30/pitch 20.
Call one action tool. Keep strategy at most two short sentences; omit unused optional
fields. Claim a milestone only with visible evidence. answer stops for review only when the
entire run_goal is visibly complete, never when a subgoal finishes. No progress is inferred
from an action alone. Honor explicit no-input connectivity tests with keys=[],duration_ticks=1.
propose_decision holds keys simultaneously at 20 ticks/s, maximum 20 ticks, always.
Use mine_block for a 60–80-tick stationary attack hold (default 80), only after a reachable
block is visibly under the crosshair. Approach and aim in separate propose_decision calls.
Never combine movement with long mining. Bare-hand logs need 60 continuous attack ticks;
use mine_block for 70–80 ticks, since releasing attack resets breaking. Relative yaw degrees:
positive right; pitch positive down. Hotbar 1–9. GUI click_x/click_y are integers 0–1000,
top-left origin, valid only when screen_open. If uncertain, stop briefly with empty keys.
"""

_HOLO_COMPACT_SYSTEM = """Control Minecraft survival using only the screenshot and player inputs.
Follow run_goal; use recent results/failures to avoid ineffective repetition. First gather
logs, then craft planks/sticks/table, wooden pickaxe, and stone tools through visible GUI.
Return ONE native action call with controls only. Omit unused fields. Optional intent is
one short target phrase; intent is REQUIRED for mine_block and GUI interactions. No narrative
needed. Optional milestone+evidence requires a visible acquired item in the inventory/hotbar.
A standing trunk, dropped item, breaking particles, or planned action does NOT prove wood
collected. Omit milestone+evidence until acquisition is visible.
Distinguish leaves from solid log trunks. Leaves under the crosshair: attack for 10 ticks
to clear them before moving. Approach and aim separately. If exploration is enabled,
prefer probes <=10 ticks and small look changes when progress is uncertain.
propose_decision: simultaneous keys, 20 ticks/second, at most20 ticks. Positive yaw turns
right, positive pitch looks down. GUI click_x/y integers0–1000, only with screen_open.
Bare-hand logs need at least60 CONTINUOUS attack ticks. Separate 10–20-tick punches reset
breaking and cannot harvest a log. Once a reachable oak/birch log is under the crosshair,
call mine_block with target intent and default80 ticks; do not repeat short punches or
reselect an empty hotbar slot. mine_block is stationary attack only, duration60–80 ticks;
approach/aim first, never move while mining. After the block breaks, safely approach the
dropped item to pick it up, then verify the inventory/hotbar before claiming wood.
Keep controls safe. Never use cheats, commands, or hidden game state.
answer only when the entire run_goal is visibly complete; give visible evidence, not
an action-based assumption. Explicit no-input tests use empty keys and duration_ticks1.
"""

_FLAT_ACTION_FIELDS = {
    "keys",
    "duration_ticks",
    "yaw_delta",
    "pitch_delta",
    "hotbar",
    "scroll",
}
_FLAT_DECISION_FIELDS = {"objective", "strategy", "expected_effect", "milestone", "evidence"}
_FLAT_CLICK_FIELDS = {"click_x", "click_y", "click_button"}


def _holo_tool_schema() -> dict[str, Any]:
    """Flat native fields avoid nested serialization and unnecessary schema tokens."""
    return {
        "type": "object",
        "properties": {
            "objective": {"type": "string", "minLength": 1, "maxLength": 300},
            "strategy": {"type": "string", "minLength": 1, "maxLength": 1500},
            "expected_effect": {"type": "string", "minLength": 1, "maxLength": 500},
            "keys": {"type": "array", "items": {"type": "string", "enum": list(get_args(Key))}},
            "duration_ticks": {"type": "integer", "minimum": 1, "maximum": 20},
            "yaw_delta": {"type": "number", "minimum": -90, "maximum": 90},
            "pitch_delta": {"type": "number", "minimum": -60, "maximum": 60},
            "hotbar": {"type": ["integer", "null"], "minimum": 1, "maximum": 9},
            "click_x": {"type": "integer", "minimum": 0, "maximum": 1000},
            "click_y": {"type": "integer", "minimum": 0, "maximum": 1000},
            "click_button": {"type": "string", "enum": ["left", "right", "middle"]},
            "scroll": {"type": "integer", "minimum": -9, "maximum": 9},
            "milestone": {
                "type": ["string", "null"],
                "enum": [
                    "wood",
                    "crafting_table",
                    "wooden_pickaxe",
                    "stone_tools",
                    None,
                ],
            },
            "evidence": {"type": "string", "maxLength": 1000},
        },
        "required": ["objective", "strategy", "expected_effect", "keys", "duration_ticks"],
        "additionalProperties": False,
    }


def _holo_mining_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            **{
                key: value
                for key, value in _holo_tool_schema()["properties"].items()
                if key in _FLAT_DECISION_FIELDS
            },
            "duration_ticks": {"type": "integer", "minimum": 60, "maximum": 80},
        },
        "required": ["objective", "strategy", "expected_effect"],
        "additionalProperties": False,
    }


def _compact_holo_schema(*, mining: bool = False) -> dict[str, Any]:
    schema = _holo_mining_schema() if mining else _holo_tool_schema()
    schema["properties"] = {
        key: value
        for key, value in schema["properties"].items()
        if key not in {"objective", "strategy", "expected_effect"}
    }
    schema["properties"]["intent"] = {"type": "string", "minLength": 1, "maxLength": 120}
    schema["required"] = ["intent"] if mining else ["keys", "duration_ticks"]
    return schema


def _control_description(action: Action) -> str:
    parts = [f"hold {'+'.join(action.keys) or 'no keys'} for {action.duration_ticks} ticks"]
    if action.yaw_delta or action.pitch_delta:
        parts.append(f"look yaw {action.yaw_delta:g}°, pitch {action.pitch_delta:g}°")
    if action.cursor:
        parts.append(
            f"{action.cursor.button} click at ({action.cursor.x:.3f}, {action.cursor.y:.3f})"
        )
    if action.hotbar is not None:
        parts.append(f"select hotbar {action.hotbar}")
    if action.scroll:
        parts.append(f"scroll {action.scroll}")
    return "; ".join(parts)


def _fill_compact_metadata(
    arguments: dict[str, Any],
    context: dict[str, Any],
    intent: str | None,
    *,
    mining: bool = False,
    screen_open: bool = False,
) -> dict[str, Any]:
    action = Action.model_validate(arguments["action"], strict=True)
    needs_target = mining or (
        screen_open
        and bool(action.keys or action.cursor or action.hotbar is not None or action.scroll)
    )
    legacy_explicit_plan = all(
        isinstance(arguments.get(field), str) and arguments[field].strip()
        for field in ("objective", "strategy", "expected_effect")
    )
    if needs_target and not intent and not legacy_explicit_plan:
        raise ValueError("Compact mining and GUI interactions require a short target intent")
    controls = _control_description(action)
    objective = context.get("objective") or context.get("run_goal")
    if not isinstance(objective, str) or not objective.strip():
        objective = "Take the next bounded step"
    # These labels describe requested inputs, never a visual observation or achieved result.
    arguments.setdefault("objective", objective[:300])
    strategy = f"Control-only plan: {controls}. Verify the outcome in the next frame."
    if intent:
        strategy = f"Model intent: {intent}. {strategy}"
    arguments.setdefault("strategy", strategy)
    arguments.setdefault("expected_effect", f"Attempt inputs: {controls}. Outcome is unverified.")
    return arguments


def _mining_decision(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise TypeError("Mining arguments must be an object")
    if set(arguments) - (_FLAT_DECISION_FIELDS | {"duration_ticks"}):
        raise ValueError("mine_block cannot include movement, look, GUI, or extra control fields")
    duration = arguments.get("duration_ticks", 80)
    if type(duration) is not int or not 60 <= duration <= 80:
        raise ValueError("mine_block duration_ticks must be an integer from 60 to 80")
    return {
        **{key: value for key, value in arguments.items() if key in _FLAT_DECISION_FIELDS},
        "action": {"keys": ["attack"], "duration_ticks": duration},
    }


def _nested_decision(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise TypeError("Decision arguments must be an object")
    if "action" in arguments:
        # Preserve historical nested responses and the hosted JSON-string variant.
        if isinstance(arguments["action"], str):
            arguments["action"] = json.loads(arguments["action"])
        if isinstance(arguments["action"].get("cursor"), str):
            arguments["action"]["cursor"] = json.loads(arguments["action"]["cursor"])
        return arguments
    if set(arguments) - (_FLAT_ACTION_FIELDS | _FLAT_DECISION_FIELDS | _FLAT_CLICK_FIELDS):
        raise ValueError("Unknown flat tool argument")
    if not {"keys", "duration_ticks"} <= arguments.keys():
        raise ValueError("Missing flat action fields")
    if type(arguments["duration_ticks"]) is not int or not 1 <= arguments["duration_ticks"] <= 20:
        raise ValueError(
            "propose_decision duration_ticks must be an integer from 1 to 20; "
            "use mine_block for stationary mining"
        )
    action = {key: value for key, value in arguments.items() if key in _FLAT_ACTION_FIELDS}
    if arguments.keys() & _FLAT_CLICK_FIELDS:
        if not {"click_x", "click_y"} <= arguments.keys():
            raise ValueError("A GUI click needs both coordinates")
        action["cursor"] = {
            "x": arguments["click_x"],
            "y": arguments["click_y"],
            "button": arguments.get("click_button", "left"),
        }
    return {
        **{key: value for key, value in arguments.items() if key in _FLAT_DECISION_FIELDS},
        "action": action,
    }


def _decision_error_reason(error: Exception) -> str:
    if isinstance(error, ValidationError):
        parts = []
        for item in error.errors(include_input=False, include_context=False, include_url=False)[:3]:
            location = ".".join(str(part) for part in item["loc"])[:100]
            parts.append(f"{location}: {item['msg'][:180]}")
        return "; ".join(parts)
    if isinstance(error, json.JSONDecodeError):
        return "tool arguments must contain valid JSON"
    if type(error) is ValueError and str(error) in {
        "mine_block cannot include movement, look, GUI, or extra control fields",
        "mine_block duration_ticks must be an integer from 60 to 80",
        "propose_decision duration_ticks must be an integer from 1 to 20; use mine_block for stationary mining",
        "mine_block requires the gameplay view, not an open GUI",
        "Unknown flat tool argument",
        "Missing flat action fields",
        "A GUI click needs both coordinates",
        "GUI click outside a GUI",
        "Invalid normalized cursor",
        "Milestone without evidence",
        "Incomplete or multiple responses",
        "Expected one tool call",
        "Invalid answer",
        "Completion requires visible evidence",
        "Unknown tool",
        "Compact intent must be a nonempty string of at most 120 characters",
        "Compact mining and GUI interactions require a short target intent",
    }:
        return str(error)
    return "tool call has missing fields or incorrect JSON types"


class HoloProvider(_Provider):
    name = "holo"

    async def decide(self, observation: Observation, context: dict[str, Any]) -> ModelResult:
        compact = getattr(self.settings, "holo_compact", False)
        body = {
            "model": self.settings.holo_model,
            "reasoning_effort": self.settings.holo_reasoning_effort,
            "temperature": 0.8,
            "max_tokens": 256 if compact and self.settings.holo_reasoning_effort == "none" else 700,
            "messages": [
                {"role": "system", "content": _HOLO_COMPACT_SYSTEM if compact else _HOLO_SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "context": {
                                **context,
                                "allow_exploration": self.settings.allow_exploration,
                            },
                            "frame_id": observation.frame_id,
                            "screen_open": observation.screen_open,
                            "image_width": observation.width,
                            "image_height": observation.height,
                        },
                        ensure_ascii=False,
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "<observation>\n"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{observation.mime_type};base64,{observation.image_base64}",
                            },
                        },
                        {"type": "text", "text": "\n</observation>"},
                    ],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "propose_decision",
                        "description": "Execute one bounded player input; movement or short attack at most20 ticks.",
                        "parameters": _compact_holo_schema() if compact else _holo_tool_schema(),
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "mine_block",
                        "description": "Hold attack on an already-aimed reachable block for 60–80 "
                        "ticks, stationary. Approach and aim separately. Duration defaults to 80.",
                        "parameters": _compact_holo_schema(mining=True)
                        if compact
                        else _holo_mining_schema(),
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "answer",
                        "description": "Stop when the entire objective is complete.",
                        "parameters": {
                            "type": "object",
                            "properties": {"content": {"type": "string"}},
                            "required": ["content"],
                            "additionalProperties": False,
                        },
                    },
                },
            ],
            "tool_choice": "required",
        }
        request, response, latency = await self._post(
            self.settings.holo_base_url.rstrip("/") + "/chat/completions",
            self.settings.holo_api_key,
            body,
            self.settings.holo_model,
        )
        completed = False
        try:
            choices = response["choices"]
            if len(choices) != 1 or choices[0].get("finish_reason") == "length":
                raise ValueError("Incomplete or multiple responses")
            calls = choices[0]["message"]["tool_calls"]
            if len(calls) != 1:
                raise ValueError("Expected one tool call")
            call = calls[0]["function"]
            arguments = json.loads(call["arguments"])
            if call["name"] == "answer":
                # Completion remains a claim for operator review, never a fabricated milestone.
                if set(arguments) != {"content"} or not isinstance(arguments["content"], str):
                    raise ValueError("Invalid answer")
                if not arguments["content"].strip():
                    raise ValueError("Completion requires visible evidence")
                completed = True
                decision = Decision(
                    objective=str(context.get("run_goal") or "Review stone-tools completion"),
                    strategy="Objective appears complete; hold still for operator review.",
                    expected_effect="No controls held",
                    action=Action(duration_ticks=1),
                    evidence=arguments["content"][:1000],
                )
            elif call["name"] in {"propose_decision", "mine_block"}:
                intent = None
                if compact and isinstance(arguments, dict) and "intent" in arguments:
                    intent = arguments.pop("intent")
                    if not isinstance(intent, str) or not intent.strip() or len(intent) > 120:
                        raise ValueError(
                            "Compact intent must be a nonempty string of at most 120 characters"
                        )
                if call["name"] == "mine_block":
                    if observation.screen_open:
                        raise ValueError("mine_block requires the gameplay view, not an open GUI")
                    arguments = _mining_decision(arguments)
                else:
                    arguments = _nested_decision(arguments)
                cursor = arguments.get("action", {}).get("cursor")
                if cursor is not None:
                    if not observation.screen_open:
                        raise ValueError("GUI click outside a GUI")
                    for axis in ("x", "y"):
                        coordinate = cursor[axis]
                        if type(coordinate) is not int or not 0 <= coordinate <= 1000:
                            raise ValueError("Invalid normalized cursor")
                        cursor[axis] = coordinate / 1000
                if compact:
                    arguments = _fill_compact_metadata(
                        arguments,
                        context,
                        intent,
                        mining=call["name"] == "mine_block",
                        screen_open=observation.screen_open,
                    )
                decision = Decision.model_validate(arguments, strict=True)
                if decision.milestone and not decision.evidence.strip():
                    raise ValueError("Milestone without evidence")
            else:
                raise ValueError("Unknown tool")
            usage = response.get("usage", {})
            model = response.get("model", self.settings.holo_model)
            if not isinstance(usage, dict) or not isinstance(model, str):
                raise TypeError("Invalid response metadata")
        except (
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            ValidationError,
        ) as error:
            reason = _redact(_decision_error_reason(error), self._secrets)
            raise self._invalid(
                f"Holo4 returned an invalid or unsafe decision: {reason}",
                request,
                response,
                latency,
                self.settings.holo_model,
            ) from None
        return ModelResult(
            decision=decision,
            request=request,
            response=response,
            latency_ms=latency,
            usage=usage,
            model=model,
            completed=completed,
        )


def _probability(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Expected a finite probability")
    return float(value)


class JevProvider(_Provider):
    name = "jev"

    async def decide(self, observation: Observation, context: dict[str, Any]) -> ModelResult:
        try:
            prior = Decision.model_validate(context["previous_decision"], strict=True)
        except (KeyError, TypeError, ValueError, ValidationError):
            raise ReplanRequired(
                "Jev needs a valid prior Holo4 plan",
                provider=self.name,
                model=self.settings.jev_model,
            ) from None
        action = prior.action
        validate_proposal = context.get("validate_unexecuted_plan") is True
        exploration_permitted = (
            self.settings.allow_exploration
            and not observation.screen_open
            and action.duration_ticks <= 10
            and action.cursor is None
            and action.hotbar is None
            and action.scroll == 0
            and set(action.keys) <= {"forward", "back", "left", "right", "attack"}
            and abs(action.yaw_delta) <= 30
            and abs(action.pitch_delta) <= 20
        )
        if not validate_proposal and (
            observation.screen_open
            or action.cursor is not None
            or action.scroll
            or action.hotbar is not None
            or set(action.keys) & {"inventory", "drop", "swap", "escape"}
        ):
            raise ReplanRequired(
                "One-shot or menu actions require fresh Holo4 planning",
                provider=self.name,
                model=self.settings.jev_model,
            )
        if validate_proposal and (
            (action.cursor is not None and not observation.screen_open)
            or (observation.screen_open and (action.yaw_delta or action.pitch_delta))
        ):
            raise ReplanRequired(
                "The proposed action does not match the current gameplay or GUI screen",
                provider=self.name,
                model=self.settings.jev_model,
            )
        # Validate one exact action. Offering overlapping safe alternatives split
        # probability mass and caused false vetoes on recorded gameplay frames.
        # Keep the same acceptance, danger, and continuation thresholds.
        candidates = {"continue": action}
        criteria = {"continue": "Repeat the previous bounded action, target still valid."}
        if action.duration_ticks > 20:
            criteria["continue"] = (
                "Repeat the complete stationary mining hold on the valid target. "
                "Do not shorten it: releasing attack early resets block breaking."
            )
        criteria["replan"] = "Stop and obtain a fresh plan: unsafe, stuck, complete, or uncertain."
        action_question = (
            "Which candidate safely continues the existing strategy on this screenshot? "
            "Judge safe intermediate progress from this bounded action, not completion of the "
            "entire objective. Approaching a target or clearing obstructing leaves can be valid. "
            "Choose replan if its target or assumptions no longer hold."
        )
        continuation_question = (
            "Is executing this exact supplied action appropriate for the current visible "
            "situation, without repeating an ineffective action? Safe intermediate progress "
            "is sufficient; the entire objective need not finish in one action."
        )
        if validate_proposal:
            criteria["continue"] = (
                "Execute the exact proposed Holo action ONCE, unchanged, because its target "
                "and expected effect are visibly valid on the current screenshot."
            )
            action_question = (
                "Validate the exact Holo proposal, which has NOT been executed, against the "
                "CURRENT fresh screenshot. Continue authorizes this action once, unchanged. "
                "If its target is uncertain, visible evidence is insufficient, assumptions "
                "no longer hold, or a different action is needed, choose replan. GUI cursor "
                "x/y are fractions 0–1 of the image; duration is in 20Hz ticks; hotbar is 1–9. "
                "Do not infer validity from the old plan's claims. "
                "Judge the immediate bounded step, not completion of the entire objective; "
                "approaching a target or clearing leaves can be valid intermediate progress."
            )
            continuation_question = (
                "Does the CURRENT screenshot clearly support executing the exact proposed "
                "action once, with the intended target and expected effect still valid? "
                "Safe intermediate progress is sufficient, not completion of the entire objective. "
                "Answer no when the target is ambiguous or the image lacks sufficient evidence."
            )
        if exploration_permitted:
            criteria["continue"] = (
                "Execute the exact bounded experiment once: safely test an approach, clear "
                "a leaf obstruction, or inspect with a small look, even if progress is uncertain."
            )
            criteria["replan"] = (
                "Reject concrete danger, an invalid action target, or repeating the same "
                "experiment after recorded failure; obtain another plan."
            )
            action_question = (
                "Judge this permitted world action as a safe bounded experiment of at most "
                "10 ticks, not a promise of progress. A cautious approach or short attack to "
                "clear leaves can be valid even if the outcome is uncertain. Continue permits "
                "the exact proposed action once; replan for concrete danger, an invalid target, "
                "or repetition of an executed action with visible failure evidence. A rejected "
                "unexecuted proposal is not evidence of physical failure. Do not demand certainty "
                "that this experiment will complete a subgoal."
            )
            continuation_question = (
                "Is this short world action an appropriate safe bounded experiment to test "
                "an approach, clear obstructing leaves, or inspect the scene? Uncertainty "
                "about progress alone is not a reason to reject. Reject concrete danger, "
                "an invalid action target, or repetition of an executed action with visible failure "
                "evidence. A prior veto alone does not establish that an action failed in the world."
            )
        reference = str(uuid.uuid4())
        body = {
            "model": self.settings.jev_model,
            "state": {
                "screenshot": reference,
                "run_goal": context.get("run_goal", "Obtain stone tools"),
                "evaluation_mode": "unexecuted_plan" if validate_proposal else "continuation",
                "exploration_permitted": exploration_permitted,
                "current_frame": {
                    "frame_id": observation.frame_id,
                    "tick": observation.tick,
                    "width": observation.width,
                    "height": observation.height,
                },
                "plan_source_frame_id": context.get("plan_source_frame_id"),
                "objective": prior.objective,
                "strategy": prior.strategy,
                "expected_effect": prior.expected_effect,
                "recent_actions": context.get("recent_actions", []),
                "failures": context.get("failures", []),
                "candidates": {key: item.model_dump() for key, item in candidates.items()},
                "screen_open": observation.screen_open,
            },
            "assets": {
                reference: {
                    "type": "image",
                    "format": "jpg" if observation.mime_type == "image/jpeg" else "png",
                    "data_base64": observation.image_base64,
                }
            },
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": action_question,
                    "criteria": criteria,
                },
                "danger": {
                    "type": "noul",
                    "instructions": "Does the screenshot show immediate danger from lava, falling, "
                    "drowning, fire, death, or a nearby attacking enemy?",
                },
                "can_continue": {
                    "type": "noul",
                    "instructions": continuation_question,
                },
                "blocker": {
                    "type": "choice",
                    "instructions": "If the proposed bounded action cannot safely make the next "
                    "intermediate step, what visible factor best explains why? Do not require "
                    "the whole objective to finish in one action. Choose none if this step is "
                    "appropriate, uncertain if the screenshot lacks sufficient evidence.",
                    "criteria": {
                        "obstruction": "An obstacle blocks the immediate action and must be cleared or avoided.",
                        "target_mismatch": "The crosshair or GUI target differs from the intended target.",
                        "danger": "Immediate visible danger makes this action inappropriate.",
                        "goal_done": "The current subgoal is already complete; plan the next step.",
                        "uncertain": "Insufficient visual evidence to judge the proposed action.",
                        "none": "No visible blocker; this intermediate step is appropriate.",
                    },
                },
            },
        }
        request, response, latency = await self._post(
            self.settings.jev_base_url.rstrip("/") + "/v1/systemone",
            self.settings.jev_api_key,
            body,
            self.settings.jev_model,
            {"TypeSafe-Danger-Zone-Images": "true"},
        )
        try:
            answers = response["answers"]
            choice = answers["action"]
            danger = answers["danger"]
            can_continue = answers["can_continue"]
            if (
                choice["type"] != "choice"
                or danger["type"] != "noul"
                or can_continue["type"] != "noul"
            ):
                raise ValueError("Wrong answer type")
            selected = choice["choice"]
            probabilities = choice["probabilities"]
            if set(probabilities) != set(criteria):
                raise ValueError("Wrong option set")
            probability_values = {key: _probability(value) for key, value in probabilities.items()}
            if not math.isclose(sum(probability_values.values()), 1, abs_tol=0.01):
                raise ValueError("Invalid probability distribution")
            confidence = _probability(choice["confidence"])
            hazard = _probability(danger["noul"])
            continuation = _probability(can_continue["noul"])
            if selected not in probability_values:
                raise ValueError("Unknown choice")
            usage = response.get("usage", {})
            model = response.get("model", self.settings.jev_model)
            if not isinstance(usage, dict) or not isinstance(model, str):
                raise TypeError("Invalid response metadata")
        except (AttributeError, KeyError, TypeError, ValueError):
            raise self._invalid(
                "Jev returned an invalid decision; replan required",
                request,
                response,
                latency,
                self.settings.jev_model,
                replan=True,
            ) from None
        if (
            selected == "replan"
            or probability_values[selected] < 0.65
            or hazard >= 0.35
            or continuation < 0.70
        ):
            blocker = answers.get("blocker", {})
            blocker_reasons = {
                "obstruction": "A visible obstruction blocks the next step; clear or navigate around it.",
                "target_mismatch": "The crosshair or GUI target does not match the intended target; re-aim.",
                "danger": "Immediate visible danger requires a safer response.",
                "goal_done": "The current subgoal appears complete; plan the next step.",
                "uncertain": "Visual evidence is insufficient; obtain a clearer view or plan a smaller step.",
            }
            blocker_choice = blocker.get("choice") if isinstance(blocker, dict) else None
            detail = (
                blocker_reasons.get(blocker_choice, "") if isinstance(blocker_choice, str) else ""
            )
            if not isinstance(blocker, dict) or blocker.get("type") != "choice":
                detail = ""
            raise self._invalid(
                "Jev abstained because continuation is uncertain or unsafe"
                + (f": {detail}" if detail else ""),
                request,
                response,
                latency,
                model,
                replan=True,
            )
        decision = Decision(
            objective=prior.objective,
            strategy=prior.strategy,
            expected_effect=prior.expected_effect,
            action=candidates[selected],
            milestone=None,
            evidence=f"Jev {'validated an unexecuted proposal once' if validate_proposal else 'selected ' + selected}; "
            f"selected probability {probability_values[selected]:.2f}, confidence {confidence:.2f}, "
            f"hazard probability {hazard:.2f}. Milestones require Holo4 verification.",
        )
        return ModelResult(
            decision=decision,
            request=request,
            response=response,
            latency_ms=latency,
            usage=usage,
            model=model,
        )
