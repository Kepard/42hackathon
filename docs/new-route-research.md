# From-scratch Nether route research

The revised demonstration requires gathering, mining, crafting, and smelting all
supplies, then casting a complete portal from natural lava and water. Structure
loot and the previous ruined-portal repair route are excluded. Roughly two minutes
is a performance target, not a measured result or a reason to add artificial delay.

## Recommended seed

Use **`-7131204740862824481`**, Java Edition **1.21.1**, default terrain,
Survival, Easy, commands off, and no bonus chest. The world spawn is `(0,64,0)`;
the initial player position can vary within Minecraft's normal spawn radius.

The original [three lava pools report](https://www.reddit.com/r/minecraftseeds/comments/1n87raa/3_lava_pools_at_spawn/)
identified this seed on Java 1.20.1. We independently generated it using the
official **1.21.1** server distribution and inspected its saved region data on
2026-11-08. The three pools and required resources exist in that exact version.

| Resource or work area | Verified natural coordinates | Evidence and use |
| --- | --- | --- |
| Wood | Acacia base `(-11,64,-3)`; another `(-9,63,7)` | About 11 blocks from world spawn; normal logs and planks |
| Stone | Pool rims and shallow ground | Mine cobblestone for stone tools, furnace, and mold supports |
| Iron vein A | `(-12,51,-13)` | Ten connected iron-ore blocks around x -13..-11, y 50..51, z -14..-13; buried, requiring a real mining descent |
| Iron vein B | `(-13,48,-14)` | Five connected iron-ore blocks, one exposed to a cave at `(-14,47,-14)` |
| Coal | `(9,63,3)` and `(9,63,4)` | Two exposed ore blocks beside lava; use cautious navigation or wood fuel |
| Gravel | Closest `(15,57,7)`; exposed face `(18,55,9)` | Connected deposit contains 123 gravel blocks; actual flint acquisition must be verified |
| Water | `(-12,62,3)` | Natural still-water source, open to the sky |
| Lava pool A | x 4..8, y 63, z 1..5 | 22 exposed still-lava source cells; proposed route target `(4,63,2)` |
| Lava pool B | x 2..7, y 63, z 7..10 | 22 exposed still-lava source cells |
| Lava pool C | x 9..13, y 63, z 3..10 | 31 exposed still-lava source cells |
| Initial workspace survey | Lower corner `(-3,65,-10)` | A 5×6 footprint with solid ground at y 64 and no hard overhead obstruction; fourteen short-grass cells occupy its headroom, so it is not an all-air site |
| Verified casting plan | Origin `(-1,64,-6)`, rotation `2` | Ground footprint x -4..0, z -10..-5, y 64; owned water source at `(-1,68,-7)`. Passes the bounded water-flow check with the normal replaceable-plant handling described below |

The executor must validate the loaded blocks and its workspace before acting.
Coordinates are route hints, not a substitute for observed inventory, reachability,
source-fluid checks, or actual construction. No existing obsidian or portal is
needed. The first iron vein is not exposed at the surface; the route deliberately
includes underground mining and returning to the work area.

Minimum iron is four ingots: three for a bucket and one for flint and steel. Four
furnace smelts take 40 seconds in one normal furnace, so gathering gravel or mold
blocks during smelting is the main opportunity for useful overlap. A complete
minimum portal requires ten newly formed obsidian blocks. The actual runtime,
mining path, flint drops, casting sequence, and Nether entry still require live
validation before any timing claim.

## Casting-site validation

The first terrain survey treated short grass as non-solid headroom. A subsequent
check against the stricter original Java `validSite` requirement found **zero**
all-air sites across 10,086 candidate origins and all four rotations. The initially
suggested footprint contained fourteen `minecraft:short_grass` blocks. Calling
that area clear did not establish that it satisfied the original all-air contract.

The executor was corrected to accept only air or replaceable, empty-collision,
nonfluid plants in headroom. A plant occupying an actual mold placement is broken
through normal mining and server confirmation; other plants can wash away under
normal water flow. Hard obstructions, fluid-filled floors, and existing obsidian
remain rejected. This does not modify the natural research world.

An isolated Java harness used the compiled **`CastPortalPlan` and
`CastWaterSafety` implementations** with exported natural 1.21.1 terrain. It
reproduced site geometry, required ground and frame-base cells, all 75 exposed
lava sources within the executor's source bounds, the nine-block mold, excavated
bottom cells, and all ten partial-frame states. With the plant allowance, it found
844 geometry candidates and 797 passing water-flow candidates. The first valid
candidate when ordered from world spawn was origin **`(-1,64,-6)`, rotation `2`**;
it passed every partial-frame flood check. This footprint has thirteen short-grass
cells and a minimum horizontal Manhattan separation of thirteen blocks between
its predicted waterfall feet and the observed lava sources. Origins `(0,64,-6)`
and `(-2,64,-6)` with rotation `2` also passed. The actual client must still select
and revalidate a site from its current position and loaded terrain.

This is read-only geometry and conservative fluid-reach validation. It does not
prove successful player reach, bucket use, casting, or a two-minute live run.

## Other candidates checked

| Seed | Published description | Exact 1.21.1 findings | Decision |
| --- | --- | --- | --- |
| `-1718501946501227358` | [Java 1.21 plains and lava](https://minecraft.how/fr/graine/29-plains-surface-lava) | Spawn `(16,66,0)`. Nearby source pools at `(72,62,-7)` and `(72,75,-34)` have solid terrain overhead; nearest observed iron is underground around y 45. | Less convenient than the open pools at the recommended spawn. |
| `-14414820596686441` | [Java 1.21 mountain with exposed iron](https://www.mcseedshare.com/seed/ZMkS5Izm) | Spawn `(0,152,0)`, wood nearby, open-air iron at `(-4,162,-30)`. A 46-source lava pool near `(-6,124,11)` is beneath a stone roof; nearby water is also covered. | Steep navigation and a constrained casting area make it a poorer initial demo. |

## Reproducible evidence

Three separate research worlds were generated without players, item grants,
block edits, or structure-loot interactions. Only saving commands were sent to
those isolated servers. The live demonstration world was not modified by this
research. Region palettes and packed block-state indices were decoded directly;
resource connectivity, adjacent air, source-fluid levels, overhead obstruction,
and workspace clearance were checked against those blocks.

Local evidence is retained under `runs/evaluations/from-scratch-seed/`:
`pools-analysis.json`, `pools-detail.json`, `plains-analysis.json`, and
`mountain-analysis.json`. The stricter site check is retained as
`cast-site-check.txt`, `SeedCastCheck.java`, `terrain.bin`, and `lava.csv`.
The full isolated worlds are in
`/tmp/holospeed-seed-research/`; they are research artifacts, not acceptance runs.
All three worlds report Minecraft data version **3955** and version **1.21.1**.
The server distribution SHA-256 is
`e3bc55693e93cda0188f2e60aea28113fc647c5e85a15fa3d1b347349231b4bb`.
