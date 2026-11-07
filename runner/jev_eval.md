# Offline Jev pixel evaluation

This module never connects to Minecraft, executes moves, or changes Holo's gameplay.

```sh
uv pip install --python runner/.venv/bin/python -e 'runner[jev]'
runner/.venv/bin/python -m mc_observatory.jev_eval prepare --runs runs --dataset "$HOME/jev-study" --count 200
# Inspect sheet-*.png and add labels.json before reading predictions.
runner/.venv/bin/python -m mc_observatory.jev_eval evaluate --dataset "$HOME/jev-study"
runner/.venv/bin/python -m mc_observatory.jev_eval summarize --dataset "$HOME/jev-study"
```

Set `TYPESAFE_API_KEY` in the environment; never place it in a dataset or command argument.
The pinned SDK sends the image/Box opt-in header. The model is `jev-1.14.0`.
Each frame gets one request with four questions per phase: sequential and concurrency4.
Only the unmodified screenshot and static questions enter the request, not labels,
source telemetry, route hints, run names, or model intentions.

Preparation requires a successful API response and the current screenshot's membership
in its recorded `input_image_refs`. It deduplicates image bytes, preserves SHA256 and
input provenance, and samples round-robin across runs. It refuses to overwrite a study.
The only automatic label is the first pre-action frame's initial hotbar inventory when
available. Post-action telemetry and historical evaluator milestones do not prove a
later screenshot's inventory, crosshair target, or outline; those require manual labels.

`labels.json` maps string frame IDs to label overrides. Binary labels are true/false/null;
unknowns are excluded from their metric. `nearest_trunk` is `[left,top,right,bottom]`,
normalized0..1, or null when unlabelled. The `crosshair_on_log` question requires **both**
bark under the crosshair **and** a visible black selection outline. `log_in_hotbar`
includes the inventory GUI's bottom hotbar row, not recipe icons or the held-item sprite.
Keep labels and image/response artifacts outside Git.

The cost estimate uses the published $0.042/M input tokens, with free output tokens.
The harness caps its budget at $1.80, reserving a conservative million-token charge for
every in-flight request and unknown failed request. SDK retries are disabled, and the
first API error aborts queued work. Existing paid results cannot be overwritten.

The November7 study is at `/home/ubuntu/jev-eval-20261107`: 200 unique exact model frames
from15 runs, manual visual labels, 30 manually boxed frames, 400 API responses, and
`report.json`. Six ambiguous outline cases are unscored. Only8 positive outlined-log
cases exist, the box subset repeats fixed-spawn views, and there are no positive iron
frames. This is an exploratory perception/latency test, not a held-out navigation or
gameplay benchmark. Inspect recall and the majority baseline, not accuracy alone.
