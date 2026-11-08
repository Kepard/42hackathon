"""Correlate new progression with state evidence while retaining legacy journals."""

import json

import pytest
from holospeed.analysis import analyze_run, compare_runs
from holospeed.broadcast import sanitize
from holospeed.recording import RunRecorder
from test_analysis import journal, portal_events, portal_step_events, skill_events


@pytest.mark.parametrize(
    ("milestone", "item"),
    [
        ("stone_pickaxe", "minecraft:stone_pickaxe"),
        ("furnace", "minecraft:furnace"),
        ("iron_mined", "minecraft:raw_iron"),
        ("iron_smelted", "minecraft:iron_ingot"),
        ("bucket", "minecraft:water_bucket"),
        ("flint_and_steel", "minecraft:flint_and_steel"),
    ],
)
@pytest.mark.parametrize("held", [False, True])
def test_new_material_milestones_require_exact_current_inventory(tmp_path, milestone, item, held):
    events = portal_step_events(
        milestone, inventory={item: 1} if held else {"minecraft:fire_charge": 1}
    )
    events[0][2].pop("skill")
    events[1][2]["evidence"].pop("verified_steps")
    events.insert(
        1,
        (
            "model_response",
            105,
            {"provider": "holo", "milestone": milestone, "inventory": {item: 64}},
        ),
    )
    facts = analyze_run(journal(tmp_path, events))["facts"]
    assert bool(facts["state_confirmed_milestones"]) is held
    assert bool(facts["uncorroborated_state_milestones"]) is not held
    if held:
        assert facts["state_confirmed_milestones"][0]["acquisition_evidence"] == "current_inventory"


@pytest.mark.parametrize("milestone", ["water_collected", "lava_located", "portal_built"])
def test_new_casting_steps_require_actual_matching_client_flags(tmp_path, milestone):
    events = portal_step_events(milestone)
    good = journal(tmp_path, events, name="good")
    assert analyze_run(good)["facts"]["state_confirmed_milestones"][0]["id"] == milestone
    events[0][2]["skill"]["verified_steps"] = []
    bad = journal(tmp_path, events, name="bad")
    assert analyze_run(bad)["facts"]["state_confirmed_milestones"] == []


def cast_events(verified=True):
    events = portal_events()
    for index in (0, 1, 3):
        events[index][2]["skill"] = "cast_portal"
    events[2][2]["skill"] = {
        "kind": "cast_portal",
        "status": "completed",
        "verified_steps": ["water_collected", "lava_located", "portal_built", "portal_lit"]
        if verified
        else [],
    }
    return events


def test_full_frame_cast_requires_frame_and_ignition_evidence_in_addition_to_nether(tmp_path):
    good = journal(tmp_path, cast_events(), name="cast")
    bad = journal(tmp_path, cast_events(False), name="unproven-cast")
    historical = journal(tmp_path, portal_events(), name="legacy-portal")
    assert analyze_run(good)["facts"]["skills"]["full_frame_verified"] == 1
    assert analyze_run(good)["facts"]["skills"]["completed"] == 1
    incomplete = analyze_run(bad)["facts"]["skills"]
    assert incomplete["dimension_verified"] == 1  # Actual Nether arrival remains factual.
    assert incomplete["completed"] == 0 and incomplete["unverified_completions"] == 1
    assert analyze_run(historical)["facts"]["skills"]["completed"] == 1
    assert compare_runs([historical, good])["metrics"]["skills_full_frame_verified"] == [0, 1]


@pytest.mark.parametrize("status", ["failed", "cancelled", "running", "completed"])
@pytest.mark.parametrize("fresh", [True, False])
def test_smelting_inventory_prediction_cannot_override_failed_client_confirmation(
    tmp_path, status, fresh
):
    events = skill_events(candidate="smelt-iron", item="minecraft:iron_ingot", target=4)
    for _, _, data in events:
        data["skill"] = "smelt"
    inventory = {"minecraft:iron_ingot": 4}
    events += [
        (
            "game_state",
            100,
            {
                "id": "smelt-state",
                "inventory": inventory,
                "skill": {"kind": "smelt", "status": status},
                "world_ready": True,
                "cache_age_ms": 0 if fresh else 1001,
                "player": {"health": 20},
            },
        ),
        (
            "skill_completed",
            110,
            {
                "candidate_id": "smelt-iron",
                "item": "minecraft:iron_ingot",
                "target_count": 4,
                "state_id": "smelt-state",
                "inventory": inventory,
            },
        ),
    ]
    skills = analyze_run(journal(tmp_path, events))["facts"]["skills"]
    assert skills["completed"] == int(status == "completed" and fresh)


def test_public_route_metadata_roundtrips_without_relabeling_historical_runs(tmp_path):
    recorder = RunRecorder(
        tmp_path,
        "live",
        {"route": "from_scratch", "goal": "nether", "route_target": {"x": 4, "y": 63, "z": 2}},
    )
    recorder.finish("paused")
    public = sanitize(recorder.summary)
    assert public["config"]["route"] == "from_scratch"
    assert "nether_route" not in public["config"]
    report = analyze_run(recorder.path)
    assert report["run"]["route"] == "from_scratch" and report["run"]["goal"] == "nether"
    (recorder.path / "summary.json").unlink()
    assert (
        analyze_run(recorder.path)["run"]["route"] == "from_scratch"
    )  # Durable run_started fallback.
    historical = journal(tmp_path, [], name="old")
    assert analyze_run(historical)["run"]["route"] is None


def test_new_route_shared_crafting_milestone_needs_owned_table_not_an_empty_claim(tmp_path):
    events = portal_step_events("crafting_table")
    events[0][2].pop("skill")
    events[1][2]["evidence"].pop("verified_steps")
    path = journal(tmp_path, events)
    summary = json.loads((path / "summary.json").read_text())
    summary["config"].update(route="from_scratch", nether_route="ruined_portal")
    (path / "summary.json").write_text(json.dumps(summary))
    facts = analyze_run(path)["facts"]
    assert facts["state_confirmed_milestones"] == []
    assert facts["uncorroborated_state_milestones"][0]["id"] == "crafting_table"
