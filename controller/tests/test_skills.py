"""Exercise recipe progression with real counts and bounded skill alternatives."""

import copy

import pytest
from holospeed.game_state import GameState
from holospeed.skills import (
    CRAFT_ALLOWLIST,
    MINE_ALLOWLIST,
    PLANK_ITEMS,
    PORTAL_CAPABILITY,
    PORTAL_ROUTE_SEED,
    STONE_MATERIALS,
    confirmed_milestones,
    goal_reached,
    next_skills,
)


def cell(x, y, z, item="air", solid=False):
    return {
        "position": {"x": x, "y": y, "z": z},
        "id": "minecraft:" + item,
        "solid": solid,
        "full_cube": solid,
        "fluid": "minecraft:empty",
        "hardness": 2.0 if solid else 0.0,
    }


def fixture(items=None):
    blocks = [
        cell(x, y, z, "stone" if y == 63 else "air", y == 63)
        for x in range(-2, 3)
        for y in range(63, 68)
        for z in range(-2, 3)
    ]
    log = cell(2, 65, 0, "oak_log", True)
    blocks = [log if item["position"] == log["position"] else item for item in blocks]
    value = {
        "type": "game_state",
        "id": "state-1",
        "tick": 20,
        "sampled_at_ms": 1000,
        "cache_age_ms": 0,
        "world_ready": True,
        "screen_open": False,
        "dimension": "minecraft:overworld",
        "player": {
            "position": {"x": 0.5, "y": 64.0, "z": 0.5},
            "eye_position": {"x": 0.5, "y": 65.62, "z": 0.5},
            "eye_height": 1.62,
            "yaw": 0.0,
            "pitch": 0.0,
            "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
            "on_ground": True,
            "health": 20.0,
            "hunger": 20,
            "selected_slot": 0,
            "block_interaction_range": 4.5,
        },
        "inventory": [],
        "screen": None,
        "crosshair": {"type": "miss"},
        "local_blocks": blocks,
        "local_bounds": {
            "min": {"x": -2, "y": 63, "z": -2},
            "max": {"x": 2, "y": 67, "z": 2},
            "complete": True,
        },
        "nearby_blocks": [log, cell(0, 63, 2, "stone", True)],
        "nearby_entities": [],
    }
    set_inventory(value, items or {})
    return value


def set_inventory(value, items):
    value["inventory"] = [
        {"slot": index, "item": name if ":" in name else "minecraft:" + name, "count": count}
        for index, (name, count) in enumerate(items.items())
        if count
    ]


def test_first_log_candidate_advertises_navigation_without_inventing_a_valid_path():
    value = fixture()
    value["nearby_blocks"] = [cell(7, 65, 0, "oak_log", True)]
    value["local_blocks"] = [
        cell(**block["position"]) if block["id"] == "minecraft:oak_log" else block
        for block in value["local_blocks"]
    ]
    candidate = next_skills(GameState.model_validate(value))[0]
    assert candidate["skill"] == "mine"
    assert candidate["nearest_target"]["distance"] > value["player"]["block_interaction_range"]
    assert candidate["execution_mode"] == "navigate_mine_collect"
    assert candidate["navigation_included"] is True
    assert candidate["initial_target_reach_required"] is False
    assert candidate["path_validated"] is False
    assert candidate["tool_requirement"] == "none"


def candidates(items=None):
    return next_skills(GameState.model_validate(fixture(items)))


def consume(items, choices, amount):
    for name in sorted(choices):
        taken = min(items.get(name, 0), amount)
        items[name] = items.get(name, 0) - taken
        amount -= taken
    assert amount == 0, "planner suggested a recipe with missing ingredients"


def apply_inventory_postcondition(items, candidate):
    """Model vanilla recipe accounting, not client execution or model behavior."""
    item = candidate["item"]
    increase = candidate["target_count"] - items.get(item, 0)
    assert increase > 0
    if candidate["skill"] == "craft":
        if item.endswith("_planks"):
            assert increase % 4 == 0
            consume(items, {item.removesuffix("_planks") + "_log"}, increase // 4)
        elif item == "minecraft:stick":
            assert increase % 4 == 0
            consume(items, PLANK_ITEMS, increase // 2)
        elif item == "minecraft:crafting_table":
            consume(items, PLANK_ITEMS, 4 * increase)
        elif item == "minecraft:wooden_pickaxe":
            consume(items, PLANK_ITEMS, 3 * increase)
            consume(items, {"minecraft:stick"}, 2 * increase)
        else:
            consume(items, STONE_MATERIALS, 3 * increase)
            consume(items, {"minecraft:stick"}, 2 * increase)
    items[item] = candidate["target_count"]


def test_empty_inventory_reaches_stone_tools_in_finite_inventory_confirmed_steps():
    value = fixture()
    inventory = {}
    completed = []
    sequence = []
    for _ in range(16):
        set_inventory(value, inventory)
        state = GameState.model_validate(value)
        completed.extend(name for name in confirmed_milestones(state) if name not in completed)
        options = next_skills(state, completed)
        if not options:
            break
        option = options[0]
        sequence.append((option["skill"], option["item"], option["target_count"]))
        assert option["item"] in (MINE_ALLOWLIST if option["skill"] == "mine" else CRAFT_ALLOWLIST)
        assert 1 <= option["target_count"] <= 64
        assert 20 <= option["max_ticks"] <= 1800
        apply_inventory_postcondition(inventory, option)
    assert sequence == [
        ("mine", "minecraft:oak_log", 3),
        ("craft", "minecraft:oak_planks", 12),
        ("craft", "minecraft:crafting_table", 1),
        ("craft", "minecraft:stick", 8),
        ("craft", "minecraft:wooden_pickaxe", 1),
        ("mine", "minecraft:cobblestone", 6),
        ("craft", "minecraft:stone_pickaxe", 1),
        ("craft", "minecraft:stone_axe", 1),
    ]
    assert completed == ["wood", "crafting_table", "wooden_pickaxe", "stone_tools"]
    assert inventory["minecraft:stone_pickaxe"] == inventory["minecraft:stone_axe"] == 1
    assert len(sequence) == len(set(sequence))


def test_consumed_logs_and_planks_do_not_reset_earlier_resource_goals():
    value = fixture({"crafting_table": 1, "wooden_pickaxe": 1, "stick": 4, "cobblestone": 6})
    option = next_skills(
        GameState.model_validate(value),
        [{"id": "wood", "evidence": "previous inventory"}, {"id": "crafting_table"}],
    )[0]
    assert option["skill"] == "craft"
    assert option["item"] == "minecraft:stone_pickaxe"
    assert option["completed_milestones"] == ["wood", "crafting_table"]
    assert (
        candidates({"crafting_table": 1, "stone_pickaxe": 1, "stick": 2, "cobblestone": 3})[0][
            "item"
        ]
        == "minecraft:stone_axe"
    )


def test_existing_planks_tools_and_sticks_reduce_remaining_wood_budget():
    assert candidates({"oak_planks": 12})[0]["item"] == "minecraft:crafting_table"
    assert (
        candidates({"crafting_table": 1, "stone_pickaxe": 1, "stick": 2})[0]["item"]
        == "minecraft:cobblestone"
    )
    option = candidates({"crafting_table": 1, "wooden_pickaxe": 1})[0]
    assert (option["item"], option["target_count"]) == ("minecraft:oak_log", 1)
    assert candidates({"stone_pickaxe": 1, "stone_axe": 1}) == []


def test_available_log_variants_are_converted_without_mining_more():
    options = candidates({"birch_log": 1, "oak_log": 2})
    assert {(item["skill"], item["item"], item["target_count"]) for item in options} == {
        ("craft", "minecraft:oak_planks", 8),
        ("craft", "minecraft:birch_planks", 4),
    }
    assert candidates({"stripped_dark_oak_wood": 3})[0]["item"] == "minecraft:dark_oak_planks"


def test_mixed_planks_and_stone_recipe_tags_are_credited():
    option = candidates({"oak_planks": 5, "birch_planks": 6})[0]
    assert option["item"] == "minecraft:crafting_table"
    option = candidates(
        {"crafting_table": 1, "stick": 4, "cobblestone": 2, "cobbled_deepslate": 2, "blackstone": 2}
    )[0]
    assert option["item"] == "minecraft:stone_pickaxe"  # No obsolete wooden pickaxe is needed.


def test_mining_counts_are_total_per_output_item_and_material_alternatives_are_distinct():
    value = fixture(
        {
            "crafting_table": 1,
            "wooden_pickaxe": 1,
            "stick": 4,
            "cobblestone": 1,
            "cobbled_deepslate": 2,
        }
    )
    value["nearby_blocks"].append(cell(-3, 63, 0, "deepslate", True))
    options = next_skills(GameState.model_validate(value))
    assert {(item["item"], item["target_count"], item["missing_count"]) for item in options} == {
        ("minecraft:cobblestone", 4, 3),
        ("minecraft:cobbled_deepslate", 5, 3),
    }


def test_dropped_logs_are_candidates_but_not_confirmed_inventory_milestones():
    value = fixture()
    value["nearby_blocks"] = []
    value["local_blocks"] = [
        cell for cell in value["local_blocks"] if cell["id"] != "minecraft:oak_log"
    ]
    value["local_bounds"]["complete"] = False
    value["nearby_entities"] = [
        {
            "id": 8,
            "type": "minecraft:item",
            "position": {"x": 1.0, "y": 64.0, "z": 0.0},
            "stack": {"item": "minecraft:birch_log", "count": 4},
        }
    ]
    state = GameState.model_validate(value)
    assert confirmed_milestones(state) == []
    options = next_skills(state)
    assert len(options) == 1
    assert options[0]["item"] == "minecraft:birch_log"
    assert options[0]["nearest_target"]["kind"] == "drop"
    assert options[0]["nearest_target"]["count"] == 4
    assert options[0]["target_count"] == 3


def test_candidates_include_route_evidence_and_risks_without_claiming_a_clear_path():
    value = fixture()
    value["player"].update(health=6.0, hunger=4)
    lava = cell(3, 64, 0, "lava")
    lava["fluid"] = "minecraft:lava"
    value["nearby_blocks"].append(lava)
    value["nearby_entities"] = [
        {"id": 3, "type": "minecraft:creeper", "position": {"x": 2, "y": 64, "z": 0}}
    ]
    option = next_skills(GameState.model_validate(value))[0]
    assert option["nearest_target"]["position"] == {"x": 2, "y": 65, "z": 0}
    assert {"low_health", "low_hunger", "lava_within_5_blocks", "nearby_hostile:creeper"}.issubset(
        option["risks"]
    )
    assert not option["path_validated"]


def test_placed_reachable_table_prevents_duplicate_crafting_after_table_is_consumed():
    value = fixture({"oak_planks": 3, "stick": 6})
    table = cell(1, 64, 0, "crafting_table", True)
    value["local_blocks"] = [
        table if item["position"] == table["position"] else item for item in value["local_blocks"]
    ]
    value["nearby_blocks"].append(table)
    state = GameState.model_validate(value)
    option = next_skills(state)[0]
    assert option["item"] == "minecraft:wooden_pickaxe"
    assert option["table_available"]
    assert not option["table_carried"]
    assert "crafting_table" in confirmed_milestones(state)
    value.update(
        screen_open=True,
        screen={
            "type": "InventoryScreen",
            "title": "Inventory",
            "sync_id": 0,
            "cursor": {"item": "minecraft:air", "count": 0},
        },
    )
    assert next_skills(GameState.model_validate(value))[0]["item"] == "minecraft:wooden_pickaxe"


def test_lost_table_history_does_not_substitute_for_current_recipe_prerequisites():
    value = fixture({"oak_planks": 4, "stone_pickaxe": 1, "stick": 2, "cobblestone": 3})
    option = next_skills(GameState.model_validate(value), ["crafting_table"])[0]
    assert option["item"] == "minecraft:crafting_table"


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(world_ready=False, player=None, dimension=None),
        lambda value: value["player"].update(health=0.0),
        lambda value: value.update(
            screen_open=True, screen={"type": "GameMenuScreen", "title": "Paused", "sync_id": None}
        ),
        lambda value: value.update(
            screen_open=True,
            screen={
                "type": "InventoryScreen",
                "title": "Inventory",
                "sync_id": 0,
                "cursor": {"item": "minecraft:oak_log", "count": 1},
            },
        ),
        lambda value: value.update(
            screen_open=True,
            screen={
                "type": "CraftingScreen",
                "title": "Crafting",
                "sync_id": 1,
                "slots": [{"slot": 2, "item": "minecraft:oak_planks", "count": 1, "x": 0, "y": 0}],
            },
        ),
    ],
)
def test_non_executable_state_has_no_candidates(change):
    value = fixture({"oak_planks": 12})
    change(value)
    assert next_skills(GameState.model_validate(value)) == []


def test_empty_inventory_screen_allows_crafting_but_not_mining():
    value = fixture({"oak_planks": 12})
    value.update(
        screen_open=True,
        screen={
            "type": "InventoryScreen",
            "title": "Inventory",
            "sync_id": 0,
            "cursor": {"item": "minecraft:air", "count": 0},
        },
    )
    assert next_skills(GameState.model_validate(value))[0]["item"] == "minecraft:crafting_table"
    value["inventory"] = []
    assert next_skills(GameState.model_validate(value)) == []


def test_planning_is_pure_and_unknown_log_species_are_not_invented():
    value = fixture()
    value["nearby_blocks"] = []
    value["local_blocks"] = [
        item for item in value["local_blocks"] if item["id"] != "minecraft:oak_log"
    ]
    value["local_bounds"]["complete"] = False
    state = GameState.model_validate(value)
    before = copy.deepcopy(state.model_dump())
    assert next_skills(state) == []
    assert state.model_dump() == before


def test_active_skill_owns_controls_and_missing_baritone_only_blocks_mining():
    value = fixture()
    value["skill"] = {
        "baritone_available": False,
        "active": False,
        "target_count": 0,
        "inventory_count": 0,
        "status": "idle",
        "phase": "idle",
        "elapsed_ticks": 0,
        "max_ticks": 0,
    }
    assert next_skills(GameState.model_validate(value)) == []
    set_inventory(value, {"oak_planks": 12})
    assert next_skills(GameState.model_validate(value))[0]["item"] == "minecraft:crafting_table"
    value["skill"].update(active=True, status="running")
    assert next_skills(GameState.model_validate(value)) == []


def with_remembered_table(value, position=None, *, baritone_available=True):
    value["skill"] = {
        "baritone_available": baritone_available,
        "active": False,
        "target_count": 6,
        "inventory_count": 6,
        "status": "completed",
        "phase": "done",
        "elapsed_ticks": 100,
        "max_ticks": 1200,
        "known_table": position or {"x": 1, "y": 79, "z": 0},
    }
    return value


def test_return_to_remembered_table_after_mining_does_not_gather_duplicate_table_resources():
    value = with_remembered_table(
        fixture({"oak_planks": 1, "wooden_pickaxe": 1, "stick": 6, "cobblestone": 6})
    )
    state = GameState.model_validate(value)
    before = copy.deepcopy(state.model_dump())
    options = next_skills(state, ["wood", "crafting_table", "wooden_pickaxe"])
    assert len(options) == 1
    option = options[0]
    assert option["item"] == "minecraft:stone_pickaxe"
    assert option["max_ticks"] == 1200
    assert option["known_table"] == {"x": 1, "y": 79, "z": 0}
    assert option["table_available"] and not option["table_carried"]
    assert option["table_navigation_required"]
    assert "table_route_unverified" in option["risks"]
    assert not option["path_validated"]
    assert option["nearest_target"] is None  # Memory never becomes a current observed block.
    assert "crafting_table" not in confirmed_milestones(state)
    assert state.model_dump() == before


def test_remembered_table_without_baritone_does_not_assume_a_navigation_route():
    value = with_remembered_table(
        fixture({"oak_planks": 1, "wooden_pickaxe": 1, "stick": 6, "cobblestone": 6}),
        baritone_available=False,
    )
    assert next_skills(GameState.model_validate(value)) == []
    set_inventory(value, {"oak_planks": 4, "wooden_pickaxe": 1, "stick": 6, "cobblestone": 6})
    option = next_skills(GameState.model_validate(value))[0]
    assert option["item"] == "minecraft:crafting_table"
    assert not option["table_navigation_required"]
    assert not option["table_available"]


def test_current_missing_table_cell_overrides_remembered_position():
    value = with_remembered_table(
        fixture({"oak_planks": 4, "wooden_pickaxe": 1, "stick": 6, "cobblestone": 6}),
        position={"x": 1, "y": 64, "z": 0},  # Actual sampled cell here is air.
    )
    option = next_skills(GameState.model_validate(value))[0]
    assert option["item"] == "minecraft:crafting_table"
    assert not option["table_available"]
    assert not option["table_navigation_required"]


def test_carried_table_needs_no_navigation_even_with_remote_table_memory():
    value = with_remembered_table(
        fixture({"crafting_table": 1, "wooden_pickaxe": 1, "stick": 6, "cobblestone": 6})
    )
    option = next_skills(GameState.model_validate(value))[0]
    assert option["item"] == "minecraft:stone_pickaxe"
    assert option["max_ticks"] == 240
    assert not option["table_navigation_required"]
    assert "table_route_unverified" not in option["risks"]


def test_nether_goal_requires_actual_ready_live_player_in_nether_dimension():
    value = fixture({"stone_pickaxe": 1, "stone_axe": 1})
    state = GameState.model_validate(value)
    assert goal_reached(state, "early_game")
    assert not goal_reached(state, "nether")
    assert "nether" not in confirmed_milestones(state)
    value["dimension"] = "minecraft:the_nether"
    state = GameState.model_validate(value)
    assert goal_reached(state, "nether")
    assert confirmed_milestones(state)[-1] == "nether"
    assert next_skills(state, goal="nether") == []
    value["player"]["health"] = 0.0
    assert not goal_reached(GameState.model_validate(value), "nether")
    value.update(world_ready=False, player=None)
    assert not goal_reached(GameState.model_validate(value), "nether")


def test_new_goal_preserves_early_prerequisites_and_never_invents_unimplemented_skills():
    state = GameState.model_validate(fixture())
    assert next_skills(state, goal="nether")[0]["item"] == "minecraft:oak_log"
    assert next_skills(state, goal="nether")[0]["target_count"] == 3
    finished_early = GameState.model_validate(fixture({"stone_pickaxe": 1, "stone_axe": 1}))
    assert next_skills(finished_early, goal="nether") == []  # Await advertised new capabilities.
    assert not goal_reached(finished_early, "nether")
    with pytest.raises(ValueError, match="Run goal"):
        goal_reached(state, "unknown")
    with pytest.raises(ValueError, match="Run goal"):
        next_skills(state, goal="unknown")


def portal_state(items=None, *, baritone_available=True, capabilities=None):
    value = fixture(items)
    value["skill"] = {
        "baritone_available": baritone_available,
        "active": False,
        "target_count": 0,
        "inventory_count": 0,
        "status": "idle",
        "phase": "idle",
        "elapsed_ticks": 0,
        "max_ticks": 0,
        "capabilities": [PORTAL_CAPABILITY] if capabilities is None else capabilities,
    }
    return value


def test_verified_capability_and_matching_seed_prioritize_direct_nether_route_without_tools():
    state = GameState.model_validate(portal_state())
    before = copy.deepcopy(state.model_dump())
    options = next_skills(
        state, goal="nether", declared_seed=PORTAL_ROUTE_SEED, route="ruined_portal"
    )
    assert len(options) == 1
    candidate = options[0]
    assert candidate["skill"] == "portal"
    assert candidate["item"] == "minecraft:nether_portal"
    assert candidate["target_count"] == 1 and candidate["max_ticks"] == 1800
    assert candidate["target"] == {"x": 48, "y": 68, "z": 48}
    assert candidate["postcondition"] == "dimension"
    assert candidate["expected_dimension"] == "minecraft:the_nether"
    assert candidate["owned_supplies"] == {
        "minecraft:obsidian": 0,
        "minecraft:flint_and_steel": 0,
        "minecraft:fire_charge": 0,
    }
    assert "required_chest_supplies_unverified" in candidate["risks"]
    assert candidate["route_evidence"]["requires_live_verification"]
    assert candidate["nearest_target"] is None and not candidate["path_validated"]
    assert state.model_dump() == before
    assert not goal_reached(state, "nether")


@pytest.mark.parametrize("seed", [None, "", "42", "-1950884254474434484"])
def test_portal_seed_route_is_never_assumed_for_unknown_or_other_seed(seed):
    state = GameState.model_validate(portal_state({"stone_pickaxe": 1, "stone_axe": 1}))
    assert next_skills(state, goal="nether", declared_seed=seed, route="ruined_portal") == []


@pytest.mark.parametrize(
    "capabilities", [[], ["craft:minecraft:bucket"], ["portal:minecraft:obsidian"]]
)
def test_portal_route_requires_the_exact_advertised_capability(capabilities):
    state = GameState.model_validate(
        portal_state({"stone_pickaxe": 1, "stone_axe": 1}, capabilities=capabilities)
    )
    assert (
        next_skills(state, goal="nether", declared_seed=PORTAL_ROUTE_SEED, route="ruined_portal")
        == []
    )


def test_portal_route_does_not_override_early_goal_or_missing_pathfinder():
    state = GameState.model_validate(portal_state())
    assert (
        next_skills(state, goal="early_game", declared_seed=PORTAL_ROUTE_SEED)[0]["skill"] == "mine"
    )
    state = GameState.model_validate(
        portal_state({"stone_pickaxe": 1, "stone_axe": 1}, baritone_available=False)
    )
    assert (
        next_skills(state, goal="nether", declared_seed=PORTAL_ROUTE_SEED, route="ruined_portal")
        == []
    )


def test_owned_portal_supplies_are_evidence_but_never_prove_entry():
    value = portal_state({"obsidian": 1, "fire_charge": 2})
    state = GameState.model_validate(value)
    candidate = next_skills(
        state, goal="nether", declared_seed=PORTAL_ROUTE_SEED, route="ruined_portal"
    )[0]
    assert "required_chest_supplies_unverified" not in candidate["risks"]
    assert "seed_route_requires_in_game_verification" in candidate["risks"]
    assert not goal_reached(state, "nether")
    value["skill"].update(
        kind="portal",
        item="minecraft:nether_portal",
        status="completed",
        phase="done",
        inventory_count=1,
    )
    assert not goal_reached(GameState.model_validate(value), "nether")
    value["dimension"] = "minecraft:the_nether"
    state = GameState.model_validate(value)
    assert goal_reached(state, "nether")
    assert (
        next_skills(state, goal="nether", declared_seed=PORTAL_ROUTE_SEED, route="ruined_portal")
        == []
    )


def test_portal_verified_steps_remain_confirmed_after_materials_are_consumed():
    value = portal_state({"stone_pickaxe": 1, "stone_axe": 1})
    value["skill"].update(
        kind="portal",
        item="minecraft:nether_portal",
        status="running",
        active=True,
        phase="enter_portal",
        verified_steps=[
            "portal_lit",
            "portal_repaired",
            "ignition_acquired",
            "obsidian_acquired",
            "chest_looted",
            "portal_located",
        ],
    )
    state = GameState.model_validate(value)
    assert confirmed_milestones(state) == [
        "stone_tools",
        "portal_located",
        "chest_looted",
        "obsidian_acquired",
        "ignition_acquired",
        "portal_repaired",
        "portal_lit",
    ]
    assert not goal_reached(state, "nether")
    value["dimension"] = "minecraft:the_nether"
    assert confirmed_milestones(GameState.model_validate(value))[-1] == "nether"


@pytest.mark.parametrize("ignition", ["flint_and_steel", "fire_charge"])
def test_current_portal_supplies_confirm_only_acquisition_without_portal_claims(ignition):
    state = GameState.model_validate(fixture({"obsidian": 1, ignition: 1}))
    assert confirmed_milestones(state) == ["obsidian_acquired", "ignition_acquired"]


def test_empty_or_default_portal_steps_do_not_manufacture_progress_from_phase_or_status():
    value = portal_state()
    value["skill"].update(
        kind="portal",
        item="minecraft:nether_portal",
        status="completed",
        phase="portal_lit",
        inventory_count=1,
    )
    assert confirmed_milestones(GameState.model_validate(value)) == []
    value["skill"]["verified_steps"] = ["portal_lit"]
    assert confirmed_milestones(GameState.model_validate(value)) == ["portal_lit"]
    value["player"]["health"] = 0.0
    assert confirmed_milestones(GameState.model_validate(value)) == []
    value.update(world_ready=False, player=None)
    assert confirmed_milestones(GameState.model_validate(value)) == []


def test_inventory_and_verified_step_evidence_are_deduplicated_in_milestone_order():
    value = portal_state({"obsidian": 2, "fire_charge": 1})
    value["skill"]["verified_steps"] = ["ignition_acquired", "obsidian_acquired", "chest_looted"]
    assert confirmed_milestones(GameState.model_validate(value)) == [
        "chest_looted",
        "obsidian_acquired",
        "ignition_acquired",
    ]
