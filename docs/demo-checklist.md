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
  Jev key for the two-model demonstration. Defaults are `holo4-35b-a3b`, reasoning effort `none`,
  `jev-1.14.0`, and `MODEL_TIMEOUT_SECONDS=30`. Use `GAMEPLAY_MODE=state` and
  `RUN_GOAL=nether`, or `RUN_GOAL=early_game` for the wood-to-stone-tools benchmark;
  keep `VALIDATE_HOLO_PLANS=true` for fresh-state Jev checks. See
  [provider contracts](providers.md).
- [ ] From the repository root run
  `uv run holospeed init-bridge --minecraft-dir "/actual/game-directory"`.
  Confirm `config/holospeed.json` was created inside that instance.
- [ ] Start `uv run holospeed serve --mode live` and restart the Minecraft
  instance. Confirm the console identifies **LIVE · MINECRAFT** and a connected
  bridge, rather than a demo fixture.
- [ ] Generate a new Java 1.21.1 Survival/Easy world for seed
  `-7131204740862824481`, commands off and no bonus chest. Set matching
  `MINECRAFT_SEED`, `NETHER_ROUTE=from_scratch`, and
  `CAST_PORTAL_TARGET={"x":4,"y":63,"z":2}`. Verify empty inventory before start.
  Record version, seed, initial state, and human interventions separately; a
  configuration declaration is not proof of the actual world seed.
- [ ] Require all supplies to come from gathering, mining, crafting, and smelting.
  Do not reuse a looted chest, structure furnace, existing obsidian frame, or
  previously advanced world. Roughly two minutes is the performance target;
  do not add artificial waiting or claim an unmeasured time.
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
- [ ] Share the public URL separately from the private operator link. Verify that
  **New run** works without a password and loads a fresh prepared-world copy.
  Resume, Pause, End run, voice, and Devin actions still require operator access.
- [ ] Check command results and the 30-second delivery deadline. Refocus Minecraft
  when guarded rearming reports an unavailable or unfocused game.
- [ ] Keep complete local journals, frames, and compressed state assets. Hosted
  archives are sanitized bounded copies; HLS retains a rolling live buffer.

## Verify control before a long attempt

- [ ] Verify that structured state, Baritone availability, and the
  `smelt:minecraft:iron_ingot` and `cast_portal:minecraft:nether_portal`
  capabilities are reported for the Nether route.
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
- [ ] For the from-scratch route, show the 14 real checkpoints: wood, table,
  wooden pickaxe, stone pickaxe, furnace, iron mined, iron smelted, bucket,
  flint and steel, water collected, lava located, portal built, portal lit, Nether.
  A resource's first acquisition does not establish the skill's entire target
  quantity. Confirm four mined/smelted iron for bucket and ignition, actual flint,
  and nine owned dirt/cobblestone mold blocks after recipe consumption.
- [ ] Verify ten newly cast obsidian cells, actual portal interior blocks, and
  normal bucket/placement/mining interactions. The current mod rejects the old
  structure-looting portal command. No chest loot or existing frame may substitute
  for the required gathering and construction.
- [ ] Confirm Nether entry only from a fresh, ready, living player in
  `minecraft:the_nether`. Preserve the linked state snapshot and screenshot.
  Confirm the completed single-player world pauses after this evidence is saved
  so an unattended player is not left exposed to mobs.
- [ ] Show one failure and recovery: obstruction, missed interaction, or stale
  decision. Check that the approach changes or the agent pauses after its budget.
- [ ] Report success count across repeated attempts, elapsed wall time, completed
  actions, errors, recoveries, and measured provider latency. Session time includes
  pauses; it is not an official Minecraft speedrun timer.
- [ ] Distinguish Holo4 strategy, Jev fresh-state validation/routine selection, and local
  Baritone/recipe execution. Missing providers or deterministic fallback must be
  identified accurately; do not report them as a two-model benchmark. Keep the
  gameplay mode in comparisons because historical visual-mode veto policies differ.
- [ ] End the run and inspect replay. In **Run archive**, select two runs and
  choose **Compare runs**. Check duration, actions, observations, errors, recoveries,
  tokens, and milestone splits against the saved summaries. Keep DEMO/LIVE labels
  visible and do not interpret missing values as zero.

The [verification report](state-aware-verification.md) and
[independent review](run-judge.md) retain actual early-game and historical
ruined-portal results, including model-validation failures. Those timings do not
validate this new mining-and-casting route. [New seed research](new-route-research.md)
confirms natural resources in 1.21.1; complete live execution and measured timing
must be checked independently.

## Voice and live explanation

- [ ] Check that **On a mission** shows actual Holo4 and Jev request lifecycles,
  input sources, recorded decisions, and latency. A completed call must not remain
  animated as live inference. Local skill progress is a separate stage.
- [ ] Open the private operator link before using the microphone or typed commands.
  Start the microphone with an explicit click; check partial captions and final
  transcripts separately. Partial captions must never execute an action.
- [ ] Test a spoken pause/stop, then a bounded resume only in a ready world.
  Inspect the private command acknowledgment and controller journal. Questions
  should use recorded game state; guidance must stay within available skills.
- [ ] Keep the Gladia key on the server and distinguish real microphone/browser
  verification from API or synthetic-PCM tests. Follow [voice setup](gladia.md).

## Analyze a failure after the run

The dashboard's **Devin engineering** menu can prepare the exact redacted brief
for a selected run. Review it and explicitly confirm the fixed 2-ACU cap before
creating an investigation. Public viewers can inspect published findings and
proposed tests but cannot submit sessions. The CLI alternative is:

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
| Nether route is absent | Check `RUN_GOAL=nether`, `NETHER_ROUTE=from_scratch`, the lava landmark, observed prerequisites, and exact `smelt`/`cast_portal` capabilities. |
| Actions are rejected or cancelled | Game focus/pause state, local stop latch, source-frame expiry, or a world transition. Read the acknowledgment reason. |
| Model repeatedly fails | Read provider errors and status codes in the local journal; verify model access, key configuration, and latency budget. |
| Progress label looks wrong | Inspect inventory, dimension, recorded client `verified_steps`, and the linked state; historical visual-mode labels were model claims. |
| Hosted camera target differs from playback FPS | Compare capture age, measured playback FPS, upload logs, and actual game rendering. |
| Replay loses images after moving files | Restore the full run directory, including `frames/`, under the configured runs directory. |
