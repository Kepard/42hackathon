from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest
from holospeed.analysis import analyze_run, compare_runs


def journal(tmp_path: Path, events: list[tuple], *, name="run", mode="live", duration=1000) -> Path:
    path = tmp_path / name
    path.mkdir()
    (path / "summary.json").write_text(
        json.dumps(
            {
                "id": name,
                "mode": mode,
                "status": "finished",
                "duration_ms": duration,
                "config": {"operator_declared_seed": "123"},
            }
        )
    )
    with (path / "events.jsonl").open("w") as stream:
        for seq, (kind, elapsed, data) in enumerate(events, 1):
            stream.write(
                json.dumps({"seq": seq, "type": kind, "elapsed_ms": elapsed, "data": data}) + "\n"
            )
    return path


def skill_events(*, command="skill-1", candidate="mine-log", item="minecraft:birch_log", target=3):
    parameters = {"skill": "mine", "item": item, "target_count": target, "max_ticks": 1200}
    return [
        ("skill_started", 10, {"id": candidate, **parameters}),
        ("bridge_command", 11, {"type": "skill", "id": command, **parameters}),
    ]


def state_event(identifier="state-1", count=3, *, elapsed=100, tick=20):
    return (
        "game_state",
        elapsed,
        {
            "id": identifier,
            "tick": tick,
            "path": f"states/{identifier}.json.gz",
            "sha256": "a" * 64,
            "inventory": [{"slot": 0, "item": "minecraft:birch_log", "count": count}],
        },
    )


def outcome_event(kind="skill_completed", *, state_id="state-1", count=3, elapsed=110):
    return (
        kind,
        elapsed,
        {
            "candidate_id": "mine-log",
            "item": "minecraft:birch_log",
            "target_count": 3,
            "inventory": {"minecraft:birch_log": count},
            "state_id": state_id,
            "reason": "Inventory target met" if kind == "skill_completed" else "No progress",
        },
    )


def portal_events():
    parameters = {
        "skill": "portal",
        "item": "minecraft:nether_portal",
        "target_count": 1,
        "max_ticks": 1200,
        "postcondition": "dimension",
        "expected_dimension": "minecraft:the_nether",
    }
    return [
        ("skill_started", 10, {"id": "enter-nether", **parameters}),
        ("bridge_command", 11, {"type": "skill", "id": "portal-rpc", **parameters}),
        (
            "game_state",
            100,
            {
                "id": "nether-state",
                "tick": 150,
                "world_ready": True,
                "cache_age_ms": 0,
                "dimension": "minecraft:the_nether",
                "player": {"health": 20},
                "inventory": [],
                "path": "states/nether-state.json.gz",
            },
        ),
        (
            "skill_completed",
            110,
            {
                **parameters,
                "candidate_id": "enter-nether",
                "state_id": "nether-state",
                "dimension": "minecraft:the_nether",
                "inventory": {},
                "reason": "Ready Nether dimension confirmed",
            },
        ),
        (
            "milestone",
            111,
            {
                "id": "nether",
                "verification": "game_state",
                "state_id": "nether-state",
                "tick": 150,
                "evidence": {"inventory": {}, "dimension": "minecraft:the_nether"},
            },
        ),
    ]


def test_portal_completion_uses_dimension_not_a_portal_inventory_item(tmp_path):
    facts = analyze_run(journal(tmp_path, portal_events()))["facts"]
    skills = facts["skills"]
    assert skills["completed"] == skills["dimension_verified"] == 1
    assert skills["inventory_verified"] == skills["unverified_completions"] == 0
    outcome = skills["outcome_samples"][0]
    assert outcome["postcondition_verified"] is outcome["dimension_verified"] is True
    assert outcome["inventory_verified"] is False
    assert outcome["inventory_count"] == 0
    assert outcome["state_ref"]["dimension"] == "minecraft:the_nether"
    assert [item["id"] for item in facts["state_confirmed_milestones"]] == ["nether"]
    assert facts["state_confirmed_milestones"][0]["evidence"] == {
        "inventory": {},
        "dimension": "minecraft:the_nether",
    }


@pytest.mark.parametrize(
    ("index", "patch"),
    [
        (0, {"expected_dimension": "minecraft:overworld"}),
        (0, {"postcondition": "inventory"}),
        (2, {"dimension": "minecraft:overworld"}),
        (2, {"world_ready": False}),
        (2, {"world_ready": "true"}),
        (2, {"cache_age_ms": 1001}),
        (2, {"cache_age_ms": None}),
        (2, {"player": {"health": 0}}),
        (2, {"player": None}),
        (3, {"state_id": "missing"}),
        (3, {"dimension": "minecraft:overworld"}),
        (3, {"expected_dimension": "minecraft:overworld"}),
        (3, {"postcondition": "inventory"}),
    ],
)
def test_portal_skill_claim_needs_matching_ready_live_dimension_evidence(tmp_path, index, patch):
    events = portal_events()
    events[index][2].update(patch)
    skills = analyze_run(journal(tmp_path, events))["facts"]["skills"]
    assert skills["completed"] == skills["dimension_verified"] == 0
    assert skills["unverified_completions"] == 1


def test_portal_skill_cannot_reference_a_pre_attempt_nether_snapshot(tmp_path):
    events = portal_events()
    kind, _, data = events.pop(2)
    events.insert(0, (kind, 0, data))
    skills = analyze_run(journal(tmp_path, events))["facts"]["skills"]
    assert skills["completed"] == 0
    assert skills["unverified_completions"] == 1


def test_portal_metadata_cannot_redefine_success_as_remaining_in_overworld(tmp_path):
    events = portal_events()
    for index in (0, 1, 3):
        events[index][2]["expected_dimension"] = "minecraft:overworld"
    for index in (2, 3):
        events[index][2]["dimension"] = "minecraft:overworld"
    skills = analyze_run(journal(tmp_path, events))["facts"]["skills"]
    assert skills["completed"] == 0
    assert skills["unverified_completions"] == 1


@pytest.mark.parametrize(
    ("state_patch", "evidence"),
    [
        ({"dimension": "minecraft:overworld"}, {"dimension": "minecraft:the_nether"}),
        ({"dimension": "minecraft:overworld"}, {"dimension": "minecraft:overworld"}),
        ({}, {}),
        ({"world_ready": False}, {"dimension": "minecraft:the_nether"}),
        ({"cache_age_ms": 1001}, {"dimension": "minecraft:the_nether"}),
        ({"player": {"health": 0}}, {"dimension": "minecraft:the_nether"}),
    ],
)
def test_nether_milestone_rejects_missing_or_contradictory_dimension(
    tmp_path, state_patch, evidence
):
    events = portal_events()
    events[2][2].update(state_patch)
    events[4][2]["evidence"] = {"inventory": {}, **evidence}
    facts = analyze_run(journal(tmp_path, events))["facts"]
    assert facts["state_confirmed_milestones"] == []
    assert [item["id"] for item in facts["uncorroborated_state_milestones"]] == ["nether"]


def test_nether_milestone_cannot_borrow_a_future_snapshot(tmp_path):
    events = portal_events()
    milestone = events.pop()
    events.insert(0, (milestone[0], 0, milestone[2]))
    facts = analyze_run(journal(tmp_path, events))["facts"]
    assert facts["state_confirmed_milestones"] == []
    assert len(facts["uncorroborated_state_milestones"]) == 1


def portal_step_events(step="portal_lit", *, inventory=None):
    counts = inventory or {}
    return [
        (
            "game_state",
            100,
            {
                "id": "portal-progress",
                "tick": 120,
                "world_ready": True,
                "cache_age_ms": 25,
                "dimension": "minecraft:overworld",
                "player": {"health": 20},
                "inventory": counts.copy(),
                "skill": {"verified_steps": [step]},
            },
        ),
        (
            "milestone",
            110,
            {
                "id": step,
                "verification": "game_state",
                "state_id": "portal-progress",
                "tick": 120,
                "evidence": {
                    "inventory": counts.copy(),
                    "dimension": "minecraft:overworld",
                    "verified_steps": [step],
                },
            },
        ),
    ]


@pytest.mark.parametrize(
    "step",
    [
        "portal_located",
        "chest_looted",
        "obsidian_acquired",
        "ignition_acquired",
        "portal_repaired",
        "portal_lit",
    ],
)
def test_portal_substeps_correlate_actual_client_checks(tmp_path, step):
    facts = analyze_run(journal(tmp_path, portal_step_events(step)))["facts"]
    assert facts["uncorroborated_state_milestones"] == []
    assert len(facts["state_confirmed_milestones"]) == 1
    milestone = facts["state_confirmed_milestones"][0]
    assert milestone["id"] == step
    assert milestone["portal_step_evidence"] == "client_verified_step"
    assert milestone["state_ref"]["verified_steps"] == [step]
    assert milestone["evidence"]["verified_steps"] == [step]
    # Journal correlation does not claim to replay Minecraft geometry itself.
    assert milestone["independently_verified"] is False


@pytest.mark.parametrize(
    ("state_patch", "milestone_patch"),
    [
        ({"skill": {}}, {}),
        ({"skill": {"verified_steps": ["portal_repaired"]}}, {}),
        ({"skill": {"verified_steps": ["portal_lit", "invented_step"]}}, {}),
        ({"skill": {"verified_steps": ["portal_lit", "portal_lit"]}}, {}),
        ({"skill": {"verified_steps": "portal_lit"}}, {}),
        ({"world_ready": False}, {}),
        ({"cache_age_ms": 1001}, {}),
        ({"player": {"health": 0}}, {}),
        ({}, {"state_id": "unlinked-state"}),
        ({}, {"tick": 119}),
        ({}, {"evidence": {"inventory": {}}}),
    ],
)
def test_portal_substeps_reject_unsupported_or_unready_flags(
    tmp_path, state_patch, milestone_patch
):
    events = portal_step_events()
    events[0][2].update(state_patch)
    events[1][2].update(milestone_patch)
    facts = analyze_run(journal(tmp_path, events))["facts"]
    assert facts["state_confirmed_milestones"] == []
    assert len(facts["uncorroborated_state_milestones"]) == 1
    assert facts["uncorroborated_state_milestones"][0]["portal_step_evidence"] is None


def test_model_output_cannot_supply_missing_client_portal_flags(tmp_path):
    events = portal_step_events()
    events[0][2].pop("skill")
    events.insert(
        1,
        (
            "model_response",
            105,
            {
                "provider": "holo",
                "verified_steps": ["portal_lit"],
                "skill": {"verified_steps": ["portal_lit"]},
            },
        ),
    )
    facts = analyze_run(journal(tmp_path, events))["facts"]
    assert facts["state_confirmed_milestones"] == []
    assert len(facts["uncorroborated_state_milestones"]) == 1


def test_portal_substep_cannot_reference_future_client_verification(tmp_path):
    events = portal_step_events()
    milestone = events.pop()
    events.insert(0, (milestone[0], 0, milestone[2]))
    facts = analyze_run(journal(tmp_path, events))["facts"]
    assert facts["state_confirmed_milestones"] == []
    assert len(facts["uncorroborated_state_milestones"]) == 1


@pytest.mark.parametrize(
    ("step", "item"),
    [
        ("obsidian_acquired", "minecraft:obsidian"),
        ("ignition_acquired", "minecraft:fire_charge"),
        ("ignition_acquired", "minecraft:flint_and_steel"),
    ],
)
def test_resource_acquisition_uses_actual_inventory_without_client_flags(tmp_path, step, item):
    events = portal_step_events(step, inventory={item: 1})
    events[0][2].pop("skill")
    events[1][2]["evidence"].pop("verified_steps")
    facts = analyze_run(journal(tmp_path, events))["facts"]
    milestone = facts["state_confirmed_milestones"][0]
    assert milestone["id"] == step
    assert milestone["portal_step_evidence"] == "current_inventory"
    assert facts["uncorroborated_state_milestones"] == []


@pytest.mark.parametrize("retained", [True, False])
def test_consumed_obsidian_requires_retained_client_acquisition_check(tmp_path, retained):
    events = portal_step_events("obsidian_acquired")
    earlier = portal_step_events("obsidian_acquired", inventory={"minecraft:obsidian": 1})[0]
    earlier[2].update(id="before-consumption", tick=115)
    events.insert(0, (earlier[0], 90, earlier[2]))
    if not retained:
        events[1][2]["skill"]["verified_steps"] = []
    facts = analyze_run(journal(tmp_path, events))["facts"]
    if retained:
        milestone = facts["state_confirmed_milestones"][0]
        assert milestone["state_ref"]["inventory_counts"] == {}
        assert milestone["evidence"]["inventory"] == {}
        assert milestone["portal_step_evidence"] == "client_verified_step"
    else:
        assert facts["state_confirmed_milestones"] == []
        assert len(facts["uncorroborated_state_milestones"]) == 1


def test_state_run_timeout_has_one_selection_and_one_unacknowledged_skill(tmp_path):
    # Mirrors the first state-aware live trial: metrics precede StrategySelection;
    # the bridge times out after dispatch, before even accepting the mining skill.
    call = {
        "provider": "holo",
        "latency_ms": 5000,
        "usage": {"prompt_tokens": 3214, "completion_tokens": 198, "total_tokens": 3412},
    }
    path = journal(
        tmp_path,
        [
            state_event("before", 0, elapsed=10),
            ("model_request", 500, {"provider": "holo", "call_id": "call-1"}),
            (
                "model_response",
                5500,
                {**call, "call_id": "call-1", "http_status": 200},
            ),
            ("inference_metrics", 5508, call),
            ("model_call", 5510, {**call, "candidate_id": "mine-log", "model": "holo4"}),
            state_event("fresh", 0, elapsed=5600),
            *((kind, elapsed + 5700, data) for kind, elapsed, data in skill_events()),
            ("error", 10729, {"detail": "Minecraft response timed out; controls released"}),
        ],
        duration=86172,
    )
    facts = analyze_run(path)["facts"]
    assert facts["providers"]["holo"]["calls"] == 1
    assert facts["providers"]["holo"]["skill_selections"] == 1
    assert facts["providers"]["holo"]["usage"]["total_tokens"] == 3412
    assert facts["providers"]["holo"]["latency_ms"]["mean"] == 5000
    assert facts["state_observations"] == 2
    assert facts["skills"]["requests"] == facts["skills"]["dispatched"] == 1
    assert facts["skills"]["unacknowledged_requests"] == facts["skills"]["unresolved_requests"] == 1
    assert facts["skills"]["completed"] == facts["skills"]["failed"] == 0
    assert facts["actions"]["requests"] == facts["actions"]["terminal_acks"] == 0


@pytest.mark.parametrize("status", ["accepted", "completed", "rejected"])
def test_skill_ack_is_not_a_raw_action_or_verified_skill_completion(tmp_path, status):
    ack = {"type": "skill_ack", "id": "skill-1", "status": status, "skill_id": "worker-1"}
    path = journal(
        tmp_path,
        [
            *skill_events(),
            ("bridge_ack", 12, ack),
            ("skill_ack", 13, ack),
            ("raw_bridge_ack", 14, {**ack, "id": "orphan"}),
        ],
    )
    facts = analyze_run(path)["facts"]
    assert facts["actions"]["requests"] == facts["actions"]["terminal_acks"] == 0
    assert facts["timing"]["acknowledged_action_ticks"] == 0
    assert facts["skills"]["acknowledged"] == 2
    assert facts["skills"]["duplicate_acks"] == 1
    assert facts["skills"]["orphan_acks"] == 1
    assert facts["skills"]["completion_events"] == facts["skills"]["completed"] == 0
    assert facts["skills"]["unresolved_requests"] == (0 if status == "rejected" else 1)


@pytest.mark.parametrize(
    ("state_count", "outcome_count", "state_id", "target", "expected"),
    [
        (3, 3, "state-1", 3, 1),
        (2, 3, "state-1", 3, 0),
        (3, 2, "state-1", 3, 0),
        (3, 3, "missing", 3, 0),
        (3, 3, "state-1", 2, 0),
    ],
)
def test_skill_completion_requires_matching_target_and_recorded_inventory(
    tmp_path, state_count, outcome_count, state_id, target, expected
):
    path = journal(
        tmp_path,
        [
            *skill_events(target=target),
            state_event(count=state_count),
            outcome_event(state_id=state_id, count=outcome_count),
        ],
    )
    skills = analyze_run(path)["facts"]["skills"]
    assert skills["completion_events"] == 1
    assert skills["completed"] == expected
    assert skills["unverified_completions"] == 1 - expected
    assert skills["outcome_samples"][0]["inventory_verified"] is bool(expected)


def test_skill_completion_cannot_use_a_snapshot_from_before_the_attempt(tmp_path):
    path = journal(
        tmp_path,
        [state_event(elapsed=0), *skill_events(), outcome_event()],
    )
    skills = analyze_run(path)["facts"]["skills"]
    assert skills["completed"] == 0
    assert skills["unverified_completions"] == 1


def test_reused_candidate_tracks_separate_attempts_and_deduplicates_outcomes(tmp_path):
    success = outcome_event(state_id="after", elapsed=210)
    path = journal(
        tmp_path,
        [
            *skill_events(),
            ("skill_progress", 40, {"candidate_id": "mine-log", "inventory_count": 0}),
            state_event("failed", 0),
            outcome_event("skill_failed", state_id="failed", count=0),
            *(
                (kind, elapsed + 110, data)
                for kind, elapsed, data in skill_events(command="skill-2")
            ),
            ("skill_progress", 140, {"candidate_id": "mine-log", "inventory_count": 3}),
            state_event("after", 3, elapsed=200),
            success,
            (success[0], 211, success[2]),
        ],
    )
    skills = analyze_run(path)["facts"]["skills"]
    assert skills["requests"] == skills["dispatched"] == 2
    assert skills["completed"] == skills["failed"] == 1
    assert skills["duplicate_outcomes"] == 1
    assert skills["unresolved_requests"] == 0
    assert [sample["request_seq"] for sample in skills["outcome_samples"]] == [1, 6]


def test_equal_latency_metrics_pairs_are_consumed_once_in_either_order(tmp_path):
    call = {"provider": "jev", "latency_ms": 25, "usage": {"input_tokens": 7}}
    path = journal(
        tmp_path,
        [
            ("inference_metrics", 25, call),
            ("model_call", 26, {**call, "candidate_id": "mine-a"}),
            ("model_call", 50, {**call, "candidate_id": "mine-b"}),
            ("inference_metrics", 51, call),
        ],
    )
    provider = analyze_run(path)["facts"]["providers"]["jev"]
    assert provider["calls"] == provider["skill_selections"] == 2
    assert provider["usage"]["input_tokens"] == 14


def test_request_boundaries_and_missing_latency_do_not_merge_unrelated_calls(tmp_path):
    path = journal(
        tmp_path,
        [
            ("model_request", 0, {"provider": "holo", "call_id": "first"}),
            ("inference_metrics", 20, {"provider": "holo", "latency_ms": 20}),
            ("model_request", 21, {"provider": "holo", "call_id": "second"}),
            ("model_call", 41, {"provider": "holo", "latency_ms": 20}),
            ("inference_metrics", 42, {"provider": "jev"}),
            ("model_call", 43, {"provider": "jev"}),
        ],
    )
    providers = analyze_run(path)["facts"]["providers"]
    assert providers["holo"]["calls"] == providers["jev"]["calls"] == 2


def test_transport_fallback_usage_and_failures_survive_without_double_counting(tmp_path):
    path = journal(
        tmp_path,
        [
            ("model_request", 0, {"provider": "holo", "call_id": "complete"}),
            (
                "model_response",
                100,
                {
                    "provider": "holo",
                    "call_id": "complete",
                    "latency_ms": 100,
                    "http_status": 200,
                    "response": {"usage": {"input_tokens": 9}},
                },
            ),
            ("inference_metrics", 101, {"provider": "holo", "latency_ms": 100}),
            # Strong request identity still pairs after a logging stall.
            (
                "model_call",
                4000,
                {"provider": "holo", "latency_ms": 100, "candidate_id": "mine-log"},
            ),
            ("model_request", 4100, {"provider": "holo", "call_id": "parse-error"}),
            (
                "model_response",
                4200,
                {
                    "provider": "holo",
                    "call_id": "parse-error",
                    "latency_ms": 100,
                    "http_status": 200,
                    "response": {"usage": {"input_tokens": 5}},
                },
            ),
            ("strategy_fallback", 4201, {"provider": "holo", "candidate_id": "local"}),
            ("model_request", 4300, {"provider": "jev", "call_id": "cancelled"}),
            (
                "model_cancelled",
                4320,
                {"provider": "jev", "call_id": "cancelled", "latency_ms": 20},
            ),
            ("model_request", 4400, {"provider": "jev", "call_id": "network"}),
            ("model_error", 4410, {"provider": "jev", "call_id": "network", "latency_ms": 10}),
        ],
        duration=5000,
    )
    facts = analyze_run(path)["facts"]
    assert facts["providers"]["holo"]["calls"] == 2
    assert facts["providers"]["holo"]["usage"]["input_tokens"] == 14
    assert facts["providers"]["holo"]["recorded_error_calls"] == 1
    assert (
        facts["providers"]["jev"]["calls"] == facts["providers"]["jev"]["recorded_error_calls"] == 2
    )
    assert facts["strategy_fallbacks"] == 1


@pytest.mark.parametrize("first", ["inference_metrics", "model_call"])
def test_partial_model_pairs_inherit_later_identity_and_ignore_copied_records(tmp_path, first):
    second = "model_call" if first == "inference_metrics" else "inference_metrics"
    call = {"provider": "holo", "latency_ms": 50, "usage": {"input_tokens": 8}}
    path = journal(
        tmp_path,
        [
            (first, 50, call),
            (second, 51, {**call, "call_id": "partial"}),
            ("model_call", 52, {**call, "call_id": "partial"}),
            ("model_response", 53, {**call, "call_id": "partial", "http_status": 200}),
        ],
    )
    facts = analyze_run(path)["facts"]
    assert facts["providers"]["holo"]["calls"] == 1
    assert facts["providers"]["holo"]["usage"]["input_tokens"] == 8
    assert facts["journal"]["duplicate_model_records"] == 1


def test_state_milestones_remain_distinct_from_visual_claims_and_missing_evidence(tmp_path):
    recorded = {
        "verification": "game_state",
        "state_id": "state-1",
        "tick": 20,
        "evidence": {"inventory": {"minecraft:birch_log": 3}},
    }
    path = journal(
        tmp_path,
        [
            state_event(),
            ("milestone", 110, {**recorded, "id": "wood"}),
            # A placed table can be confirmed without a table stack in inventory.
            ("milestone", 111, {**recorded, "id": "crafting_table"}),
            ("milestone", 112, {**recorded, "id": "stone_tools", "state_id": "missing"}),
            ("milestone", 113, {**recorded, "id": "wooden_pickaxe", "tick": 19}),
            ("milestone", 113, {**recorded, "id": "contradiction", "evidence": {"inventory": {}}}),
            (
                "milestone",
                114,
                {
                    "id": "portal",
                    "verification": "model_visual_claim",
                    "evidence": "I see a portal",
                },
            ),
        ],
    )
    report = analyze_run(path)
    confirmed = report["facts"]["state_confirmed_milestones"]
    assert [item["id"] for item in confirmed] == ["wood", "crafting_table"]
    assert confirmed[0]["evidence"] == {"inventory": {"minecraft:birch_log": 3}}
    assert confirmed[0]["state_ref"]["path"] == "states/state-1.json.gz"
    assert confirmed[0]["independently_verified"] is False
    assert len(report["facts"]["uncorroborated_state_milestones"]) == 3
    assert [item["id"] for item in report["model_claims"]["milestones"]] == ["portal"]


def test_comparison_keeps_legacy_actions_and_adds_inventory_verified_skills(tmp_path):
    old = journal(tmp_path, [("bridge_ack", 5, {"id": "raw", "status": "completed"})], name="old")
    state = journal(
        tmp_path,
        [
            *skill_events(),
            state_event(),
            outcome_event(),
            (
                "milestone",
                120,
                {
                    "id": "wood",
                    "verification": "game_state",
                    "state_id": "state-1",
                    "tick": 20,
                    "evidence": {"inventory": {"minecraft:birch_log": 3}},
                },
            ),
        ],
        name="state",
    )
    summary = json.loads((state / "summary.json").read_text())
    summary["config"]["gameplay_mode"] = "state"
    (state / "summary.json").write_text(json.dumps(summary))
    comparison = compare_runs([old, state])
    assert comparison["metrics"]["actions_completed"] == [1, 0]
    assert comparison["metrics"]["skills_completed"] == [0, 1]
    assert comparison["metrics"]["state_confirmed_milestones"] == [0, 1]
    assert comparison["state_confirmed_milestones"][0]["runs"][0] is None
    assert comparison["milestone_claims"] == []
    assert comparison["comparability"]["same_gameplay_mode"] is False


def test_reconciles_raw_acks_metrics_vetoes_and_evidence(tmp_path):
    forward = {"keys": ["forward"], "duration_ticks": 6}
    wait = {"keys": [], "duration_ticks": 2}
    veto = "Jev abstained because continuation is uncertain or unsafe: blocked"
    path = journal(
        tmp_path,
        [
            (
                "observation",
                0,
                {"frame_id": "f1", "sha256": "same", "frame_url": "/api/runs/run/frames/one.png"},
            ),
            (
                "model_call",
                100,
                {
                    "provider": "holo",
                    "latency_ms": 100,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            ),
            (
                "inference_metrics",
                101,
                {
                    "provider": "holo",
                    "latency_ms": 100,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            ),
            (
                "plan",
                102,
                {
                    "provider": "holo",
                    "latency_ms": 100,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            ),
            ("action", 120, {"frame_id": "f1", "action": forward}),
            (
                "bridge_command",
                125,
                {"type": "action", "id": "a", "frame_id": "f1", "action": forward},
            ),
            ("bridge_ack", 425, {"id": "a", "status": "completed", "start_tick": 1, "end_tick": 7}),
            (
                "ack",
                426,
                {"id": "a", "action_seq": 5, "status": "completed", "start_tick": 1, "end_tick": 7},
            ),
            ("observation", 430, {"frame_id": "f2", "sha256": "same"}),
            ("action", 440, {"frame_id": "f2", "action": forward}),
            (
                "bridge_command",
                445,
                {"type": "action", "id": "b", "frame_id": "f2", "action": forward},
            ),
            (
                "raw_bridge_ack",
                500,
                {"id": "b", "status": "rejected", "start_tick": 8, "end_tick": 8},
            ),
            ("ack", 501, {"id": "b", "action_seq": 10, "status": "rejected"}),
            ("action", 510, {"frame_id": "f2", "action": wait}),
            (
                "bridge_command",
                511,
                {"type": "action", "id": "c", "frame_id": "f2", "action": wait},
            ),
            (
                "raw_bridge_ack",
                610,
                {"id": "c", "status": "cancelled", "start_tick": 10, "end_tick": 11},
            ),
            ("ack", 611, {"id": "c", "action_seq": 14, "status": "cancelled"}),
            (
                "model_call",
                800,
                {
                    "provider": "jev",
                    "latency_ms": 100,
                    "error": veto,
                    "response": {"usage": {"input_tokens": 3, "output_tokens": 1}},
                },
            ),
            ("recovery", 801, {"reason": veto}),
            (
                "inference_metrics",
                802,
                {
                    "provider": "jev",
                    "latency_ms": 100,
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            ),
            (
                "milestone",
                810,
                {
                    "id": "wood",
                    "evidence": "A log is visible",
                    "frame_id": "f1",
                    "verification": "model_visual_claim",
                },
            ),
            ("error", 811, {"detail": "Stopped"}),
            ("run_finished", 1000, {"status": "finished"}),
        ],
    )
    with (path / "events.jsonl").open("a") as stream:
        stream.write('{"seq":24,"data":{"unfinished":"')
    analysis = analyze_run(path)
    facts = analysis["facts"]
    assert facts["journal"]["truncated_last_line"] is True
    assert facts["journal"]["valid_events"] == 23
    assert facts["actions"]["requests"] == 3
    assert facts["actions"]["dispatched"] == 3
    assert facts["actions"]["terminal_acks"] == 3
    assert [facts["actions"][key] for key in ("completed", "rejected", "cancelled")] == [1, 1, 1]
    assert facts["actions"]["duplicate_terminal_acks"] == 3
    assert facts["actions"]["unacknowledged_requests"] == 0
    assert facts["providers"]["holo"]["calls"] == 1
    assert facts["providers"]["jev"]["usage"]["total_tokens"] == 4
    assert facts["providers"]["jev"]["recorded_error_calls"] == 1
    timing = facts["timing"]
    assert timing["model_wait_wall_ms"] == 200
    assert timing["command_round_trip_wall_ms"] == 454
    assert timing["outside_model_or_command_ms"] == 346
    assert timing["nominal_input_execution_ms_at_20_tps"] == 300
    assert timing["deliberate_wait_ticks"] == 1
    assert analysis["patterns"]["no_input_requests"] == 1
    assert analysis["patterns"]["longest_identical_action_streak"] == 2
    assert analysis["patterns"]["same_action_on_identical_frame_repeats"] == 1
    assert analysis["model_claims"]["jev_vetoes"]["count"] == 1
    claim = analysis["model_claims"]["milestones"][0]
    assert claim["independently_verified"] is False
    assert claim["observation_ref"]["seq"] == 1
    assert claim["observation_ref"]["sha256"] == "same"
    json.dumps(analysis, allow_nan=False)


def test_preserves_crash_unpaired_calls_without_double_counting_legacy_decisions(tmp_path):
    path = journal(
        tmp_path,
        [
            (
                "model_call",
                100,
                {"provider": "holo", "latency_ms": 100, "usage": {"input_tokens": 1}},
            ),
            (
                "inference_metrics",
                101,
                {"provider": "holo", "latency_ms": 100, "usage": {"input_tokens": 1}},
            ),
            (
                "decision",
                102,
                {"provider": "holo", "latency_ms": 100, "usage": {"input_tokens": 1}},
            ),
            (
                "model_call",
                500,
                {
                    "provider": "holo",
                    "latency_ms": 300,
                    "response": {"usage": {"prompt_tokens": 2}},
                },
            ),
        ],
    )
    provider = analyze_run(path)["facts"]["providers"]["holo"]
    assert provider["calls"] == 2
    assert provider["sources"] == {"inference_metrics": 1, "model_call": 1}
    assert provider["usage"]["input_tokens"] == 3
    assert provider["latency_ms"]["p95"] == 290
    assert provider["latency_ms"]["mean"] == 200


def test_legacy_decision_only_journal_and_duplicate_sequences(tmp_path):
    path = journal(
        tmp_path,
        [
            (
                "decision",
                300,
                {
                    "provider": "jev",
                    "latency_ms": 75,
                    "usage": {"input_tokens": 7, "output_tokens": 2},
                },
            )
        ],
    )
    with (path / "events.jsonl").open("a") as stream:
        stream.write((path / "events.jsonl").read_text())
        stream.write("this is malformed\n")
    result = analyze_run(path)
    assert result["facts"]["providers"]["jev"]["calls"] == 1
    assert result["facts"]["providers"]["jev"]["usage"]["total_tokens"] == 9
    assert result["facts"]["journal"]["duplicate_sequences"] == 1
    assert result["facts"]["journal"]["invalid_lines"] == 1
    assert result["facts"]["journal"]["truncated_last_line"] is False


def test_tracks_orphan_conflicting_and_unacknowledged_controls(tmp_path):
    path = journal(
        tmp_path,
        [
            (
                "raw_bridge_ack",
                1,
                {"id": "orphan", "status": "completed", "start_tick": 1, "end_tick": 3},
            ),
            ("action", 5, {"action": {"keys": ["jump"]}}),
            ("bridge_command", 6, {"type": "action", "id": "x", "action": {"keys": ["jump"]}}),
            ("bridge_ack", 50, {"id": "x", "status": "cancelled", "start_tick": 4, "end_tick": 5}),
            ("ack", 51, {"id": "x", "status": "completed", "action_seq": 2}),
            ("action", 60, {"action": {"keys": ["attack"]}}),
        ],
    )
    facts = analyze_run(path)["facts"]
    assert facts["actions"]["orphan_terminal_acks"] == 1
    assert facts["actions"]["conflicting_terminal_acks"] == 1
    assert facts["actions"]["completed"] == 1
    assert facts["actions"]["cancelled"] == 1
    assert facts["actions"]["unacknowledged_requests"] == 1
    assert facts["timing"]["unassociated_ack_ticks"] == 2
    assert facts["timing"]["active_input_ticks"] == 1


def test_large_image_string_is_elided_before_json_decode(tmp_path):
    path = tmp_path / "large"
    path.mkdir()
    with (path / "events.jsonl").open("w") as stream:
        stream.write(
            '{"seq":1,"type":"model_call","elapsed_ms":300,"data":{"provider":"holo","request":{"image_base64":"'
        )
        for _ in range(384):
            stream.write("A" * 65536)
        stream.write(
            '"},"response":{"text":"escaped \\"quote\\""},"latency_ms":123,"usage":{"prompt_tokens":42}}}\n'
        )
    tracemalloc.start()
    try:
        result = analyze_run(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4_000_000, "Image-bearing JSON must not be materialized in memory"
    assert result["facts"]["journal"]["oversized_strings_omitted"] == 1
    assert result["facts"]["providers"]["holo"]["usage"]["input_tokens"] == 42
    assert result["facts"]["providers"]["holo"]["latency_ms"]["p50"] == 123
    assert "image_base64" not in json.dumps(result)


def test_late_unknown_ack_cannot_complete_the_only_pending_request(tmp_path):
    action = {"keys": ["forward"], "duration_ticks": 6}
    path = journal(
        tmp_path,
        [
            ("action", 0, {"action": action}),
            ("bridge_command", 1, {"type": "action", "id": "current", "action": action}),
            (
                "bridge_ack",
                20,
                {"id": "old-command", "status": "completed", "start_tick": 0, "end_tick": 6},
            ),
            ("ack", 21, {"action_seq": 999, "status": "cancelled"}),
        ],
    )
    facts = analyze_run(path)["facts"]
    assert facts["actions"]["requests"] == 1
    assert facts["actions"]["unacknowledged_requests"] == 1
    assert facts["actions"]["orphan_terminal_acks"] == 2
    assert facts["timing"]["active_input_ticks"] == 0
    assert facts["timing"]["unassociated_ack_ticks"] == 6


def test_compares_recorded_facts_without_promoting_claims(tmp_path):
    first = journal(
        tmp_path,
        [("milestone", 200, {"id": "wood", "evidence": "model says so"})],
        name="first",
        mode="demo",
        duration=400,
    )
    second = journal(
        tmp_path,
        [
            ("action", 0, {"action": {"keys": ["attack"]}}),
            ("ack", 300, {"id": "a", "status": "completed", "start_tick": 0, "end_tick": 6}),
        ],
        name="second",
        duration=600,
    )
    comparison = compare_runs([first, second])
    assert comparison["metrics"]["actions_completed"] == [0, 1]
    assert comparison["second_minus_first"]["duration_ms"] == 200
    assert comparison["comparability"]["same_mode"] is False
    assert comparison["comparability"]["same_declared_seed"] is True
    assert comparison["comparability"]["independently_verified_outcomes"] is False
    assert comparison["milestone_claims"][0]["runs"][1] is None
    assert comparison["milestone_claims"][0]["runs"][0]["independently_verified"] is False
    with pytest.raises(ValueError, match="at least two"):
        compare_runs([first])
    json.dumps(comparison, allow_nan=False)


def test_separates_active_intervals_pauses_and_post_stop_inspection(tmp_path):
    path = journal(
        tmp_path,
        [
            ("run_started", 0, {"mode": "live"}),
            ("event", 0, {"name": "resumed"}),
            ("observation", 50, {"frame_id": "first"}),
            ("event", 100, {"name": "paused"}),
            ("event", 200, {"name": "resumed"}),
            ("error", 500, {"detail": "Repeated model abstentions"}),
            ("event", 900, {"name": "paused"}),
            ("run_finished", 1000, {}),
        ],
        duration=2000,
    )
    timing = analyze_run(path)["facts"]["timing"]
    assert timing["active_loop_recorded_ms"] == 400
    assert len(timing["active_loop_intervals"]) == 2
    assert [part["stopped_by"] for part in timing["active_loop_intervals"]] == ["paused", "error"]
    assert timing["last_loop_stop_ms"] == 500
    assert timing["post_stop_inspection_ms"] == 1500
    assert timing["recorded_duration_ms"] == 2000
    assert timing["loop_status"] == "stopped"


@pytest.mark.parametrize("stop", ["run_budget_reached", "goal_reached", "model_completion_claim"])
def test_recognizes_loop_completion_and_bounds_open_intervals(tmp_path, stop):
    path = journal(
        tmp_path, [("run_started", 10, {}), ("event", 100, {"name": stop})], name=stop, duration=500
    )
    stopped = analyze_run(path)["facts"]["timing"]
    assert stopped["active_loop_recorded_ms"] == 90
    assert stopped["post_stop_inspection_ms"] == 400
    with (path / "events.jsonl").open("a") as stream:
        stream.write(
            json.dumps({"seq": 3, "type": "event", "elapsed_ms": 200, "data": {"name": "resumed"}})
            + "\n"
        )
        stream.write(
            json.dumps({"seq": 4, "type": "observation", "elapsed_ms": 250, "data": {}}) + "\n"
        )
    open_run = analyze_run(path)["facts"]["timing"]
    assert open_run["active_loop_recorded_ms"] == 140
    assert open_run["active_loop_intervals"][-1]["open_ended"] is True
    assert open_run["loop_status"] == "open"
    assert open_run["post_stop_inspection_ms"] is None
