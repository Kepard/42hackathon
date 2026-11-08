# Verification and observed limits

For current state-aware gameplay, see [state-aware verification](state-aware-verification.md).

Tested on 2026-11-08. The results below are historical **visual-control** trials
and their associated automated/interface checks, preceding the state-aware
Baritone/recipe integration. They separate simulation, control integration, and
actual model-driven gameplay. Test counts and latency values describe those
recorded revisions, not a current state-aware benchmark. See
[architecture](architecture.md) for the new mode; no state-aware wood/tool
completion is established by these historical results.

## Automated and interface checks

* 196 Python tests passed after compact planning, exact Jev continuation,
  recovery-context, no-op, and offline-analysis changes, covering provider formats, fresh-frame validation,
  cancellation, credentials, action bounds, recordings, replay, and API controls.
* The Fabric mod built and remapped successfully; 14 Java tests passed.
* Both GitHub CI jobs passed for implementation commit `01c2f56`.
* Browser checks passed start → pause → finish → replay, two-run comparison,
  accurate acknowledged-action counts, and layouts at 1440px and 390px.
* The Python wheel includes the console assets and was built successfully.
* A real Minecraft development client passed the separate
  [bridge/control smoke test](../fabric-mod/VERIFICATION.md).

## First model-driven trial with executed controls

Run `20261108T074343-ad73a1d6` used Java 1.21.1, Survival/Easy, the
[selected fixed seed](demo-seed.md), Holo4-35B-A3B with thinking disabled,
and Jev 1.14.0. Cheats and the bonus chest were disabled.

Holo proposed a ten-tick attack against a leaf obstruction. Jev validated the
proposal on a new screenshot in 359ms, and the real mod acknowledged execution
from tick 23559 to 23569. The subsequent screenshot shows that the immediate
leaf obstruction cleared. This is real gameplay progress, not the demo fixture.

| Result | Observed value |
| --- | --- |
| Time until bounded recovery stop | 186.5 seconds |
| Completed actions | 1 |
| Observations | 17 |
| Recovery attempts | 8 |
| Confirmed wood/tool milestones | 0 |
| Reported input/output tokens | 54,069 / 2,385 |

Before the acknowledged action:

![Fresh model observation before leaf clearing](assets/live-before.png)

First observation after the acknowledged action:

![Model observation after the leaf obstruction cleared](assets/live-after.png)

The hotbar remains empty and the player retains full health/hunger. Later
proposals were repeatedly rejected as obstructed, and the controller stopped
when its recovery budget was exhausted. This does **not** establish reliable
wood collection, crafting, Nether progression, or a completed speedrun.

The actual run in the spectator console, including its stopped error state:

![Live Minecraft run with real inference measurements](assets/console-live.png)

All live attempts remain in the local ignored `runs/` directory,
including failed model outputs, input requests, acknowledgments, timestamps,
usage, and screenshots. Full request bodies and credentials are not published
in this report. The console can replay and compare these local runs.

Subsequent work tests compact planning, exact-action validation, and action
sequences against the next screenshot. Hosted inference variability, Jev's
experimental vision, and repeated rejected plans remain relevant limitations.

## Bounded exploration follow-up

Run `20261108T075559-7e823ced` enabled the user-selected exploration policy.
It completed a short attack after 20.5 seconds and a short forward movement
after 98.4 seconds. The next frame confirmed slight movement. It stopped after
173.5 seconds with 2 completed actions, 18 observations, 8 recoveries, and no
wood milestone. Holo median latency was 17.9 seconds; Jev median was 511ms.
These sequential trials started from different canopy views and are not a
controlled performance benchmark.

The subsequent recovery implementation adds a small camera-only scan after
two identical rejected proposals. The scan is validated by Jev against a fresh
frame, does not move the player or use items, and requires Holo to interpret the
new view. It is bounded to four attempts per run and disabled when exploration
is disabled. This is a local recovery policy, not a model-generated action or
evidence that wood collection now succeeds.

## Real camera recovery check

Run `20261108T080357-7967ff6d` verified the recovery in the actual game:
local proposal event 88 → fresh Jev approval 91 → completed ACK 95 → changed
observation 96 → Holo replanning 99. The scan held no keys, turned 25° right
and 15° down for one tick, and exposed ground and a nearby birch trunk.

![Observed view after Jev-approved camera recovery](assets/recovered-view.png)

The run executed 5 actions and captured 21 observations, then exhausted eight
recovery attempts without obtaining wood. Holo median latency was 23.0 seconds;
Jev median was 338ms. This establishes real visual recovery, not tool collection.

The original loop checked its 180-second budget only between decisions, so this
trial stopped at 193.6 seconds while an inference completed. No action executed
after the deadline. The controller now applies the remaining deadline to
observations, inference, and action execution, cancels in-flight operations when
it expires, and releases inputs. Regression tests cover this behavior and the
preservation of late cancellation acknowledgments in the complete wire journal.

## Compact planning and exact Jev continuation

Run `20261108T083505-2284d213` used compact Holo4-35B control calls with thinking
enabled (`HOLO_REASONING_EFFORT=low`), binary exact-action Jev validation, and the
same restored fixed-seed Survival/Easy world. It approached a nearby birch and
visibly broke the lower log. The stationary 80-tick attack was requested at
event 202, acknowledged at event 205 (ticks 1804–1884), and followed by screenshots
showing the block removed and a dropped birch item. The hotbar remains empty:
**log breaking is demonstrated; collection and crafting are not.**

| Result | Observed value |
| --- | --- |
| Time until recovery stop | 45.214 seconds |
| Completed controls / observations | 8 / 25 |
| Total Jev vetoes | 8 |
| Holo calls / median latency | 9 / 1.626 seconds |
| Jev calls / median latency | 16 / 358 ms |
| Independently confirmed resource milestones | 0 |

![Actual dropped birch item after autonomous mining](assets/compact-mined-log.png)

The model made a premature `wood` claim at event 199, citing an aimed standing
trunk before mining. This claim is preserved as `model_visual_claim`, **not**
accepted as evidence of collection. The console screenshot shows that historical
claim; the journal and image take precedence. The compact prompt now explicitly
requires a visible hotbar/inventory log item before claiming wood, and directs
the agent to collect the dropped item after continuous mining.

![Actual compact-planning run in the console](assets/compact-live-console.png)

Desktop and 390px mobile console checks passed without changing controller state.
The shorter Holo latency was sustained across this small live trial, but prior
runs began from different views and used different prompts, so the comparison
is not a controlled gameplay benchmark. See [the same-frame provider experiment](providers.md)
and [two-frame Jev ablation](jev-evaluation.md) for the narrower comparisons.

## Consecutive-veto follow-up

The approved policy now stops after eight **consecutive** model vetoes, resetting
that counter only after a completed input with actual controls. No-input waits
do not reset it. The cumulative recovery metric, stuck checks, hard deadline,
action limit, and four-camera-scan cap remain independent.

Run `20261108T084145-9a987763` continued from the dropped-log scene with this
policy and the tightened mining/milestone prompt. It recorded 5 completed
controls, 46 observations, four camera scans, and 21 total vetoes, then stopped
at 56.606 seconds after eight consecutive vetoes. Holo median latency was
1.231 seconds across 20 calls; Jev median was 246ms across 26 calls. The scene
became dark, and navigation/pickup remained blocked. No hotbar log or crafted
tool was observed, and the model made no milestone claims.

This verifies the changed stopping policy and continued low inference latency;
it does not establish reliable wood collection. The world was paused and the
controller returned to idle after finalizing the archive. The offline report
separates the 56.602-second active interval from 22.307 seconds of post-stop
inspection included in the 78.913-second archive duration.
