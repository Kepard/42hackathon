# Fixed-seed demonstration

Use Minecraft Java Edition **1.21.1**, seed **`-7131204740862824481`**, Survival,
Easy difficulty, default terrain, commands off, and no bonus chest. Create a new
world with an empty inventory for each from-zero acceptance run; a new dashboard
recording does not reset an existing world.

This route requires gathering wood, crafting tools and a furnace, mining iron and
flint, smelting four ingots, crafting a bucket and flint and steel, and casting a
complete ten-block obsidian frame with natural lava and water. **Structure loot
and pre-existing portal frames are excluded.** Roughly two minutes is a target
for actual gameplay, not an achieved result or an artificial minimum duration.

The original [three-pool seed report](https://www.reddit.com/r/minecraftseeds/comments/1n87raa/3_lava_pools_at_spawn/)
used Java 1.20.1. We independently generated and inspected it in **1.21.1**:
world spawn is `(0,64,0)`, with three nearby surface lava pools, wood and water,
and natural underground iron and gravel. The route's lava landmark is `(4,63,2)`.
See [verified resource coordinates and candidate comparisons](new-route-research.md).
Normal player spawn can vary within Minecraft's spawn radius.

Configure the controller with:

```dotenv
GAMEPLAY_MODE=state
RUN_GOAL=nether
NETHER_ROUTE=from_scratch
MINECRAFT_SEED=-7131204740862824481
CAST_PORTAL_TARGET={"x":4,"y":63,"z":2}
VALIDATE_HOLO_PLANS=true
```

The configured seed and landmark are declarations. They do not create a world,
load unseen chunks, supply items, or prove that a route is traversable. The
executor checks actual loaded blocks and owned inventory before each interaction.
Recreate matching settings for comparable runs, and record version, resolution,
GUI scale, difficulty, initial state, model configuration, and human interventions.

The earlier seed `-1950884254474434485` belongs to the
[historical ruined-portal trials](nether-route.md). Their successful Nether timings
must not be presented as results for this mining-and-casting route. Earlier seed
`42` was used only for control smoke tests. The separate
[verification report](state-aware-verification.md) distinguishes measured runs
from route research and automated tests.
