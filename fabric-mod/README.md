# HoloSpeed Fabric bridge

Client-only Minecraft **Java 1.21.1**, Fabric Loader **0.16.14+**, Fabric API
**0.116.5+1.21.1**, **JDK 21**. This is a client input bridge with visual observations and optional, explicit
structured state for planning. State reads use normal client APIs and currently
loaded chunks; they do not load terrain or modify the world.

## Build and install

```sh
cd fabric-mod
./gradlew build
```

On Windows use `gradlew.bat build`. Gradle 8.10.2 is pinned with a distribution
SHA-256. The first build downloads Minecraft and Fabric dependencies. Copy
`build/libs/holospeed-0.1.0.jar` into your Minecraft instance's `mods` folder,
alongside Fabric API. Do not install the `-sources.jar`.

Launch once to create `config/holospeed.json`, or create it manually:

```json
{
  "url": "ws://127.0.0.1:8765/bridge",
  "token": "THE-SAME-RANDOM-HOLOSPEED_BRIDGE_TOKEN-AS-THE-CONTROLLER",
  "captureWidth": 960
}
```

`HOLOSPEED_BRIDGE_TOKEN` in the launch environment overrides the config token.
Never use sponsor API keys here; those stay in the Python controller. The bridge
accepts loopback URLs only and authenticates through the WebSocket Authorization
header. It retries connection every five seconds.

For development, `./gradlew runClient` starts an offline development client.
Enter a singleplayer world manually, focus Minecraft, then press **F9** to start
or resume through the controller.
The game must remain focused and unpaused while inputs execute. A model action
against the title screen is rejected. Keep GUI scale fixed during a run.

## Controls and safety

* **F8** immediately stops held inputs and latches the bridge off. An explicit
  dashboard Start can rearm through the guarded `arm` protocol while Minecraft
  is focused; it resumes only normal pause/empty crafting screens. **F9** rearms
  and requests start/resume from the controller using a fresh screenshot. These
  keys work while inventory screens are open.
* Focus loss, death, world change, controller disconnect, local pause, or the
  wall-clock watchdog releases held keys. Each accepted command gets a terminal
  acknowledgement with actual tick timing.
* Movement is limited to 20 ticks. A **stationary attack-only hold** may last
  80 ticks so barehand log mining is not reset between model calls. Longer
  mining holds cannot include movement, camera turns, GUI clicks, hotbar changes,
  or scrolling, and are rejected in menus.
* Commands cannot overlap. Used or stale frames, duplicate command IDs, invalid
  bounds, contradictory movement, and unknown action fields are rejected.
* World attack/use, movement, jump, sneak, sprint, inventory/drop/swap pulses,
  hotbar, camera deltas, scrolling, and normalized GUI clicks are supported.
  Sneak provides shift-click in inventories. GUI drag gestures are not yet part
  of protocol v1; click-based crafting is supported.

The HUD shows objectives, status, held inputs, model latency, elapsed time, and
milestones. Captures happen at the end of vanilla rendering, including inventory
screens, **before** the HoloSpeed HUD is drawn. Thus model observations do not
include the agent's own claims. PNG scaling and encoding run off the game thread.
In state-aware mode the HUD reads actual vanilla and Baritone input flags and
labels the active local skill; unavailable input readback is shown explicitly.

See [the bridge protocol](../docs/protocol.md) for existing message schemas and
[structured state](STATE_PROTOCOL.md) for the read-only `state` extension, and
[bounded skills](SKILL_PROTOCOL.md) for normal-mechanics mining, crafting, smelting, and building a new portal from natural lava and water. `./gradlew test`
checks action bounds and the stationary mining exception. A build/test pass does
not establish successful gameplay; validate the early-game milestone in a real
world with the selected providers.
