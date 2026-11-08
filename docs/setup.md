# State-aware live setup

Use Python 3.11+, Java 21, Minecraft Java Edition **1.21.1**, Fabric Loader
**0.16.14 or newer**, and the Fabric API build for **1.21.1**. Run the controller
and game on the same computer. The local control bridge uses loopback and an
instance-specific token; model credentials remain in the controller environment.

## Install and configure

1. From the repository root, install controller dependencies:

   ```sh
   uv sync --extra dev
   ```

2. Copy `.env.example` to `.env`. Keep `GAMEPLAY_MODE=state` for the hybrid
   loop. Set `HCOMPANY_API_KEY` and `JEV_API_KEY` for the two-model demonstration.
   Defaults are `holo4-35b-a3b`, `HOLO_REASONING_EFFORT=none`,
   `jev-1.14.0`, and `MODEL_TIMEOUT_SECONDS=30`; state-aware orchestration applies
   an additional eight-second strategy-selection cap. Record missing providers or local
   fallback use accurately; do not describe a fallback-only run as a model result.
   `HOLO_COMPACT`, `VALIDATE_HOLO_PLANS`, and `ALLOW_EXPLORATION` configure the
   legacy visual-action path rather than the state-aware skill selector.

   The environment-backed goal defaults to `RUN_GOAL=nether`. The implemented
   ruined-portal route requires this exact declaration and a matching world:

   ```dotenv
   GAMEPLAY_MODE=state
   RUN_GOAL=nether
   MINECRAFT_SEED=-1950884254474434485
   ```

   Create or select the intended Java 1.21.1 survival world separately. Setting
   `MINECRAFT_SEED` records route context; it does not change an existing world.
   Use `RUN_GOAL=early_game` for the wood-to-stone-tools benchmark. Direct
   `Settings()` construction retains that early-game default for test fixtures.

3. Find the launcher's actual Minecraft instance directory and launch that
   profile once so its normal files exist. Custom launchers may use a directory
   other than the default `.minecraft`. Configure the bridge:

   ```sh
   uv run holospeed init-bridge --minecraft-dir "/path/to/game-directory"
   ```

   This writes matching credentials to `.env` and the instance's
   `config/holospeed.json`. Set `HOLOSPEED_PORT` first if changing the default
   8765. Use the profile path, not its `mods` subdirectory.

4. Build the HoloSpeed mod:

   ```sh
   cd fabric-mod
   ./gradlew build
   ```

   Use `gradlew.bat build` on Windows. Copy `build/libs/holospeed-0.1.0.jar`
   into the profile's `mods` folder together with Fabric API for 1.21.1. Do not
   install the `-sources.jar`. See the [Fabric guide](../fabric-mod/README.md).

5. Return to the repository root and install the pinned Baritone API build:

   ```sh
   python3 scripts/install-baritone.py --minecraft-dir "/path/to/game-directory"
   ```

   The installer fetches official **baritone-api-fabric-1.11.3.jar**, verifies
   its published SHA-256 and size, and leaves a source/license notice beside it.
   It requires `options.txt` or `launcher_profiles.json`, refuses another
   Baritone JAR or a mismatched existing file, and never starts/restarts Minecraft.
   Use the API build: the standalone artifact obfuscates the integration API.

6. Start the controller, then restart Minecraft with the configured profile:

   ```sh
   uv run holospeed serve --mode live
   ```

   Enter the intended survival world and open `http://127.0.0.1:8765` to inspect
   the connected client. Keep Minecraft focused and use F9 to start/resume.
   F8 stops and latches local control off; F9 rearms it. An explicit local or
   authenticated dashboard Start/Resume also requests guarded rearming. The mod
   still requires a focused, living player in a usable world; it can close the
   pause menu or an empty inventory/crafting screen. Starting the server alone
   does not begin gameplay.

## Check the first run

Confirm structured state, Baritone availability, and the portal capability
`portal:minecraft:nether_portal`. The Nether planner uses the fixed-seed waypoint
`(48,68,48)` only when the declared seed and capability match. The executor must
open the actual chest and verify the actual frame, items, repair, and ignition;
route research does not establish current loot. See [route evidence](nether-route.md).

Watch the verified steps: portal located, chest looted, obsidian acquired,
ignition acquired, portal repaired, portal lit, then Nether entered. A phase
label or accepted request is not a completed step. The final check is a fresh
`minecraft:the_nether` dimension with a ready, living player. Mining/crafting
skills instead complete against actual total inventory counts. Intermediate
client proofs remain available after obsidian or fire charges are consumed.

Holo selects high-level strategy and Jev chooses routine candidates when
configured. Local skills execute across ticks without a cloud call per key
press. [Architecture](architecture.md) explains the division of work.

Test F8 during movement/mining, pause during inference, and disconnect recovery
before a longer attempt. During the expected portal transfer, controls release
and the controller waits up to five seconds for fresh destination state; other
world changes pause the run. Preserve the entire run directory with frames,
compressed state assets, commands, outcomes, and model records. The separate
[live verification report](state-aware-verification.md) records observed results.

## Hosted camera and operator controls

On Linux/X11, install `xwininfo` and FFmpeg with `x11grab` and H.264 encoding.
Set `HOLOSPEED_SITE_TOKEN` and `HOLOSPEED_INGEST_TOKEN` in the ignored `.env`,
matching the site's gateway access and Worker `INGEST_TOKEN`. From the repository
root, with the controller and Minecraft already running:

```sh
uv run --env-file .env python -m holospeed.broadcast \
  --site-url https://holospeed-live-kepard.kepard.chatgpt.site \
  --controller-url http://127.0.0.1:8765 \
  --display :97 --video-fps 60 --runs-dir runs
```

Choose the actual X11 display and controller port. The publisher's own default
controller port is 8768, so the explicit URL above matches the standard 8765
controller. H.264/fMP4 HLS targets 60 FPS with this command; omitting
`--video-fps` follows display refresh, falling back to 60. JPEG fallback defaults
to one FPS. Target capture rate, measured playback FPS, and Minecraft render
rate are distinct. Only the named Minecraft window is captured.

The public viewing URL does not grant control. A separate private
`#control=<CONTROL_TOKEN>` operator link enables New recorded run, Resume,
Pause, and Stop & archive. Keep that link separate from the public URL and
publisher credentials. New recorded run archives the current recording and
starts another in the same Minecraft world; it does not reset the world.

Commands expire after 30 seconds if not dispatched. The publisher persists each
claim before applying a local controller operation. If a request's outcome is
uncertain after interruption, it reports that result without automatically
executing the command again. Stopping the publisher stops observation/command
delivery; use Pause or F8 to stop the agent. Full hosting and archive limits are
in the [dashboard guide](../dashboard/README.md).

## Devin engineering workflow

Configure `DEVIN_API_KEY` and `DEVIN_ORG_ID` for the v3 integration. Run
`uv run holospeed devin-identity` to inspect accessible organization information,
then prepare a local digest with `uv run holospeed analyze "runs/<run-id>"`.
Add `--context-file investigation.json` for current fix/verification context.
Only `--submit` creates a session, capped at 2 ACU; gameplay never waits for it.
Inspect a submitted session using `uv run holospeed devin-session "<session-id>"`.
Generated tests are proposals requiring repository validation. See the
[workflow](devin.md) and [real investigation report](devin-investigation.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Installer rejects the path | Select an existing client profile containing `options.txt` or `launcher_profiles.json`; launch that profile once if needed. |
| Installer finds another Baritone JAR | Review the existing version manually; do not load two Baritone mods. The installer leaves it unchanged. |
| Browser release download fails | The installer tries the exact official GitHub asset API once; both routes use the same pinned digest. |
| Baritone unavailable | Correct API/Fabric artifact, correct profile, supported Minecraft version, and client restart after installation. |
| State request fails | Update both controller and mod; the earlier screenshot-only bridge cannot run state-aware skills. |
| Skill rejected or paused | Read the recorded reason: focus/pause/world state, input ownership, prerequisites, inventory, or deadline may have changed. |
| No candidate is available | Inspect inventory, sampled resources, hazards, and crafting prerequisites; missing sampled terrain is not known air. |
| Nether route is absent | `RUN_GOAL=nether`, exact `MINECRAFT_SEED`, Overworld state, Baritone, and a mod advertising `portal:minecraft:nether_portal` are required. |
| Dashboard start is rejected | Refocus Minecraft and inspect the rearm reason; a different screen, occupied cursor/grid, dead player, or unavailable world can block it. |
| Camera target says 60 but playback is slower | Inspect measured playback FPS, capture age, upload logs, and game rendering; target FPS alone is not throughput evidence. |
| Resource claim looks wrong | Inspect exact inventory slots and milestone evidence; dropped items and model prose do not prove acquisition. |

Baritone source and LGPL notices are linked in the installed source notice and
[research notes](state-aware-research.md). Official sources:
[release](https://github.com/cabaletta/baritone/releases/tag/v1.11.3),
[license](https://github.com/cabaletta/baritone/blob/v1.11.3/LICENSE), and
[artifact guidance](https://github.com/cabaletta/baritone/blob/v1.11.3/SETUP.md).

For a simulated presentation without a game or credentials, use
`uv run holospeed serve --mode demo`. Gladia remains deferred.
