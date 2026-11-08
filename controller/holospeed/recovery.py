"""Bounded planning advice from control history, without inferred world state.

This module cannot approve or execute an action. Its output helps the planner
distinguish rejected proposals, completed inputs, and still-unverified effects.
The normal Jev validation, action limits, and deadline remain authoritative.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .models import Action

HISTORY_LIMIT = 8
MOVEMENT_KEYS = {"forward", "back", "left", "right", "jump", "sneak", "sprint"}
PROPOSAL_FEEDBACK_KINDS = {None, "proposal_rejected", "provider_uncertainty"}


def _records(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    # Preserve an invalid entry as a break in the history instead of silently
    # joining two otherwise nonconsecutive rejection records around it.
    return [record if isinstance(record, Mapping) else {} for record in value[-HISTORY_LIMIT:]]


def _proposal_feedback(record: Mapping[str, Any]) -> bool:
    kind = record.get("feedback_kind")
    return kind is None or isinstance(kind, str) and kind in PROPOSAL_FEEDBACK_KINDS


def _action(value: Any) -> Action | None:
    try:
        return Action.model_validate(value)
    except (ValueError, TypeError):
        return None


def _family(action: Action) -> str:
    if (
        action.cursor
        or action.hotbar
        or action.scroll
        or set(action.keys) & {"inventory", "escape", "drop", "swap", "use"}
    ):
        return "interaction"
    if set(action.keys) & MOVEMENT_KEYS:
        return "movement"
    if "attack" in action.keys:
        return "attack"
    if action.yaw_delta or action.pitch_delta:
        return "camera"
    return "idle"


def _signature(action: Action) -> str:
    # Rewording a strategy, reordering simultaneous keys, or changing only the
    # hold duration does not constitute a new approach to the same obstruction.
    data = action.model_dump(exclude={"duration_ticks"})
    data["keys"] = sorted(data["keys"])
    return json.dumps(data, sort_keys=True)


def _visible_checks(family: str, screen_open: bool) -> list[str]:
    if screen_open:
        return [
            "Identify the current GUI and the visible item or slot before acting.",
            "Verify the resulting slot contents; an input ACK does not prove crafting succeeded.",
            "Do not use world-camera recovery while a GUI is open.",
        ]
    if family == "attack":
        return [
            "Identify the block under the crosshair and whether it is visibly within reach.",
            "Look for mining cracks, a removed block, or a dropped item; hand animation alone is insufficient.",
            "A short punch may not finish a log. Once aim and range are verified, a stationary mining hold may be needed within the existing action limits.",
            "Claim wood only after a log item is visible in the hotbar or inventory.",
        ]
    if family == "movement":
        return [
            "Compare nearby block edges before and after the completed movement.",
            "If the view did not shift, inspect the path, elevation, and crosshair target before another step.",
            "A completed movement ACK proves input execution, not displacement or a clear route.",
        ]
    if family == "camera":
        return [
            "Interpret the new view before selecting a movement or mining action.",
            "A changed camera view is information, not resource or navigation progress.",
            "Use nonzero yaw or pitch for a requested turn; empty keys with zero rotation is a no-op.",
        ]
    if family == "idle":
        return [
            "A no-op cannot turn the camera, move, or mine; choose an action consistent with the expected effect.",
            "Re-identify the current target from the screenshot instead of assuming the previous subgoal succeeded.",
        ]
    return [
        "State one effect that the next screenshot can confirm or refute.",
        "Verify visible results before updating resource or milestone claims.",
    ]


def build_recovery_context(
    recent_actions: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
    *,
    screen_open: bool = False,
    allow_exploration: bool = True,
) -> dict[str, Any]:
    """Summarize at most eight current-run records for advisory prompt context.

    Failures may describe a rejected continuation of an already completed
    action. They never establish that the earlier action failed physically.
    Missing ACKs and unknown action identities are kept out of success/repeat
    counts. No coordinates, inventory telemetry, or old-run facts are consumed.
    """
    completed: list[tuple[Action, Mapping[str, Any]]] = []
    for record in _records(recent_actions):
        ack = record.get("ack")
        action = _action(record.get("action"))
        if action is not None and isinstance(ack, Mapping) and ack.get("status") == "completed":
            completed.append((action, record))

    failure_tail = _records(failures)
    latest = _action(failure_tail[-1].get("previous_action")) if failure_tail else None
    repeated = 0
    if latest is not None and _proposal_feedback(failure_tail[-1]):
        signature = _signature(latest)
        for record in reversed(failure_tail):
            action = _action(record.get("previous_action"))
            if not _proposal_feedback(record) or action is None or _signature(action) != signature:
                break
            repeated += 1

    last_completed = completed[-1][0] if completed else None
    focus = latest or last_completed
    family = _family(focus) if focus is not None else "unknown"
    repeated_proposal = repeated >= 2
    advice = [
        "Use the current screenshot as the source of world facts; history is feedback, not a scene description.",
        "Do not infer a successful effect from an ACK or a failed physical effect from a rejected proposal.",
    ]
    if repeated_proposal:
        advice.append(
            "The same input proposal was rejected repeatedly. Re-identify the target and obstacle; changing wording or hold duration alone is not a new approach. Retry it only if the fresh view supplies a concrete new justification."
        )
    if allow_exploration and not screen_open and repeated_proposal:
        advice.append(
            "Consider one bounded view inspection to resolve uncertainty before movement or mining. Any camera or exploration proposal still requires the normal fresh-frame validation and remaining recovery budget."
        )
    if not allow_exploration:
        advice.append(
            "Exploration is disabled. Do not introduce an exploratory probe; replan from supported visual evidence or pause."
        )
    if last_completed is not None and _family(last_completed) == "camera":
        advice.append(
            "The latest completed input requested a view change. Reassess the new screenshot; previous obstacle assumptions may no longer apply."
        )

    return {
        "source": "current_run_action_and_rejection_history",
        "advisory_only": True,
        "completed_inputs_in_window": len(completed),
        "latest_completed_frame_id": completed[-1][1].get("frame_id") if completed else None,
        "latest_feedback_action_family": family,
        "latest_feedback_kind": failure_tail[-1].get("feedback_kind", "legacy_unspecified")
        if failure_tail
        else None,
        "consecutive_same_proposal_rejections": repeated,
        "repeated_proposal": repeated_proposal,
        "rejected_action": latest.model_dump() if latest is not None else None,
        "visible_checks": _visible_checks(family, screen_open),
        "guidance": advice,
        "world_state_claims": [],
    }


def shape_recovery_context(
    context: Mapping[str, Any],
    *,
    screen_open: bool = False,
    allow_exploration: bool = True,
) -> dict[str, Any]:
    """Return a new planner context, resetting a repeatedly blocked subgoal.

    Call before a Holo replan. Do not apply to an unexecuted proposal currently
    awaiting Jev validation: that proposal must retain its exact meaning.
    """
    shaped = dict(context)
    recovery = build_recovery_context(
        context.get("recent_actions", []),
        context.get("failures", []),
        screen_open=screen_open,
        allow_exploration=allow_exploration,
    )
    shaped["recovery_guidance"] = recovery
    if (
        recovery["repeated_proposal"]
        and context.get("previous_decision") is None
        and not context.get("validate_unexecuted_plan")
        and isinstance(context.get("run_goal"), str)
        and context["run_goal"].strip()
    ):
        shaped["previous_subgoal_not_world_fact"] = context.get("objective")
        shaped["objective"] = context["run_goal"]
        shaped["strategy"] = (
            "Reassess the current screenshot and choose the next visually supported subgoal. "
            "Do not assume the previous target, position, or expected effect is still valid."
        )
    return shaped
