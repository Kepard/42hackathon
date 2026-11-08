# Offline run analysis

`holospeed.analysis.analyze_run(Path("runs/<id>"))` returns JSON-compatible
evidence without opening screenshots or calling a provider.
`compare_runs([Path("runs/<first>"), Path("runs/<second>")])` aligns those facts
and reports second-minus-first numeric differences. The caller decides how to
present or save the result.

The analyzer streams JSONL in 64 KiB chunks. JSON strings larger than 8 KiB are
elided before decoding, so embedded image/base64 data is never loaded as one
large Python string. Metadata records over 1 MiB are skipped and counted.
Malformed records, duplicate sequence numbers, and a truncated final append are
reported; earlier complete evidence remains usable.

Provider distributions include minimum, maximum, mean and interpolated
p50/p90/p95/p99 latency. A raw `model_call` and its subsequent
`inference_metrics` record count as one call, matched by provider, order and
identical latency. Unpaired raw calls survive crashes between the two writes.
`decision` metrics are used only for older journals containing neither call
source. Failed/rejected calls remain in API usage and latency totals. Missing
usage is counted separately from a provider-reported zero.

Controller action requests and matching bridge commands are reconciled.
`ack`, `bridge_ack`, and `raw_bridge_ack` are deduplicated by command ID or
linked action sequence. Conflicts, unmatched requests, and orphan ACKs are
explicit counts. The first recorded terminal result wins when ACKs conflict.
An ACK confirms control execution; it does not confirm success in Minecraft.

Timing distinguishes model request windows, command-to-ACK wall-clock windows,
their overlap, and unattributed elapsed time. Command windows include scheduling
and acknowledgment overhead. A separate nominal execution estimate uses ACK
ticks at 20 TPS; pure no-input waits and orphan ACK ticks are kept separate.
Neither measurement is a benchmark-legal speedrun timer.

`active_loop_intervals` separate explicit start/resume periods from paused or
post-stop inspection time. Errors, pause/takeover, budget stops, goal claims,
model-completion claims, and run finish close an active interval. Multiple
resumes create multiple intervals. `active_loop_recorded_ms` sums those
intervals; `post_stop_inspection_ms` measures archive time after the final
recorded loop stop. Open intervals end provisionally at the last event and are
marked as lower bounds. Missing lifecycle evidence produces null active timing.

Repeated actions, no-input commands, and identical recorded frame hashes are
patterns only: mining may legitimately repeat a stationary action. Jev veto
reason counts and milestone evidence references are placed under
`model_claims`, never promoted to independently verified gameplay outcomes.
Comparisons distinguish demo/live mode and operator-declared seeds; matching
metadata does not establish equivalent world conditions.
