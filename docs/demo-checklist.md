# Demo checklist

Use this checklist to prepare a truthful, repeatable HoloSpeed demonstration.
The console's scripted early-game simulation and live Minecraft are separate
operating modes. The environment-backed live goal now defaults to Nether entry.

## Rehearse the presentation

- [ ] From the repository root, run `uv sync --extra dev` and
  `uv run holospeed serve --mode demo`.
- [ ] Open `http://127.0.0.1:8765`. Confirm **DEMO · SIMULATION** and that frames
  identify themselves as synthetic; no Minecraft or provider calls are involved.
- [ ] Start the sequence and inspect objectives, requested inputs, latency traces,
  activity, and milestones. Simulation timings are not real model benchmarks.
- [ ] Pause during an action, then resume. Confirm keys clear while paused.
- [ ] End the run. Open **Run archive**, replay it, scrub backward and forward,
  change playback speed, and download the event journal.
- [ ] Check that the corresponding `runs/<run-id>/` contains `summary.json`,
  `events.jsonl`, and the recorded frames. Keep the directory intact for replay.

## Prepare the live client

- [ ] Use Java 21, Minecraft Java Edition 1.21.1, Fabric Loader 0.16.14 or newer,
  and the Fabric API build for 1.21.1. Verify the launcher's actual instance path.
- [ ] Build in `fabric-mod` with `./gradlew build` (`gradlew.bat build` on
  Windows). Install `build/libs/holospeed-0.1.0.jar` and Fabric API into that
  instance's `mods` folder.
- [ ] Install the pinned Baritone API mod with
  `python3 scripts/install-baritone.py --minecraft-dir "/actual/game-directory"`.
  See [setup](setup.md) for the verified artifact and duplicate-version checks.
- [ ] Copy `.env.example` to `.env` and configure the local H Company key and
  optional Jev key. Defaults are `holo4-35b-a3b`, reasoning effort `none`,
  `jev-1.14.0`, and `MODEL_TIMEOUT_SECONDS=30`. Use `GAMEPLAY_MODE=state` and
  `RUN_GOAL=nether`, or `RUN_GOAL=early_game` for the wood-to-stone-tools benchmark;
  `VALIDATE_HOLO_PLANS` applies to the legacy visual-action loop. See
  [provider contracts](providers.md).
- [ ] From the repository root run
  `uv run holospeed init-bridge --minecraft-dir "/actual/game-directory"`.
  Confirm `config/holospeed.json` was created inside that instance.
- [ ] Start `uv run holospeed serve --mode live` and restart the Minecraft
  instance. Confirm the console identifies **LIVE · MINECRAFT** and a connected
  bridge, rather than a demo fixture.
- [ ] Enter the Java 1.21.1 survival world for seed `-1950884254474434485` and set
  the same `MINECRAFT_SEED` declaration for the implemented portal route.
  Record version, seed, starting conditions, and any human interventions separately;
  the configured seed is a declaration, not proof of the world's actual seed.
- [ ] Confirm the HoloSpeed HUD fits the chosen resolution/GUI scale without
  obscuring the hotbar or crafting area. The model's archived frame should contain
  vanilla UI but not the HoloSpeed HUD.
- [ ] Keep Minecraft focused and press **F9** to start/resume through the
  controller. Paused or unfocused gameplay rejects/cancels controls. Refocus
  Minecraft after using the browser; F9 avoids starting from an unfocused window.
  Explicit authenticated dashboard Start/Resume also requests guarded rearming.

## Prepare the hosted presentation

- [ ] Start the Minecraft-window publisher following [setup](setup.md) and the
  [dashboard guide](../dashboard/README.md), with the actual controller URL and X11
  display. Use `--video-fps 60` for the 60 FPS HLS target.
- [ ] Confirm live HLS playback, current capture age, and measured browser playback
  FPS. The encoder target does not establish 60 distinct game frames per second.
  Check the one-FPS JPEG fallback if video is unavailable.
- [ ] Share the public viewer URL separately from the private operator link.
  Verify that New recorded run, Resume, Pause, and Stop & archive require operator
  access. A new recorded run uses the current Minecraft world; it does not reset it.
- [ ] Check command results and the 30-second delivery deadline. Refocus Minecraft
  when guarded rearming reports an unavailable or unfocused game.
- [ ] Keep complete local journals, frames, and compressed state assets. Hosted
  archives are sanitized bounded copies; HLS retains a rolling live buffer.

## Verify control before a long attempt

- [ ] Verify that structured state, Baritone availability, and the
  `portal:minecraft:nether_portal` capability are reported for the Nether route.
  Press **F9** for a short live run; inspect state, candidate selection, skill
  request/acknowledgment, running status, and outcome evidence. Mining/crafting
  use actual inventory; Nether completion requires the destination dimension.
  A skill accepted by the client is not yet complete. Record provider use/latency.
- [ ] Press **F8** during movement or mining. Confirm local inputs release and
  the controller pauses. F8 latches the mod off; **F9** locally reenables control
  and requests start. Authenticated Start/Resume instead uses guarded rearming.
  Check that HTTP/dashboard rearming rejects occupied crafting slots/cursor.
- [ ] Pause from the console during inference; confirm no late movement begins.
- [ ] Disconnect the controller during a held action. Confirm the mod releases
  inputs through its disconnect/watchdog path before reconnecting.
- [ ] Resume only after returning to a usable world. Verify that resumption
  reads fresh state and replans rather than reusing a stale candidate.
- [ ] During the active portal skill's expected dimension transfer, confirm inputs
  release and fresh destination state arrives within the five-second grace.
  Other world changes should pause the run.

These checks require a real Minecraft client; passing the automated or simulated
tests does not establish their actual in-game behavior. Do not describe a feature
as gameplay-validated until you have observed its evidence.

## Demonstrate measurable progress

- [ ] For the early-game benchmark, show acquired wood, table, wooden pickaxe,
  and both stone tools.
  Verify actual inventory/table state and preserve a supporting screenshot;
  dropped items and a model's claim do not establish acquisition.
- [ ] For Nether entry, show the fixed-seed route and actual chest/frame checks.
  The direct route does not require stone tools. Verify portal located, chest
  looted, obsidian acquired, ignition acquired, portal repaired, and portal lit
  against their recorded client proofs. Consumed supplies retain their verified
  acquisition history; phase labels alone do not establish a milestone.
- [ ] Confirm Nether entry only from a fresh, ready, living player in
  `minecraft:the_nether`. Preserve the linked state snapshot and screenshot.
- [ ] Show one failure and recovery: obstruction, missed interaction, or stale
  decision. Check that the approach changes or the agent pauses after its budget.
- [ ] Report success count across repeated attempts, elapsed wall time, completed
  actions, errors, recoveries, and measured provider latency. Session time includes
  pauses; it is not an official Minecraft speedrun timer.
- [ ] Distinguish Holo4 strategy from Jev's routine candidate selection and local
  Baritone/recipe execution. Missing providers or deterministic fallback must be
  identified accurately; do not report them as a two-model benchmark. Keep the
  gameplay mode in comparisons because historical visual-mode veto policies differ.
- [ ] End the run and inspect replay. In **Run archive**, select two runs and
  choose **Compare runs**. Check duration, actions, observations, errors, recoveries,
  tokens, and milestone splits against the saved summaries. Keep DEMO/LIVE labels
  visible and do not interpret missing values as zero.

Actual resource collection and a separate stone-tools continuation are recorded
in [state-aware verification](state-aware-verification.md). Fixed-seed development
trial `20261108T100936-acd35b07` started with empty inventory and reached the
Nether goal at 25.304 seconds, with all seven portal checkpoints supported and
zero recorded errors or recoveries. The [independent review](run-judge.md) checked
the linked state hashes and visual evidence. A fresh repeat after the final merge
remains pending. The [route notes](nether-route.md) distinguish seed research from
current-world checks; one fixed-seed trial does not establish full-run or
random-seed reliability.

## Analyze a failure after the run

1. Preserve the entire run directory. Downloading the JSONL journal alone does
   not include the separate screenshot files used by the visual replay.
2. Run `uv run holospeed analyze "runs/<run-id>"` to inspect the local digest.
   Add `--context-file investigation.json` for current fixes and verification
   context. This dry run performs no Devin request.
3. If submitting, configure `DEVIN_API_KEY` and `DEVIN_ORG_ID`, then run
   `uv run holospeed analyze "runs/<run-id>" --submit`. This explicitly creates
   one Devin session capped at 2 ACU; nothing starts automatically during play.
4. Save the returned session ID and inspect it with
   `uv run holospeed devin-session "<session-id>"`. Findings propose tests and
   fixes; validate them against the actual run before applying changes.

The digest excludes attachments, raw model payloads, and credentials, and only
contains a bounded diagnostic sample. Devin should report missing evidence,
not infer facts from screenshots it did not receive. A submission timeout can
be ambiguous; inspect session history before retrying.

A [real Devin investigation](devin-investigation.md) returned structured findings
and proposed Java tests for the table-return failure. Those proposals still need
validation against the current repository; remote stub tests are not local
regression-test results. See the complete [Devin workflow](devin.md).

## Troubleshoot the first connection

| Symptom | Check |
| --- | --- |
| Live controller refuses to start | Run `init-bridge` from the repository root and ensure `.env` contains the generated bridge token. |
| Bridge stays offline | Correct Minecraft/Fabric version, mod and Fabric API installed, matching local token/port, instance restarted after configuration. |
| Observe/state times out | Enter a world; use matching current controller/mod versions and an active client. |
| Mining reports Baritone unavailable | Install the API Fabric 1.11.3 JAR into this profile, avoid duplicate versions, and restart the client. |
| Nether route is absent | Check `RUN_GOAL`, the exact seed declaration, Overworld state, Baritone, and the advertised portal capability. |
| Actions are rejected or cancelled | Game focus/pause state, local stop latch, source-frame expiry, or a world transition. Read the acknowledgment reason. |
| Model repeatedly fails | Read provider errors and status codes in the local journal; verify model access, key configuration, and latency budget. |
| Progress label looks wrong | Inspect inventory, dimension, recorded client `verified_steps`, and the linked state; historical visual-mode labels were model claims. |
| Hosted camera target differs from playback FPS | Compare capture age, measured playback FPS, upload logs, and actual game rendering. |
| Replay loses images after moving files | Restore the full run directory, including `frames/`, under the configured runs directory. |

Gladia voice interaction is intentionally outside this demonstration.
