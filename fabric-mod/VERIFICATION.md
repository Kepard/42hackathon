# Fabric verification — 2026-11-08

The mod was compiled and remapped with Gradle 8.10.2 / JDK 21. The 14 JUnit
action-validation tests passed. It was then launched as an actual Minecraft
1.21.1 development client on a local Xvfb display with software OpenGL.

A singleplayer survival world was created manually with seed `42`, normal
difficulty, commands disabled, and no bonus chest. An isolated authenticated
WebSocket test controller exercised the real mod without calling any model.

Verified in-world:

* Fresh 960×540 PNG observations and terminal action ACKs with tick ranges.
* Six-tick forward input, followed by a new observation.
* Inventory open, normalized GUI click, and inventory close.
* Inventory screenshots contain the crafting screen and exclude the HoloSpeed
  HUD; the human-visible display includes the HUD.
* A 40-tick movement command is rejected.
* Controller stop cancels an active stationary mining hold.
* F8 cancels a mining hold, emits `emergency_stop`, and rejects further actions
  until F9. F9 emits `start_requested` and allows a fresh action.

Runtime verification caught and fixed two issues that a Java build cannot catch:
Minecraft options must be bound after `CLIENT_STARTED`, and the overlay must be
drawn before vanilla restores the GUI model-view matrix. A short F8 event was
initially missed by tick polling under startup lag, so operator keys now use
Minecraft's keyboard callback; the full smoke sequence passed after that fix.

This verifies the bridge and input lifecycle. It does not establish autonomous
wood gathering, crafting success, a repeatable speedrun milestone, or model
latency. Those require separate model-driven gameplay trials.

## Follow-up manual mining fixture

A separate, operator-directed test in the existing seed-42 smoke world recorded
movement, jumping, camera alignment, leaf breaking, and two 80-tick stationary
attack commands. The commands acknowledged tick intervals 4100–4180 and
5946–6026, and the HUD showed held LMB in the recordings. Neither attempt
produced visible log-breaking or a collected log; the aligned targets were
likely outside reach. A later close-range attempt was rejected because a zombie
had killed the player during night-time setup. The death guard behaved correctly.

The disposable smoke world was then changed to Peaceful through vanilla options
for setup, but the test timebox ended without successful log-acquisition evidence.
The original HoloSpeed Demo world was preserved, and its controller configuration
was restored. No model calls, cheats, inventory injection, world-state API queries,
or mod source changes were used in this follow-up.

**Result: log acquisition remains inconclusive**, and this is not an autonomous
gameplay milestone. Ignored local evidence is in
`runs/evaluations/manual-mining/`: `result.json`, complete frame/action/ACK records,
PNG observations, and three short recordings. Scheduler completion and a held-key
HUD are not evidence of successful mining by themselves.

A subsequent [autonomous compact-planning trial](../docs/verification.md#compact-planning-and-exact-jev-continuation)
did visibly break a birch log through the same 80-tick control path. That separate
result demonstrates block breaking, while collected wood remains unverified.


## State-aware skills and stone tools

The user later approved structured client state and normal interaction APIs.
The bounded state sampler, recipe interactions, optional Baritone 1.11.3 mining,
world-scoped table memory, and operator `arm` protocol were compiled and tested.
The first live mining attempt exposed a render-thread deadlock in Baritone's
block-drop lookup. Immutable lookup construction now runs on a daemon worker;
only the prepared lookup is passed to the mining process on the client thread.
Cancellation discards late preparation results without issuing controls.

The actual state-aware trial `20261108T093147-8080fe49` completed six skills:
three birch logs, twelve planks, a crafting table, eight sticks, a wooden pickaxe,
and six cobblestone. Each result was verified from inventory. Mining ended at
y=52, while the previously placed table was at y=67, leaving the original
planner without a reachable table.

After bounded loaded-chunk table rediscovery and normal Baritone return were
implemented, continuation run `20261108T094549-4c23f633` returned from y=52 to
y=66 and crafted both a stone pickaxe and a stone axe. The controller verified
both in inventory: two skills, zero failures, 25.540 seconds. The operator
`arm` and API start flow also worked. Screenshot evidence was saved at
`/tmp/holospeed-stone-tools.png`. This was a continuation of the earlier six
skills, not a fresh complete eight-skill run.

## Historical ruined portal implementation (retired)

The earlier artifact compiled and remapped successfully with all 64 JUnit tests
passing. It added the seed-specific ruined portal route documented in
[SKILL_PROTOCOL.md](SKILL_PROTOCOL.md), using normal navigation, chest clicks,
owned-item placement, ignition, and portal dwell. Six intermediate milestones
are derived from observed geometry, server-confirmed inventory gains, and
actual portal blocks. Nether entry requires the actual Nether dimension.

Chest verification uses a read-only client network mixin to observe when a full
server inventory packet has been applied. The helper normally reopens the chest
and waits for a new packet before confirming a loot gain. It does not assume
loot-table contents or infer success from client prediction alone.

At this historical checkpoint, that portal workflow had not completed live validation. Its geometry,
request validation, inventory synchronization identity tracking, and existing
control lifecycle tests pass; the actual route, new mixin startup, placement,
and dimension transition still need the next Minecraft trial. A separate
normal-mechanics furnace helper also compiles and passes seven focused tests,
but was not exposed as an executable capability or used by that route.


## From-scratch construction implementation

The new user-selected goal retires the structure-looting route and introduces
normal raw-iron/coal/dirt mining, verified flint collection, furnace/bucket/flint
and steel recipes, owned-furnace smelting, and a ten-block bucket-cast portal.
The complete Gradle build/remap passes **82 Java tests**, with zero failures or
errors. Casting geometry, strict requests, actual server block-state evidence,
fresh hotbar confirmation, and bounded downhill-water checks have focused
regression coverage. Natural water regeneration is accepted only after a fresh
server packet confirms the filled bucket. Live validation of the complete empty-inventory sequence
and its elapsed time is still pending; no two-minute completion is claimed.

Read-only terrain analysis of an independently generated Minecraft1.21.1 world
with seed `-7131204740862824481` ran the compiled casting plan and water checker
against actual saved blocks. The site at origin `(-1,64,-6)`, rotation2, passes
all ten partial-frame checks; nearby alternatives pass too. This is a seed
feasibility check, not gameplay evidence. The runtime receives only the lava
landmark and must rediscover sources and a valid site from loaded client cells.
Short grass initially excluded every otherwise suitable footprint; the helper
now permits replaceable plants during selection and normally mines plants in
actual mold cells before placing blocks.

## Fresh-world launcher lifecycle

The normal save/unload/load bridge compiles and has seven focused regressions
covering final server-thread termination, actual pause before readiness, prompt
cancellation, late arrivals, timeout quarantine, stale callbacks, and path
validation. Independent lifecycle review found no blocker. Live template-clone
loading remains pending the next client restart; no launcher gameplay was
performed during implementation.

## Workstation vegetation recovery

Live run `20261108T114834-a16d6d05` completed logs, planks, a table, and sticks,
then rejected wooden-pickaxe crafting because acacia leaves blocked every safe
placement ray. The saved complete local cube shows that clearing the leaf at
`(-12,65,-4)` exposes a grass-floor face only1.85 blocks from the eye, without
recorded entity intersection or an adjacent fluid hazard. The new shared
placement helper supports that normal bounded clearance for tables/furnaces;
a recorded-coordinate regression checks the geometry. Independent source
review found no remaining blocker. Live recovery remains to be confirmed after
the next combined client restart.

The combined workstation-recovery and transparent-pause artifact builds and
remaps with **95 Java tests passing**, zero failures/errors. F9 now waits for
the same arm readiness checks as dashboard Start before emitting its start
event; the transparent screen retains vanilla integrated-server pause behavior.
