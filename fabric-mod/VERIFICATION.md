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
