# Nether-entry route for the demonstration seed

Researched on 2026-11-08 for Minecraft Java 1.21.1, seed
`-1950884254474434485`. The recommended next milestone is a fresh game-state
snapshot whose dimension is `minecraft:the_nether`. A built frame, a lit portal,
or a model claim is not completion.

## Use the nearby ruined portal first

The seed appears in [KenanKardes's verified 1:15.970 set-seed run](https://www.speedrun.com/mc/runs/y9w6v92z)
(Java 1.21.1, Easy). Its [linked primary video](https://youtu.be/VmXjyTetO70)
is a route reference, not a performance claim for this agent. A separate
[exact-seed implementation](https://github.com/gigafrosxh/minecraft-ai-speedrunner/blob/953c1937f1af235987a7372b3b930f48ac6ea7ba/src/main/java/dev/daniel/aispeedrunner/speedrun/SpeedrunStrategy.java)
identifies the opening ruined portal near `(48, 68, 48)`.

Read-only inspection of already-generated chunks in the local **HoloSpeed Fresh**
world independently confirmed this geometry. No world, player, block, or inventory
was changed by that inspection. These coordinates are route knowledge; the live
executor must navigate normally and validate the current blocks before interacting.

| Feature | Actual generated position / extent |
| --- | --- |
| Ruined-portal chest | `(49, 68, 52)`, west-facing, single chest |
| Portal plane | Constant `x=50`; portal axis `Z` |
| Bottom frame | `y=68`, `z=48..52` |
| Side pillars | `z=48` and `z=52`, `y=69..71` |
| Top frame | `y=72`, `z=48..52` |
| Missing required obsidian | **`(50, 72, 50)` only** |
| Support directly above the gap | Gold block at `(50, 73, 50)` |
| Interior | **3 wide × 3 high**, `z=49..51`, `y=69..71`; all nine cells were air |
| Crying obsidian | `(50, 72, 48)`, an optional corner, not a required frame edge |
| Western approach support | Stone-brick slabs at `x=49`, `y=68`, `z=49..51` |

Do not shrink this into a new two-wide portal: repairing the existing three-wide
frame needs only one obsidian. Do not mine its crying-obsidian corner. The chest
was still an unopened `minecraft:chests/ruined_portal` loot table during inspection;
its contents must be learned from normal opening and synchronized container slots.
There is no claim here that obsidian or an ignition item has already been obtained.

## Smallest useful execution sequence

1. Navigate to the ruined-portal area using a bounded ordinary path. Check current
   terrain and the chest, not only arrival near the route waypoint.
2. Open the actual reachable chest and transfer useful stacks with vanilla screen
   interactions. Preserve inventory-space checks, cursor ownership, and synchronization.
   Require at least one real obsidian plus a fire charge or flint and steel before
   attempting repair/ignition; record actual loot rather than assuming fixed contents.
3. Revalidate the saved frame and choose a reachable face adjacent to the one gap.
   Place exactly one owned obsidian through normal `interactBlock`, then confirm
   that `(50,72,50)` is obsidian and all required frame edges are present. The gold
   block's underside is one possible supporting face; the executor must verify
   reach and raycast from its actual position. No support block needs to be removed.
4. Confirm that the entire interior is clear. Equip the real ignition item and use
   it on an appropriate base face, for example the top of `(50,68,50)`, then wait
   for an actual `minecraft:nether_portal` block at the interior.
5. Walk into the active portal and remain inside for the normal Survival transfer
   delay (approximately 80 ticks). Release controls during dimension transition.
   Confirm a fresh, ready Nether snapshot before completing the milestone.

An expected portal transition needs explicit handling in the existing generic
world-change cancellation path. It must still release inputs and reject old-world
observations, while allowing the supervisor to verify the new dimension. No
teleportation call or direct block/inventory mutation is part of this sequence.

For the Nether goal, stone tools are a useful recorded checkpoint, not a mandatory
prerequisite when this portal and its supplies are available. If actual chest loot
cannot support the one-block repair and ignition, return a specific missing-item
result; do not invent supplies or silently switch to an unbounded search.

## Reuse and fallback research

[Baritone 1.11.3](https://github.com/cabaletta/baritone/releases/tag/v1.11.3)
provides the existing navigation and mining primitives. Keep its public API
integration and the nonblocking lookup preparation already implemented in this
repository. A route waypoint does not prove a traversable path or safe interaction.

[AltoClef](https://github.com/gaucho-matrero/altoclef/tree/af22e3bc2f03dde45da703f5f7535baae18ea486)
provides useful task decomposition: acquire prerequisites, approach, perform one
interaction, verify its postcondition, and retry/recover within limits. Relevant
sources are its [bucket portal task](https://github.com/gaucho-matrero/altoclef/blob/af22e3bc2f03dde45da703f5f7535baae18ea486/src/main/java/adris/altoclef/tasks/construction/compound/ConstructNetherPortalBucketTask.java),
[obsidian portal task](https://github.com/gaucho-matrero/altoclef/blob/af22e3bc2f03dde45da703f5f7535baae18ea486/src/main/java/adris/altoclef/tasks/construction/compound/ConstructNetherPortalObsidianTask.java),
and [portal-entry task](https://github.com/gaucho-matrero/altoclef/blob/af22e3bc2f03dde45da703f5f7535baae18ea486/src/main/java/adris/altoclef/tasks/movement/EnterNetherPortalTask.java).
The repository has a verified [MIT license](https://github.com/gaucho-matrero/altoclef/blob/af22e3bc2f03dde45da703f5f7535baae18ea486/LICENSE),
Copyright 2020 Adris Jautakas, but targets older Minecraft versions. Reuse its
ideas or properly attributed code only after adapting and testing the actual 1.21.1 APIs.

The exact-seed NPC project above is used only as a factual route reference. Its
current Git tree lacked a license file when checked, despite a distribution page's
MIT label; no implementation code was copied from it.

A general lava-pool route would require acquiring iron, a bucket, water, ignition,
and a verified lava source; building/casting each frame block; then validating the
same complete-frame and dimension postconditions. That is a larger fallback than
repairing this already-generated portal, so it is not the first demonstration route.
