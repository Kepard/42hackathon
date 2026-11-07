from dataclasses import dataclass, field

LADDER = (
    "log", "crafting_table", "wooden_pickaxe", "cobblestone", "stone_pickaxe",
    "furnace", "iron_ore", "iron_ingot", "kill_hostile_mob", "flint_and_steel",
    "obsidian", "portal", "enter_nether",
)
HOSTILES = frozenset(
    "minecraft:" + mob for mob in (
        "zombie", "husk", "drowned", "zombie_villager", "skeleton", "stray",
        "wither_skeleton", "creeper", "spider", "cave_spider", "enderman",
        "endermite", "silverfish", "slime", "magma_cube", "witch", "pillager",
        "vindicator", "evoker", "vex", "ravager", "blaze", "ghast", "hoglin",
        "zoglin", "piglin_brute", "phantom", "guardian", "elder_guardian",
        "shulker", "warden", "wither", "ender_dragon",
    )
)


def _counts(mapping):
    if not isinstance(mapping, dict):
        raise ValueError("Counts must be an object")
    for name, count in mapping.items():
        if not isinstance(name, str) or type(count) is not int or count < 0:
            raise ValueError("Counts must map item/entity IDs to nonnegative integers")
    return {name for name, count in mapping.items() if count > 0}


@dataclass
class LadderEvaluator:
    """Latches observed evidence; awards only the contiguous ordered prefix.

    Equal rung weights are a local convention, not Vals' unpublished point rubric.
    Create/reset one evaluator per world; a changed world_id clears all evidence.
    """

    world_id: str | None = None
    obtained: set[str] = field(default_factory=set)
    evidence: set[str] = field(default_factory=set)

    def reset(self):
        self.world_id = None
        self.obtained.clear()
        self.evidence.clear()

    def update(self, state):
        if not isinstance(state, dict):
            raise ValueError("State must be an object")
        inventory = state.get("inventory", [])
        if not isinstance(inventory, list):
            raise ValueError("Inventory must be a list")
        observed = set()
        for stack in inventory:
            if not isinstance(stack, dict) or "id" not in stack or "count" not in stack:
                raise ValueError("Invalid inventory stack")
            observed |= _counts({stack["id"]: stack["count"]})
        stats = state.get("stats", {})
        if not isinstance(stats, dict):
            raise ValueError("Stats must be an object")
        for category in ("picked_up", "crafted", "mined"):
            observed |= _counts(stats.get(category, {}))
        killed = _counts(stats.get("killed", {}))
        advancements = state.get("advancements", [])
        if not isinstance(advancements, list) or not all(isinstance(a, str) for a in advancements):
            raise ValueError("Advancements must be a list of IDs")
        if state.get("resetting") or state.get("in_world") is False:
            return self.result()
        world = state.get("world_id")
        if world is not None and self.world_id is not None and world != self.world_id:
            self.reset()
        if world is not None:
            self.world_id = world
        self.obtained |= observed
        if any(item.startswith("minecraft:") and item.endswith(("_log", "_wood", "_stem", "_hyphae")) for item in self.obtained):
            self.evidence.add("log")
        for rung in LADDER:
            if f"minecraft:{rung}" in self.obtained:
                self.evidence.add(rung)
        if self.obtained & {"minecraft:raw_iron", "minecraft:deepslate_iron_ore", "minecraft:iron_ore"}:
            self.evidence.add("iron_ore")
        if killed & HOSTILES:
            self.evidence.add("kill_hostile_mob")
        if state.get("portal_entered") is True or state.get("portal_lit") is True:
            self.evidence.add("portal")
        if state.get("dimension") == "minecraft:the_nether" or "minecraft:story/enter_the_nether" in advancements:
            self.evidence.update(("portal", "enter_nether"))
        return self.result()

    def result(self):
        credited = []
        for rung in LADDER:
            if rung not in self.evidence:
                break
            credited.append(rung)
        return {
            "completed": credited, "observed": [r for r in LADDER if r in self.evidence],
            "next": LADDER[len(credited)] if len(credited) < len(LADDER) else None,
            "completed_count": len(credited), "total": len(LADDER),
            "fraction": len(credited) / len(LADDER),
        }

    def has_item(self, item_id):
        return item_id in self.obtained
