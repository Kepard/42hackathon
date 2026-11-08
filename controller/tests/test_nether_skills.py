"""From-scratch dependency accounting; no fixture assumes completed world actions."""

import copy

import pytest
from holospeed.game_state import GameState
from holospeed.skills import (
    BUILD_BLOCK_RESERVE,
    CAST_CAPABILITY,
    CRAFT_ALLOWLIST,
    FROM_SCRATCH_MILESTONES,
    MINE_ALLOWLIST,
    PLANK_ITEMS,
    PORTAL_CAPABILITY,
    PORTAL_ROUTE_SEED,
    SMELT_ALLOWLIST,
    STONE_MATERIALS,
    confirmed_milestones,
    next_skills,
)
from test_skills import apply_inventory_postcondition, cell, consume, fixture, set_inventory

TARGET = {"x": 4, "y": 63, "z": 2}


def nether_fixture(items=None):
    value = fixture(items)
    value["skill"] = {
        "baritone_available": True,
        "active": False,
        "target_count": 0,
        "inventory_count": 0,
        "status": "idle",
        "phase": "idle",
        "elapsed_ticks": 0,
        "max_ticks": 0,
        "capabilities": [
            *(f"mine:{item}" for item in sorted(MINE_ALLOWLIST)),
            *(f"craft:{item}" for item in sorted(CRAFT_ALLOWLIST)),
            *(f"smelt:{item}" for item in sorted(SMELT_ALLOWLIST)),
            CAST_CAPABILITY,
            PORTAL_CAPABILITY,
        ],
    }
    value["nearby_blocks"].extend(
        [
            cell(3, 63, 0, "iron_ore", True),
            cell(3, 63, 1, "gravel", True),
            cell(3, 64, 2, "dirt", True),
        ]
    )
    return value


def options(value, history=(), target=TARGET):
    return next_skills(GameState.model_validate(value), history, goal="nether", route_target=target)


def apply_recipe(items, candidate):
    item = candidate["item"]
    increase = candidate["target_count"] - items.get(item, 0)
    if candidate["skill"] == "smelt":
        consume(items, {"minecraft:raw_iron"}, increase)
        if items.get("minecraft:coal", 0):
            consume(items, {"minecraft:coal"}, 1)
        else:
            consume(items, PLANK_ITEMS, 3)  # Four ingots use three spare planks.
        items[item] = candidate["target_count"]
    elif candidate["skill"] == "craft" and item == "minecraft:furnace":
        consume(items, STONE_MATERIALS, 8)
        items[item] = candidate["target_count"]
    elif candidate["skill"] == "craft" and item == "minecraft:bucket":
        consume(items, {"minecraft:iron_ingot"}, 3)
        items[item] = candidate["target_count"]
    elif candidate["skill"] == "craft" and item == "minecraft:flint_and_steel":
        consume(items, {"minecraft:iron_ingot"}, 1)
        consume(items, {"minecraft:flint"}, 1)
        items[item] = candidate["target_count"]
    else:
        apply_inventory_postcondition(items, candidate)


def test_zero_inventory_reaches_full_casting_prerequisites_without_loot_or_optional_axe():
    value = nether_fixture()
    inventory, history, sequence = {}, [], []
    for _ in range(24):
        set_inventory(value, inventory)
        state = GameState.model_validate(value)
        history.extend(
            name for name in confirmed_milestones(state, "nether") if name not in history
        )
        candidate = options(value, history)[0]
        assert candidate["route"] == "from_scratch" and candidate["loot_allowed"] is False
        sequence.append((candidate["skill"], candidate["item"], candidate["target_count"]))
        if candidate["skill"] == "cast_portal":
            assert candidate["target"] == TARGET
            assert candidate["building_blocks_owned"] >= BUILD_BLOCK_RESERVE
            assert inventory["minecraft:iron_ingot"] == 0
            assert inventory["minecraft:bucket"] == inventory["minecraft:flint_and_steel"] == 1
            break
        apply_recipe(inventory, candidate)
    else:
        pytest.fail("Prerequisite graph repeated instead of reaching portal construction")
    assert sequence == [
        ("mine", "minecraft:oak_log", 3),
        ("craft", "minecraft:oak_planks", 12),
        ("craft", "minecraft:crafting_table", 1),
        ("craft", "minecraft:stick", 4),
        ("craft", "minecraft:wooden_pickaxe", 1),
        ("mine", "minecraft:cobblestone", 11),
        ("craft", "minecraft:stone_pickaxe", 1),
        ("craft", "minecraft:furnace", 1),
        ("mine", "minecraft:raw_iron", 4),
        ("mine", "minecraft:flint", 1),
        ("smelt", "minecraft:iron_ingot", 4),
        ("craft", "minecraft:bucket", 1),
        ("craft", "minecraft:flint_and_steel", 1),
        ("mine", "minecraft:dirt", 9),
        ("cast_portal", "minecraft:nether_portal", 1),
    ]
    assert history == list(FROM_SCRATCH_MILESTONES[:9])
    assert "minecraft:stone_axe" not in inventory and "minecraft:coal" not in inventory
    assert "portal_built" not in history and "nether" not in history


def ready_inventory(**overrides):
    return {
        "crafting_table": 1,
        "stone_pickaxe": 1,
        "bucket": 1,
        "flint_and_steel": 1,
        "dirt": 9,
        **overrides,
    }


@pytest.mark.parametrize(
    "owned,expected",
    [
        ({}, 11),
        ({"cobblestone": 2}, 11),
        ({"cobblestone": 2, "cobbled_deepslate": 3}, 8),
        ({"furnace": 1}, 3),
        ({"iron_ingot": 4}, 3),
        ({"bucket": 1, "flint_and_steel": 1}, 3),
    ],
)
def test_combined_stone_visit_credits_existing_recipe_materials_and_finished_items(owned, expected):
    value = nether_fixture({"crafting_table": 1, "wooden_pickaxe": 1, "stick": 2, **owned})
    candidate = next(item for item in options(value) if item["item"] == "minecraft:cobblestone")
    assert candidate["skill"] == "mine"
    assert candidate["target_count"] == expected


def test_combined_stone_visit_crafts_both_recipes_without_another_mine_and_preserves_mold():
    inventory = {
        "minecraft:crafting_table": 1,
        "minecraft:wooden_pickaxe": 1,
        "minecraft:stick": 2,
        "minecraft:cobblestone": 11,
        "minecraft:dirt": 9,
    }
    value = nether_fixture(inventory)
    candidate = options(value)[0]
    assert candidate["item"] == "minecraft:stone_pickaxe"
    apply_recipe(inventory, candidate)
    set_inventory(value, inventory)
    candidate = options(value)[0]
    assert candidate["skill"] == "craft" and candidate["item"] == "minecraft:furnace"
    apply_recipe(inventory, candidate)
    assert inventory["minecraft:cobblestone"] == 0
    assert inventory["minecraft:dirt"] == 9


def test_existing_stone_without_a_pickaxe_crafts_tool_before_furnace_mining():
    value = nether_fixture({"crafting_table": 1, "stick": 2, "cobblestone": 3})
    candidate = options(value)[0]
    assert candidate["skill"] == "craft" and candidate["item"] == "minecraft:stone_pickaxe"


def test_missing_furnace_capability_does_not_prefetch_unusable_furnace_materials():
    value = nether_fixture({"crafting_table": 1, "stick": 2, "wooden_pickaxe": 1})
    value["skill"]["capabilities"].remove("craft:minecraft:furnace")
    assert options(value)[0]["target_count"] == 3


def test_failed_combined_mine_can_upgrade_from_partial_real_materials():
    value = nether_fixture({"crafting_table": 1, "stick": 2, "wooden_pickaxe": 1, "cobblestone": 4})
    value["skill"].update(
        kind="mine", item="minecraft:cobblestone", status="failed", phase="failed"
    )
    candidate = options(value)[0]
    assert candidate["skill"] == "craft" and candidate["item"] == "minecraft:stone_pickaxe"


def test_default_route_never_chooses_ruined_portal_even_with_its_seed_and_capability():
    state = GameState.model_validate(nether_fixture())
    new = next_skills(state, goal="nether", declared_seed=PORTAL_ROUTE_SEED, route_target=TARGET)
    assert new[0]["skill"] == "mine"
    legacy = next_skills(
        state, goal="nether", declared_seed=PORTAL_ROUTE_SEED, route="ruined_portal"
    )
    assert legacy[0]["skill"] == "portal"


def test_iron_budget_credits_already_crafted_bucket_and_ignition_without_remining_four():
    value = nether_fixture(
        {
            "crafting_table": 1,
            "stone_pickaxe": 1,
            "furnace": 1,
            "bucket": 1,
            "raw_iron": 1,
            "flint": 1,
            "coal": 1,
        }
    )
    candidate = options(value)[0]
    assert (candidate["skill"], candidate["target_count"]) == ("smelt", 1)
    set_inventory(
        value,
        {
            "crafting_table": 1,
            "stone_pickaxe": 1,
            "furnace": 1,
            "flint_and_steel": 1,
            "iron_ingot": 3,
        },
    )
    assert options(value)[0]["item"] == "minecraft:bucket"


def test_furnace_consumption_is_not_credited_as_portal_mold_material():
    value = nether_fixture(ready_inventory(dirt=0, cobblestone=8))
    candidate = options(value)[0]
    assert candidate["item"] == "minecraft:dirt" and candidate["target_count"] == 1
    set_inventory(value, ready_inventory(dirt=0, cobblestone=0))
    assert options(value)[0]["target_count"] == 9
    set_inventory(value, ready_inventory(dirt=4, cobblestone=5))
    assert options(value)[0]["skill"] == "cast_portal"


def test_unobserved_iron_is_only_a_bounded_advertised_search_not_a_verified_target():
    value = nether_fixture({"crafting_table": 1, "stone_pickaxe": 1, "furnace": 1})
    value["nearby_blocks"] = [
        block for block in value["nearby_blocks"] if block["id"] != "minecraft:iron_ore"
    ]
    candidate = options(value)[0]
    assert candidate["item"] == "minecraft:raw_iron"
    assert candidate["nearest_target"] is None and not candidate["path_validated"]
    assert candidate["search_allowed"] is True and candidate["max_ticks"] == 1200
    value["skill"]["capabilities"].remove("mine:minecraft:raw_iron")
    assert options(value) == []


def test_casting_needs_exact_capability_target_and_current_prerequisites():
    value = nether_fixture(ready_inventory())
    assert options(value, target=None) == []
    value["skill"]["capabilities"].remove(CAST_CAPABILITY)
    assert options(value) == []
    value["skill"]["capabilities"].append(CAST_CAPABILITY)
    with pytest.raises(ValueError):
        options(value, target={"x": "4", "y": 63, "z": 2})
    before = copy.deepcopy(value)
    assert options(value)[0]["postcondition"] == "dimension"
    assert value == before
    set_inventory(value, ready_inventory(flint_and_steel=0, fire_charge=1, iron_ingot=1, flint=1))
    assert options(value)[0]["item"] == "minecraft:flint_and_steel"


def test_water_bucket_can_start_casting_but_lava_only_requires_recovery_not_new_iron():
    value = nether_fixture(ready_inventory(bucket=0, water_bucket=1))
    assert options(value)[0]["skill"] == "cast_portal"
    set_inventory(value, ready_inventory(bucket=0, lava_bucket=1))
    assert options(value) == []


def test_from_scratch_milestones_require_current_items_or_verified_cast_steps():
    value = nether_fixture(
        {
            "stone_pickaxe": 1,
            "furnace": 1,
            "raw_iron": 4,
            "iron_ingot": 4,
            "water_bucket": 1,
            "fire_charge": 1,
        }
    )
    state = GameState.model_validate(value)
    assert confirmed_milestones(state, "nether") == [
        "stone_pickaxe",
        "furnace",
        "iron_mined",
        "iron_smelted",
        "bucket",
        "water_collected",
    ]
    value["skill"].update(kind="cast_portal", phase="portal_built", status="completed")
    assert "portal_built" not in confirmed_milestones(GameState.model_validate(value), "nether")
    value["skill"]["verified_steps"] = [
        "water_collected",
        "lava_located",
        "portal_built",
        "portal_lit",
    ]
    assert confirmed_milestones(GameState.model_validate(value), "nether")[-4:] == [
        "water_collected",
        "lava_located",
        "portal_built",
        "portal_lit",
    ]
    assert "nether" not in confirmed_milestones(GameState.model_validate(value), "nether")


def test_own_reachable_furnace_allows_cancelled_smelt_resume_without_duplicate_recipe():
    value = nether_fixture(
        {"crafting_table": 1, "stone_pickaxe": 1, "raw_iron": 4, "flint": 1, "coal": 1}
    )
    furnace = cell(1, 64, 0, "furnace", True)
    value["local_blocks"] = [
        furnace if block["position"] == furnace["position"] else block
        for block in value["local_blocks"]
    ]
    value["nearby_blocks"].append(furnace)
    value["skill"]["known_furnace"] = furnace["position"]
    assert options(value)[0]["skill"] == "smelt"
    value["skill"]["known_furnace"] = None
    assert options(value)[0]["item"] == "minecraft:cobblestone"


@pytest.mark.parametrize("unusable", ["crimson_planks", "warped_stem", "oak_wood"])
def test_smelting_does_not_credit_fuel_the_executor_will_not_burn(unusable):
    value = nether_fixture(
        {
            "crafting_table": 1,
            "stone_pickaxe": 1,
            "furnace": 1,
            "raw_iron": 4,
            "flint": 1,
            unusable: 3,
        }
    )
    assert options(value)[0]["item"] == "minecraft:coal"


@pytest.mark.parametrize(
    "target",
    [
        {"x": 0, "y": -64, "z": 0},
        {"x": 0, "y": 314, "z": 0},
        {"x": -29_999_969, "y": 64, "z": 0},
        {"x": 0, "y": 64, "z": 29_999_968},
    ],
)
def test_casting_target_reserves_world_border_and_height_room_for_full_frame(target):
    with pytest.raises(ValueError, match="construction bounds"):
        options(nether_fixture(ready_inventory()), target=target)


def test_owned_open_furnace_resumes_pending_contents_without_mining_duplicate_iron():
    value = nether_fixture({"crafting_table": 1, "stone_pickaxe": 1, "flint": 1})
    value["skill"].update(
        kind="smelt", status="cancelled", phase="cancelled", known_furnace={"x": 1, "y": 64, "z": 0}
    )
    value.update(
        screen_open=True,
        screen={
            "type": "FurnaceScreen",
            "title": "Furnace",
            "sync_id": 2,
            "cursor": {"item": "minecraft:air", "count": 0},
            "slots": [
                {"slot": 0, "item": "minecraft:raw_iron", "count": 3, "x": 0, "y": 0},
                {"slot": 1, "item": "minecraft:coal", "count": 1, "x": 0, "y": 0},
                {"slot": 2, "item": "minecraft:iron_ingot", "count": 1, "x": 0, "y": 0},
            ],
        },
    )
    candidate = options(value)[0]
    assert (candidate["skill"], candidate["target_count"]) == ("smelt", 4)
    assert candidate["id"] == "resume-smelt-minecraft-iron_ingot-4"
    assert candidate["resume_owned_furnace"] and candidate["pending_furnace_items"] == 4
    assert "iron_smelted" not in confirmed_milestones(GameState.model_validate(value), "nether")
    value["skill"]["known_furnace"] = None
    assert options(value) == []
    value["skill"]["known_furnace"] = {"x": 1, "y": 64, "z": 0}
    value["screen"]["slots"][2]["item"] = "minecraft:gold_ingot"
    assert options(value) == []


def test_closer_unowned_furnace_does_not_hide_reachable_owned_furnace():
    value = nether_fixture(
        {"crafting_table": 1, "stone_pickaxe": 1, "raw_iron": 4, "flint": 1, "coal": 1}
    )
    owned, other = cell(2, 64, 0, "furnace", True), cell(1, 64, 1, "furnace", True)
    replacements = {tuple(block["position"].values()): block for block in (owned, other)}
    value["local_blocks"] = [
        replacements.get(tuple(block["position"].values()), block)
        for block in value["local_blocks"]
    ]
    value["nearby_blocks"].extend([owned, other])
    value["skill"]["known_furnace"] = owned["position"]
    assert options(value)[0]["skill"] == "smelt"
