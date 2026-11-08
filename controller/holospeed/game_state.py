"""Bounded Minecraft state and pure helpers for normal-mechanics skills.

These helpers describe the observed world; they never send controls, modify the
world, or substitute an inferred inventory for the client's actual inventory.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from itertools import combinations
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RegistryId = Annotated[str, Field(pattern=r"^[a-z0-9_.-]+:[a-z0-9_./-]+$", max_length=160)]
Capability = Annotated[
    str, Field(pattern=r"^[a-z][a-z_]{0,31}:[a-z0-9_.-]+:[a-z0-9_./-]+$", max_length=194)
]
Coordinate = Annotated[float, Field(ge=-30_000_000, le=30_000_000)]
BlockCoordinate = Annotated[int, Field(ge=-30_000_000, le=29_999_999)]
Tick = Annotated[int, Field(ge=0, le=2**63 - 1)]
PortalStep = Literal[
    "portal_located",
    "chest_looted",
    "obsidian_acquired",
    "ignition_acquired",
    "portal_repaired",
    "portal_lit",
]
PORTAL_STEPS = (
    "portal_located",
    "chest_looted",
    "obsidian_acquired",
    "ignition_acquired",
    "portal_repaired",
    "portal_lit",
)


class StateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Vec3(StateModel):
    x: Coordinate
    y: Coordinate
    z: Coordinate

    def distance_to(self, other: Vec3) -> float:
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))


class BlockPos(StateModel):
    x: BlockCoordinate
    y: BlockCoordinate
    z: BlockCoordinate

    @property
    def key(self) -> tuple[int, int, int]:
        return self.x, self.y, self.z

    @property
    def center(self) -> Vec3:
        return Vec3(x=self.x + 0.5, y=self.y + 0.5, z=self.z + 0.5)


class Velocity(StateModel):
    x: float = Field(ge=-256, le=256)
    y: float = Field(ge=-256, le=256)
    z: float = Field(ge=-256, le=256)


class ItemStack(StateModel):
    item: RegistryId
    count: int = Field(ge=0, le=99)

    @model_validator(mode="after")
    def empty_stack(self) -> ItemStack:
        if (self.item == "minecraft:air") != (self.count == 0):
            raise ValueError("Empty stacks must have item minecraft:air and count zero")
        return self


class InventorySlot(ItemStack):
    slot: int = Field(ge=0, le=40)


class ScreenSlot(ItemStack):
    slot: int = Field(ge=0, le=255)
    x: int = Field(ge=-32768, le=32767)
    y: int = Field(ge=-32768, le=32767)


class ScreenState(StateModel):
    type: str = Field(min_length=1, max_length=160)
    title: str = Field(max_length=512)
    sync_id: int | None = Field(ge=0, le=2**31 - 1)
    slots: list[ScreenSlot] = Field(default_factory=list, max_length=256)
    cursor: ItemStack | None = None
    width: int | None = Field(default=None, ge=1, le=32768)
    height: int | None = Field(default=None, ge=1, le=32768)
    origin_x: int | None = Field(default=None, ge=-32768, le=32767)
    origin_y: int | None = Field(default=None, ge=-32768, le=32767)
    slots_truncated: bool = False

    @model_validator(mode="after")
    def unique_slots(self) -> ScreenState:
        if len({slot.slot for slot in self.slots}) != len(self.slots):
            raise ValueError("Duplicate screen slots")
        return self


class PlayerState(StateModel):
    position: Vec3
    eye_position: Vec3
    eye_height: float = Field(ge=0, le=4)
    yaw: float = Field(ge=-1e12, le=1e12)
    pitch: float = Field(ge=-90, le=90)
    velocity: Velocity
    on_ground: bool
    health: float = Field(ge=0, le=1024)
    hunger: int = Field(ge=0, le=20)
    selected_slot: int = Field(ge=0, le=8)
    block_interaction_range: float = Field(default=4.5, ge=0, le=64)


class BlockState(StateModel):
    position: BlockPos
    id: RegistryId
    solid: bool
    fluid: RegistryId
    hardness: float = Field(ge=-1, le=1_000_000)
    full_cube: bool | None = None


class EntityState(StateModel):
    id: int = Field(ge=0, le=2**31 - 1)
    type: RegistryId
    position: Vec3
    health: float | None = Field(default=None, ge=0, le=1_000_000)
    stack: ItemStack | None = None


class CrosshairState(StateModel):
    type: Literal["miss", "block", "entity"]
    position: Vec3 | None = None
    block_pos: BlockPos | None = None
    block_id: RegistryId | None = None
    entity_id: int | None = Field(default=None, ge=0, le=2**31 - 1)
    distance: float | None = Field(default=None, ge=0, le=1024)

    @model_validator(mode="after")
    def target_shape(self) -> CrosshairState:
        if self.type == "block" and (self.block_pos is None or self.block_id is None):
            raise ValueError("Block hit requires block_pos and block_id")
        if self.type == "entity" and self.entity_id is None:
            raise ValueError("Entity hit requires entity_id")
        if self.type != "block" and (self.block_pos is not None or self.block_id is not None):
            raise ValueError("Only block hits have block fields")
        if self.type != "entity" and self.entity_id is not None:
            raise ValueError("Only entity hits have entity_id")
        return self


class LocalBounds(StateModel):
    min: BlockPos
    max: BlockPos
    complete: bool

    @model_validator(mode="after")
    def valid_volume(self) -> LocalBounds:
        spans = tuple(high - low + 1 for low, high in zip(self.min.key, self.max.key))
        if any(span <= 0 for span in spans) or math.prod(spans) > 729:
            raise ValueError("Local bounds must describe at most 729 cells")
        return self

    def contains(self, position: BlockPos) -> bool:
        return all(
            low <= point <= high
            for low, point, high in zip(self.min.key, position.key, self.max.key)
        )


class SkillState(StateModel):
    """Uncached status of a bounded, normal-mechanics client skill."""

    baritone_available: bool
    active: bool
    skill_id: str | None = Field(default=None, min_length=1, max_length=128)
    kind: Literal["mine", "craft", "portal"] | None = None
    item: RegistryId | None = None
    target_count: int = Field(ge=0, le=64)
    inventory_count: int = Field(ge=0, le=41 * 99)
    status: Literal["idle", "running", "completed", "failed", "cancelled"]
    reason: str | None = Field(default=None, max_length=300)
    phase: str = Field(max_length=64)
    elapsed_ticks: int = Field(ge=0, le=1800)
    max_ticks: int = Field(ge=0, le=1800)
    known_table: BlockPos | None = None
    capabilities: list[Capability] = Field(default_factory=list, max_length=64)
    verified_steps: list[PortalStep] = Field(default_factory=list, max_length=6)
    keys: (
        list[
            Literal["forward", "back", "left", "right", "jump", "sneak", "sprint", "attack", "use"]
        ]
        | None
    ) = Field(default=None, max_length=9)

    @model_validator(mode="after")
    def unique_keys(self) -> SkillState:
        if self.keys is not None and len(self.keys) != len(set(self.keys)):
            raise ValueError("Duplicate skill keys")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("Duplicate skill capabilities")
        if len(self.verified_steps) != len(set(self.verified_steps)):
            raise ValueError("Duplicate verified portal steps")
        return self


class GameState(StateModel):
    """The Fabric `state` request's flat, read-only `game_state` response."""

    type: Literal["game_state"] = "game_state"
    id: str = Field(min_length=1, max_length=128)
    tick: Tick
    world_ready: bool
    screen_open: bool
    dimension: RegistryId | None
    player: PlayerState | None
    inventory: list[InventorySlot] = Field(default_factory=list, max_length=41)
    screen: ScreenState | None = None
    crosshair: CrosshairState
    nearby_blocks: list[BlockState] = Field(default_factory=list, max_length=512)
    nearby_entities: list[EntityState] = Field(default_factory=list, max_length=128)
    nearby_radius: int = Field(default=8, ge=0, le=8)
    nearby_blocks_truncated: bool = False
    nearby_entities_truncated: bool = False
    local_blocks: list[BlockState] = Field(default_factory=list, max_length=729)
    local_bounds: LocalBounds | None = None
    sampled_at_ms: Tick
    cache_age_ms: int = Field(ge=0, le=86_400_000)
    skill: SkillState | None = None

    @model_validator(mode="after")
    def consistent_snapshot(self) -> GameState:
        if self.world_ready and (self.player is None or self.dimension is None):
            raise ValueError("Ready world requires player and dimension")
        if self.screen is not None and not self.screen_open:
            raise ValueError("Screen data requires an open screen")
        if len({slot.slot for slot in self.inventory}) != len(self.inventory):
            raise ValueError("Duplicate inventory slots")
        if len({entity.id for entity in self.nearby_entities}) != len(self.nearby_entities):
            raise ValueError("Duplicate entity IDs")
        for blocks in (self.local_blocks, self.nearby_blocks):
            if len({block.position.key for block in blocks}) != len(blocks):
                raise ValueError("Duplicate block positions")
        if self.local_blocks and self.local_bounds is None:
            raise ValueError("Local cells require their sampled bounds")
        if self.local_bounds:
            if any(not self.local_bounds.contains(block.position) for block in self.local_blocks):
                raise ValueError("Local block lies outside sampled bounds")
            volume = math.prod(
                high - low + 1
                for low, high in zip(self.local_bounds.min.key, self.local_bounds.max.key)
            )
            if self.local_bounds.complete and len(self.local_blocks) != volume:
                raise ValueError("Complete local sample is missing cells")
        return self

    def is_fresh(self, now_ms: int, max_age_ms: int = 1000) -> bool:
        """Unknown-world, future-dated, or cached stale state cannot be actionable."""
        if max_age_ms < 0:
            raise ValueError("Maximum age must be nonnegative")
        age = now_ms - self.sampled_at_ms
        return self.world_ready and 0 <= age <= max_age_ms and self.cache_age_ms <= max_age_ms


# Exact vanilla 1.21.1 item IDs: substring matching would count unrelated items
# or omit nether stems. Bamboo blocks are not members of Minecraft's logs tag.
OVERWORLD_WOODS = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry")
LOG_ITEMS = frozenset(
    f"minecraft:{prefix}{wood}_{suffix}"
    for wood, suffixes in [
        *((wood, ("log", "wood")) for wood in OVERWORLD_WOODS),
        ("crimson", ("stem", "hyphae")),
        ("warped", ("stem", "hyphae")),
    ]
    for prefix in ("", "stripped_")
    for suffix in suffixes
)
PLANK_ITEMS = frozenset(
    f"minecraft:{wood}_planks" for wood in (*OVERWORLD_WOODS, "crimson", "warped", "bamboo")
)
PICKAXE_ITEMS = frozenset(
    f"minecraft:{material}_pickaxe"
    for material in ("wooden", "stone", "iron", "golden", "diamond", "netherite")
)


@dataclass(frozen=True, slots=True)
class AimAdjustment:
    """A bounded relative camera change plus the exact desired orientation."""

    yaw_delta: float
    pitch_delta: float
    target_yaw: float
    target_pitch: float
    distance: float
    needs_more_turn: bool


def wrap_degrees(angle: float) -> float:
    """Return the shortest signed Minecraft yaw in [-180, 180)."""
    if not math.isfinite(angle):
        raise ValueError("Angle must be finite")
    return (angle + 180.0) % 360.0 - 180.0


def _bounded_angle_limit(value: float, upper: float, name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= upper:
        raise ValueError(f"{name} must be greater than zero and at most {upper}")
    return value


def item_counts(snapshot: GameState) -> dict[str, int]:
    """Count actual player inventory only, avoiding duplicate GUI-slot copies."""
    counts: dict[str, int] = {}
    for slot in snapshot.inventory:
        if slot.count:
            counts[slot.item] = counts.get(slot.item, 0) + slot.count
    return counts


def known_items(snapshot: GameState) -> frozenset[str]:
    return frozenset(item_counts(snapshot))


def count_item(snapshot: GameState, item: str) -> int:
    return item_counts(snapshot).get(item, 0)


def log_count(snapshot: GameState) -> int:
    return sum(count for item, count in item_counts(snapshot).items() if item in LOG_ITEMS)


def relative_aim(
    player: PlayerState,
    target: Vec3,
    *,
    max_yaw_delta: float = 90,
    max_pitch_delta: float = 60,
) -> AimAdjustment:
    """Aim from eye position using Minecraft yaw (+Z=0, +X=-90) and pitch.

    Output defaults fit the existing Action bounds. A larger turn must use a
    fresh state for another step; `needs_more_turn` makes that explicit.
    """
    _bounded_angle_limit(max_yaw_delta, 180, "Yaw limit")
    _bounded_angle_limit(max_pitch_delta, 180, "Pitch limit")
    dx = target.x - player.eye_position.x
    dy = target.y - player.eye_position.y
    dz = target.z - player.eye_position.z
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    horizontal = math.hypot(dx, dz)
    target_yaw = (
        wrap_degrees(math.degrees(math.atan2(-dx, dz))) if horizontal else wrap_degrees(player.yaw)
    )
    target_pitch = -math.degrees(math.atan2(dy, horizontal)) if distance else player.pitch
    yaw = wrap_degrees(target_yaw - player.yaw)
    pitch = target_pitch - player.pitch
    return AimAdjustment(
        yaw_delta=max(-max_yaw_delta, min(max_yaw_delta, yaw)),
        pitch_delta=max(-max_pitch_delta, min(max_pitch_delta, pitch)),
        target_yaw=target_yaw,
        target_pitch=target_pitch,
        distance=distance,
        needs_more_turn=abs(yaw) > max_yaw_delta or abs(pitch) > max_pitch_delta,
    )


def aim_at_block(player: PlayerState, block: BlockState) -> AimAdjustment:
    return relative_aim(player, block.position.center)


def local_block_map(snapshot: GameState) -> dict[tuple[int, int, int], BlockState]:
    """Unknown cells remain absent; discovery-only nearby blocks never imply air."""
    return {block.position.key: block for block in snapshot.local_blocks}


def _ray_clear(origin: Vec3, target: Vec3, cells: dict[tuple[int, int, int], BlockState]) -> bool:
    """Conservative voxel traversal, including every cell touched at corners."""
    start = (origin.x, origin.y, origin.z)
    end = (target.x, target.y, target.z)
    current = [math.floor(value) for value in start]
    destination = tuple(math.floor(value) for value in end)
    directions = [end[axis] - start[axis] for axis in range(3)]
    steps = [1 if value > 0 else -1 if value < 0 else 0 for value in directions]
    deltas = [abs(1 / value) if value else math.inf for value in directions]
    crossings = [
        (current[axis] + (1 if steps[axis] > 0 else 0) - start[axis]) / directions[axis]
        if directions[axis]
        else math.inf
        for axis in range(3)
    ]

    def clear(position: tuple[int, int, int]) -> bool:
        if position == destination:
            return True
        cell = cells.get(position)
        return cell is not None and not cell.solid

    if not clear(tuple(current)):
        return False
    for _ in range(256):
        if tuple(current) == destination:
            return True
        crossing = min(crossings)
        if not math.isfinite(crossing) or crossing > 1:
            return False
        axes = [axis for axis, value in enumerate(crossings) if abs(value - crossing) < 1e-10]
        # At an edge/corner, checking all touched neighbors avoids passing a
        # diagonal ray between solid blocks due to floating-point tie ordering.
        for count in range(1, len(axes) + 1):
            for selected in combinations(axes, count):
                neighbor = current.copy()
                for axis in selected:
                    neighbor[axis] += steps[axis]
                if not clear(tuple(neighbor)):
                    return False
        for axis in axes:
            current[axis] += steps[axis]
            crossings[axis] += deltas[axis]
    return False


def find_reachable_block(
    snapshot: GameState,
    block_ids: Collection[str] = LOG_ITEMS,
    *,
    max_reach: float | None = None,
) -> BlockState | None:
    """Nearest breakable target with known clear eye-to-center reach.

    This is conservative: unknown cells and partial collision shapes obstruct
    the ray. A target's visible corner may be reachable even when its center is
    not. The helper does not assert server permission or successful mining.
    """
    if not snapshot.world_ready or snapshot.player is None or snapshot.screen_open:
        return None
    if snapshot.player.health <= 0:
        return None
    reach = snapshot.player.block_interaction_range if max_reach is None else max_reach
    if isinstance(reach, bool) or not math.isfinite(reach) or not 0 <= reach <= 64:
        raise ValueError("Reach must be finite and between zero and 64")
    reach = min(reach, snapshot.player.block_interaction_range)
    cells = local_block_map(snapshot)
    candidates = {block.position.key: block for block in snapshot.nearby_blocks}
    candidates.update(cells)
    candidates = {
        position: block
        for position, block in candidates.items()
        if block.id in block_ids and block.hardness >= 0
    }
    eye = snapshot.player.eye_position
    for block in sorted(
        candidates.values(),
        key=lambda item: (eye.distance_to(item.position.center), item.position.key),
    ):
        if eye.distance_to(block.position.center) <= reach and _ray_clear(
            eye, block.position.center, cells
        ):
            return block
    return None


_DANGEROUS_BLOCKS = frozenset(
    f"minecraft:{name}"
    for name in (
        "lava",
        "fire",
        "soul_fire",
        "magma_block",
        "cactus",
        "campfire",
        "soul_campfire",
        "sweet_berry_bush",
        "powder_snow",
        "wither_rose",
        "cobweb",
        "pointed_dripstone",
    )
)


def walkable_neighbors(snapshot: GameState, origin: BlockPos | None = None) -> list[BlockPos]:
    """Known flat cardinal steps with two clear cells and a full-cube floor.

    No jumping, drops, swimming, or inferred collision geometry. Missing cells
    or unknown full-cube flags are excluded. This is a neighborhood query, not
    a movement executor or a guarantee about dynamic entities.
    """
    if not snapshot.world_ready or snapshot.player is None or snapshot.screen_open:
        return []
    if not snapshot.player.on_ground or snapshot.player.health <= 0:
        return []
    if origin is None:
        # A slab/path height is not a full-block flat step; its collision shape
        # needs a richer traversal rule than this conservative neighborhood.
        if abs(snapshot.player.position.y - round(snapshot.player.position.y)) > 1e-4:
            return []
        origin = BlockPos(
            **{
                axis: math.floor(getattr(snapshot.player.position, axis))
                for axis in ("x", "y", "z")
            }
        )
    cells = local_block_map(snapshot)
    result = []
    for dx, dz in ((0, 1), (1, 0), (0, -1), (-1, 0)):
        position = (origin.x + dx, origin.y, origin.z + dz)
        foot = cells.get(position)
        head = cells.get((position[0], position[1] + 1, position[2]))
        floor = cells.get((position[0], position[1] - 1, position[2]))
        if any(
            block is None or block.fluid != "minecraft:empty" or block.id in _DANGEROUS_BLOCKS
            for block in (foot, head, floor)
        ):
            continue
        if not foot.solid and not head.solid and floor.solid and floor.full_cube is True:
            result.append(BlockPos(x=position[0], y=position[1], z=position[2]))
    return result
