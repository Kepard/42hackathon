# Jev continuation evaluation

Evaluated on 2026-11-08 using saved Minecraft screenshots. No game controls were
executed by this evaluation. The live comparison made two Jev API calls; no Holo
requests were made.

## Finding

The continuation contract asks Jev to choose between two often acceptable actions
(`continue` and a shorter `careful` variant), then requires the *selected option*
to have probability at least 0.65. This confuses certainty about **which safe
duration to prefer** with certainty that **any action is safe and useful**.

Use binary verification of one exact bounded candidate: `continue` or `replan`.
Keep the existing acceptance gates unchanged:

* Selected option is `continue`, with probability at least 0.65.
* Immediate-danger score is below 0.35.
* Mechanical-action suitability is at least 0.70.
* Local action bounds, stale-frame checks, GUI checks, and input watchdogs still
  apply independently.

Do not use the sum of two safe-looking options as permission to execute an
arbitrary one. Instead, remove the competing candidate and ask Jev about the
exact action that would execute. If a shorter variant is needed, construct it
deterministically before verification, with its own exact duration and target.
Never shorten stationary log mining below its required hold duration.

## Recorded evidence

The audit covered 41 Jev responses from these five live runs:

* `20261108T073329-dba03e9d`
* `20261108T074008-1295bd1f`
* `20261108T074343-ad73a1d6`
* `20261108T075559-7e823ced`
* `20261108T080357-7967ff6d`

Applying the current probability, danger, and suitability gates to the saved
responses yields nine accepted and 32 rejected decisions. The runs used evolving
prompt/gate versions, so these are a diagnostic replay of the current gates,
not a claim that nine actions actually executed.

No response crossed the danger threshold. There were 24 low-suitability scores,
19 low selected-option probabilities, and 13 explicit `replan` choices; these
groups overlap. Seven calls were continuation calls with a `careful` competitor.
All seven failed the current gates. Six failed **only** the selected-option
probability gate even though Jev preferred an action, its suitability score
passed, and its danger score passed. The seventh also had low suitability.

This evidence does not show that every veto was wrong. The images were dark,
leaf-covered canopy views. Some movement targets or attack reach were genuinely
unclear. An earlier no-input proposal promised that a tree would become visible,
which its controls could not cause. Rejecting that promised outcome is reasonable;
the planner should describe the intended observation step accurately.

## Controlled binary comparison

Both examples use run `20261108T080357-7967ff6d`. The historical response sequence
identifies its paired request and exact image. The new request changed only two
things: removed `state.candidates.careful` and removed the corresponding choice
criterion. Image, exact `continue` action, context, other question wording, model
(`jev-1.14.0`), and acceptance thresholds stayed unchanged.

| Recorded response | Exact action | Historical three-way probabilities | Historical suitability / danger | New binary probabilities | New suitability / danger | New latency | Result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 51 | Stationary 20° yaw, 1 tick | Continue 0.61; careful 0.27; replan 0.12 | 0.83 / 0.04 | Continue 0.82; replan 0.18 | 0.82 / 0.03 | 524 ms | Reject → accept |
| 71 | Forward, 10 ticks | Continue 0.63; careful 0.27; replan 0.10 | 0.85 / 0.02 | Continue 0.89; replan 0.11 | 0.84 / 0.02 | 462 ms | Reject → accept |

These are single requests on two recorded frames. Sampling variation, caching,
and hosted load were not controlled. They support the contract change as an
explanation for these split votes; they do not establish a calibrated safety
classifier or a general gameplay success rate. Latencies include client setup
and the request. Raw evaluation results stayed in local `/tmp` files.

## Further contract changes to evaluate

1. **Describe the immediate mechanical effect separately from the goal.** A
   stationary camera turn can reveal a new view; it cannot promise that an unseen
   birch tree will appear. A short approach need not collect wood. A leaf-clearing
   attack should not be rejected merely because leaves obstruct the eventual
   route. Keep evidence for target type, reach, and movement support separate.
2. **Distinguish action classes.** A no-input observation has no movement effect.
   A camera-only action needs an active gameplay view. An attack needs an
   appropriate crosshair target and adequate duration. Movement needs a safe
   immediate direction. A generic question about achieving the whole objective
   imposes the wrong evidence requirement on several of these actions.
3. **Keep rejection separate from failed execution.** A Jev veto is an unexecuted
   proposal. Repeated vetoes do not prove the player tried and failed to move or
   mine. Feeding them back as executed failures can cause progressively stronger
   rejection without any new outcome evidence. Track `proposal_rejected` and
   `executed_no_progress` separately, with frame and action IDs.
4. **Make blocker explanations descriptive.** A leaf obstacle may be the intended
   target of a clearing action. In the camera example, Jev still labeled
   `obstruction` after approving the turn. An optional blocker choice should not
   silently become a second veto. Separate observed obstruction from whether it
   makes this specific control invalid.

`scripts/evaluate_jev.py --variant focused` contains an experimental,
action-specific question set for follow-up evaluation. It has **not** been
validated by the two-call experiment above; production should adopt the measured
binary ablation first rather than attributing results to an untested redesign.

## Reproduce without controlling the game

Inspect the historical result and proposed questions offline:

```sh
uv run python scripts/evaluate_jev.py runs/20261108T080357-7967ff6d 51 --variant binary
```

Make one explicitly requested API call using the saved frame and local
`JEV_API_KEY`:

```sh
uv run python scripts/evaluate_jev.py runs/20261108T080357-7967ff6d 51 --variant binary --submit
```

Each invocation with `--submit` makes exactly one request and can incur provider
usage. Logs and images are local run artifacts and are not committed. A fresh
checkout therefore needs its own recorded run and response sequence.

Official references: [choice probabilities and confidence](https://docs.typesafe.ai/primitives/choice),
[parallel questions](https://docs.typesafe.ai/api/system-one), and
[experimental image input](https://docs.typesafe.ai/images).
