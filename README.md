# Minecraft observatory: mod + Python evaluation core

Fabric 1.20.4 / Java 17 / Fabric API. Local experimental control endpoint, not a hardened service.

## Run

```sh
cd ~/repos/mc-observatory/mod
DISPLAY=:0 LIBGL_ALWAYS_SOFTWARE=1 ./gradlew runClient
```

The first vanilla launch may show an accessibility welcome screen: click **Continue** once.
After the title screen, the mod automatically creates `observatory-spike` in Survival,
normal difficulty, no cheats/bonus chest, fixed seed **12345**, or loads that save on later launches.
Loom supplies the offline development player `Observatory`; no Microsoft login is used.
The mod sets render distance 4, simulation distance 5 (the vanilla minimum), GUI scale 2,
vsync off, max FPS 260 and pause-on-lost-focus off.
Window/framebuffer size is requested as 640x480 through the Loom arguments.
Local saves/options are in `mod/run/` (gitignored).

## Protocol

Connect to `ws://127.0.0.1:8765`. One JSON request per WebSocket text message:

```json
{"id":1,"command":"state"}
{"id":2,"command":"screenshot"}
{"id":3,"command":"key","code":87,"duration_ms":1000}
{"id":4,"command":"key","code":87,"pressed":false}
{"id":5,"command":"mouse","dx":100,"dy":20}
{"id":6,"command":"mouse","button":0,"pressed":true}
{"id":7,"command":"mouse","button":0,"pressed":false}
{"id":8,"command":"pause"}
{"id":9,"command":"resume"}
{"id":10,"command":"reset","seed":12345,"inventory":[{"id":"minecraft:oak_log","count":4}]}
{"id":11,"command":"mouse","x":176,"y":374,"button":0,"pressed":true}
{"id":12,"command":"mouse","button":0,"pressed":false}
{"id":13,"command":"type","text":"hello"}
{"id":14,"command":"subscribe"}
{"id":15,"command":"unsubscribe"}
```

Response: `{"id":...,"ok":true,"result":{...}}` or `{"id":...,"ok":false,"error":"..."}`.
`state` returns position, yaw/pitch, health, dimension, nonempty inventory slots (registry
item IDs and counts), renderer and FPS/frame counters, `paused`, `screen_open`, and `screen`
(Java simple class name). Server snapshots expose `world_id`, `seed`, `server_time`,
`time_of_day`, `stats` (`picked_up`, `crafted`, `mined`, `killed`: nonzero registry ID → count),
completed advancement IDs, `portal_entered`, and `portal_lit`. Snapshots refresh at ~2 Hz;
inventory/pose/screen are current client-side fields, so a response is not an atomic
server/client snapshot. An empty inventory is `[]`; no-world state has
`in_world:false` and omits player fields.

`key` injects a GLFW keysym through Minecraft's keyboard handler (or the current screen),
not the OS. E opens inventory; Escape closes it; F1 toggles HUD. Use `type` for text fields.
`pressed` defaults to true; `duration_ms` schedules release on a client tick
(0..60000 ms). Timings are tick/frame-granular, not hard real-time. Disconnect releases inputs.
In-world, `mouse` uses vanilla relative look units (0.15 degrees/unit).
Button 0 is attack/left, 1 is use/right. Send a separate release.
With a GUI open, `x,y` are absolute **framebuffer/screenshot pixels**, converted to GUI scale;
movement without a button is supported. This also moves the GLFW cursor. Clicks call the
vanilla screen methods. Dragging, scrolling, modifier chords and supplementary-plane Unicode
are not implemented. `type` dispatches Java chars to the open screen; without one it errors.

`pause` freezes the integrated server tick manager (same mechanism as `/tick freeze`),
releases controls and suppresses API and physical keyboard/mouse input plus client movement.
Rendering and network/control threads stay alive. `resume` unfreezes. Pause does not open
Minecraft's pause menu; ordinary menu-based singleplayer pause remains vanilla behavior.
Inputs during pause/reset return `ignored:true`.

`reset` validates the inventory, disconnects, and creates a unique fresh Survival save with
the requested signed 64-bit seed. Each stack must be 1..its normal max count, up to 36 stacks.
The reply arrives after server inventory grant and client in-world readiness (120s mod
deadline, 150s Python timeout). Previous saves are retained under `mod/run/saves`; remove
them manually when no longer needed. Reset is intended for a ready local client, not to
interrupt initial resource loading. Invalid inventory fails before changing the world.

Portal evidence is not inferred from possession of obsidian or use stats: `portal_entered`
latches actual server-player contact with portal blocks; `portal_lit` latches when that
player uses flint-and-steel/fire charge and the target becomes a new nether-portal block.
Flags reset with a new world and last for this mod process, not across restarts; a completed
Nether advancement remains an additional persistent signal.

`screenshot` returns `png_base64`, width, height, capture point. Capture runs on the render
thread at **InGameHud.render HEAD** when no GUI is open: world and first-person hand are
present; HUD/hotbar/crosshair and later overlays are absent. With a GUI, hidden HUD, or no
HUD hook, it falls back to `GameRenderer.render TAIL`, including GUI/HUD as applicable.
An independent WebSocket-side timer returns an error after five seconds even if rendering
stalls; capture success/error uses one completion guard. Dashboard subscriptions separately
emit `{"event":"frame","data":{...screenshot...}}` then `{"event":"state","data":{...}}`
at approximately 2 Hz, without request IDs. Stream captures are end-of-frame, not pre-HUD.
The Python client drops oldest queued events beyond 8; the mod skips slow subscribers with
buffered data. Streams are best-effort, not a fixed-rate video recorder.
No auth: loopback-only, for trusted local clients. Concurrent controllers are not isolated.
Java-WebSocket is relocated/shaded in the built mod JAR (dev runs use the dependency directly).

## Python 3.12 runner

```sh
cd ~/repos/mc-observatory/runner
uv venv --python 3.12
uv pip install -e '.[test]'
.venv/bin/python -m pytest -q -m 'not live'
```

The base client/evaluator needs only `websockets`; task/session tracing integration is optional.
For the local cua-bench checkout, this environment installs only its non-agent/non-sandbox
dependencies, deliberately avoiding installing an unused model/VM stack:

```sh
uv pip install --no-deps -e ~/repos/cua/libs/cua-bench
uv pip install aiohttp rich tqdm jinja2 html5lib beautifulsoup4 pillow python-dotenv datasets pyyaml
MC_LIVE=1 .venv/bin/python -m pytest -q -s
```

`MinecraftClient` is an async context manager, multiplexes correlated responses and bounded
push events, and exposes `state`, `screenshot` (PNG bytes), `reset`, `pause`, `resume`, `key`,
`mouse`, `click`, `type_text`, `subscribe` and `unsubscribe`. Errors become `ModError`;
ordinary calls time out after 10s. The server must be running first.

`LadderEvaluator.update(state)` latches inventory and picked-up/crafted/mined evidence per
`world_id`. It awards only the contiguous prefix:

log → crafting table → wooden pickaxe → cobblestone → stone pickaxe → furnace → iron ore
→ iron ingot → kill hostile mob → flint and steel → obsidian → portal → enter Nether.

Evidence seen early is retained but cannot score until gaps are filled. Raw iron also
satisfies iron ore. Hostile kills use an explicit entity allowlist, not total mob kills.
Nether dimension (or its completed advancement) implies portal evidence. Starting inventory
can satisfy obtained goals. `fraction = completed/13` is a **local equal-weight convention**,
not Vals' unpublished per-rung weights. JSON sub-tasks specify seed, starting inventory,
goal and wall-clock time limit; setup time is excluded, pauses after setup still consume time.

`MinecraftSession` implements the lifecycle/screenshot/action subset of cua-bench's
`DesktopSession`, translating click/right/double-click, move, single key, type, wait and done.
`tasks.py` uses its actual `Task`, `tasks_config`, `setup_task`, `evaluate_task` decorators;
`trace_dir` uses its `Tracing` and exports HF dataset + PNGs. This is intentionally **not a
complete DesktopSession/provider**: shell/files, CSS/native apps, VM lifecycle, scrolling,
dragging and chords are unsupported. Cua-bench's `cb run` constructs sandbox environments;
we do not force the local client into that factory. Call the decorated hooks with a
`MinecraftSession` directly, as the live integration test does. No agent loop is built.

The opt-in live test resets seed 12345 with four logs from the `craft_planks` sub-task,
freezes/resumes and rejects bad requests, hides the HUD and captures, opens inventory,
uses actual cua-bench click actions to craft four planks, checks both inventory/crafted stats
and reward, types into chat, receives push events, then resets seed 54321 with empty inventory.
It expects 640×480 and GUI scale 2; crafting coordinates are fixture-specific, not an agent.
Evidence is in `spike/core/`: inventory before/after, pause/typing screenshots, states and
cua-bench trajectory. Tests of hostile kills and Nether/portal scoring are pure fake-state
tests; actually lighting/traversing a portal or killing a mob is not live-tested here.

### Recorded checks

Python 3.12.16, local cua-bench 0.3.1, Minecraft 1.20.4 under llvmpipe:

```text
66 passed, 14 warnings in 94.78s (0:01:34)
```

After final mod changes, the live tests were repeated against a fresh launch:

```text
LIVE reset seed=12345 logs=4; pause stable; hidden-HUD capture; GUI crafted oak_planks=4; typing; frame/state stream=4+4; reward=1.0
2 passed, 14 warnings in 90.85s (0:01:30)
BUILD SUCCESSFUL in 3s
7 actionable tasks: 6 executed, 1 up-to-date
```

Warnings are upstream cua-bench `Tracing`'s deprecated `datetime.utcnow()` call; its dataset
and PNG export succeeded. Outputs: `spike/core-tests.log`, `spike/core-live-final.log`,
`spike/core-build.log`. Inventory-before/after and typed-chat PNGs were visually inspected.

## Probe / evidence

```sh
python3 -m pip install 'websockets>=12'
python3 ~/repos/mc-observatory/spike/probe.py
```

The probe expects a clear forward path and changes the player position/view. It checks 640x480
capture, survival, distance 4, W for one second and release, explicit key press/release,
relative mouse look, button press/release acknowledgements, and unknown-command errors.
Its FPS measurement has 10 seconds warmup then 30 seconds sampling, stationary after movement.
`results.json`, `probe.log`, and before/after/final PNGs are in `spike/`.
Observed renderer: `llvmpipe (LLVM 15.0.7, 256 bits)`; mean reported FPS 94.17,
range 72..107; 2845 rendered frames / 30.3309 seconds = 93.80 effective FPS.
These are a small static forest vista, not a guarantee while exploring/generating chunks.
W moved horizontally 4.6653 blocks and dropped 7 blocks; health changed 20 to 17.

`./gradlew build` packages the mod. Build output is in `mod/build/libs/`.
Maven Central returned intermittent HTTP 429 in this VM; the build uses Google's Maven Central
mirror for dependencies. Missing sound/shader warnings from vanilla startup did not prevent use.
