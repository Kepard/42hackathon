"""Pure prerequisite planning for bounded, inventory-verified Minecraft goals.

Candidates are alternatives for the next prerequisite, not a script to execute
blindly. Replan from a fresh GameState after every skill's inventory postcondition.
All targets are total counts of one exact inventory item, matching SkillController.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .game_state import (
    LOG_ITEMS,
    OVERWORLD_WOODS,
    PICKAXE_ITEMS,
    PLANK_ITEMS,
    PORTAL_STEPS,
    BlockPos,
    GameState,
    find_reachable_block,
    item_counts,
)

TABLE = "minecraft:crafting_table"
STICK = "minecraft:stick"
WOOD_PICK = "minecraft:wooden_pickaxe"
STONE_PICK = "minecraft:stone_pickaxe"
STONE_AXE = "minecraft:stone_axe"
FURNACE = "minecraft:furnace"
RAW_IRON = "minecraft:raw_iron"
IRON_INGOT = "minecraft:iron_ingot"
BUCKET = "minecraft:bucket"
WATER_BUCKET = "minecraft:water_bucket"
FLINT = "minecraft:flint"
FLINT_AND_STEEL = "minecraft:flint_and_steel"
COAL = "minecraft:coal"
DIRT = "minecraft:dirt"
CAST_CAPABILITY = "cast_portal:minecraft:nether_portal"
BUILD_BLOCK_RESERVE = 9
WOOD_FUELS = frozenset(
    {f"minecraft:{wood}_planks" for wood in (*OVERWORLD_WOODS, "bamboo")}
    | {f"minecraft:{prefix}{wood}_log" for wood in OVERWORLD_WOODS for prefix in ("", "stripped_")}
)
STONE_MATERIALS = frozenset(
    {"minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:blackstone"}
)
MINE_LOGS = frozenset(f"minecraft:{wood}_log" for wood in OVERWORLD_WOODS)
CRAFT_PLANKS = frozenset(f"minecraft:{wood}_planks" for wood in OVERWORLD_WOODS)
MINE_SOURCES = {
    "minecraft:cobblestone": frozenset({"minecraft:stone", "minecraft:cobblestone"}),
    "minecraft:cobbled_deepslate": frozenset(
        {"minecraft:deepslate", "minecraft:cobbled_deepslate"}
    ),
    RAW_IRON: frozenset({"minecraft:iron_ore", "minecraft:deepslate_iron_ore"}),
    COAL: frozenset({"minecraft:coal_ore", "minecraft:deepslate_coal_ore"}),
    FLINT: frozenset({"minecraft:gravel"}),
    DIRT: frozenset({DIRT, "minecraft:grass_block"}),
    **{item: frozenset({item}) for item in MINE_LOGS},
}
MINE_ALLOWLIST = frozenset(MINE_SOURCES) | {"minecraft:stone", "minecraft:deepslate"}
CRAFT_ALLOWLIST = CRAFT_PLANKS | {
    TABLE,
    STICK,
    WOOD_PICK,
    STONE_PICK,
    STONE_AXE,
    FURNACE,
    BUCKET,
    FLINT_AND_STEEL,
}
SMELT_ALLOWLIST = frozenset({IRON_INGOT})
MILESTONE_ORDER = (
    "wood",
    "crafting_table",
    "wooden_pickaxe",
    "stone_tools",
    *PORTAL_STEPS,
    "nether",
)
FROM_SCRATCH_MILESTONES = (
    "wood",
    "crafting_table",
    "wooden_pickaxe",
    "stone_pickaxe",
    "furnace",
    "iron_mined",
    "iron_smelted",
    "bucket",
    "flint_and_steel",
    "water_collected",
    "lava_located",
    "portal_built",
    "portal_lit",
    "nether",
)
NETHER_ROUTES = frozenset({"from_scratch", "ruined_portal"})
RUN_GOALS = frozenset({"early_game", "nether"})
PORTAL_ROUTE_SEED = "-1950884254474434485"
PORTAL_CAPABILITY = "portal:minecraft:nether_portal"
PORTAL_WAYPOINT = {"x": 48, "y": 68, "z": 48}
PORTAL_ROUTE_SOURCE = (
    "https://github.com/gigafrosxh/minecraft-ai-speedrunner/blob/main/"
    "src/main/java/dev/daniel/aispeedrunner/speedrun/SpeedrunStrategy.java"
)
_HANDLED_SCREENS = {"InventoryScreen", "CraftingScreen"}
_HOSTILES = {
    "minecraft:creeper",
    "minecraft:skeleton",
    "minecraft:stray",
    "minecraft:zombie",
    "minecraft:husk",
    "minecraft:drowned",
    "minecraft:spider",
    "minecraft:cave_spider",
    "minecraft:witch",
    "minecraft:slime",
    "minecraft:pillager",
    "minecraft:ravager",
}


def _crafting_table_available(state: GameState, counts: Mapping[str, int]) -> bool:
    if counts.get(TABLE, 0):
        return True  # The client skill places this using an ordinary interactBlock.
    if state.screen and state.screen.type == "CraftingScreen":
        return True
    # The craft skill can close an empty inventory screen before interacting.
    # Query the same geometry with that screen closed, without mutating state.
    geometry = state.model_copy(update={"screen_open": False, "screen": None})
    return find_reachable_block(geometry, {TABLE}) is not None


def confirmed_milestones(
    state: GameState, goal: str = "early_game", route: str = "from_scratch"
) -> list[str]:
    """Report inventory and client-verified evidence; never model claims.

    The caller may retain earlier confirmed milestones after their ingredients
    are consumed. A later tool does not manufacture an unobserved earlier event.
    Portal step memory comes only from the mod's world-scoped verification
    checks, so consuming obsidian or ignition does not erase proven acquisition.
    """
    if not state.world_ready or state.player is None or state.player.health <= 0:
        return []
    counts = item_counts(state)
    found = []
    if any(counts.get(item, 0) for item in LOG_ITEMS | PLANK_ITEMS):
        found.append("wood")
    if _crafting_table_available(state, counts):
        found.append("crafting_table")
    if counts.get(WOOD_PICK, 0):
        found.append("wooden_pickaxe")
    if counts.get(STONE_PICK, 0) and counts.get(STONE_AXE, 0):
        found.append("stone_tools")
    if state.skill:
        found.extend(state.skill.verified_steps)
    if counts.get("minecraft:obsidian", 0):
        found.append("obsidian_acquired")
    if counts.get("minecraft:flint_and_steel", 0) or counts.get("minecraft:fire_charge", 0):
        found.append("ignition_acquired")
    if state.dimension == "minecraft:the_nether":
        found.append("nether")
    if goal == "nether" and route == "from_scratch":
        for identifier, items in (
            ("stone_pickaxe", {STONE_PICK}),
            ("furnace", {FURNACE}),
            ("iron_mined", {RAW_IRON, "minecraft:iron_ore", "minecraft:deepslate_iron_ore"}),
            ("iron_smelted", {IRON_INGOT}),
            ("bucket", {BUCKET, WATER_BUCKET, "minecraft:lava_bucket"}),
            ("flint_and_steel", {FLINT_AND_STEEL}),
            ("water_collected", {WATER_BUCKET}),
        ):
            if any(counts.get(item, 0) for item in items):
                found.append(identifier)
        return [name for name in FROM_SCRATCH_MILESTONES if name in found]
    return [name for name in MILESTONE_ORDER if name in found]


def goal_reached(state: GameState, goal: str = "early_game") -> bool:
    """Confirm a goal from current client evidence, never from model claims."""
    if goal not in RUN_GOALS:
        raise ValueError("Run goal must be early_game or nether")
    milestone = "stone_tools" if goal == "early_game" else "nether"
    return milestone in confirmed_milestones(state)


def _plank_sources(plank: str) -> frozenset[str]:
    wood = plank.removeprefix("minecraft:").removesuffix("_planks")
    return frozenset(
        f"minecraft:{prefix}{wood}_{kind}"
        for prefix in ("", "stripped_")
        for kind in ("log", "wood")
    )


def _history(values: Iterable[str | Mapping[str, Any]]) -> list[str]:
    recognized = {value if isinstance(value, str) else value.get("id") for value in values}
    order = dict.fromkeys((*MILESTONE_ORDER, *FROM_SCRATCH_MILESTONES))
    return [name for name in order if name in recognized]


class _Planner:
    def __init__(self, state: GameState, completed: Iterable[str | Mapping[str, Any]]) -> None:
        self.state = state
        self.counts = item_counts(state)
        self.completed = _history(completed)
        self.table_local = _crafting_table_available(state, self.counts)
        self.known_table = state.skill.known_table if state.skill else None
        # This is world-scoped client memory of an actual placement/open, not a
        # current observation. Contradictory current cells take precedence.
        observed_table_cell = next(
            (
                block
                for block in (*state.local_blocks, *state.nearby_blocks)
                if self.known_table and block.position.key == self.known_table.key
            ),
            None,
        )
        remembered = self.known_table is not None and (
            observed_table_cell is None or observed_table_cell.id == TABLE
        )
        self.table_navigation = bool(
            not self.table_local and remembered and state.skill.baritone_available
        )
        self.table_available = self.table_local or self.table_navigation
        self.risks = self._risks()

    def _risks(self) -> list[str]:
        state = self.state
        result = []
        if state.player.health <= 8:
            result.append("low_health")
        if state.player.hunger <= 6:
            result.append("low_hunger")
        if state.local_bounds is None or not state.local_bounds.complete:
            result.append("local_map_incomplete")
        if state.nearby_blocks_truncated or state.nearby_entities_truncated:
            result.append("nearby_scan_truncated")
        if any(
            block.fluid in {"minecraft:lava", "minecraft:flowing_lava"}
            and state.player.position.distance_to(block.position.center) <= 5
            for block in (*state.local_blocks, *state.nearby_blocks)
        ):
            result.append("lava_within_5_blocks")
        for entity in sorted(state.nearby_entities, key=lambda item: item.id):
            if entity.type in _HOSTILES:
                code = "nearby_hostile:" + entity.type.removeprefix("minecraft:")
                if code not in result:
                    result.append(code)
                if len(result) >= 10:
                    break
        return result

    def evidence(self, item: str) -> list[dict[str, Any]]:
        """Known block/drop targets, with distance as evidence rather than a route claim."""
        player = self.state.player
        sources = MINE_SOURCES.get(item, frozenset({item}))
        blocks = {block.position.key: block for block in self.state.nearby_blocks}
        blocks.update({block.position.key: block for block in self.state.local_blocks})
        result = [
            {
                "kind": "block",
                "item": block.id,
                "position": block.position.model_dump(),
                "distance": round(player.position.distance_to(block.position.center), 3),
            }
            for block in blocks.values()
            if block.id in sources and block.hardness >= 0
        ]
        result.extend(
            {
                "kind": "drop",
                "item": entity.stack.item,
                "position": entity.position.model_dump(),
                "distance": round(player.position.distance_to(entity.position), 3),
                "count": entity.stack.count,
            }
            for entity in self.state.nearby_entities
            if entity.type == "minecraft:item"
            and entity.stack
            and entity.stack.item == item
            and entity.stack.count > 0
        )
        return sorted(
            result,
            key=lambda target: (
                target["distance"],
                target["kind"],
                tuple(target["position"].values()),
            ),
        )

    def candidate(self, skill: str, item: str, target: int, reason: str) -> dict[str, Any]:
        allowlist = {"mine": MINE_ALLOWLIST, "craft": CRAFT_ALLOWLIST, "smelt": SMELT_ALLOWLIST}
        if item not in allowlist.get(skill, ()):
            raise ValueError("Unsupported skill target")
        current = self.counts.get(item, 0)
        if not current < target <= 64:
            raise ValueError("A candidate must increase inventory to a bounded total")
        evidence = self.evidence(item) if skill == "mine" else []
        risks = self.risks.copy()
        if skill == "mine" and not evidence:
            risks.append("target_not_observed")
        requires_table = skill == "craft" and item in {
            WOOD_PICK,
            STONE_PICK,
            STONE_AXE,
            FURNACE,
            BUCKET,
        }
        navigate_table = requires_table and self.table_navigation
        if navigate_table:
            risks.append("table_route_unverified")
            reason += (
                " Return to the previously observed crafting table; its current presence "
                "and route must be verified by the client skill."
            )
        return {
            "id": f"{skill}-{item.replace(':', '-')}-{target}",
            "skill": skill,
            "item": item,
            "target_count": target,
            "max_ticks": 1200 if skill in {"mine", "smelt"} or navigate_table else 240,
            "reason": reason,
            "inventory_count": current,
            "missing_count": target - current,
            "nearest_target": evidence[0] if evidence else None,
            "observed_targets": len(evidence),
            "path_validated": False,
            "execution_mode": "navigate_mine_collect"
            if skill == "mine"
            else "normal_recipe_interactions"
            if skill == "craft"
            else "normal_furnace_interactions",
            "navigation_included": skill == "mine" or navigate_table,
            "initial_target_reach_required": False if skill == "mine" or navigate_table else None,
            "tool_requirement": (
                "stone_pickaxe_or_better"
                if item == RAW_IRON
                else "suitable_pickaxe"
                if item in {"minecraft:cobblestone", "minecraft:cobbled_deepslate", COAL}
                else "none"
            )
            if skill == "mine"
            else None,
            "risks": risks,
            "sample_tick": self.state.tick,
            "completed_milestones": self.completed.copy(),
            "requires_table": requires_table,
            "table_available": self.table_available,
            "table_carried": bool(self.counts.get(TABLE, 0)),
            "known_table": self.known_table.model_dump() if self.known_table else None,
            "table_navigation_required": navigate_table,
        }

    def mine(self, outputs: Iterable[str], deficit: int, reason: str) -> list[dict[str, Any]]:
        if self.state.screen_open:
            return []
        if self.state.skill and not self.state.skill.baritone_available:
            return []
        candidates = [
            self.candidate("mine", item, self.counts.get(item, 0) + deficit, reason)
            for item in sorted(outputs)
            if self.evidence(item)
        ]
        return sorted(
            candidates,
            key=lambda candidate: (candidate["nearest_target"]["distance"], candidate["id"]),
        )

    def portal_route(self, declared_seed: str | None) -> list[dict[str, Any]]:
        """A known-seed route is a hypothesis the advertised executor must verify.

        The nearby ruined portal avoids an unnecessary iron/bucket detour. Its
        chest remains unknown until opened normally, and its frame must be
        checked before placement. No route metadata is inserted into game state.
        """
        skill = self.state.skill
        if (
            declared_seed != PORTAL_ROUTE_SEED
            or self.state.dimension != "minecraft:overworld"
            or skill is None
            or not skill.baritone_available
            or PORTAL_CAPABILITY not in skill.capabilities
        ):
            return []
        obsidian = self.counts.get("minecraft:obsidian", 0)
        ignition = any(
            self.counts.get(item, 0)
            for item in ("minecraft:flint_and_steel", "minecraft:fire_charge")
        )
        risks = [*self.risks, "seed_route_requires_in_game_verification"]
        if obsidian < 1 or not ignition:
            risks.append("required_chest_supplies_unverified")
        return [
            {
                "id": "portal-fixed-seed-48-68-48",
                "skill": "portal",
                "item": "minecraft:nether_portal",
                "target_count": 1,
                "max_ticks": 1800,
                "target": PORTAL_WAYPOINT.copy(),
                "reason": (
                    "Use the fixed-seed ruined portal: inspect its actual chest and frame, "
                    "repair only confirmed gaps with owned obsidian, ignite with an owned item, "
                    "and enter. Success requires the actual Nether dimension."
                ),
                "postcondition": "dimension",
                "expected_dimension": "minecraft:the_nether",
                "route_evidence": {
                    "kind": "fixed_seed_route",
                    "declared_seed": declared_seed,
                    "source": PORTAL_ROUTE_SOURCE,
                    "expected_chest": {"x": 49, "y": 68, "z": 52},
                    "expected_missing_frame_block": {"x": 50, "y": 72, "z": 50},
                    "requires_live_verification": True,
                },
                "owned_supplies": {
                    "minecraft:obsidian": obsidian,
                    "minecraft:flint_and_steel": self.counts.get("minecraft:flint_and_steel", 0),
                    "minecraft:fire_charge": self.counts.get("minecraft:fire_charge", 0),
                },
                "nearest_target": None,
                "path_validated": False,
                "risks": risks,
                "sample_tick": self.state.tick,
                "completed_milestones": self.completed.copy(),
            }
        ]

    def ensure_planks(self, required: int) -> list[dict[str, Any]]:
        count = sum(self.counts.get(item, 0) for item in PLANK_ITEMS)
        missing = required - count
        if missing <= 0:
            return []
        candidates = []
        for plank in sorted(CRAFT_PLANKS):
            logs = sum(self.counts.get(item, 0) for item in _plank_sources(plank))
            if logs:
                batches = min(logs, math.ceil(missing / 4))
                target = self.counts.get(plank, 0) + batches * 4
                candidates.append(
                    self.candidate(
                        "craft",
                        plank,
                        target,
                        f"Convert available logs into {batches * 4} planks; remaining recipes need "
                        f"{required} combined planks and inventory currently has {count}.",
                    )
                )
        if candidates:
            return sorted(
                candidates, key=lambda candidate: (-candidate["missing_count"], candidate["id"])
            )
        logs_needed = math.ceil(missing / 4)
        return self.mine(
            MINE_LOGS,
            logs_needed,
            f"Gather {logs_needed} logs for the missing {missing} planks in the remaining "
            "recipe prerequisites; existing crafted items are already credited.",
        )

    def plan(self) -> list[dict[str, Any]]:
        missing_pick = int(not self.counts.get(STONE_PICK, 0))
        missing_axe = int(not self.counts.get(STONE_AXE, 0))
        if not missing_pick and not missing_axe:
            return []
        stone_needed = 3 * (missing_pick + missing_axe)
        stone_count = sum(self.counts.get(item, 0) for item in STONE_MATERIALS)
        needs_wood_pick = stone_count < stone_needed and not any(
            self.counts.get(item, 0) for item in PICKAXE_ITEMS
        )
        stick_needed = 2 * (missing_pick + missing_axe + int(needs_wood_pick))
        missing_sticks = max(0, stick_needed - self.counts.get(STICK, 0))
        stick_batches = math.ceil(missing_sticks / 4)
        plank_needed = (
            4 * int(not self.table_available) + 3 * int(needs_wood_pick) + 2 * stick_batches
        )
        plank_count = sum(self.counts.get(item, 0) for item in PLANK_ITEMS)
        if plank_count < plank_needed:
            return self.ensure_planks(plank_needed)
        if not self.table_available:
            return [
                self.candidate(
                    "craft", TABLE, 1, "Craft the missing table for the remaining tool recipes."
                )
            ]
        if missing_sticks:
            target = self.counts.get(STICK, 0) + stick_batches * 4
            return [
                self.candidate(
                    "craft",
                    STICK,
                    target,
                    f"Craft {stick_batches * 4} sticks for the remaining tools; {stick_needed} total "
                    "sticks are required before those recipes consume them.",
                )
            ]
        if needs_wood_pick:
            return [
                self.candidate(
                    "craft",
                    WOOD_PICK,
                    1,
                    "Craft a wooden pickaxe so normal survival mining can collect the missing stone materials.",
                )
            ]
        if stone_count < stone_needed:
            return self.mine(
                ("minecraft:cobblestone", "minecraft:cobbled_deepslate"),
                stone_needed - stone_count,
                f"Collect the missing {stone_needed - stone_count} stone-tool materials; inventory "
                f"already contains {stone_count} of the {stone_needed} required for remaining tools.",
            )
        target = STONE_PICK if missing_pick else STONE_AXE
        return [
            self.candidate(
                "craft",
                target,
                1,
                "Craft the missing stone pickaxe from available ingredients."
                if missing_pick
                else "Craft the missing stone axe from available ingredients to complete the tool pair.",
            )
        ]


class _NetherPlanner(_Planner):
    """Dependency accounting for a no-loot, full-frame survival route."""

    def supports(self, skill: str, item: str) -> bool:
        advertised = self.state.skill.capabilities if self.state.skill else []
        if advertised:
            return f"{skill}:{item}" in advertised
        # Older clients can still complete their original early-game skills.
        return (
            skill == "mine"
            and item in MINE_LOGS | {"minecraft:cobblestone", "minecraft:cobbled_deepslate"}
            or skill == "craft"
            and item in CRAFT_PLANKS | {TABLE, STICK, WOOD_PICK, STONE_PICK, STONE_AXE}
        )

    def mine(self, outputs: Iterable[str], deficit: int, reason: str) -> list[dict[str, Any]]:
        outputs = [item for item in outputs if self.supports("mine", item)]
        observed = super().mine(outputs, deficit, reason)
        if (
            observed
            or self.state.screen_open
            or not self.state.skill
            or not self.state.skill.baritone_available
        ):
            return observed
        # Baritone may search loaded chunks beyond the bounded model sample.
        # This is a bounded search, not a claim that an ore or path is present.
        return [
            {
                **self.candidate("mine", item, self.counts.get(item, 0) + deficit, reason),
                "search_allowed": True,
                "search_scope": "Bounded Baritone search of loaded world; target is not yet observed.",
            }
            for item in sorted(outputs)
        ]

    def craft(self, item: str, reason: str) -> list[dict[str, Any]]:
        return [self.candidate("craft", item, 1, reason)] if self.supports("craft", item) else []

    def furnace_available(self) -> bool:
        if self.counts.get(FURNACE, 0):
            return True
        known = self.state.skill.known_furnace if self.state.skill else None
        if known is None:
            return False
        # The furnace executor only reopens its own currently reachable furnace.
        reachable = find_reachable_block(self.state, {FURNACE}, target_position=known)
        return reachable is not None

    def resume_open_furnace(self) -> list[dict[str, Any]]:
        screen, skill = self.state.screen, self.state.skill
        if not screen or screen.type != "FurnaceScreen" or screen.slots_truncated:
            return []
        if (
            not skill
            or skill.kind != "smelt"
            or not skill.known_furnace
            or not self.supports("smelt", IRON_INGOT)
        ):
            return []
        if screen.cursor and screen.cursor.count:
            return []
        slots = {slot.slot: slot for slot in screen.slots if slot.slot in {0, 1, 2}}
        if 0 not in slots or 2 not in slots:
            return []
        iron_inputs = {RAW_IRON, "minecraft:iron_ore", "minecraft:deepslate_iron_ore"}
        if (
            slots[0].count
            and slots[0].item not in iron_inputs
            or slots[2].count
            and slots[2].item != IRON_INGOT
        ):
            return []
        has_bucket = any(
            self.counts.get(item, 0) for item in (BUCKET, WATER_BUCKET, "minecraft:lava_bucket")
        )
        target = 3 * int(not has_bucket) + int(not self.counts.get(FLINT_AND_STEEL, 0))
        missing = target - self.counts.get(IRON_INGOT, 0)
        pending = slots[0].count + slots[2].count
        if (
            missing <= 0
            or pending + sum(self.counts.get(item, 0) for item in iron_inputs) < missing
        ):
            return []
        candidate = self.candidate(
            "smelt",
            IRON_INGOT,
            target,
            "Resume the owned furnace; pending input/output are not acquired ingots. Reopen and verify server inventory before completion.",
        )
        candidate.update(
            id=f"resume-smelt-minecraft-iron_ingot-{target}",
            resume_owned_furnace=True,
            pending_furnace_items=pending,
            known_furnace=skill.known_furnace.model_dump(),
        )
        return [candidate]

    def plan_from_scratch(self, route_target: dict | None) -> list[dict[str, Any]]:
        counts = self.counts
        has_stone_pick = bool(counts.get(STONE_PICK, 0))
        stone_count = sum(counts.get(item, 0) for item in STONE_MATERIALS)
        need_wood_pick = (
            not has_stone_pick
            and stone_count < 3
            and not any(counts.get(item, 0) for item in PICKAXE_ITEMS)
        )
        need_stone_pick = not has_stone_pick
        has_bucket = any(
            counts.get(item, 0) for item in (BUCKET, WATER_BUCKET, "minecraft:lava_bucket")
        )
        need_ignition = not counts.get(FLINT_AND_STEEL, 0)
        iron_required = 3 * int(not has_bucket) + int(need_ignition)
        ingots_missing = max(0, iron_required - counts.get(IRON_INGOT, 0))
        need_furnace = ingots_missing > 0 and not self.furnace_available()
        if has_stone_pick and (
            need_furnace
            and not self.supports("craft", FURNACE)
            or not has_bucket
            and not self.supports("craft", BUCKET)
            or need_ignition
            and not self.supports("craft", FLINT_AND_STEEL)
        ):
            return []
        need_table = (
            need_stone_pick or need_wood_pick or need_furnace or not has_bucket
        ) and not self.table_available
        sticks_required = 2 * (int(need_wood_pick) + int(need_stone_pick))
        missing_sticks = max(0, sticks_required - counts.get(STICK, 0))
        stick_batches = math.ceil(missing_sticks / 4)
        planks_required = 4 * int(need_table) + 3 * int(need_wood_pick) + 2 * stick_batches
        if sum(counts.get(item, 0) for item in PLANK_ITEMS) < planks_required:
            return [
                candidate
                for candidate in self.ensure_planks(planks_required)
                if self.supports(candidate["skill"], candidate["item"])
            ]
        if need_table:
            return self.craft(TABLE, "Craft a table for the pickaxe, furnace and bucket recipes.")
        if missing_sticks:
            return [
                self.candidate(
                    "craft",
                    STICK,
                    counts.get(STICK, 0) + stick_batches * 4,
                    "Craft only the sticks still required for the wooden and stone pickaxes.",
                )
            ]
        if need_wood_pick:
            return self.craft(
                WOOD_PICK,
                "Craft a wooden pickaxe to gather the remaining stone recipe materials.",
            )
        if need_stone_pick:
            batch_furnace = need_furnace and self.supports("craft", FURNACE)
            stone_required = 3 + 8 * int(batch_furnace)
            # Gather both recipes before returning to the table. If enough
            # stone was acquired without a pickaxe, craft that pickaxe first
            # instead of proposing mining that cannot collect its drops.
            can_mine_stone = any(counts.get(item, 0) for item in PICKAXE_ITEMS)
            partial_mining_failure = bool(
                stone_count >= 3
                and self.state.skill
                and self.state.skill.kind == "mine"
                and self.state.skill.item in STONE_MATERIALS
                and self.state.skill.status == "failed"
            )
            if (
                stone_count < stone_required
                and (can_mine_stone or stone_count < 3)
                and not partial_mining_failure
            ):
                return self.mine(
                    ("minecraft:cobblestone", "minecraft:cobbled_deepslate"),
                    stone_required - stone_count,
                    "Gather the missing stone pickaxe and furnace recipe materials in one mining visit before returning to the table."
                    if batch_furnace
                    else "Gather only the missing stone-pickaxe materials for this executable recipe.",
                )
            return self.craft(
                STONE_PICK,
                "Craft the stone pickaxe for iron; preserve remaining recipe materials and skip the optional axe.",
            )
        if need_furnace:
            if stone_count < 8:
                return self.mine(
                    ("minecraft:cobblestone", "minecraft:cobbled_deepslate"),
                    8 - stone_count,
                    "Mine the eight remaining furnace materials using the stone pickaxe.",
                )
            return self.craft(
                FURNACE,
                "Craft an owned furnace for smelting mined iron; use no looted workstation.",
            )
        raw_iron = sum(
            counts.get(item, 0)
            for item in (RAW_IRON, "minecraft:iron_ore", "minecraft:deepslate_iron_ore")
        )
        if raw_iron < ingots_missing:
            return self.mine(
                (RAW_IRON,),
                ingots_missing - raw_iron,
                "Mine only the iron still required for one bucket and one flint and steel (four ingots total).",
            )
        if need_ignition and not counts.get(FLINT, 0):
            return self.mine(
                (FLINT,),
                1,
                "Mine gravel until an actual flint is collected; a gravel block alone does not satisfy this step.",
            )
        if ingots_missing:
            fuel_capacity = 8 * (
                counts.get(COAL, 0) + counts.get("minecraft:charcoal", 0)
            ) + 1.5 * sum(counts.get(item, 0) for item in WOOD_FUELS)
            if fuel_capacity < ingots_missing:
                return self.mine(
                    (COAL,),
                    math.ceil((ingots_missing - fuel_capacity) / 8),
                    "Gather enough furnace fuel; existing spare planks/logs are credited and tools are never burned.",
                )
            if not self.supports("smelt", IRON_INGOT):
                return []
            candidate = self.candidate(
                "smelt",
                IRON_INGOT,
                iron_required,
                "Smelt the remaining mined iron in one server-verified batch for bucket and ignition recipes.",
            )
            candidate.update(
                furnace_carried=bool(counts.get(FURNACE, 0)),
                known_furnace=self.state.skill.known_furnace.model_dump()
                if self.state.skill and self.state.skill.known_furnace
                else None,
                fuel_capacity=fuel_capacity,
            )
            return [candidate]
        if not has_bucket:
            return self.craft(
                BUCKET,
                "Craft a bucket from three owned ingots; reserve one ingot for flint and steel.",
            )
        if need_ignition:
            return self.craft(
                FLINT_AND_STEEL,
                "Craft flint and steel from the remaining owned ingot and mined flint.",
            )
        if not any(counts.get(item, 0) for item in (BUCKET, WATER_BUCKET)):
            # The casting executor cannot safely begin with only a lava bucket.
            # It still counts as an existing bucket, so never waste three more
            # ingots to work around an unresolved fluid-recovery step.
            return []
        # Recipe consumption is complete before reserving the mold: furnace
        # cobblestone cannot also be credited as portal construction material.
        building_blocks = counts.get(DIRT, 0) + counts.get("minecraft:cobblestone", 0)
        if building_blocks < BUILD_BLOCK_RESERVE:
            candidates = self.mine(
                (DIRT, "minecraft:cobblestone"),
                BUILD_BLOCK_RESERVE - building_blocks,
                "Reserve nine combined dirt/cobblestone blocks for the casting mold and standing pad; furnace materials were already consumed.",
            )
            return sorted(candidates, key=lambda candidate: candidate["item"] != DIRT)
        if not self.supports("cast_portal", "minecraft:nether_portal") or route_target is None:
            return []
        target = BlockPos.model_validate(route_target).model_dump()
        if not -63 <= target["y"] <= 313 or any(
            not -29_999_968 <= target[axis] <= 29_999_967 for axis in ("x", "z")
        ):
            raise ValueError("Casting landmark exceeds the executor's construction bounds")
        return [
            {
                "id": f"cast-portal-{target['x']}-{target['y']}-{target['z']}",
                "skill": "cast_portal",
                "item": "minecraft:nether_portal",
                "target_count": 1,
                "max_ticks": 1800,
                "target": target,
                "reason": "Collect natural water with the owned bucket, cast all ten frame blocks from natural lava, verify the frame, ignite with crafted flint and steel, and enter.",
                "postcondition": "dimension",
                "expected_dimension": "minecraft:the_nether",
                "route": "from_scratch",
                "building_block_reserve": BUILD_BLOCK_RESERVE,
                "building_blocks_owned": building_blocks,
                "loot_allowed": False,
                "path_validated": False,
                "risks": [*self.risks, "lava_site_requires_live_verification"],
                "sample_tick": self.state.tick,
                "completed_milestones": self.completed.copy(),
            }
        ]


def next_skills(
    state: GameState,
    completed_milestones: Iterable[str | Mapping[str, Any]] = (),
    *,
    goal: str = "early_game",
    declared_seed: str | None = None,
    route: str = "from_scratch",
    route_target: dict | None = None,
) -> list[dict[str, Any]]:
    """Return bounded alternatives for the next executable recipe prerequisite.

    History is passed as model context, never as a substitute for actual current
    ingredients. Already-converted resources are credited by the dependency
    graph; no past requirement to hold logs or planks is reintroduced.

    The caller must check freshness and supervise execution. An empty list can
    mean completion, an unavailable resource, or a state requiring recovery.
    Candidate metadata must be omitted from the strict wire skill request.
    """
    if goal not in RUN_GOALS:
        raise ValueError("Run goal must be early_game or nether")
    if route not in NETHER_ROUTES:
        raise ValueError("Nether route must be from_scratch or ruined_portal")
    if not state.world_ready or state.player is None or state.player.health <= 0:
        return []
    if goal_reached(state, goal):
        return []
    if state.skill and state.skill.active:
        return []
    if (
        goal == "nether"
        and route == "from_scratch"
        and state.screen
        and state.screen.type == "FurnaceScreen"
    ):
        return [
            {**candidate, "route": "from_scratch", "loot_allowed": False}
            for candidate in _NetherPlanner(state, completed_milestones).resume_open_furnace()
        ]
    if state.screen_open:
        if state.screen is None or state.screen.type not in _HANDLED_SCREENS:
            return []
        if state.screen.cursor and state.screen.cursor.count:
            return []
        grid_end = 9 if state.screen.type == "CraftingScreen" else 4
        if state.screen.slots_truncated or any(
            1 <= slot.slot <= grid_end and slot.count for slot in state.screen.slots
        ):
            return []
    if goal == "nether":
        if route == "from_scratch":
            planner = _NetherPlanner(state, completed_milestones)
            return [
                {**candidate, "route": "from_scratch", "loot_allowed": False}
                for candidate in planner.plan_from_scratch(route_target)
                if planner.supports(candidate["skill"], candidate["item"])
            ]
        planner = _Planner(state, completed_milestones)
        direct_route = planner.portal_route(declared_seed)
        if direct_route:
            return direct_route
    planner = _Planner(state, completed_milestones)
    return planner.plan()
