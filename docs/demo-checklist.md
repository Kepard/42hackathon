# Demo checklist

Use this checklist to prepare a truthful, repeatable HoloSpeed demonstration.
The console simulation and live Minecraft are separate operating modes.

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
- [ ] Copy `.env.example` to `.env` and configure the local H Company key and
  optional Jev key. Defaults are `holo4-35b-a3b`, reasoning effort `none`,
  `jev-1.14.0`, and `MODEL_TIMEOUT_SECONDS=30`. Leave
  `VALIDATE_HOLO_PLANS=true` for fresh-frame validation. See
  [provider contracts](providers.md).
- [ ] From the repository root run
  `uv run holospeed init-bridge --minecraft-dir "/actual/game-directory"`.
  Confirm `config/holospeed.json` was created inside that instance.
- [ ] Start `uv run holospeed serve --mode live` and restart the Minecraft
  instance. Confirm the console identifies **LIVE · MINECRAFT** and a connected
  bridge, rather than a demo fixture.
- [ ] Enter a survival world with a documented seed and nearby wood/exposed stone.
  Record version, seed, starting conditions, and any human interventions separately;
  the visual-only bridge does not read the world seed for you.
- [ ] Confirm the HoloSpeed HUD fits the chosen resolution/GUI scale without
  obscuring the hotbar or crafting area. The model's archived frame should contain
  vanilla UI but not the HoloSpeed HUD.
- [ ] Keep Minecraft focused and press **F9** to start/resume through the
  controller. Paused or unfocused gameplay rejects/cancels controls. Refocus
  Minecraft after using the browser; F9 avoids starting from an unfocused window.

## Verify control before a long attempt

- [ ] Press **F9** to start a short live run. With Jev enabled, verify that the
  journal contains the initial Holo plan, a second screenshot, Jev validation,
  and an action/ACK tied to that second frame. No action should execute between
  the plan and validation. Both model latencies and token usage should be counted.
- [ ] Press **F8** during movement or mining. Confirm local inputs release and
  the controller pauses. F8 latches the mod off; press **F9** to rearm and request
  a fresh start/resume. Check these controls with the inventory open too.
- [ ] Pause from the console during inference; confirm no late movement begins.
- [ ] Disconnect the controller during a held action. Confirm the mod releases
  inputs through its disconnect/watchdog path before reconnecting.
- [ ] Resume only after returning to a usable world view. Verify that resumption
  captures a fresh observation and replans rather than reusing an old action.

These checks require a real Minecraft client; passing the automated or simulated
tests does not establish their actual in-game behavior. Do not describe a feature
as gameplay-validated until you have observed its evidence.

## Demonstrate measurable progress

- [ ] Start with one goal: visible wood collection, then table, wooden pickaxe,
  and stone tools. Verify the resulting items in screenshots before counting
  each milestone; a model's claim alone is insufficient.
- [ ] Show one failure and recovery: obstruction, missed interaction, or stale
  decision. Check that the approach changes or the agent pauses after its budget.
- [ ] Report success count across repeated attempts, elapsed wall time, completed
  actions, errors, recoveries, and measured provider latency. Session time includes
  pauses; it is not an official Minecraft speedrun timer.
- [ ] Distinguish Holo4 planning from Jev's fresh-frame validation and continuation
  checks. A missing Jev key gives a Holo-only run; it must not be presented as a
  two-model benchmark. Rejected validation must cause replanning without execution;
  eight repeated abstentions stop the loop for review.
- [ ] End the run and inspect replay. In **Run archive**, select two runs and
  choose **Compare runs**. Check duration, actions, observations, errors, recoveries,
  tokens, and milestone splits against the saved summaries. Keep DEMO/LIVE labels
  visible and do not interpret missing values as zero.

The initial implementation has not demonstrated reliable real-game wood/tool
completion, Nether progression, or a full speedrun. See the
[gameplay acceptance criteria](gameplay.md) for the experiments needed next.

## Analyze a failure after the run

1. Preserve the entire run directory. Downloading the JSONL journal alone does
   not include the separate screenshot files used by the visual replay.
2. Run `uv run holospeed analyze "runs/<run-id>"` to inspect the local digest.
   This dry run performs no Devin request.
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

## Troubleshoot the first connection

| Symptom | Check |
| --- | --- |
| Live controller refuses to start | Run `init-bridge` from the repository root and ensure `.env` contains the generated bridge token. |
| Bridge stays offline | Correct Minecraft/Fabric version, mod and Fabric API installed, matching local token/port, instance restarted after configuration. |
| Observe times out | Enter a world; the client needs an active rendered gameplay frame. |
| Actions are rejected or cancelled | Game focus/pause state, local stop latch, source-frame expiry, or a world transition. Read the acknowledgment reason. |
| Model repeatedly fails | Read provider errors and status codes in the local journal; verify model access, key configuration, and latency budget. |
| Progress label looks wrong | Inspect the milestone's evidence and frame. Live labels are model claims. |
| Replay loses images after moving files | Restore the full run directory, including `frames/`, under the configured runs directory. |

Gladia voice interaction is intentionally outside this demonstration.
