# State-aware HoloSpeed: integration research

Verified against upstream repositories and versioned API documentation on
2026-11-08. This document recommends an implementation; it does not claim that
the integrations below have already been installed or passed a live run.

## Recommended direction

Keep the existing Fabric 1.21.1 player, renderer, bridge, and dashboard. Add
structured game-state observations and use **Baritone 1.11.3's public API** for
navigation, mining, and dropped-item collection. Build a small, interruptible
early-game task coordinator around inventory postconditions and vanilla recipes.
Use Holo4 to choose and revise high-level tasks, assess unusual scenes, and explain
strategy. Do not put remote inference between each movement or mining tick.

This is a **state-aware autonomous survival agent**: it uses coordinates, block
and entity state, inventory, recipes, and screenshots. Its normal interaction
rules still require reaching blocks, spending ingredients, waiting for block
breaking, and receiving inventory updates. Item spawning, teleportation, creative
inventory, and direct world edits are outside the proposed control interface.

The user has authorized structured state and normal interaction APIs for crafting
and other actions. These APIs remove avoidable GUI targeting errors, while
keyboard/mouse remains available for interaction and demonstrations. Do not describe the new mode as
visual-only, and do not present a state-assisted result as a standard competitive
speedrun submission.

## Baritone: verified Fabric 1.21.1 integration

The official [v1.11.3 release][baritone-release] explicitly supports Minecraft
**1.21 and 1.21.1**, including Fabric. The tagged
[Fabric metadata][baritone-mod] independently declares:

- Mod ID: `baritone`.
- Minecraft: `["1.21", "1.21.1"]`.
- Fabric Loader: `>=0.14.22`.
- License metadata: `LGPL-3.0`; source headers allow version 3 or later.
- Tagged build properties use Java 21. The current HoloSpeed Java 21/Fabric
  Loader 0.16.14 target meets those declared requirements.

Use this exact release asset:

```text
https://github.com/cabaletta/baritone/releases/download/v1.11.3/baritone-api-fabric-1.11.3.jar
Size:   4810562 bytes
SHA256: f9a9b17a41d7d7ad22c759058b14fd01e3319663282e66f99e18153237fa6767
```

The digest above comes from GitHub's release-asset metadata. The actual JAR was
downloaded and its byte count and SHA-256 matched those values; its embedded
`fabric.mod.json` also confirms the expected compatibility. It is staged outside
the repository under `/tmp/holospeed-baritone/`, not installed in a game profile
or executed by this research. The browser download URL and `checksums.txt`
returned HTTP 504 in this environment; GitHub's exact official asset endpoint
worked with `Accept: application/octet-stream`:
`https://api.github.com/repos/cabaletta/baritone/releases/assets/540245077`.

The source-only installer uses this pinned official URL, size, and digest:

```bash
python3 scripts/install-baritone.py --minecraft-dir /path/to/existing/minecraft/profile
```

It requires an existing profile with `options.txt` or `launcher_profiles.json`,
refuses conflicting Baritone JARs or a mismatched existing artifact, verifies the
complete download before publishing it, and writes a source/license notice
beside the mod. It does not launch or restart Minecraft. No dependency binary is
committed to this repository. On a network failure it tries the exact official
asset API once; both routes must pass the same pinned size and SHA-256 check.

### Artifact and mapping details

Upstream's [setup guide][baritone-setup] says **API** builds retain the public
`baritone.api` package and obfuscate implementation packages. The API JAR includes
the runnable mod; it is not just a compile-time interface library. The
**standalone** artifact obfuscates everything and is unsuitable for another mod
calling its API. The **unoptimized** build is intended for debugging.

A concrete initial dependency approach is a pinned, checksum-verified local JAR
fed through Loom's `modImplementation(files(...))`, with the same JAR installed
in the runtime mods directory. This is a proposed build configuration to verify,
not a claim that a build has passed. Do not invent a Maven coordinate or assume
that a repository accepting `baritone:baritone-fabric:1.11.3` has been verified.
Upstream publishes release assets; its tagged publication configuration does not
specify a publishing repository.

The tagged source uses Mojang mappings; HoloSpeed uses Yarn. Let Loom remap the
Fabric mod dependency. Methods taking only primitive values or strings are easy
to isolate, but Minecraft types in signatures still need correct remapping.
Keep all integration calls inside a small adapter using only `baritone.api`.
Retain upstream licensing/notices and a pinned source link when distributing the
dependency; avoid copying private implementation classes into the project.

### Exact public API entry points

These names are verified in the v1.11.3 tagged source, not inferred from chat
commands:

```java
IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();

// Prepare on a dedicated worker: registry/drop lookup can wait for the client.
CompletableFuture<BlockOptionalMetaLookup> pending = CompletableFuture.supplyAsync(
    () -> new BlockOptionalMetaLookup("minecraft:birch_log"), preparationExecutor);

// Later, on a client tick, after checking cancellation, world, and ownership:
if (pending != null && pending.isDone()) {
    CompletableFuture<BlockOptionalMetaLookup> finished = pending;
    pending = null; // Consume once; never block the client on unfinished work.
    // Desired TOTAL inventory count, not "collect four additional items".
    baritone.getMineProcess().mine(4, finished.join());
}

// Supply a traversable destination, not the occupied trunk block itself.
baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(x, y, z));

boolean miningActive = baritone.getMineProcess().isActive();
boolean pathActive = baritone.getPathingBehavior().isPathing();

baritone.getPathingBehavior().cancelEverything();
// Emergency shutdown only; normal cancellation may finish an unsafe-to-stop move.
baritone.getPathingBehavior().forceCancel();
baritone.getInputOverrideHandler().clearAllKeys();
```

References: [BaritoneAPI][baritone-api], [IBaritone][baritone-ibaritone],
[IMineProcess][baritone-mine], [GoalBlock][baritone-goal],
[ICustomGoalProcess][baritone-custom], [IPathingBehavior][baritone-path],
[IInputOverrideHandler][baritone-input]. These examples are an integration sketch;
the scheduler must run commands through the client's thread and ownership rules.

The public overload is `IMineProcess.mine(int, BlockOptionalMetaLookup)`, with
`baritone.api.utils.BlockOptionalMetaLookup(String...)`. It supports multiple
block identifiers. Resolve actual available log types from the loaded
world/registry rather than hard-coding a nonexistent generic log ID. If the
desired task is a delta, compute `current matching count + requested delta`.

**Observed startup deadlock:** the first state-aware runtime trial on 2026-11-08
called `mineByName(int, String...)` on the render/client thread. In Baritone
1.11.3, constructing its drop lookup can wait on a registry future that needs
that same client thread to finish. Rendering and the skill acknowledgment then
stalled. Do not call `mineByName` or construct the lookup synchronously on the
client thread. HoloSpeed's [SkillController](../fabric-mod/src/main/java/ai/holospeed/client/SkillController.java)
now enters `preparing_mining`, constructs only the immutable lookup on a dedicated
daemon worker, then polls completion from client ticks and invokes `mine(int,
lookup)` there. The worker never starts controls; cancellation discards a late
result. This fix addresses startup scheduling, not proof of mining success.

### Mining already includes pickup

The tagged [MineProcess implementation][baritone-mine-impl] checks the current
matching inventory count every tick and stops when it reaches the requested
quantity. It also scans matching dropped `ItemEntity` objects and uses them as
pathing destinations. Relevant [settings][baritone-settings] include:

- `mineScanDroppedItems = true` by default.
- `mineGoalUpdateInterval = 5` ticks by default.
- `mineDropLoiterDurationMSThanksLouca = 250` milliseconds by default.
- `allowParkour = false`, `maxFallHeightNoWater = 3` by default.
- `allowInventory = false` by default; changing this affects automatic hotbar
  transfers and needs deliberate coordination with our inventory executor.
- `exploreForBlocks = true` by default; disable or bound it for an early demo so
  an absent target produces a reportable outcome instead of unlimited wandering.

This directly addresses the observed HoloSpeed failure where a log broke but its
dropped item remained on the ground. A successful block break alone is not a
resource-acquisition postcondition. Likewise, `isActive() == false` can mean
success, cancellation, or failure; check inventory and task outcome separately.

The quantity filter is based on expected **block drops**, not merely the block's
own item ID. Tagged `BlockOptionalMeta` derives item hashes from loot tables with
a plain netherite pickaxe. Thus a vanilla `minecraft:stone` mining target counts
cobblestone pickup; require an appropriate actual tool and independently verify
the cobblestone inventory target. Custom loot tables or special tools can make
actual drops differ from that default estimate.

Before each task, record the settings it relies on. Initially prefer no parkour,
no water-bucket falls, limited drop heights, and a bounded task deadline. Do not
blindly disable block breaking when asking the mining process to gather wood.
Use one controller as the owner of movement/look/attack: the old keyboard action
executor must not press keys while Baritone owns the task. F8/pause, disconnect,
death, dimension changes, or a timeout must cancel the process and clear both
Baritone overrides and bridge-held inputs.

Baritone is a mature pathfinder and resource executor, **not** a complete
crafting, Nether-routing, combat, or speedrun strategy implementation.

## Crafting with current Minecraft data

Read the version's recipe manager and item registry instead of letting an LLM
invent recipes or numeric IDs. Plan ingredient counts from current inventory,
including recipe output multiplicity, crafting-grid size, remaining items, and
the need to place/reach a crafting table. Retain actual registry identifiers in
task logs.

The [Yarn 1.21.1+build.3 interaction API][yarn-interactions] confirms these normal
client interaction methods:

- `clickRecipe(int syncId, RecipeEntry<?> recipe, boolean craftAll)`.
- `clickSlot(int syncId, int slotId, int button, SlotActionType actionType,
  PlayerEntity player)`.
- `interactBlock(ClientPlayerEntity player, Hand hand, BlockHitResult hitResult)`.
- `attackBlock(BlockPos pos, Direction direction)` and
  `updateBlockBreakingProgress(BlockPos pos, Direction direction)`.
- `cancelBlockBreaking()`.

The [recipe manager][yarn-recipes] exposes `getAllOfType(RecipeType<T>)`,
`getFirstMatch(...)`, and `getRemainingStacks(...)`. Use the loaded game's recipes
and actual screen handler. `clickRecipe` is recipe placement, not proof that an
output was acquired; take the output through the handler and await authoritative
slot/inventory updates. Check that the recipe is available/unlocked and the
correct crafting screen is open. If direct interaction is selected, keep normal
reach, game-mode, ingredient, and server validation intact. Never mutate item
stacks or world blocks to simulate successful actions.

## Other reusable projects

| Project | Verified compatibility/license | Recommended role |
| --- | --- | --- |
| [Mineflayer][mineflayer] | Package 4.39.0, MIT, Node >=22; `lib/version.js` lists tested 1.21.1 | Excellent alternative execution backend; it creates a separate protocol bot and does not control our current Fabric player. |
| [mineflayer-pathfinder][pathfinder] | Package 2.4.5, MIT | Reuse navigation goal/replanning patterns; choose this only with a Mineflayer backend. |
| [mineflayer-collectblock][collectblock] | Package 1.7.0, MIT | Strong reference for approach, tool selection, dig, then dropped-item collection. |
| [AltoClef][altoclef] | MIT; actual build targets 1.18.2 with a custom Baritone fork | Closest full-game task-system precedent; selectively reuse reviewed task logic rather than porting the entire old client now. |
| [Voyager][voyager] | MIT; tested setup is Minecraft 1.19 | Reuse persistent skills, feedback, prerequisites, and explicit success checks; its research harness is not a drop-in survival speedrunner. |
| [CraftGround][craftground] | README supports 1.21.0 and 26.2, not explicitly 1.21.1; inconsistent license metadata | Observation/evaluation reference; not the navigation or crafting solver needed for the demo. |

Mineflayer's [API][mineflayer-api] has `findBlocks`, `blockAt`, `canDigBlock`,
`digTime`, `dig`, `recipesFor`, `recipesAll`, `craft`, and `placeBlock`.
`recipesFor` filters by available inventory; `craft(recipe, count, table)` counts
**recipe executions**, not desired output items. Pathfinder provides
`GoalLookAtBlock`, `GoalGetToBlock`, `GoalNear`, and `GoalFollow`, plus explicit
`stuck`, `dig_error`, `place_error`, `timeout`, and `noPath` outcomes.
Collectblock's source-confirmed API is **`bot.collectBlock.collect(...)`**, with
capital `B`, and `cancelTask()`; its API Markdown has a stale lowercase typo.
The current Mineflayer package requires Node >=22 despite older README wording.

AltoClef's upstream build pins Minecraft **1.18.2**, Yarn `1.18.2+build.3`, and
Fabric Loader `0.13.3`. Its [speedrun tasks][altoclef-speedrun] include
`BeatMinecraft2Task`, `KillEnderDragonWithBedsTask`, and
`WaitForDragonAndPearlTask`. Its resource tasks encode useful inventory targets,
tool prerequisites, dropped-item pickup, inventory-space recovery, and movement
progress checks. The upstream project claims autonomous game completion, but
that is not evidence that it runs on 1.21.1 or is the current best speedrunner.
Its mappings, inventory/recipe APIs, mixins, and custom Baritone integration make
a wholesale port substantial. Prioritize the early-game task ideas under MIT
attribution; evaluate Nether and dragon modules as later work.

Voyager's setup explicitly uses a Creative/Peaceful, command-enabled environment;
some helper code issues `/give`, `/setblock`, and gamerule changes. Its code
generation loop and harness should not be imported wholesale. Reviewed
`mineBlock`/`craftItem` ideas are useful, but preserve explicit errors: upstream
`craftItem` can catch errors without rethrowing. Our skills should be registered,
bounded functions with input validation and inventory postconditions.

CraftGround has a genuine upstream license discrepancy: its README says LGPLv3,
its root `LICENSE` contains GPLv3, and `pyproject.toml` contains an MIT classifier
while pointing to that license file. Do not copy its implementation without
resolving that discrepancy. Its [observation schema][craftground-observations]
is useful: position, velocity, inventory, raycast, health, hunger, entities,
surrounding blocks, collision flags, and progress statistics. Its action docs
still mark semantic craft/equip/place/destroy actions unsupported.

## Concrete early-game architecture

1. **State snapshots:** capture on the client thread with world/session ID,
   dimension, tick, position, orientation, velocity, grounded/collision flags,
   health/hunger/air, inventory counts and durability, screen/slot state, targeted
   block, nearby blocks/entities/items, and available recipes. Keep unknown or
   unloaded areas explicit. Label the source of any additional integrated-server
   data; full-state mode must not imply that an unloaded client chunk is known.
2. **Task coordinator:** implement a small typed set such as acquire logs,
   navigate, collect drop, craft item, place/use table, acquire stone, and stop.
   Each task has prerequisites, allowed executor, deadline, progress evidence,
   cancellation, and a result. These are proposed HoloSpeed task names, not
   upstream Baritone API names.
3. **Local execution:** run pathing/mining continuously at the game tick rate.
   Compute progress from distance/path changes, block updates, mining state,
   health, and inventory deltas. Separate observations, requested actions,
   accepted interactions, and completed postconditions in the journal.
4. **Holo4 decisions:** ask for a task and target at meaningful boundaries:
   initial plan, completed subtask, new hazard, missing prerequisites, persistent
   failure, or strategic branch. Include compact structured state and a current
   screenshot. Give Holo concrete candidate tasks and their prerequisites, not
   every raw block in the world. It remains responsible for strategy and unusual
   visual interpretation, while deterministic code handles known mechanics.
5. **Jev decisions:** if retained, use it for occasional candidate ranking or
   uncertain situations. Do not make it veto every already-valid local pathing
   tick. State checks handle immediate reach, resource, and hazard invariants.
6. **Learning/recovery:** remember failed target positions, causes, attempted
   prerequisites, and outcomes. Distinguish an unexecuted proposal rejection from
   a physical failure. Retry after changed conditions, not merely after another
   model response. Escalate repeated skill failures with the relevant state and
   screenshot to Holo; Devin can analyze archived failures later.

The first acceptance sequence should be: inventory-confirmed logs → planks and
sticks → placed/reachable crafting table → wooden pickaxe → enough cobblestone
for a declared stone-tool goal → crafted tools confirmed in inventory. Derive
the required counts from the selected recipes; do not conflate broken blocks,
dropped items, and acquired materials. Use fixed test worlds to reproduce path,
pickup, inventory-full, inaccessible-target, missing-table, cancellation, and
disconnect failures, then test new survival worlds. Only broaden to food, iron,
Nether entry, and combat once this sequence is reliable.

## Evidence limits

This research verifies published compatibility, source-level APIs, and relevant
design patterns. It does not establish live Baritone compatibility with our mod's
mixins, guarantee a full run, or benchmark competing agents. The next concrete
engineering check is the pinned Fabric JAR loading beside HoloSpeed, followed by
one bounded log-acquisition task whose inventory postcondition is observed.

[baritone-release]: https://github.com/cabaletta/baritone/releases/tag/v1.11.3
[baritone-mod]: https://github.com/cabaletta/baritone/blob/v1.11.3/fabric/src/main/resources/fabric.mod.json
[baritone-setup]: https://github.com/cabaletta/baritone/blob/v1.11.3/SETUP.md
[baritone-api]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/api/java/baritone/api/BaritoneAPI.java
[baritone-ibaritone]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/api/java/baritone/api/IBaritone.java
[baritone-mine]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/api/java/baritone/api/process/IMineProcess.java
[baritone-goal]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/api/java/baritone/api/pathing/goals/GoalBlock.java
[baritone-custom]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/api/java/baritone/api/process/ICustomGoalProcess.java
[baritone-path]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/api/java/baritone/api/behavior/IPathingBehavior.java
[baritone-input]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/api/java/baritone/api/utils/IInputOverrideHandler.java
[baritone-mine-impl]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/main/java/baritone/process/MineProcess.java
[baritone-settings]: https://github.com/cabaletta/baritone/blob/v1.11.3/src/api/java/baritone/api/Settings.java
[yarn-interactions]: https://maven.fabricmc.net/docs/yarn-1.21.1+build.3/net/minecraft/client/network/ClientPlayerInteractionManager.html
[yarn-recipes]: https://maven.fabricmc.net/docs/yarn-1.21.1+build.3/net/minecraft/recipe/RecipeManager.html
[mineflayer]: https://github.com/PrismarineJS/mineflayer
[mineflayer-api]: https://github.com/PrismarineJS/mineflayer/blob/master/docs/api.md
[pathfinder]: https://github.com/PrismarineJS/mineflayer-pathfinder
[collectblock]: https://github.com/PrismarineJS/mineflayer-collectblock/blob/master/src/CollectBlock.ts
[altoclef]: https://github.com/gaucho-matrero/altoclef
[altoclef-speedrun]: https://github.com/gaucho-matrero/altoclef/tree/main/src/main/java/adris/altoclef/tasks/speedrun
[voyager]: https://github.com/MineDojo/Voyager
[craftground]: https://github.com/yhs0602/CraftGround
[craftground-observations]: https://github.com/yhs0602/CraftGround/blob/main/docs/observation_space/index.md
