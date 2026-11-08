# Devin investigation: returning to the crafting table

On 2026-11-08, HoloSpeed submitted one real, **2-ACU-capped** investigation through
the Devin v3 API. [Open the Devin session](https://app.devin.ai/sessions/7fce0168c2934648ad4e8b8c0af6b7e7).
Devin returned structured failure analysis, improvement suggestions, and two
proposed regression-test files. These are engineering proposals, not changes
automatically applied to HoloSpeed.

## Recorded failure

Run `20261108T093147-8080fe49` completed six local skills: three logs, twelve
planks, a crafting table, eight sticks, a wooden pickaxe, and six cobblestone.
The table was last observed at **(32, 67, 41)** in state event **336**. After
mining, the player was around **(36.5, 52, 35.6)**, holding six cobblestone, six
sticks, and one plank, with no table in inventory. Event **530** confirmed the
cobblestone target; event **535** then reported “No eligible skill”.

Devin correctly distinguished sufficient tool ingredients from a missing usable
crafting table. The historical planner could not return to the table, and one
remaining plank was insufficient to craft a replacement. Its output also
distinguished the final failure from incomplete evidence for the earlier startup
and model-output errors.

## Fix status and useful follow-ups

The main issue was already fixed before submission. Current local code remembers
verified tables per world, discovers nearby tables in loaded cells after restart,
returns through bounded Baritone navigation, and reserves stone-tool ingredients
from scaffolding. Run `20261108T094549-4c23f633` subsequently returned from y52 to
y66 and acquired both stone tools in a 25.54-second continuation with zero skill
failures. That was a continuation, not a fresh full run.

Devin proposed these follow-ups:

- Keep contingency planks/logs, or retrieve the table before leaving it, so a
  destroyed or unreachable table does not leave the recipe chain without a fallback.
- Report a concrete blocked prerequisite and evidence state ID when planning
  cannot continue, rather than a generic terminal error.
- Verify that a loaded missing table is forgotten permanently and cannot be
  credited again after its cell unloads.
- Test material reservation, path cancellation, and exact setting restoration
  across success, failure, timeout, cancellation, and exceptions.

The last two behaviors already exist in current code. They are useful coverage
targets, not confirmed new defects. Some suggestions also require reachable or
otherwise known resources; a proposed fallback does not establish a valid route.

## Generated tests and validation limits

Devin produced `StoneToolTableRegressionTest.java` and
`ReturnNavigationMaterialReservationTest.java`, covering seven proposed cases.
It reported compiling and running them against its own reference and failing
stubs. **It did not run them against HoloSpeed's current planner, Fabric mod, or
Minecraft.** The Java API names in those proposals are assumptions; HoloSpeed's
prerequisite planner is actually Python.

The remote branch available to Devin was `fde6b93`, the earlier visual-control
implementation. The investigation explicitly disclosed that current local
changes and their Java tests were unavailable remotely. The proposed files were
therefore retained as review material and were not added to the build.

The integration itself passes 15 focused tests, including v3 request shape,
bounded usage, no ambiguous retries, secret/artifact redaction, state-aware
inventory evidence, event-priority budgeting, and structured test-source output.
The real investigation created no pull request and did not authorize code pushes,
merges, deployments, live game sessions, or provider credential access.

The complete redacted prompt, supplemental skill history, submission receipt,
latest session response, and structured findings are preserved locally under
`runs/evaluations/devin/`. That directory is ignored by Git. See the
[Devin workflow](devin.md) for repeatable submission and result-inspection commands.
