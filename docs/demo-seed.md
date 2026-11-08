# Fixed-seed demonstration

Use Minecraft Java Edition **1.21.1**, seed **`-7131204740862824481`**, Survival,
Easy difficulty, default terrain, commands off, and no bonus chest. Create a new
world with an empty inventory, full health, and full hunger, then save and unload
it before preparing the demonstration template. Dashboard **New run** loads a
fresh copy of that template for every attempt and starts the agent after checking
the starting state.

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
Normal player spawn can vary within Minecraft's spawn radius. Template copies
reuse the particular spawn saved in the prepared source world.

## Prepare the starting world

Create the source world in Minecraft 1.21.1 using the settings above. Save and
quit to the title screen before gathering resources or changing the world. From
the repository root, prepare a new destination directory outside that save:

```sh
uv run python -m holospeed.demo_launch \
  /path/to/minecraft/saves/holospeed-source \
  /path/to/holospeed-template \
  --seed=-7131204740862824481
```

Replace the paths with the source save and a destination that does not already
exist. Preparation verifies an unloaded Survival 1.21.1 world with commands and
bonus chest disabled, empty inventory and Ender chest, full health and hunger,
and the expected seed. It copies the save to `DEST/world` and records its metadata
and file hashes in `DEST/manifest.json`; it does not edit player state, world time,
or terrain. Treat this prepared directory as immutable. The launcher checks the
manifest before using it and rejects changed files.

## Configure and launch

Configure the controller with:

```dotenv
GAMEPLAY_MODE=state
RUN_GOAL=nether
NETHER_ROUTE=from_scratch
MINECRAFT_SEED=-7131204740862824481
DEMO_TEMPLATE_DIR=/path/to/holospeed-template
MINECRAFT_SAVES_DIR=/path/to/minecraft/saves
CAST_PORTAL_TARGET={"x":4,"y":63,"z":2}
VALIDATE_HOLO_PLANS=true
```

Restart the controller after configuring the paths. Keep Minecraft and its
Fabric bridge connected; the title screen is sufficient. Unlock the dashboard
controls and choose **New run**. The launcher saves and unloads the previous
world, copies the template into a unique `holospeed-demo-<UUID>` directory under
`MINECRAFT_SAVES_DIR`, and loads that save through Minecraft's normal world-loading
API. It verifies the loaded seed, Survival mode, empty inventory, full health and
hunger, and released controls before arming and starting the agent.

The dashboard reports saving, copying, loading, verifying, and starting stages.
Loading time is separate from the new run timer. **Pause**, **End run**, or **F8**
cancels an active launch. Previous worlds and logs remain intact; any new copy
already created is preserved on failure or cancellation, and another **New run**
uses a distinct destination. The local `POST /api/new-run` endpoint accepts
`{"command_id":"<UUID>"}` and returns `202` when preparation is accepted; follow
`state.launch` for the actual result.

The configured seed must match the prepared save. The landmark remains a route
hint: the executor checks actual loaded blocks and owned inventory before each
interaction. Use the same immutable template for comparable runs, and record
version, resolution, GUI scale, difficulty, initial state, model configuration,
and human interventions.

The earlier seed `-1950884254474434485` belongs to the
[historical ruined-portal trials](nether-route.md). Their successful Nether timings
must not be presented as results for this mining-and-casting route. Earlier seed
`42` was used only for control smoke tests. The separate
[verification report](state-aware-verification.md) distinguishes measured runs
from route research and automated tests.
