# Minecraft observatory: mod + Python evaluation core

Fabric 1.20.4 / Java 17 / Fabric API. Local experimental control endpoint and screenshot-only Holo agent.

## Run

### On-demand Devin engineering

The dashboard's Devin panel works on saved runs only. It does not participate in the
gameplay loop or send interventions to the running agent. Configure the backend with
`DEVIN_API_KEY` (v3 service key) and optionally `DEVIN_ORG_ID` (default
`org-3c2e5fe738364ec3b7f653b263cedafd`). Credentials stay server-side. Restart the
backend after installing the updated runner (`uv pip install -e 'runner[test]'`).

- **Create Challenge:** describe a task; confirm that Devin may open a PR. The prompt
  supplies the task schema and a real example, requires validation tests and targets
  `github.com/Kepard/42hackathon`. **Add locally** independently validates the returned
  task and saves it to `runs/user-tasks.json`, without changing packaged tasks. The
  start-run selector refreshes immediately; the CLI and cua-bench tasks also load it.
  Set `OBS_USER_TASKS_FILE` to override the path. Existing IDs cannot be overwritten.
- **Investigate Run:** select a finished run in **Runs**, then click Investigate Run.
  The backend uploads one zip containing summary, steps, interventions and up to 20
  screenshots: first, last, then failure-tagged steps and their neighbors. Images
  are capped at 25 MiB. A manifest records missing images and zero-based step numbers.
  Model reasoning text is removed; declared intents, actions, outcomes and metrics
  remain. The investigator receives no repo checkout and is instructed to use only
  the bundle, with no gameplay, source inspection or PR creation. Evidence buttons
  select the corresponding replay frame (UI step labels are one-based).
- **Review Fix:** choose a suggested fix in an investigation and explicitly confirm
  PR creation. A new session verifies it against the repo, adds an offline/mocked
  regression test, and opens a PR. Neither PR workflow runs automatically.

Sessions are limited to 5 ACUs, with session memory disabled. Status is polled every
four seconds while the panel is open. Metadata and validated structured results are
persisted in `runs/devin/<job-id>/job.json`; investigation zips stay alongside them.
They survive browser/server restarts.
Some v3 sessions return valid structured output while `waiting_for_user`; the panel
treats that result as ready while retaining the actual remote status.
Creation/upload failures are not retried
automatically (a failed network response might still have created a remote session).
The UI links directly to Devin for approval requests or manual follow-up.

REST: `POST /api/devin/jobs` with `kind: challenge|investigate|review_fix`, optional
`description`, `run_id`, `investigation_id` and zero-based `fix_index`;
`GET /api/devin/jobs[?run_id=...]`; `GET /api/devin/jobs/{id}` polls and persists status;
`POST /api/devin/jobs/{id}/add-task`; `GET /api/devin/task-schema`.
All routes inherit the backend's local Host/origin restrictions.

API contracts follow the documented v3 endpoints:
[create](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions),
[get](https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session),
[messages](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions-messages),
[attachments](https://docs.devin.ai/api-reference/v3/attachments/post-organizations-attachments).
The adapter uses multipart field `file`, returned `url` in `attachment_urls`, and
Draft-7 `structured_output_schema` / `structured_output_required` on session creation.

Offline checks: `runner/.venv/bin/pytest runner/tests -m 'not live' -q` and
`cd dashboard && npm run build`. Explicit live verification (reuses a saved
investigation if present; never triggers either PR workflow):
`cd dashboard && DEVIN_LIVE=1 node tests/devin-live.mjs`. Screenshots/results go under
the ignored `spike/ui/` directory. Use a trusted loopback workspace, not a public
unauthenticated deployment.

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
vanilla screen methods. Synthetic Shift is supported for shift-clicking. Other modifier chords,
dragging, scrolling and supplementary-plane Unicode
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
emit `{"event":"frame","data":{"jpeg_base64":"...","width":640,"height":480,"capture_point":"end_frame_viewer"}}`
then `{"event":"state","data":{...}}` at approximately 5 Hz, without request IDs.
Viewer captures are end-of-frame and include the observatory overlay. Agent captures remain PNG.
The Python client drops oldest queued events beyond 8; the mod skips slow subscribers with
buffered data. Streams are best-effort, not a fixed-rate video recorder.

`overlay` accepts a `payload` JSON object (maximum 8192 characters); `{}` disables it. Fields:
`objective`, `completed` (0..13), `current_rung`, `intent`, `action`, `api_latency_ms`, `badges`.
The compact panel renders at HUD TAIL, after agent capture. For GUI agent captures, overlay drawing
is suppressed for that frame and the clean fallback is captured; that frame is not streamed.
Subsequent viewer frames include the panel again. F1 hides the HUD and panel; vanilla GUIs may
cover part of it. No telemetry is composited into agent PNGs.
Loopback-only; browser `Origin` headers are rejected by default. Explicitly allow trusted origins
with comma-separated `OBS_ALLOWED_ORIGINS` or Java property `observatory.allowedOrigins` (property
wins). Optional `OBS_TOKEN` requires `Authorization: Bearer <token>` at the WebSocket handshake;
the Python client reads the same environment variable (or accepts `token=`). Native clients omit
Origin. Tokens are never sent in URLs. Browser dashboards with tokens need a backend proxy capable
of setting authorization headers. Concurrent controllers are not isolated.
Java-WebSocket is relocated/shaded in the built mod JAR (dev runs use the dependency directly).

## Python 3.12 runner

```sh
cd ~/repos/mc-observatory/runner
uv venv --python 3.12
uv pip install -e '.[test]'
.venv/bin/python -m pytest -q -m 'not live'
```

The package needs `websockets` and `aiohttp`; task/session tracing integration is optional.
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
Crafting sub-tasks explicitly require `stats.crafted` evidence; picking up, mining, or starting
with the target item cannot satisfy a craft goal. The ladder's obtained goals remain broader.

`MinecraftSession` implements the lifecycle/screenshot/action subset of cua-bench's
`DesktopSession`, translating click/right/double-click, move, single key, type, wait and done.
`tasks.py` uses its actual `Task`, `tasks_config`, `setup_task`, `evaluate_task` decorators;
`trace_dir` uses its `Tracing` and exports HF dataset + PNGs. This is intentionally **not a
complete DesktopSession/provider**: shell/files, CSS/native apps, VM lifecycle, scrolling,
dragging and chords are unsupported. Cua-bench's `cb run` constructs sandbox environments;
we do not force the local client into that factory. Call the decorated hooks with a
`MinecraftSession` directly, as the live integration test does.

## Holo agent loop

With the mod running and `HCOMPANY_API_KEY` already in your environment:

```sh
cd ~/repos/mc-observatory/runner
uv pip install -e '.[test]'
.venv/bin/obs run --task get_log --model holo4-27b --tier skills --timing freeze --max-steps 30 --out ../runs/
.venv/bin/obs run --task ladder --model holo4-35b-a3b --tier raw --timing realtime --max-steps 100 --time-budget 1800 --out ../runs/
```

Each invocation creates a unique run directory. **Do not commit runs, screenshots, logs or
traces**; `runs/` and spike outputs are ignored. `--no-trace` runs without cua-bench installed.
Otherwise install the optional local cua-bench integration as above. Available options include
`--history-steps` (default 8 text summaries), `--history-images` (default 1, includes current),
`--temperature`, model sampling `--seed`, `--time-budget` (seconds, default 180),
`--max-cost-usd`, `--failure-window` (default 5) and `--url`. Task definitions determine the
world seed and inventory independently of model sampling seed. The run time budget is explicit;
it defaults to the get-log task's three-minute budget, but may be overridden for longer trials.

`HoloAgent` subclasses cua-bench `BaseAgent` when installed and exposes `perform_task` with
`AgentResult`; it expects our Minecraft session, not a generic sandbox. Its `steps` generator
also works without cua-bench. It calls only screenshot/input/pause APIs. `MinecraftRunner`
separately reads state for grading/diagnostics, appends it to records, and never feeds it into
the model. Prompts contain a concise controls primer, current goal, previous input summaries
and only the configured screenshots. They contain no inventory, coordinates, health, GUI flags,
milestone feedback or other state telemetry. The image files are the exact PNGs sent as data URLs.

- **raw:** named key down/up/press, relative mouse look, mouse button down/up/click, GUI
  `click_at`, text and millisecond waits. Multiple calls run sequentially; no parallel actions.
- **skills:** bounded, timed input-only movement, jump, **relative** yaw/pitch look, held attack,
  use, hotbar selection, inventory open/Escape, GUI click/shift-click and second-based waits.
  No teleporting, block targeting, inventory queries or state-assisted aiming. All calls in
  both tiers require an intent sentence. Coordinates are normalized 0..1000, top-left origin;
  the mod rejects coordinate clicks without a GUI, instead of silently attacking the crosshair.
- **freeze:** pause before screenshot/API, resume for sequential action execution, release
  actual inputs and pause again. Logically held raw inputs are restored during the next action
  window. Runs finish paused. **realtime:** never calls pause/resume; held raw inputs remain
  active during API calls until explicitly released. Vanilla menus can still pause singleplayer.

`holo.PRICING` is a configurable-per-model table in USD per million input/cached/output tokens:
`holo4-27b`: **0.40 / 0.04 / 3.00**; `holo4-35b-a3b`: **0.30 / 0.03 / 2.00**. Any model ID is
accepted; unknown pricing produces `null` cost and disallows a dollar budget until a rate is
added. Cached tokens are subtracted from normal input; reasoning tokens are reported separately
but already included in output billing. Dollar budgets are checked after each response, so one
in-flight request can overshoot; actions stop once the cap is reached. No hidden API retries.

Outputs: `steps.jsonl`, `screenshots/`, `summary.json`, optional cua-bench `trajectory/`. Step
records include index, timestamps, exact image references, model/goal, reasoning (`reasoning_content`
or `reasoning`), intents, parsed/raw calls, executed primitives, API/action timings, token/cost
breakdown, evaluator snapshot, privileged metrics and errors. Summary includes task success,
ladder fraction, wall-clock time-to-reach per milestone, steps, wall time, mean/nearest-rank p95
API latency, cost, tokens and failure tags. A successful get-log task scores **1/13** of the
ladder, not 1.0. State stats can lag ~0.5 seconds. BaseAgent-only use records actions but has no
scoring authority; use `MinecraftRunner` for scored runs.

Failure tags include `api_error`, `no_tool_call`, `invalid_tool_args`, `gui_stuck`,
`stuck_position`, `repeated_action_loop`, `death`, `timeout`, `budget_exhausted`. GUI/repetition
diagnostics reset with progress; movement checks require movement actions over a configurable
window and <0.2 blocks displacement. These are heuristic labels, not proof of model error;
ordinary mining can legitimately repeat actions. Only death, success, limits or external stop
end a run automatically. Additional `action_error`/`metrics_error` tags distinguish transport
or scoring failures. Model errors never trigger privileged corrective gameplay.

For dashboard/voice embedding, pass a `RunController` to agent/runner. Its async `pause()`,
`resume()`, `stop()` and `set_goal(text)` take effect between steps; current API/action batches
are not interrupted by hooks. Controller pauses still consume the wall-clock budget. In
realtime a controller pause stops decisions, not simulation. `set_goal` updates the model's
instruction, not the runner's immutable scoring target. `events` is a bounded asyncio queue
(oldest dropped beyond 100); optional sync/async `callback(record)` receives every completed
step with intent, actions and privileged metrics. Keep dashboard consumers trusted; those
events are not model inputs. Callback exceptions propagate rather than silently losing events.

Mocked tests cover parsing (including malformed args), billing, pixel-only history, freeze
ordering, realtime, macros/shift-click, controller hooks, failure taxonomy and runner scoring:

```sh
.venv/bin/python -m pytest -q tests/test_holo.py
```

The cost cap is checked on every completion, including missing/malformed tools. Task success
also enforces the task JSON deadline, independently of the agent wall-time budget. Before a
terminal failure, the runner allows 600 ms of input-free simulation and re-reads scoring state
to catch stats refreshed every ten ticks, then restores freeze. The settle interval counts
toward the task deadline and is logged as `final_settle_ms`. Screenshot failures never inherit
the preceding step's executed actions.

## Hybrid web dashboard

Three processes, each in its own terminal (keep the API key and optional `OBS_TOKEN` server-side):

```sh
# 1. Minecraft Java (Java 17 / software renderer)
cd ~/repos/mc-observatory/mod
DISPLAY=:0 LIBGL_ALWAYS_SOFTWARE=1 ./gradlew runClient

# 2. FastAPI backend, Python 3.12, one process/worker only
cd ~/repos/mc-observatory
uv pip install --python runner/.venv/bin/python -e 'runner[test]'
# cua-bench is already installed on this VM; on a fresh environment also install:
# uv pip install --python runner/.venv/bin/python -e ~/repos/cua/libs/cua-bench
runner/.venv/bin/python -m uvicorn mc_observatory.server:app --host 127.0.0.1 --port 8000

# 3. React / Vite UI
cd ~/repos/mc-observatory/dashboard
source ~/.nvm/nvm.sh
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. `HCOMPANY_API_KEY` must already be in the backend environment;
never put it into Vite variables or browser requests. The UI proxies `/api` and `/ws` to port
8000. The browser never connects to the mod. Both services are loopback-only; this is not an
authenticated public/multi-user deployment. HTTP and WS origins and Host headers are restricted
to local dashboard addresses. Do not expose these ports or use multiple backend workers.
The backend owns one persistent authenticated mod connection, one active run and its controller.
The CLI must not control Minecraft concurrently with a dashboard run.

REST (`/docs` on port 8000 provides schemas):
- `POST /api/runs`: task/model/tier/timing, `max_steps`, `time_budget`, optional `max_cost_usd`.
  Returns run ID, or 409 if busy, 503 if Minecraft/key is unavailable, 422 for invalid config.
- `GET /api/status`, `/api/runs`, `/api/runs/{id}` (summary, steps and interventions).
- `GET /api/runs/{id}/screenshots/{step}`: exact zero-based agent PNG.
- `POST /api/control`: `{action:pause|resume|stop|set_goal, source:ui|voice|devin, text?:...}`.
  Controls take effect between steps, not in the middle of a model call/action batch.
  Goal changes are accepted after setup and return an expiring, single-use confirmation token.
- `POST /api/control/confirm-goal`: `{token, source}` applies the proposed goal. Merely proposing
  never changes the agent instruction. The scoring target remains the original task.
- `GET /api/leaderboard`: model × tier × task aggregates from summaries; mean/p95 API latency
  is computed from individual steps. Milestone times average reached trials only. Unknown
  pricing is not treated as zero. Failure counts count runs carrying each tag.

`WS /ws/live` publishes `snapshot`, `connection`, `frame` (JPEG), `agent_frame` (exact PNG),
`step`, `overlay`, `intervention`, `summary`, `run_status`, `error` and heartbeat events.
Slow viewers have bounded queues; the oldest queued event is dropped. Reconnecting browsers
receive current frames and reload persisted steps/interventions. Dashboard restart does not
resume an interrupted agent. The run root defaults to repo `runs/`; override with `OBS_RUNS_DIR`.

Run directories include `run.json`, `steps.jsonl`, `summary.json`, screenshots, an
`interventions.jsonl` audit trail, chronological `events.jsonl` (steps + interventions), and
cua-bench trajectory export. Every intervention records UTC timestamp and source. Cua-bench
intervention records retain original timestamps in their data even though exported at run end.
Voice/Gladia and Devin panels are intentionally disabled placeholders for the next phase.

The dev client already persists `pauseOnLostFocus:false`. State now reports
`pause_on_lost_focus` and `window_focused`. A live unfocused test stayed in the world without
a pause menu; the earlier 35b menu-opening cause remains unverified, not attributed to focus.

Checks:
```sh
cd ~/repos/mc-observatory
runner/.venv/bin/python -m pytest -q runner/tests -m 'not live'
runner/.venv/bin/python spike/verify_overlay.py
cd dashboard
npm run build
# Explicit opt-in: performs a paid live trial using the backend key and existing Chrome CDP.
node tests/live.mjs
```

Playwright verified start → pause → propose/confirm goal → resume, replay next frame and
leaderboard, without browser errors. The UI get-log trial ended at 30 steps, score 0,
68.48 seconds, $0.0411436 (`budget_exhausted`); the model did not obtain a log. Screenshots
and overlay comparison evidence are ignored under `spike/ui/`, never committed. World
agent PNGs with overlay off/on were pixel-identical in the comparison; GUI differences were
702 pixels (<0.5%, animated rendering), while the viewer JPEG visibly included the panel.

### Holo smoke observations

Single seeded get-log trials, skills caps 30 steps and raw cap 15, all freeze mode. Runs stop
early on state-verified task success. Wall times below exclude world-reset/setup and trace export.

| Model / tier | Success | Steps | Ladder fraction | Agent wall seconds | Mean / p95 API ms | Cost USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| holo4-27b / skills | yes | 11 | 1/13 | 28.77 | 968.6 / 2544.3 | 0.015458 |
| holo4-35b-a3b / skills | no | 30 | 0 | 68.60 | 999.8 / 1521.2 | 0.02574514 |
| holo4-27b / raw | yes | 3 | 1/13 | 8.35 | 591.7 / 662.0 | 0.0036612 |

The 35b trial repeatedly mined without obtaining a log, then was in GameMenuScreen and
repeatedly clicked normalized y=195/255 instead of the visible Back-to-Game button around
y=325. Tagged `gui_stuck`, `repeated_action_loop`, `budget_exhausted`. The cause of the menu
opening is **unverified** (no Escape tool call was recorded); the missed button targeting was
verified against the exact input image. No state-based recovery was injected. The 27b skills
trial navigated and used a longer mining hold; the raw trial held left mouse and waited.
World seeds matched but vanilla spawn positions differed, so these are smoke checks, not a
controlled model ranking. All three exported JSONL, summaries, exact screenshots and cua-bench
traces locally, with no artifacts committed.

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
