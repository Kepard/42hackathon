# Findings from five live Minecraft trials

These are five successive trials in the same Minecraft 1.21.1 world on
2026-11-08, not five independent seed tests. The world and time of day continued
between trials, and the controller changed during the session. All trials used
Holo4 `holo4-35b-a3b` and Jev `jev-1.14.0`. None produced screenshot evidence of
wood, a crafting table, a wooden pickaxe, or stone tools. These results validate
parts of the control and recovery pipeline, not the early-game acceptance goal.

## Results and evidence quality

| Run suffix | Completed inputs | Frames | Recoveries | Errors | Event-derived stop | Holo median | Jev median |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| `dba03e9d` | 0 | 7 | 5 | 4 | Error at 102.203 s | 19.174 s | 424 ms |
| `1295bd1f` | 0 | 15 | 7 | 0 | Disconnected at 142.972 s | 18.154 s | 274 ms |
| `ad73a1d6` | 1 | 17 | 8 | 1 | Error at 186.545 s | 23.319 s | 283 ms |
| `7e823ced` | 2 | 18 | 8 | 1 | Error at 173.503 s | 17.901 s | 511 ms |
| `7967ff6d` | 5 | 21 | 8 | 1 | Error at 193.576 s | 22.967 s | 338 ms |

Counts come from the saved journals and summaries; latency medians include
recorded `model_call` events, including rejected responses, and exclude canceled
calls. An input counts only when an `ack` reports `completed`. There were no
recorded progression milestones. Full hearts, hunger and empty hotbars remain
visible in the reviewed final frames.

Historical summaries have two limitations: early cleanup overwrote terminal
errors as `interrupted`, and archive duration included idle time between loop
failure and controller shutdown. The stop times above therefore use the journal
event, not `summary.duration_ms`. The subsequent error-preservation fix must not
be read as retroactively correcting the original evidence. Later deadline tests
also do not retroactively turn the final 193.6-second trial into a 180-second run.

The linked journals and screenshots below are local ignored run artifacts. A
fresh clone needs the corresponding run directories to open them.

## What happened

**`dba03e9d`: invalid proposals and unsupported target assumptions.** Holo
responses 5, 11 and 41 requested `forward+attack` for 60 ticks, `forward` for
40 ticks, and `forward+attack` for 70 ticks. The controller rejected these
motion-duration violations. No action reached the executor. Plan 32 described
facing a trunk and beginning to break it, while the
[initial frame](../runs/20261108T073329-dba03e9d/frames/000003-fecbb0bb858a.png)
and [last frame](../runs/20261108T073329-dba03e9d/frames/000039-2d43f0974977.png)
show the same immediate leaf obstruction and empty hotbar. A forward-only plan
also expected a log to be obtained: its expected effect skipped mining and
collection. The visible evidence does not support either resource claim.

**`1295bd1f`: repeated rejected proposals, not failed movement.** Plans 8, 20,
32, 44, 56, 68 and 80 repeated `back` for 10 ticks. All were rejected by Jev;
none executed. Recovery records incorrectly retained `previous_action: null`,
losing the proposal identity needed to recognize the loop. The
[initial frame](../runs/20261108T074008-1295bd1f/frames/000003-152058599aeb.png)
and [last frame](../runs/20261108T074008-1295bd1f/frames/000087-8b0bb07313af.png)
show unchanged leaf occlusion. Ambient clouds and a chicken changed; that is not
evidence of player movement. Rejection feedback must retain the proposed action
without recording it as an executed attempt.

**`ad73a1d6`: one real leaf-clearing input.** Movement proposals were repeatedly
rejected. Holo plan 56 switched to a 10-tick attack. Jev validated fresh frame 57
in 359 ms; action 63 received completed ACK 64 at 118.555 s, with mod ticks
23559 to 23569. The [post-action frame 65](../runs/20261108T074343-ad73a1d6/frames/000065-0bbc5b6b3a3c.png)
shows a canopy opening replacing the
[initial immediate leaf obstruction](../runs/20261108T074343-ad73a1d6/frames/000003-7af30fdf080e.png). This
supports leaf-clearing progress, not wood collection. Later attack proposals
were rejected until the eight-recovery limit stopped the loop.

**`7e823ced`: bounded exploration improved initial execution but did not solve
the loop.** Attack ACK 16 arrived at 20.523 s. Its
[next frame](../runs/20261108T075559-7e823ced/frames/000017-5379a4612144.png)
does not show a clear block-layout change from the
[initial view](../runs/20261108T075559-7e823ced/frames/000003-90e6cc030954.png); the target may
have been out of reach, but that is an inference. Repeated attack plans received
low-confidence Jev responses. Holo eventually switched to forward movement;
ACK 60 at 98.361 s and [frame 61](../runs/20261108T075559-7e823ced/frames/000061-652525f183c0.png)
show slight displacement of nearby leaf geometry. Later forward proposals were
rejected as obstructed. There is no visible wood in the
[last frame](../runs/20261108T075559-7e823ced/frames/000109-85cc3639bea6.png).

**`7967ff6d`: camera recovery changed the view and triggered a new Holo plan.**
Five inputs completed: one attack, one forward move, two Holo-proposed turns,
and one local recovery turn. Plan 28 initially described turning right but
encoded zero rotation and no keys, demonstrating that strategy prose can
contradict the actual input. A later Holo-proposed 20-degree turn executed at
73.987 s and [frame 49](../runs/20261108T080357-7967ff6d/frames/000049-21c297e0d870.png)
shows the changed view. This turn was model-originated, not local recovery.

The local recovery chain is separately visible: event 88 recorded
`origin: local_camera_recovery`, Jev call 91 approved it in 315 ms, action 94
requested an empty-key one-tick turn of yaw 25/pitch 15, and ACK 95 completed
at 120.664 s. [Frame 96](../runs/20261108T080357-7967ff6d/frames/000096-7c0f960e6684.png)
shows the view turned down and right, revealing ground and a nearby birch trunk.
Holo call 99 interpreted that new frame. This establishes real visual recovery,
but subsequent movement still failed validation. The
[last frame](../runs/20261108T080357-7967ff6d/frames/000134-07626166a20e.png)
shows a centered birch trunk, full health and an empty hotbar. The 180-second
budget was exceeded by in-flight inference; no action executed after that
deadline. Remaining-budget cancellation was subsequently added and tested.

## Recovery changes supported by this evidence

The pure helper in [recovery.py](../controller/holospeed/recovery.py) prepares
planning context from at most eight recent inputs and rejection records. It
does not call a model, approve an action, lower a confidence threshold, or read
Minecraft inventory, positions, blocks or entities.

- Separate completed inputs from rejected proposals. An ACK establishes input
  execution; it does not establish movement, block removal, collection or crafting.
  Explicit `unverified_execution`, `bridge_rejected` and `visual_no_change`
  feedback does not count toward repeated proposal rejection. Only proposal
  rejection/provider uncertainty does; legacy records without a kind remain
  unspecified historical feedback. Missing proposal identity cannot establish
  which action repeated.
- Recognize repeated input shapes even if the planner reorders simultaneous
  keys, rewrites its explanation, or changes only hold duration.
- When a replan follows two repeated rejections, return the planning objective
  to the run goal and mark the old subgoal as history. Re-identify the target and
  route from the new screenshot. A new view can justify reconsidering an earlier
  proposal; the helper does not permanently blacklist an action.
- Tie expected effects to the action: movement changes nearby geometry; mining
  needs target/range and crack or removal evidence; resource collection needs
  item evidence. A short attack and a stationary scene alone do not prove mining
  failure. Camera motion supplies information rather than resource progress.
- Consider a bounded camera inspection when the world view is open and
  exploration is enabled. It still requires fresh-frame Jev validation, existing
  action limits, the remaining run budget and the bounded recovery count.
- Preserve an active Jev-validation proposal exactly. The helper must not
  replace its objective or attach a different action during validation.

The integration entry point is `shape_recovery_context(context,
screen_open=observation.screen_open, allow_exploration=settings.allow_exploration)`.
It returns a new context and leaves its input unchanged. Its guidance is
advisory; actual progress remains a screenshot-review question. Unit tests cover
the observed repetition, missing proposal identity, no-op turn, short-vs-stationary
mining interpretation, completed-ACK distinction, GUI boundaries and preservation
of active validation. These tests do not establish successful gameplay.

The next useful trial should measure whether fresh target description and
subgoal reset reduce repeated rejections, and whether a visually identified,
reachable log can be mined and collected. Keep the same safety gates and record
interventions, time of day, all proposals, completed inputs and visible item
evidence. Do not treat additional camera movement as meeting the wood milestone.
