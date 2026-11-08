"""Planning feedback regressions motivated by the five recorded live trials."""

from copy import deepcopy

import pytest
from holospeed.models import Action
from holospeed.recovery import build_recovery_context, shape_recovery_context


def rejected(action):
    return {
        "reason": "Jev abstained: a visible obstruction blocks the next step",
        "objective": "Walk toward the birch trunk",
        "previous_action": action.model_dump() if action is not None else None,
        "expected_effect": "The player moves closer to the trunk",
    }


def completed(action, frame_id="fresh-frame"):
    return {
        "action": action.model_dump(),
        "frame_id": frame_id,
        "ack": {"status": "completed"},
    }


def test_repeated_forward_proposals_reset_stale_subgoal_without_changing_run_goal():
    context = {
        "run_goal": "Obtain wood, then stone tools",
        "objective": "Keep walking toward the birch trunk",
        "strategy": "Walk forward again",
        "previous_decision": None,
        "failures": [
            rejected(Action(keys=["forward", "left"], duration_ticks=6)),
            rejected(Action(keys=["left", "forward"], duration_ticks=10)),
        ],
        "recent_actions": [],
    }
    original = deepcopy(context)
    shaped = shape_recovery_context(context)
    assert context == original
    assert shaped["objective"] == context["run_goal"]
    assert shaped["previous_subgoal_not_world_fact"] == context["objective"]
    advice = shaped["recovery_guidance"]
    assert advice["consecutive_same_proposal_rejections"] == 2
    assert advice["completed_inputs_in_window"] == 0
    assert advice["world_state_claims"] == []
    assert any("fresh view" in line for line in advice["guidance"])


def test_missing_rejected_action_identity_does_not_invent_a_repeated_attempt():
    # The second live trial lost all rejected proposal identities. Its failure
    # count cannot establish which movement was attempted, much less executed.
    advice = build_recovery_context([], [rejected(None)] * 7)
    assert advice["consecutive_same_proposal_rejections"] == 0
    assert not advice["repeated_proposal"]
    assert advice["rejected_action"] is None
    assert advice["completed_inputs_in_window"] == 0


def test_rejection_of_continuation_does_not_erase_a_completed_input():
    action = Action(keys=["forward"], duration_ticks=10)
    advice = build_recovery_context([completed(action)], [rejected(action)])
    assert advice["completed_inputs_in_window"] == 1
    assert advice["consecutive_same_proposal_rejections"] == 1
    assert not advice["repeated_proposal"]
    assert advice["world_state_claims"] == []
    assert any("not displacement" in line for line in advice["visible_checks"])


def test_only_completed_acknowledgments_count_as_executed_inputs():
    action = Action(keys=["attack"], duration_ticks=10).model_dump()
    records = [
        {"action": action},
        {"action": action, "ack": {"status": "rejected"}},
        {"action": action, "ack": {"status": "cancelled"}},
        {"action": action, "ack": {"status": "completed"}, "frame_id": "verified-wire"},
    ]
    advice = build_recovery_context(records, [])
    assert advice["completed_inputs_in_window"] == 1
    assert advice["latest_completed_frame_id"] == "verified-wire"


def test_stationary_mining_history_requests_visual_checks_without_claiming_a_stall():
    attack = Action(keys=["attack"], duration_ticks=80)
    advice = build_recovery_context([completed(attack)] * 8, [])
    assert advice["latest_feedback_action_family"] == "attack"
    assert not advice["repeated_proposal"]
    assert advice["world_state_claims"] == []
    assert any("mining cracks" in line for line in advice["visible_checks"])
    assert any("hotbar or inventory" in line for line in advice["visible_checks"])
    assert not any("rejected repeatedly" in line for line in advice["guidance"])


def test_no_op_cannot_be_used_as_evidence_for_a_claimed_camera_turn():
    noop = Action(keys=[], duration_ticks=1, yaw_delta=0, pitch_delta=0)
    advice = build_recovery_context([], [rejected(noop), rejected(noop)])
    assert advice["latest_feedback_action_family"] == "idle"
    assert any("no-op cannot turn" in line for line in advice["visible_checks"])


def test_camera_recovery_reopens_visual_assumptions_without_claiming_progress():
    forward = Action(keys=["forward"], duration_ticks=10)
    scan = Action(keys=[], duration_ticks=1, yaw_delta=25, pitch_delta=15)
    advice = build_recovery_context(
        [completed(scan, "post-rejection-scan")], [rejected(forward), rejected(forward)]
    )
    assert any("previous obstacle assumptions" in line for line in advice["guidance"])
    assert advice["latest_completed_frame_id"] == "post-rejection-scan"
    assert advice["world_state_claims"] == []


@pytest.mark.parametrize(
    ("screen_open", "allow_exploration"), [(True, True), (False, False), (True, False)]
)
def test_gui_and_disabled_exploration_never_suggest_a_world_camera_probe(
    screen_open, allow_exploration
):
    failures = [rejected(Action(keys=["forward"], duration_ticks=10))] * 2
    advice = build_recovery_context(
        [], failures, screen_open=screen_open, allow_exploration=allow_exploration
    )
    assert not any("Consider one bounded view" in line for line in advice["guidance"])
    if screen_open:
        assert any("current GUI" in line for line in advice["visible_checks"])
    if not allow_exploration:
        assert any("Exploration is disabled" in line for line in advice["guidance"])


@pytest.mark.parametrize("pending_validation", [False, True])
def test_active_or_pending_proposal_keeps_its_exact_objective(pending_validation):
    action = Action(keys=["forward"], duration_ticks=10)
    context = {
        "run_goal": "Obtain wood",
        "objective": "Inspect the current trunk",
        "strategy": "Use the current proposal",
        "previous_decision": None if pending_validation else {"action": action.model_dump()},
        "validate_unexecuted_plan": pending_validation,
        "failures": [rejected(action)] * 2,
    }
    shaped = shape_recovery_context(context)
    assert shaped["objective"] == context["objective"]
    assert shaped["strategy"] == context["strategy"]
    assert shaped["previous_decision"] == context["previous_decision"]


def test_changed_direction_breaks_rejection_streak_and_history_remains_bounded():
    forward = rejected(Action(keys=["forward"], duration_ticks=10))
    left = rejected(Action(keys=["left"], duration_ticks=10))
    advice = build_recovery_context([], [forward] * 30 + [left])
    assert advice["consecutive_same_proposal_rejections"] == 1
    assert not advice["repeated_proposal"]
    advice = build_recovery_context([], [forward] * 30)
    assert advice["consecutive_same_proposal_rejections"] == 8


def test_invalid_or_hidden_state_fields_do_not_become_world_facts():
    invalid = {"keys": ["forward"], "duration_ticks": 80}
    advice = build_recovery_context(
        [{"action": invalid, "ack": {"status": "completed"}, "inventory": {"wood": 64}}],
        [{"previous_action": invalid, "position": [100, 64, 200]}] * 2,
    )
    assert advice["completed_inputs_in_window"] == 0
    assert advice["world_state_claims"] == []
    assert advice["rejected_action"] is None
    assert "inventory" not in advice
    assert "position" not in advice


@pytest.mark.parametrize(
    "feedback_kind", ["unverified_execution", "bridge_rejected", "visual_no_change", "unknown"]
)
def test_execution_feedback_does_not_masquerade_as_repeated_proposal_rejection(feedback_kind):
    action = Action(keys=["forward"], duration_ticks=10)
    feedback = {**rejected(action), "feedback_kind": feedback_kind}
    advice = build_recovery_context([completed(action)], [feedback, feedback])
    assert advice["latest_feedback_kind"] == feedback_kind
    assert advice["consecutive_same_proposal_rejections"] == 0
    assert not advice["repeated_proposal"]
    assert advice["completed_inputs_in_window"] == 1
    assert advice["world_state_claims"] == []


@pytest.mark.parametrize("feedback_kind", ["proposal_rejected", "provider_uncertainty"])
def test_explicit_proposal_feedback_can_trigger_replanning(feedback_kind):
    feedback = {
        **rejected(Action(keys=["forward"], duration_ticks=10)),
        "feedback_kind": feedback_kind,
    }
    advice = build_recovery_context([], [feedback, feedback])
    assert advice["repeated_proposal"]
    assert advice["consecutive_same_proposal_rejections"] == 2


def test_execution_feedback_breaks_a_rejected_proposal_streak():
    proposal = rejected(Action(keys=["forward"], duration_ticks=10))
    unverified_execution = {**proposal, "feedback_kind": "unverified_execution"}
    advice = build_recovery_context([], [proposal, unverified_execution, proposal])
    assert advice["consecutive_same_proposal_rejections"] == 1
    assert not advice["repeated_proposal"]


@pytest.mark.parametrize("malformed", [None, "not a history", {"reason": "not a list"}, 42])
def test_malformed_history_cannot_invent_rejections_or_change_the_objective(malformed):
    context = {
        "run_goal": "Gather wood",
        "objective": "Inspect the current view",
        "failures": malformed,
        "recent_actions": malformed,
    }
    shaped = shape_recovery_context(context)
    assert shaped["objective"] == context["objective"]
    assert shaped["recovery_guidance"]["completed_inputs_in_window"] == 0
    assert not shaped["recovery_guidance"]["repeated_proposal"]


def test_malformed_record_breaks_rejection_streak_instead_of_joining_its_neighbors():
    proposal = rejected(Action(keys=["forward"], duration_ticks=10))
    advice = build_recovery_context([], [proposal, None, proposal])
    assert advice["consecutive_same_proposal_rejections"] == 1
