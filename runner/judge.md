# Observe-and-stop watchdog

Run alongside one Nether attempt:

```
runner/.venv/bin/python -m mc_observatory.judge runs/RUN_ID
```

It reads exact run traces every60s. Wood/stone rung budget:5min; iron/obsidian/portal:8min. New evaluator-completed rungs reset the timer. Four consecutive matching failures/API errors, or six identical actions with no scene change, interrupt through the backend stop endpoint. Intent wording is ignored when comparing calls. Only the owning active run may be stopped. No inputs, goals, resets or gameplay advice are sent.

Files: `judge_notes.jsonl`, `judge_metadata.json` (`judge_cost:0`, separate from agent cost), and on interrupt `judge_verdict.json` plus up to3 exact model frames in `judge_evidence/`. This heuristic-only implementation makes no35b calls and does not alter agent accounting or run.json. The supervising engineer reports each interrupt and investigates before starting another run; the watchdog itself never restarts anything.

Manual stop:

```
curl -s -X POST http://127.0.0.1:8000/api/control -H 'Content-Type: application/json' -d '{"action":"stop","source":"devin","text":"judge: reason"}'
```
