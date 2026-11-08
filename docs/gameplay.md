# Historical early-game plan and research

The current demonstration builds a complete portal from gathered and mined
materials on a [new seed](demo-seed.md). This document preserves the earlier
early-game acceptance plan and broader speedrunning references; its proposed
structure-loot routes are not permitted in the current from-scratch demonstration.
See [architecture](architecture.md) for the active implementation.

HoloSpeed's first playable target was state-aware early-game progression: collect wood,
craft a table, make a wooden pickaxe, then obtain stone tools. This is an acceptance
target, not a claim that the initial implementation already completes it. A working
bridge or synthetic demo does not establish autonomous Minecraft performance.

## First milestone

Use a documented fixed seed, survival mode, daylight start, and nearby wood and
exposed stone. Save the seed and game/mod versions with the run. The current mode
explicitly uses structured inventory, coordinates, blocks, entities, and screenshots.
Baritone handles navigation/mining/pickup, and normal recipe/interaction APIs
handle crafting. See [architecture](architecture.md) and [setup](setup.md).

| Stage | Expected behavior | Evidence required |
| --- | --- | --- |
| Wood | Locate a tree, approach, mine continuously, collect dropped logs | Fresh player inventory counts, with linked state/frame evidence |
| Crafting table | Craft planks and table; place/use it when needed | Actual inventory or reachable placed-table state |
| Wooden pickaxe | Craft sticks and pickaxe with the correct crafting handler | Pickaxe in player inventory after ingredient consumption |
| Stone tools | Reach stone, mine materials, craft a stone pickaxe and stone axe | Both tools in inventory, linked to skill outcomes |
| Recovery | Encounter a deliberate obstruction or failed interaction, inspect, change approach | Failure reason, a distinct retry, and verified progress or a clear pause |
| Operator control | Pause during movement/mining or disconnect the controller | All held inputs released within the bridge watchdog budget |

Run the same scenario at least five times and report success count, completion
time, human interventions, model latency percentiles, failed actions, and recovery
outcomes. Then test multiple seeds before claiming generalized progress. Current
milestones require state evidence; historical visual-mode labels remain model
claims until their screenshot evidence is reviewed.

## Route progression

Begin with the structureless opening because it isolates basic movement, aiming,
crafting, and resource management. After it is reliable, add opportunistic village
starts for food, beds, and iron, then lava-pool portal construction. Later route
work includes bastion identification and routing, piglin bartering, fortress/blaze
combat, stronghold navigation, and the End fight. The runner-maintained
[Minecraft Speedrun Guide](https://github.com/Metacor/Minecraft-Speedrun-Guide)
separates these openings and documents inventory shortcuts and practice routes.
The community's [tools and practice resources](https://www.minecraftspeedrunning.com/public-resources/tools-and-resources)
provide maps and utilities for evaluating those skills independently.

Do not apply 1.16.1 bastion assumptions unchanged to 1.21.1. Mojang introduced
Piglin Brutes and changed bartering in
[Java Edition 1.16.2](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-16-2).
The initial Fabric target is 1.21.1; a later 1.16.1 port would align more closely
with the established speedrun practice ecosystem. Keep route prompts and test
scenarios version-specific.

## Controls and latency

Holo4 chooses high-level strategy and revises it after blocked skills, using
structured state and optional visual context. Jev selects among locally validated
routine candidates. Local code owns navigation, tick timing, continuous mining,
pickup, recipe interactions, acknowledgments, cancellation, and input release.
Cloud inference latency must never determine how long a key remains pressed.
One executor owns controls at a time; late replies cannot override a pause.

The retained visual-control mode still has separate movement, camera, and GUI
input semantics.
World look uses relative yaw/pitch; GUI interactions use normalized cursor
coordinates converted using the captured frame and current GUI dimensions.
Begin with short movement bursts and bounded mining holds. These are tuning
choices, not measured latency guarantees. State mode uses normal recipe/slot
interactions instead of timing screen coordinates for each ingredient. Precise
combat and advanced speedrun techniques still need dedicated live validation.

The core distinction is whether an action was requested, accepted, started, or
completed. Log each stage. Avoid rewarding a request that never reached the mod.
On pause, timeout, disconnect, or world transition, release all controls and
invalidate old work. See [the bridge protocol](protocol.md).

## Recovery and memory

Similar screenshots alone do not prove failure: successful mining keeps the
camera nearly stationary. Compare skill progress, path/distance changes, block
updates, inventory deltas, elapsed time, and failure reasons. A recovery sequence
is: cancel active work, inspect fresh state and frame, revise target or unmet
prerequisite, retry within a small budget, then replan or pause. Rejected proposals
are not physical failures, and camera motion is not resource acquisition.

Keep concise failure notes attached to run, objective, and observation IDs, such
as "mining reached the target, but the dropped item was not collected."
Treat them as fallible observations. Persisting these notes is experience memory,
not model-weight training or proof of general learning. Voyager's
[iterative feedback and verified skill-library design](https://arxiv.org/abs/2305.16291)
is useful inspiration. Reuse reviewed skill/task patterns rather than its
command-enabled research harness; no model-generated code executes in our loop.

## Reuse references

| Project | Useful ideas | Boundary |
| --- | --- | --- |
| [Baritone](https://github.com/cabaletta/baritone/releases/tag/v1.11.3) | Supported Fabric navigation, continuous mining, dropped-item pickup | Separate official 1.11.3 API JAR; LGPL-3.0 metadata, source headers v3-or-later. |
| [AltoClef](https://github.com/gaucho-matrero/altoclef) | Survival task decomposition, inventory targets, prerequisites, recovery | MIT; upstream targets 1.18.2/custom Baritone, so it is a reference rather than a drop-in dependency. |
| [Mineflayer](https://github.com/PrismarineJS/mineflayer) | Inventory-aware crafting and approach/mine/pickup patterns | MIT; a separate protocol bot backend, not the current Fabric player. |
| [CraftGround](https://github.com/yhs0602/CraftGround) | Structured observation schema and evaluation | README, LICENSE, and package metadata conflict; no implementation copied. See [verified research](state-aware-research.md). |
| [MineStudio](https://github.com/CraftJarvis/MineStudio) | Trajectory organization, callbacks, recording, crash recovery | MIT; its MineRL-based environment is not the custom Fabric bridge. |
| [Fabric HUD documentation](https://docs.fabricmc.net/1.21.1/develop/rendering/hud) | Rendering the in-game presentation | Use documentation matching the game version; rendering APIs change. |

HoloSpeed's presentation should remain outside model observations so that the
agent cannot mistake its own labels for environmental evidence. Preserve the
vanilla hotbar, inventory, and other game UI the agent needs to inspect.

## Replay and analysis limits

Visual replay is a synchronized inspection of recorded screenshots, decisions,
inputs, acknowledgments, and events. It can explain what the agent observed and
what actually executed. It is not a deterministic re-run of the Minecraft world.
The same seed and action sequence can diverge because of timing, entities,
simulation state, or game/mod changes. True re-execution would additionally need
world snapshots, exact configuration, controlled timing, and divergence detection.

Preserve complete local run records, including failed requests and actions. A
Devin analysis receives only a bounded, redacted digest when explicitly submitted;
it does not receive screenshot files, raw model payloads, credentials, or the
entire local archive. The adapter uses the
[Devin v3 Create Session API](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions),
sets a two-ACU cap, disables memory, and requests causes, tests, fixes, and missing
evidence. It never submits automatically during gameplay and does not retry an
ambiguous session-creation timeout. Review session history before retrying.
An API response or a proposed fix is not a validated game result.

The initial demo has no full-run completion guarantee. Nether routing, precise
combat, repeatable early-tool success, and adaptation across unknown worlds must
be measured in the actual Minecraft client. The current [Gladia voice integration](gladia.md)
adds operator-requested transcription and bounded commands; its verification
status is documented separately.
