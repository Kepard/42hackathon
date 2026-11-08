# Gameplay plan and research

HoloSpeed's first playable target is visual early-game progression: collect wood,
craft a table, make a wooden pickaxe, then obtain stone tools. This is an acceptance
target, not a claim that the initial implementation already completes it. A working
bridge or synthetic demo does not establish autonomous Minecraft performance.

## First milestone

Use a documented fixed seed, survival mode, daylight start, and nearby wood and
exposed stone. Save the seed and game/mod versions with the run. The planner must
use screenshots and keyboard/mouse-equivalent actions; structured diagnostics must
not silently become inventory, block, coordinate, or entity observations.

| Stage | Expected behavior | Evidence required |
| --- | --- | --- |
| Wood | Locate a tree, approach it, aim, hold attack, collect dropped logs | Before/after frames and visible inventory or hotbar evidence |
| Crafting table | Open inventory, craft planks and table, place table | GUI action acknowledgments and a frame showing the placed table |
| Wooden pickaxe | Craft sticks and pickaxe through the table GUI | Screenshot showing the resulting item, not only a model assertion |
| Stone tools | Reach stone, mine cobblestone, craft stone pickaxe | Visible result and linked executed actions |
| Recovery | Encounter a deliberate obstruction or failed interaction, inspect, change approach | Failure reason, a distinct retry, and verified progress or a clear pause |
| Operator control | Pause during movement/mining or disconnect the controller | All held inputs released within the bridge watchdog budget |

Run the same scenario at least five times and report success count, completion
time, human interventions, model latency percentiles, failed actions, and recovery
outcomes. Then test multiple seeds before claiming generalized progress. Model
milestone labels are hypotheses until their screenshot evidence is reviewed.

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

Holo4 should ground objectives in the current screenshot and revise the plan.
Jev can refine bounded actions using that plan. Local code owns tick timing,
acknowledgments, expiry, and input release. Cloud inference latency must never
determine how long a key remains pressed. One action is in flight at a time;
late results must not overwrite a newer pause or plan.

Movement, camera rotation, and inventory manipulation need different semantics.
World look uses relative yaw/pitch; GUI interactions use normalized cursor
coordinates converted using the captured frame and current GUI dimensions.
Begin with short movement bursts and bounded mining holds. These are tuning
choices, not measured latency guarantees. The first protocol does not implement
every human crafting gesture: advanced click-drag crafting and precise combat
timing need dedicated in-game validation before they are advertised.

The core distinction is whether an action was requested, accepted, started, or
completed. Log each stage. Avoid rewarding a request that never reached the mod.
On pause, timeout, disconnect, or world transition, release all controls and
invalidate old work. See [the bridge protocol](protocol.md).

## Recovery and memory

Similar screenshots alone do not prove failure: successful mining keeps the
camera nearly stationary. Compare repeated actions with their expected effects,
elapsed time, visible changes, and previous failures. A recovery sequence is:
release controls, inspect a fresh frame, change aim or position, retry within a
small budget, then replan or pause. Repeated camera motion is also not evidence
of objective progress.

Keep concise failure notes attached to run, objective, and observation IDs, such
as "two mining attempts produced no visible progress; try a closer position."
Treat them as fallible observations. Persisting these notes is experience memory,
not model-weight training or proof of general learning. Voyager's
[iterative feedback and verified skill-library design](https://arxiv.org/abs/2305.16291)
is useful inspiration; its code-based Minecraft control is a different capability
from visual keyboard/mouse execution.

## Reuse references

| Project | Useful ideas | Boundary |
| --- | --- | --- |
| [CraftGround](https://github.com/yhs0602/CraftGround) | Fabric bridge, capture pipeline, human-style action space | GPL-3.0; do not copy code without handling its license. Its [action reference](https://yhs0602.github.io/CraftGround/action_space.html) is a useful comparison. |
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
be measured in the actual Minecraft client. Gladia voice integration is deferred.
