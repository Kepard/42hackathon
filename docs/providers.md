# Provider contracts

Verified against official documentation on 2026-11-08. Adapters use one request per
decision, with a deadline and no retries against stale screenshots. The local
controller owns controls and recovery; model output is validated JSON, never code.

## H Company: Holo4 planner

`POST https://api.hcompany.ai/v1/chat/completions`, bearer authentication.
Both `holo4-27b` and `holo4-35b-a3b` are supported. `HOLO_MODEL` selects the model;
the 35B variant performed better in the initial latency probes below.
Use `GET /v1/models` to verify key access. Holo4 requires paid model access.

The adapter declares native function tools with `tool_choice: required`, wraps
each screenshot in `<observation>`, and validates one decision. GUI coordinates
arrive as integers in 0–1000 and are converted to the bridge's 0–1 coordinates.
No GUI clicks are accepted while the gameplay screen is active. Actions are
bounded to one second, except stationary attack-only mining can last four seconds
so bare-hand log breaking can finish without resetting progress. Milestone claims
require visible evidence and remain claims to be checked against run observations.

The app defaults to `reasoning_effort: none` for latency. This is a deliberate
trade-off against the provider's recommendation to think in agent loops; initial
hosted smoke tests with thinking exceeded the controller's freshness budget.
Holo4-27b accepts `low`, `medium`, `xhigh`, and `none`; `high` is invalid. The hosted
API itself defaults to `xhigh` when the setting is omitted. Usage and raw responses
are journaled. Thinking traces are never copied into the next prompt; explicit
context carries durable facts.

Native tools use compact, flat fields for strategy and controls, with a 700-token
output budget and omitted optional defaults. `propose_decision` is limited to
20 ticks for movement, aiming, GUI interaction, or short attacks. `mine_block`
permits only a stationary attack lasting 60–80 ticks (80 when omitted), with no
movement, look, hotbar, or click fields. Approach and aim in separate actions;
clearing obstructing leaves with a short attack is a valid intermediate step.
This keeps the longer mining duration out of the movement tool's schema.
The adapter still accepts historical nested
`action`/`cursor` objects or JSON strings, then applies the same strict validation.
Requests are journaled before HTTP starts; responses, transport errors,
and cancellations retain the same call ID. A model completion is a reviewable
claim that pauses controls, not an automatically confirmed milestone.
Invalid decisions report bounded, credential-redacted field errors and schema
guidance to the next planner call; rejected actions are never silently clamped.

### Initial hosted latency probes

These are individual requests on 2026-11-08, using the same synthetic 960×540
image, not real Minecraft performance or latency percentiles. All used
`reasoning_effort: none`; the minimal tool requested only an empty key list and
one tick. The full adapter included the actual strategy prompt and action schema.

| Model | Request | Result |
| --- | --- | --- |
| Holo4-27B | Minimal flat tool | Timed out after 45.2 s |
| Holo4-35B-A3B | Minimal flat tool | Valid tool call in 0.97 s; 39 output tokens |
| Holo4-27B | Full adapter | Timed out after 45.1 s |
| Holo4-35B-A3B | Full adapter | Valid no-input decision in 16.2 s; 107 output tokens |

One subsequent request with the compact flat schema and a recorded Minecraft
world screenshot returned a valid no-input decision in **18.2 s** (1,990 input,
103 output tokens). The image differed from the synthetic fixture, so this is not
a controlled latency comparison. No controls were executed and no gameplay
progress was claimed. The smaller schema did not demonstrate a speed improvement.

The full-adapter success exceeded the 15-second frame freshness budget. The
controller therefore keeps controls released during Holo planning (30-second
request deadline), captures a new screenshot, and asks Jev to validate the
unexecuted proposal. The fresh frame retains its own 15-second action deadline;
the original frame's lifetime is never extended. Hosted latency remains variable.

Sources: [API overview](https://hub.hcompany.ai/models-api/overview),
[native function calling](https://hub.hcompany.ai/models-api/build-an-agent/function-calling),
[agent conventions](https://hub.hcompany.ai/models-api/build-an-agent/core-concepts),
[completion reference](https://hub.hcompany.ai/models-api/chat-completions).

## TypeSafe: Jev continuation decisions

`POST https://api.typesafe.ai/v1/systemone`, bearer authentication.
Pin `jev-1.14.0` for reproducibility. Jev vision is experimental and requires
`TypeSafe-Danger-Zone-Images: true`. PNG/JPEG assets use UUID references and raw
base64, without a data-URI prefix.

The adapter batches a choice between repeating the previous Holo action, using
a shorter variant, or replanning, plus independent danger and continuation
questions. Long mining holds offer only continue or replan, so Jev cannot cut
breaking short. Redundant no-input candidates are omitted. It never invents
strategy or confirms milestones. GUI and one-shot
actions return directly to Holo. Low selected-option probability, hazards, malformed
responses, and inappropriate continuation raise `ReplanRequired` with the
request, response, and latency available to the run journal. Confidence and
probability are distinct: confidence is validated and logged, not used as an
additional acceptance gate. Execution requires selected-option probability ≥0.65,
continuation probability ≥0.70, danger probability <0.35, and a non-replan choice.
These thresholds still need calibration against more recorded Minecraft runs.

A separate batched `blocker` choice supplies actionable abstention feedback:
obstruction, target mismatch, danger, completed subgoal, uncertainty, or none.
These choices map to fixed messages, not arbitrary model text. Holo and Jev
evaluate the immediate effect of the bounded action; they do not require the
entire objective to finish during a movement or leaf-clearing step.

The adapter also supports an explicit `validate_unexecuted_plan: true` context
for a Holo proposal that has not yet acted. It checks that exact proposal against
a newly captured screenshot, with only `continue` or `replan`; accepted actions
are unchanged, including one-shot GUI inputs. Unclear targets, insufficient
evidence, screen mismatches, and danger must replan. Pass `run_goal` and
`plan_source_frame_id` for auditability. The controller enables this stage by
default when Jev is configured (`VALIDATE_HOLO_PLANS=true`). It never confirms
milestones and does not relax the ban on
repeating GUI actions during ordinary continuation. The caller owns one-time
execution and must retain the fresh observation's action deadline.

A batched blocker question makes abstentions useful to the next Holo plan:
obstruction, mismatched target, danger, completed goal, or uncertainty. Clearing
leaves is valid intermediate progress; the next bounded action need not finish
an entire crafting objective. No threshold bypass is used to force progress.

Sources: [System One reference](https://docs.typesafe.ai/api/system-one),
[models](https://docs.typesafe.ai/models), [images](https://docs.typesafe.ai/images),
[choice primitive](https://docs.typesafe.ai/primitives/choice).

## Devin: asynchronous failure analysis

PATs use v3: `POST https://api.devin.ai/v3/organizations/{org_id}/sessions`.
Use `DEVIN_ORG_ID`; do not send a PAT to legacy v1 endpoints. Budget sessions with
`max_acu_limit`. Creation returns `session_id`, `url`, and `status`; poll for
`status_detail: finished` as well as terminal status. `structured_output` is
populated by get/list responses. This work must stay outside the gameplay loop.

Sources: [PAT authentication](https://docs.devin.ai/api-reference/personal-access-tokens),
[create-session reference](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions).
