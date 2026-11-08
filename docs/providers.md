# Provider contracts

Verified against official documentation on 2026-11-08. The default live path is
`GAMEPLAY_MODE=state`: structured state, high-level skill selection, and local
execution through Baritone and Minecraft's normal recipe/interaction APIs.
Adapters use one request per decision with a deadline. Model output is validated
JSON selecting supplied candidates, never executable code. See
[architecture](architecture.md), [setup](setup.md), and
[upstream integration sources and licenses](state-aware-research.md).
The live environment default is `RUN_GOAL=nether`; `early_game` remains available
for the wood-to-tools benchmark. The portal route is supplied only for its
declared fixed seed and an advertised client capability.

## H Company: Holo4 planner

`POST https://api.hcompany.ai/v1/chat/completions`, bearer authentication.
Both `holo4-27b` and `holo4-35b-a3b` are supported. `HOLO_MODEL` selects the model;
the 35B variant performed better in the initial latency probes below.
Use `GET /v1/models` to verify key access. Holo4 requires paid model access.

### Current state-aware strategy

`StrategyProvider.choose(...)` receives structured game state, locally validated
skill candidates, recent outcomes, and optional screenshot context. Holo uses the
native `select_skill` tool with exactly `candidate_id` and a short `rationale`.
The candidate ID must belong to the supplied set; the model cannot change the
skill, item, quantity, coordinates, or deadline. The state-aware output budget is
512 tokens, including hosted reasoning tokens; an observed 256-token response
exhausted its budget before producing a usable selection. This does not change
the legacy compact action adapter's 256-token budget with thinking disabled.

Holo's role is choosing useful resources, progression phases, and alternatives
after a blocked skill. Local code owns reach, path execution, continuous mining,
pickup, recipe interactions, cancellation, and completion checks. A selection
does not establish success. The controller rechecks fresh state before execution;
milestones come from actual inventory, recorded client verification steps, and
dimension evidence rather than model claims. For the portal candidate, the
waypoint and expected chest/frame geometry are explicitly route knowledge to
verify in-game. Neither a selected candidate nor a completed skill acknowledgment
proves Nether entry; the controller checks `minecraft:the_nether` directly.

Prompts summarize inventory counts, nearby resource samples, hazards, screen
state, and recent skill results. Raw local geometry stays with the executor.
Dropped-item stacks and screen slots remain separate from player inventory to
avoid false acquisition counts. Unknown or unsampled terrain remains unknown.
When an image is supplied, it is wrapped in `<observation>`; model screenshots
exclude the HoloSpeed HUD.

Reasoning is configurable with `HOLO_REASONING_EFFORT`. Holo4-27B supports `low`,
`medium`, `xhigh`, and `none`; `high` is invalid and its hosted default is `xhigh`.
Holo4-35B-A3B does **not** have calibrated effort levels: level strings are accepted
and ignored, while `none` explicitly disables thinking. Thus `low` on 35B means
thinking is on, not that a low reasoning budget was applied. H recommends thinking
for agent loops. Usage and raw responses are journaled; thinking traces are never
copied into the next prompt. Explicit context carries durable facts.

### Legacy visual-control adapter

`GAMEPLAY_MODE=visual` retains the earlier screenshot-driven action loop for
comparison and diagnostics. The following control schemas and all latency probes
below describe that path, not the current state-aware skill API.

The legacy adapter declares native action tools with `tool_choice: required`.
GUI coordinates arrive as integers in 0–1000 and are converted to normalized
bridge coordinates. Movement is bounded to one second, while stationary
attack-only mining can last four seconds. Historical milestone labels are model
visual claims with linked evidence, not state-confirmed resource acquisition.

`HOLO_COMPACT=true` exposes controls plus a short `intent` phrase. The phrase is
required for mining and GUI interactions so fresh-frame validation has a target;
ordinary movement and no-input observations may omit it. Historical decisions
with explicit objective, strategy, and expected effect remain compatible. Compact
tools also accept an optional milestone with explicit visible evidence; neither
field is required, and unseen milestones must be omitted. Compact output does not
require model-generated objective, strategy, or expected-effect prose.
Missing metadata is transparently derived from the current objective and validated
inputs: “Control-only plan” and “Outcome is unverified.” It never infers an item,
milestone, or subgoal change from an action. Historical explicit decisions remain
accepted. Set `HOLO_COMPACT=false` for the verbose strategy-and-controls schema.
The output budget is 256 tokens for compact mode with thinking off and 700 tokens
otherwise; unused optional fields are omitted. `propose_decision` is limited to
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
legacy visual controller therefore keeps controls released during Holo planning (30-second
request deadline), captures a new screenshot, and asks Jev to validate the
unexecuted proposal. The fresh frame retains its own 15-second action deadline;
the original frame's lifetime is never extended. Hosted latency remains variable.

### Controlled action-only comparison

Three offline requests used Holo4-35B-A3B, the same recorded 1280×720 screenshot
with leaves obstructing the crosshair, and the same first-log objective. This
asked for a real next action, not a predetermined no-op. All three responses
passed local control validation, reported zero cached input tokens, and were
**not executed in Minecraft**.

| Output mode | Thinking | Latency | Output tokens | Proposed next action |
| --- | --- | ---: | ---: | --- |
| Verbose metadata + controls | Off (`none`) | 19.694 s | 129 | Forward for 10 ticks |
| Action-only | Off (`none`) | 1.206 s | 78 | Left for 10 ticks, yaw −30° |
| Action-only | On (`low`, level ignored on 35B) | 1.534 s | 136, including 73 reasoning | Attack for 10 ticks to clear leaves |

The thinking-on action matched the visible leaf obstruction; revealing or
collecting a log was not verified. This small comparison supported the subsequent
compact visual-control trial, consistent with H's agent-loop guidance. It does
not establish latency for the new skill-selection path or prove that
schema/output length alone caused the difference: hosted load and sampling can
vary. Source requests/responses remain available in the local benchmark journals.

Sources: [API overview](https://hub.hcompany.ai/models-api/overview),
[native function calling](https://hub.hcompany.ai/models-api/build-an-agent/function-calling),
[agent conventions](https://hub.hcompany.ai/models-api/build-an-agent/core-concepts),
[completion reference](https://hub.hcompany.ai/models-api/chat-completions).

## TypeSafe: Jev candidate selection

`POST https://api.typesafe.ai/v1/systemone`, bearer authentication.
Pin `jev-1.14.0` for reproducibility.

### Current state-aware choice

The strategy adapter sends a structured `state` and one `choice` question named
`skill`. Its criteria are the exact locally validated candidate IDs, plus
`__replan__`. Jev handles routine candidate selection without generating new
actions, parameters, milestones, or strategy prose. When a Jev key is configured,
routine boundaries use Jev even for one remaining candidate; phase changes and
recovery return to Holo. This path does not upload an
image and does not require the experimental image header.

Selected IDs, finite confidence metadata, and any returned probability
distribution are validated. Confidence/probability describe the model selection;
they are not extra per-tick execution gates. Explicit `__replan__`, unknown IDs,
or malformed answers return a recorded error/replan outcome. The caller can
rebuild candidates or use its deterministic locally validated fallback. Fresh
state preconditions and the candidate's inventory or dimension postcondition
remain authoritative. The six portal `verified_steps` come from client checks,
not Jev's probability distribution.

### Legacy visual continuation

The earlier screenshot-based Jev adapter remains available for visual mode.
Its vision support is experimental and requires
`TypeSafe-Danger-Zone-Images: true`. PNG/JPEG assets use UUID references and raw
base64, without a data-URI prefix.

The adapter batches a binary choice between executing the exact previous Holo
action or replanning, plus independent danger and continuation questions.
Overlapping safe alternatives previously split probability and caused false
vetoes in recorded tests; see the [evaluation](jev-evaluation.md). Long mining
holds remain unchanged, so Jev cannot cut breaking short. It never invents
strategy or confirms milestones. No-input observations, GUI, and one-shot
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
`plan_source_frame_id` for auditability. The visual controller enables this stage
when Jev is configured and `VALIDATE_HOLO_PLANS=true`. It never confirms
milestones and does not relax the ban on
repeating GUI actions during ordinary continuation. The caller owns one-time
execution and must retain the fresh observation's action deadline.

`ALLOW_EXPLORATION=true` permits Jev to evaluate small world actions as bounded
experiments rather than demanding certainty of progress. This profile applies
only to actions of at most 10 ticks, using movement-direction or attack keys,
with no GUI, cursor, hotbar, scroll, jump, sprint, or use; yaw is at most 30° and
pitch at most 20°. A short leaf-clearing attack or cautious approach can be useful
even when its outcome is uncertain. Concrete danger, invalid targets, and repeated
failed actions still require replanning. Probability thresholds and explicit
replan decisions remain unchanged. Longer mining and GUI actions keep strict
validation. Set `ALLOW_EXPLORATION=false` to restore strict evaluation everywhere.

These visual-mode thresholds and `ALLOW_EXPLORATION` rules do not govern the
state-aware candidate selector or interrupt each tick of a local mining skill.

## Request logging and execution ownership

Both strategy providers use the same request journal: a call ID links the
redacted request written before HTTP starts with response, status, latency,
transport failure, or cancellation. Actual model usage is retained. Complete
records stay in the local archive. The hosted journal keeps sanitized nonimage
request/response details within its upload limits; credentials and inline image
payloads are removed. Full screenshots and compressed state assets stay local.
Provider decisions are separate from requested skills, acknowledgments, running
status, and confirmed outcomes. Neither Holo nor Jev owns an unbounded held key.

Sources: [System One reference](https://docs.typesafe.ai/api/system-one),
[models](https://docs.typesafe.ai/models), [images](https://docs.typesafe.ai/images),
[choice primitive](https://docs.typesafe.ai/primitives/choice).

## Devin: asynchronous failure analysis

PATs use v3: `POST https://api.devin.ai/v3/organizations/{org_id}/sessions`.
Use `DEVIN_ORG_ID`; do not send a PAT to legacy v1 endpoints. Budget sessions with
`max_acu_limit`. Creation returns `session_id`, `url`, and `status`; poll for
`status_detail: finished` as well as terminal status. `structured_output` is
populated by get/list responses. This work must stay outside the gameplay loop.

The [engineering workflow](devin.md) preserves bounded state-aware skill and
inventory evidence, accepts current-fix context, and requests proposed regression
test source with explicit validation status. Submission is capped at 2 ACU and
never retried automatically after an ambiguous failure.

This is a working integration: a real v3 identity request and one capped
investigation of the recorded crafting-table return failure produced structured
analysis and two proposed test patches. Those patches assumed APIs and were
tested against Devin's own stubs, not the current HoloSpeed implementation; they
were retained for review rather than automatically applied. The
[investigation report](devin-investigation.md) links the actual session and records
the evidence and limitations. Current fix context can be supplied with
`analyze --context-file`; `devin-session` reads status and findings. No Devin
session is created automatically by a game tick or a run failure.

Sources: [PAT authentication](https://docs.devin.ai/api-reference/personal-access-tokens),
[create-session reference](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions).
