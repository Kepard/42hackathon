"""Validate the Fabric state boundary and geometry without a game or model API."""

import copy
import math

import pytest
from holospeed.game_state import (
    BlockPos,
    GameState,
    Vec3,
    aim_at_block,
    count_item,
    find_reachable_block,
    item_counts,
    known_items,
    log_count,
    relative_aim,
    walkable_neighbors,
)
from pydantic import ValidationError


def block(x, y, z, name="air", *, solid=False, fluid="empty", full_cube=False):
    return {
        "position": {"x": x, "y": y, "z": z},
        "id": f"minecraft:{name}",
        "solid": solid,
        "fluid": f"minecraft:{fluid}",
        "hardness": 2.0 if solid else 0.0,
        "full_cube": full_cube,
    }


def sample():
    local = [
        block(x, y, z, "stone" if y == 63 else "air", solid=y == 63, full_cube=y == 63)
        for x in range(-2, 3)
        for y in range(63, 68)
        for z in range(-2, 3)
    ]
    return {
        "type": "game_state",
        "id": "request-1",
        "tick": 200,
        "sampled_at_ms": 100_000,
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
        "inventory": [{"slot": 0, "item": "minecraft:oak_log", "count": 3}],
        "screen": None,
        "crosshair": {"type": "miss"},
        "local_blocks": local,
        "local_bounds": {
            "min": {"x": -2, "y": 63, "z": -2},
            "max": {"x": 2, "y": 67, "z": 2},
            "complete": True,
        },
        "nearby_blocks": [],
        "nearby_entities": [],
        "nearby_radius": 8,
        "nearby_blocks_truncated": False,
        "nearby_entities_truncated": False,
    }


def put_block(payload, replacement):
    position = replacement["position"]
    payload["local_blocks"] = [
        replacement if item["position"] == position else item for item in payload["local_blocks"]
    ]


def with_log():
    payload = sample()
    target = block(0, 65, 2, "oak_log", solid=True, full_cube=True)
    put_block(payload, target)
    payload["nearby_blocks"] = [copy.deepcopy(target)]
    return payload


def test_flat_wire_snapshot_round_trips_and_accepts_world_not_ready():
    value = GameState.model_validate(sample())
    assert GameState.model_validate_json(value.model_dump_json()) == value
    empty = GameState.model_validate(
        {
            "type": "game_state",
            "id": "request-menu",
            "tick": 0,
            "world_ready": False,
            "screen_open": True,
            "dimension": None,
            "player": None,
            "crosshair": {"type": "miss"},
            "sampled_at_ms": 100,
            "cache_age_ms": 0,
            "screen": {
                "type": "TitleScreen",
                "title": "Minecraft",
                "sync_id": None,
                "width": 854,
                "height": 480,
                "slots": [],
                "cursor": None,
            },
        }
    )
    assert empty.player is None
    assert not empty.is_fresh(100)
    assert find_reachable_block(empty) is None
    assert walkable_neighbors(empty) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(extra_hidden_state=True),
        lambda data: data["player"].update(health=float("nan")),
        lambda data: data["player"]["position"].update(x=float("inf")),
        lambda data: data["player"].update(hunger="20"),
        lambda data: data["player"].update(hunger=True),
        lambda data: data["inventory"][0].update(count=100),
        lambda data: data["inventory"][0].update(item="oak_log"),
        lambda data: data["inventory"][0].update(item="minecraft:air"),
        lambda data: data["inventory"].append(data["inventory"][0].copy()),
        lambda data: data["local_blocks"].pop(),
        lambda data: data["local_blocks"][0]["position"].update(x=100),
        lambda data: data.update(player=None),
        lambda data: data.update(crosshair={"type": "block", "block_id": "minecraft:stone"}),
        lambda data: data.update(crosshair={"type": "miss", "entity_id": 4}),
        lambda data: data.update(nearby_blocks=data["local_blocks"] * 5),
        lambda data: data.update(sampled_at_ms=-1),
    ],
)
def test_invalid_unbounded_or_ambiguous_state_is_rejected(mutate):
    payload = sample()
    mutate(payload)
    with pytest.raises(ValidationError):
        GameState.model_validate(payload)


def test_inventory_counts_do_not_double_count_screen_slots_or_ground_drops():
    payload = sample()
    payload["inventory"] += [
        {"slot": 9, "item": "minecraft:oak_log", "count": 4},
        {"slot": 10, "item": "minecraft:stripped_warped_hyphae", "count": 2},
        {"slot": 11, "item": "minecraft:bamboo_block", "count": 8},
        {"slot": 12, "item": "minecraft:air", "count": 0},
        {"slot": 40, "item": "minecraft:crafting_table", "count": 1},
    ]
    payload["screen_open"] = True
    payload["screen"] = {
        "type": "InventoryScreen",
        "title": "Crafting",
        "sync_id": 0,
        "slots": [{"slot": 36, "item": "minecraft:oak_log", "count": 3, "x": 8, "y": 142}],
        "cursor": {"item": "minecraft:oak_log", "count": 2},
        "slots_truncated": False,
        "width": 854,
        "height": 480,
        "origin_x": 339,
        "origin_y": 157,
    }
    payload["nearby_entities"] = [
        {
            "id": 4,
            "type": "minecraft:item",
            "position": {"x": 1, "y": 64, "z": 1},
            "stack": {"item": "minecraft:oak_log", "count": 10},
        }
    ]
    state = GameState.model_validate(payload)
    assert count_item(state, "minecraft:oak_log") == 7
    assert log_count(state) == 9
    assert count_item(state, "minecraft:stone_pickaxe") == 0
    assert "minecraft:crafting_table" in known_items(state)
    assert "minecraft:air" not in item_counts(state)


@pytest.mark.parametrize(
    ("dx", "dz", "expected"),
    [
        (0, 1, 0),
        (1, 0, -90),
        (0, -1, -180),
        (-1, 0, 90),
    ],
)
def test_minecraft_cardinal_yaw(dx, dz, expected):
    player = GameState.model_validate(sample()).player
    aim = relative_aim(
        player,
        Vec3(x=player.eye_position.x + dx, y=player.eye_position.y, z=player.eye_position.z + dz),
        max_yaw_delta=180,
    )
    assert aim.target_yaw == pytest.approx(expected)
    assert aim.yaw_delta == pytest.approx(expected)
    assert aim.pitch_delta == 0
    assert not aim.needs_more_turn


def test_aim_wraps_across_180_and_uses_eyes_instead_of_feet():
    payload = sample()
    payload["player"]["yaw"] = 179.0
    player = GameState.model_validate(payload).player
    angle = math.radians(-179)
    aim = relative_aim(
        player,
        Vec3(
            x=player.eye_position.x - math.sin(angle),
            y=player.eye_position.y,
            z=player.eye_position.z + math.cos(angle),
        ),
    )
    assert aim.yaw_delta == pytest.approx(2)
    assert aim.pitch_delta == 0
    state = GameState.model_validate(with_log())
    target = find_reachable_block(state)
    assert aim_at_block(state.player, target).target_pitch > 0  # log center is below the eyes


def test_aim_limits_large_turns_and_handles_vertical_and_zero_distance():
    player = GameState.model_validate(sample()).player
    upward = relative_aim(player, Vec3(x=0.5, y=68, z=0.5))
    assert upward.target_pitch == -90
    assert upward.pitch_delta == -60
    assert upward.needs_more_turn
    behind = relative_aim(player, Vec3(x=0.5, y=65.62, z=-1))
    assert behind.yaw_delta == -90
    assert behind.needs_more_turn
    identical = relative_aim(player, player.eye_position)
    assert identical.yaw_delta == identical.pitch_delta == identical.distance == 0
    with pytest.raises(ValueError):
        relative_aim(player, player.eye_position, max_yaw_delta=float("nan"))


def test_reachable_logs_require_clear_known_cells_and_actual_player_reach():
    payload = with_log()
    state = GameState.model_validate(payload)
    before = state.model_dump()
    target = find_reachable_block(state)
    assert target.position.key == (0, 65, 2)
    assert state.model_dump() == before
    assert find_reachable_block(state, max_reach=1) is None
    payload["player"]["block_interaction_range"] = 1.0
    assert find_reachable_block(GameState.model_validate(payload), max_reach=64) is None
    payload["player"]["block_interaction_range"] = 4.5
    put_block(payload, block(0, 65, 1, "oak_leaves", solid=True))
    assert find_reachable_block(GameState.model_validate(payload)) is None
    put_block(payload, block(0, 65, 1))
    payload["local_blocks"] = [
        cell for cell in payload["local_blocks"] if cell["position"] != {"x": 0, "y": 65, "z": 1}
    ]
    payload["local_bounds"]["complete"] = False
    assert find_reachable_block(GameState.model_validate(payload)) is None


def test_incomplete_sample_is_usable_only_where_the_ray_is_known():
    payload = with_log()
    payload["local_blocks"].pop(0)  # Missing cell is far from eye-to-log ray.
    payload["local_bounds"]["complete"] = False
    assert find_reachable_block(GameState.model_validate(payload)) is not None


def test_diagonal_ray_does_not_slip_between_solid_corner_cells():
    payload = sample()
    payload["player"]["eye_position"]["y"] = 65.5
    payload["player"]["eye_height"] = 1.5
    put_block(payload, block(2, 65, 2, "oak_log", solid=True, full_cube=True))
    assert find_reachable_block(GameState.model_validate(payload)) is not None
    put_block(payload, block(1, 65, 0, "stone", solid=True, full_cube=True))
    assert find_reachable_block(GameState.model_validate(payload)) is None


def test_screen_dead_player_and_unbreakable_target_are_not_reachable():
    payload = with_log()
    payload["screen_open"] = True
    assert find_reachable_block(GameState.model_validate(payload)) is None
    payload["screen_open"] = False
    payload["player"]["health"] = 0.0
    assert find_reachable_block(GameState.model_validate(payload)) is None
    payload["player"]["health"] = 20.0
    for cell in payload["local_blocks"] + payload["nearby_blocks"]:
        if cell["id"] == "minecraft:oak_log":
            cell["hardness"] = -1.0
    assert find_reachable_block(GameState.model_validate(payload)) is None


def test_walkable_neighbors_exclude_hazards_partial_floor_obstacles_and_unknowns():
    payload = sample()
    assert {position.key for position in walkable_neighbors(GameState.model_validate(payload))} == {
        (0, 64, 1),
        (1, 64, 0),
        (0, 64, -1),
        (-1, 64, 0),
    }
    put_block(payload, block(0, 64, 1, "water", fluid="water"))
    put_block(payload, block(1, 63, 0, "oak_fence", solid=True, full_cube=False))
    put_block(payload, block(0, 63, -1, "magma_block", solid=True, full_cube=True))
    put_block(payload, block(-1, 65, 0, "stone", solid=True, full_cube=True))
    assert walkable_neighbors(GameState.model_validate(payload)) == []
    payload = sample()
    payload["local_blocks"] = [
        cell for cell in payload["local_blocks"] if cell["position"] != {"x": 0, "y": 65, "z": 1}
    ]
    payload["local_bounds"]["complete"] = False
    assert BlockPos(x=0, y=64, z=1) not in walkable_neighbors(GameState.model_validate(payload))


def test_snapshot_freshness_cannot_be_reset_by_a_recent_cache_reply():
    payload = sample()
    state = GameState.model_validate(payload)
    assert state.is_fresh(100_500)
    assert not state.is_fresh(101_001)
    assert not state.is_fresh(99_999)
    payload["cache_age_ms"] = 1100
    assert not GameState.model_validate(payload).is_fresh(100_100)
    with pytest.raises(ValueError):
        state.is_fresh(100_100, max_age_ms=-1)


def test_skill_status_is_typed_and_bounded_without_changing_cached_state_age():
    payload = sample()
    payload["skill"] = {
        "baritone_available": True,
        "active": True,
        "skill_id": "skill-1",
        "kind": "mine",
        "item": "minecraft:oak_log",
        "target_count": 5,
        "inventory_count": 3,
        "status": "running",
        "reason": None,
        "phase": "mining",
        "elapsed_ticks": 15,
        "max_ticks": 1800,
    }
    state = GameState.model_validate(payload)
    assert state.skill.active
    assert state.skill.target_count == 5
    assert state.skill.keys is None  # Unavailable readback is distinct from verified no input.
    assert state.skill.known_table is None
    assert state.skill.capabilities == []
    payload["skill"]["capabilities"] = ["craft:minecraft:bucket", "smelt:minecraft:iron_ingot"]
    assert GameState.model_validate(payload).skill.capabilities == [
        "craft:minecraft:bucket",
        "smelt:minecraft:iron_ingot",
    ]
    for capabilities in (
        ["craft:bucket"],
        ["craft:minecraft:bucket"] * 2,
        "craft:minecraft:bucket",
    ):
        payload["skill"]["capabilities"] = capabilities
        with pytest.raises(ValidationError):
            GameState.model_validate(payload)
    payload["skill"]["capabilities"] = []
    payload["skill"]["known_table"] = {"x": 10, "y": 67, "z": -3}
    assert GameState.model_validate(payload).skill.known_table == BlockPos(x=10, y=67, z=-3)
    payload["skill"]["known_table"] = {"x": "10", "y": 67, "z": -3}
    with pytest.raises(ValidationError):
        GameState.model_validate(payload)
    payload["skill"]["known_table"] = None
    payload["skill"]["keys"] = []
    assert GameState.model_validate(payload).skill.keys == []
    payload["skill"]["keys"] = ["forward", "attack"]
    assert GameState.model_validate(payload).skill.keys == ["forward", "attack"]
    for keys in (["forward", "forward"], ["inventory"], "forward"):
        payload["skill"]["keys"] = keys
        with pytest.raises(ValidationError):
            GameState.model_validate(payload)
    payload["skill"]["keys"] = None
    assert not state.is_fresh(102_000)
    payload["skill"]["max_ticks"] = 1801
    with pytest.raises(ValidationError):
        GameState.model_validate(payload)


def test_walkable_neighbors_do_not_infer_flat_steps_from_a_slab_height():
    payload = sample()
    payload["player"]["position"]["y"] = 64.5
    assert walkable_neighbors(GameState.model_validate(payload)) == []


def test_verified_portal_steps_are_optional_bounded_unique_and_strictly_typed():
    payload = sample()
    payload["skill"] = {
        "baritone_available": True,
        "active": False,
        "target_count": 0,
        "inventory_count": 0,
        "status": "idle",
        "phase": "idle",
        "elapsed_ticks": 0,
        "max_ticks": 0,
    }
    assert GameState.model_validate(payload).skill.verified_steps == []
    steps = [
        "portal_located",
        "chest_looted",
        "obsidian_acquired",
        "ignition_acquired",
        "portal_repaired",
        "portal_lit",
    ]
    payload["skill"]["verified_steps"] = steps
    assert GameState.model_validate(payload).skill.verified_steps == steps
    for invalid in (
        steps + ["portal_lit"],
        ["portal_lit", "portal_lit"],
        ["nether"],
        ["chest_opened"],
        "portal_lit",
        [True],
        None,
    ):
        payload["skill"]["verified_steps"] = invalid
        with pytest.raises(ValidationError):
            GameState.model_validate(payload)


@pytest.mark.parametrize("kind", ["smelt", "cast_portal"])
def test_from_scratch_skill_state_preserves_owned_furnace_and_verified_cast_steps(kind):
    payload = sample()
    payload["skill"] = {
        "baritone_available": True,
        "active": True,
        "kind": kind,
        "target_count": 1,
        "inventory_count": 0,
        "status": "running",
        "phase": "working",
        "elapsed_ticks": 30,
        "max_ticks": 1800,
        "known_furnace": {"x": 2, "y": 64, "z": -1},
        "verified_steps": ["water_collected", "lava_located", "portal_built", "portal_lit"],
    }
    state = GameState.model_validate(payload)
    assert state.skill.kind == kind
    assert state.skill.known_furnace == BlockPos(x=2, y=64, z=-1)
    assert state.skill.verified_steps == payload["skill"]["verified_steps"]
    payload["skill"]["known_furnace"]["x"] = "2"
    with pytest.raises(ValidationError):
        GameState.model_validate(payload)
