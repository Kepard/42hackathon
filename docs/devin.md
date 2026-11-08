# Devin engineering investigations

Devin runs outside the Minecraft control loop. It reviews a bounded diagnostic
digest, investigates failures, generates proposed regression-test source, and
suggests minimal improvements. Gameplay never waits for its response.

## Use the dashboard

Open **Devin engineering**, select a recorded run, and choose **Prepare
investigation**. Every viewer can use this workflow without an account or
private link. The controller
prepares and persists the exact redacted prompt; the dashboard displays that
prompt, its SHA-256 digest, and the fixed **2-ACU cap**. Review the brief and
confirm **Create investigation** to submit it. The browser sends only the run ID
and reviewed digest, so a later gameplay event cannot silently change the
approved prompt.

Submission runs asynchronously. The controller stores a durable receipt before
contacting Devin and accepts only one investigation per recorded run. Duplicate
clicks return that existing receipt. A timeout, interrupted submission, or restart
with an unfinished receipt becomes **unknown** and is never submitted again
automatically; inspect Devin history before taking further action.

Session status and findings update outside the game loop. Everyone can prepare
briefs, create reviewed sessions, and read status, findings, and proposed test
source. The exact redacted prompt is available in the preparation command result
and local brief file; run events and the archived engineering summary omit it.
Proposed source is displayed as text and is not executed or applied.

Run artifacts are stored under `runs/<run-id>/engineering/`: immutable
`brief-<sha256>.json`, the creation/session responses, and a bounded public
`latest.json`. Publishing copies only the public record into the run archive.
The existing real [table-return investigation](devin-investigation.md) is attached
to its historical run for inspection without creating a new session.

## Use the CLI

The integration uses the **Devin v3 API** with a personal access token and an
organization ID. Keep `DEVIN_API_KEY` and `DEVIN_ORG_ID` in the ignored `.env`.
Read the accessible organization information without creating a session:

```sh
uv run holospeed devin-identity
```

Prepare and inspect the exact diagnostic prompt locally:

```sh
uv run holospeed analyze "runs/<run-id>"
```

To tell Devin about fixes made since that run, provide a JSON context file with
any of these string fields: `current_changes`, `verification`, `known_limitations`,
`analysis_focus`, and `source_revision`. For example:

```json
{
  "current_changes": "The client now remembers a placed table and returns to it before crafting.",
  "verification": "A real continuation acquired both stone tools; a fresh full run is still pending.",
  "analysis_focus": "Propose cancellation and table-invalidation regression tests.",
  "source_revision": "The current state-aware changes are local and may not yet be on GitHub."
}
```

```sh
uv run holospeed analyze "runs/<run-id>" --context-file investigation.json
uv run holospeed analyze "runs/<run-id>" --context-file investigation.json --submit
```

`--submit` creates **one session capped at 2 ACU** and may incur provider usage.
It returns the session ID and URL. Submission never happens on import, on a run
failure, or in the game tick loop. It does not automatically retry: a timeout can
mean that a billable session was created but its response was lost. Inspect
Devin's session history before retrying an ambiguous submission.

Read status and results using the returned ID:

```sh
uv run holospeed devin-session "<session-id>"
```

Completion can be reported as `status: running` with `status_detail: finished`.
Check both fields. `structured_output` contains a summary, likely causes,
proposed tests/fixes, missing evidence, and `test_patches`. Each test patch has an
intended repository-relative path, purpose, source code, and validation status.
Proposed code is returned as data; the controller does not execute or apply it.
Review it against the current implementation and run the relevant tests before
using it.

## Evidence and boundaries

The digest prioritizes skill starts/results, errors, milestones, and inference
metrics before repetitive telemetry. It keeps actual Minecraft inventory counts,
player position/health, table memory, and state IDs. Skill acceptance is distinct
from inventory-confirmed completion. Model claims and dropped items do not prove
acquisition. The sample is limited to 40 events and an 18,000-character JSON
budget, with omitted-event counts explicitly recorded; it is not the complete
run archive.

Only allowlisted diagnostic fields leave the machine. Provider request/response
bodies, screenshot payloads, state-file attachments, headers, arbitrary inventory
keys, local paths, and configured credentials are omitted or redacted. Current
engineering notes use the same text redaction and a bounded field allowlist.
The complete original journal and compressed state snapshots remain local.

Sessions target only `Kepard/42hackathon` and its current default branch, with
memory disabled and no secret/knowledge attachments. The prompt requests
analysis and test/patch suggestions only; it forbids pushes, PR creation, merges,
deployments, live Minecraft/model sessions, and access to other repositories.
Remote source can lag local changes, so the analysis must identify missing code
and evidence rather than invent verification results. Dashboard briefs include the
controller checkout commit when available and explicitly note that uncommitted
changes may differ. Submission ownership is durably claimed across controller
processes before contacting Devin; an uncertain response is never automatically retried.

## Verification

Focused tests cover v3 requests, the 2-ACU limit,
credential and artifact redaction, strict state/inventory evidence, event-priority
budgeting, public planning/validation evidence, context updates, structured test
source, exact reviewed prompts, concurrent controller ownership, cancellation-safe
game shutdown, and ambiguous failures without retries. A real v3 identity request and one capped investigation were submitted
on 2026-11-08 against the recorded table-return failure. It returned structured
analysis and two proposed test patches; those patches use assumed APIs and were
tested only against Devin's own stubs. See the [investigation report](devin-investigation.md)
for findings and validation limits.

References: [PAT authentication](https://docs.devin.ai/api-reference/personal-access-tokens),
[create session](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions),
and [get session](https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session).
