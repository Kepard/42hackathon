import pytest

from mc_observatory.evaluator import HOSTILES, LADDER, LadderEvaluator


def stack(item, count=1):
    return {"id": "minecraft:" + item, "count": count}


def test_empty():
    result = LadderEvaluator().update({})
    assert result["completed"] == [] and result["fraction"] == 0
    assert result["next"] == "log"


def test_order_and_persistence():
    evaluator = LadderEvaluator()
    assert evaluator.update({"inventory": [stack("wooden_pickaxe")]})["completed"] == []
    assert evaluator.update({"inventory": [stack("oak_log")]})["completed"] == ["log"]
    assert evaluator.update({"inventory": [stack("crafting_table")]})["completed"] == list(LADDER[:3])
    assert evaluator.update({})["completed"] == list(LADDER[:3])


@pytest.mark.parametrize("category,item", [("picked_up", "birch_log"), ("crafted", "stripped_oak_wood")])
def test_historical_obtained(category, item):
    assert LadderEvaluator().update({"stats": {category: {"minecraft:" + item: 1}}})["completed"] == ["log"]


def test_mined_log_requires_collection_but_other_rungs_keep_mining_credit():
    evaluator = LadderEvaluator()
    mined = {"stats": {"mined": {"minecraft:spruce_log": 1, "minecraft:cobblestone": 1}}}
    result = evaluator.update(mined)
    assert result["completed"] == [] and "log" not in result["observed"]
    assert "cobblestone" in result["observed"]
    assert evaluator.update({})["completed"] == []
    result = evaluator.update({"stats": {"picked_up": {"minecraft:spruce_log": 1}}})
    assert result["completed"] == ["log"]
    assert evaluator.update({})["completed"] == ["log"]


def test_full_ladder_stats_and_nether():
    evaluator = LadderEvaluator()
    items = ["oak_log", "crafting_table", "wooden_pickaxe", "cobblestone", "stone_pickaxe", "furnace", "raw_iron", "iron_ingot", "flint_and_steel", "obsidian"]
    state = {"stats": {"crafted": {"minecraft:" + item: 1 for item in items}, "killed": {"minecraft:zombie": 1}}, "dimension": "minecraft:the_nether"}
    result = evaluator.update(state)
    assert result["completed"] == list(LADDER) and result["fraction"] == 1 and result["next"] is None
    assert evaluator.update({}) == result


@pytest.mark.parametrize("mob", ["cow", "pig", "villager", "iron_golem"])
def test_passive_kills_do_not_count(mob):
    assert "kill_hostile_mob" not in LadderEvaluator().update({"stats": {"killed": {"minecraft:" + mob: 20}}})["observed"]


@pytest.mark.parametrize("mob", sorted(HOSTILES))
def test_hostile_kills(mob):
    assert "kill_hostile_mob" in LadderEvaluator().update({"stats": {"killed": {mob: 1}}})["observed"]


def test_portal_requires_contact_not_obsidian_or_flint_use():
    evaluator = LadderEvaluator()
    result = evaluator.update({"inventory": [stack("obsidian"), stack("flint_and_steel")], "stats": {"used": {"minecraft:flint_and_steel": 3}}})
    assert "portal" not in result["observed"]
    assert "portal" in evaluator.update({"portal_entered": True})["observed"]
    assert "portal" in LadderEvaluator().update({"portal_lit": True})["observed"]


def test_nether_advancement_is_persistent_evidence():
    result = LadderEvaluator().update({"advancements": ["minecraft:story/enter_the_nether"]})
    assert result["observed"] == ["portal", "enter_nether"] and result["completed"] == []


def test_zero_counts_and_non_minecraft_log():
    result = LadderEvaluator().update({"stats": {"picked_up": {"minecraft:oak_log": 0, "other:oak_log": 1}}})
    assert result["completed"] == []


def test_world_change_and_explicit_reset():
    evaluator = LadderEvaluator()
    evaluator.update({"world_id": "a", "inventory": [stack("oak_log")]})
    assert evaluator.update({"world_id": "b"})["completed"] == []
    evaluator.update({"inventory": [stack("oak_log")]})
    evaluator.reset()
    assert evaluator.result()["completed"] == []


def test_loading_state_does_not_pollute_evidence():
    evaluator = LadderEvaluator()
    assert evaluator.update({"in_world": False, "inventory": [stack("oak_log")]})["completed"] == []


def test_created_obsidian_is_scoring_only_latched_and_world_scoped():
    evaluator = LadderEvaluator()
    assert evaluator.update({"world_id": "a", "obsidian_created": True})["observed"] == ["obsidian"]
    assert not evaluator.has_item("minecraft:obsidian")
    assert evaluator.update({"world_id": "a"})["observed"] == ["obsidian"]
    assert evaluator.update({"world_id": "b"})["observed"] == []
    for value in (False, None, 1, "true"):
        assert LadderEvaluator().update({"obsidian_created": value})["observed"] == []
    assert LadderEvaluator().update({"in_world": False, "obsidian_created": True})["observed"] == []


@pytest.mark.parametrize("state", [None, [], {"inventory": {}}, {"inventory": [{}]}, {"inventory": [stack("oak_log", -1)]}, {"stats": []}, {"stats": {"crafted": {"x": True}}}, {"stats": {"mined": {"x": 1.5}}}, {"advancements": "abc"}])
def test_invalid_state_is_atomic(state):
    evaluator = LadderEvaluator()
    with pytest.raises(ValueError):
        evaluator.update(state)
    assert evaluator.result()["completed"] == []
