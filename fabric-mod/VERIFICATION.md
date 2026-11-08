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
