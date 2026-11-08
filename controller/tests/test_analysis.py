from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest
from holospeed.analysis import analyze_run, compare_runs


def journal(tmp_path: Path, events: list[tuple], *, name="run", mode="live", duration=1000) -> Path:
    path = tmp_path / name
    path.mkdir()
    (path / "summary.json").write_text(
        json.dumps(
            {
                "id": name,
                "mode": mode,
                "status": "finished",
                "duration_ms": duration,
                "config": {"operator_declared_seed": "123"},
            }
        )
    )
    with (path / "events.jsonl").open("w") as stream:
        for seq, (kind, elapsed, data) in enumerate(events, 1):
            stream.write(
                json.dumps({"seq": seq, "type": kind, "elapsed_ms": elapsed, "data": data}) + "\n"
            )
    return path


def test_reconciles_raw_acks_metrics_vetoes_and_evidence(tmp_path):
    forward = {"keys": ["forward"], "duration_ticks": 6}
    wait = {"keys": [], "duration_ticks": 2}
    veto = "Jev abstained because continuation is uncertain or unsafe: blocked"
    path = journal(
        tmp_path,
        [
            (
                "observation",
                0,
                {"frame_id": "f1", "sha256": "same", "frame_url": "/api/runs/run/frames/one.png"},
            ),
            (
                "model_call",
                100,
                {
                    "provider": "holo",
                    "latency_ms": 100,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            ),
            (
                "inference_metrics",
                101,
                {
                    "provider": "holo",
                    "latency_ms": 100,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            ),
            (
                "plan",
                102,
                {
                    "provider": "holo",
                    "latency_ms": 100,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            ),
            ("action", 120, {"frame_id": "f1", "action": forward}),
            (
                "bridge_command",
                125,
                {"type": "action", "id": "a", "frame_id": "f1", "action": forward},
            ),
            ("bridge_ack", 425, {"id": "a", "status": "completed", "start_tick": 1, "end_tick": 7}),
            (
                "ack",
                426,
                {"id": "a", "action_seq": 5, "status": "completed", "start_tick": 1, "end_tick": 7},
            ),
            ("observation", 430, {"frame_id": "f2", "sha256": "same"}),
            ("action", 440, {"frame_id": "f2", "action": forward}),
            (
                "bridge_command",
                445,
                {"type": "action", "id": "b", "frame_id": "f2", "action": forward},
            ),
            (
                "raw_bridge_ack",
                500,
                {"id": "b", "status": "rejected", "start_tick": 8, "end_tick": 8},
            ),
            ("ack", 501, {"id": "b", "action_seq": 10, "status": "rejected"}),
            ("action", 510, {"frame_id": "f2", "action": wait}),
            (
                "bridge_command",
                511,
                {"type": "action", "id": "c", "frame_id": "f2", "action": wait},
            ),
            (
                "raw_bridge_ack",
                610,
                {"id": "c", "status": "cancelled", "start_tick": 10, "end_tick": 11},
            ),
            ("ack", 611, {"id": "c", "action_seq": 14, "status": "cancelled"}),
            (
                "model_call",
                800,
                {
                    "provider": "jev",
                    "latency_ms": 100,
                    "error": veto,
                    "response": {"usage": {"input_tokens": 3, "output_tokens": 1}},
                },
            ),
            ("recovery", 801, {"reason": veto}),
            (
                "inference_metrics",
                802,
                {
                    "provider": "jev",
                    "latency_ms": 100,
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            ),
            (
                "milestone",
                810,
                {
                    "id": "wood",
                    "evidence": "A log is visible",
                    "frame_id": "f1",
                    "verification": "model_visual_claim",
                },
            ),
            ("error", 811, {"detail": "Stopped"}),
            ("run_finished", 1000, {"status": "finished"}),
        ],
    )
    with (path / "events.jsonl").open("a") as stream:
        stream.write('{"seq":24,"data":{"unfinished":"')
    analysis = analyze_run(path)
    facts = analysis["facts"]
    assert facts["journal"]["truncated_last_line"] is True
    assert facts["journal"]["valid_events"] == 23
    assert facts["actions"]["requests"] == 3
    assert facts["actions"]["dispatched"] == 3
    assert facts["actions"]["terminal_acks"] == 3
    assert [facts["actions"][key] for key in ("completed", "rejected", "cancelled")] == [1, 1, 1]
    assert facts["actions"]["duplicate_terminal_acks"] == 3
    assert facts["actions"]["unacknowledged_requests"] == 0
    assert facts["providers"]["holo"]["calls"] == 1
    assert facts["providers"]["jev"]["usage"]["total_tokens"] == 4
    assert facts["providers"]["jev"]["recorded_error_calls"] == 1
    timing = facts["timing"]
    assert timing["model_wait_wall_ms"] == 200
    assert timing["command_round_trip_wall_ms"] == 454
    assert timing["outside_model_or_command_ms"] == 346
    assert timing["nominal_input_execution_ms_at_20_tps"] == 300
    assert timing["deliberate_wait_ticks"] == 1
    assert analysis["patterns"]["no_input_requests"] == 1
    assert analysis["patterns"]["longest_identical_action_streak"] == 2
    assert analysis["patterns"]["same_action_on_identical_frame_repeats"] == 1
    assert analysis["model_claims"]["jev_vetoes"]["count"] == 1
    claim = analysis["model_claims"]["milestones"][0]
    assert claim["independently_verified"] is False
    assert claim["observation_ref"]["seq"] == 1
    assert claim["observation_ref"]["sha256"] == "same"
    json.dumps(analysis, allow_nan=False)


def test_preserves_crash_unpaired_calls_without_double_counting_legacy_decisions(tmp_path):
    path = journal(
        tmp_path,
        [
            (
                "model_call",
                100,
                {"provider": "holo", "latency_ms": 100, "usage": {"input_tokens": 1}},
            ),
            (
                "inference_metrics",
                101,
                {"provider": "holo", "latency_ms": 100, "usage": {"input_tokens": 1}},
            ),
            (
                "decision",
                102,
                {"provider": "holo", "latency_ms": 100, "usage": {"input_tokens": 1}},
            ),
            (
                "model_call",
                500,
                {
                    "provider": "holo",
                    "latency_ms": 300,
                    "response": {"usage": {"prompt_tokens": 2}},
                },
            ),
        ],
    )
    provider = analyze_run(path)["facts"]["providers"]["holo"]
    assert provider["calls"] == 2
    assert provider["sources"] == {"inference_metrics": 1, "model_call": 1}
    assert provider["usage"]["input_tokens"] == 3
    assert provider["latency_ms"]["p95"] == 290
    assert provider["latency_ms"]["mean"] == 200


def test_legacy_decision_only_journal_and_duplicate_sequences(tmp_path):
    path = journal(
        tmp_path,
        [
            (
                "decision",
                300,
                {
                    "provider": "jev",
                    "latency_ms": 75,
                    "usage": {"input_tokens": 7, "output_tokens": 2},
                },
            )
        ],
    )
    with (path / "events.jsonl").open("a") as stream:
        stream.write((path / "events.jsonl").read_text())
        stream.write("this is malformed\n")
    result = analyze_run(path)
    assert result["facts"]["providers"]["jev"]["calls"] == 1
    assert result["facts"]["providers"]["jev"]["usage"]["total_tokens"] == 9
    assert result["facts"]["journal"]["duplicate_sequences"] == 1
    assert result["facts"]["journal"]["invalid_lines"] == 1
    assert result["facts"]["journal"]["truncated_last_line"] is False


def test_tracks_orphan_conflicting_and_unacknowledged_controls(tmp_path):
    path = journal(
        tmp_path,
        [
            (
                "raw_bridge_ack",
                1,
                {"id": "orphan", "status": "completed", "start_tick": 1, "end_tick": 3},
            ),
            ("action", 5, {"action": {"keys": ["jump"]}}),
            ("bridge_command", 6, {"type": "action", "id": "x", "action": {"keys": ["jump"]}}),
            ("bridge_ack", 50, {"id": "x", "status": "cancelled", "start_tick": 4, "end_tick": 5}),
            ("ack", 51, {"id": "x", "status": "completed", "action_seq": 2}),
            ("action", 60, {"action": {"keys": ["attack"]}}),
        ],
    )
    facts = analyze_run(path)["facts"]
    assert facts["actions"]["orphan_terminal_acks"] == 1
    assert facts["actions"]["conflicting_terminal_acks"] == 1
    assert facts["actions"]["completed"] == 1
    assert facts["actions"]["cancelled"] == 1
    assert facts["actions"]["unacknowledged_requests"] == 1
    assert facts["timing"]["unassociated_ack_ticks"] == 2
    assert facts["timing"]["active_input_ticks"] == 1


def test_large_image_string_is_elided_before_json_decode(tmp_path):
    path = tmp_path / "large"
    path.mkdir()
    with (path / "events.jsonl").open("w") as stream:
        stream.write(
            '{"seq":1,"type":"model_call","elapsed_ms":300,"data":{"provider":"holo","request":{"image_base64":"'
        )
        for _ in range(384):
            stream.write("A" * 65536)
        stream.write(
            '"},"response":{"text":"escaped \\"quote\\""},"latency_ms":123,"usage":{"prompt_tokens":42}}}\n'
        )
    tracemalloc.start()
    try:
        result = analyze_run(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4_000_000, "Image-bearing JSON must not be materialized in memory"
    assert result["facts"]["journal"]["oversized_strings_omitted"] == 1
    assert result["facts"]["providers"]["holo"]["usage"]["input_tokens"] == 42
    assert result["facts"]["providers"]["holo"]["latency_ms"]["p50"] == 123
    assert "image_base64" not in json.dumps(result)


def test_late_unknown_ack_cannot_complete_the_only_pending_request(tmp_path):
    action = {"keys": ["forward"], "duration_ticks": 6}
    path = journal(
        tmp_path,
        [
            ("action", 0, {"action": action}),
            ("bridge_command", 1, {"type": "action", "id": "current", "action": action}),
            (
                "bridge_ack",
                20,
                {"id": "old-command", "status": "completed", "start_tick": 0, "end_tick": 6},
            ),
            ("ack", 21, {"action_seq": 999, "status": "cancelled"}),
        ],
    )
    facts = analyze_run(path)["facts"]
    assert facts["actions"]["requests"] == 1
    assert facts["actions"]["unacknowledged_requests"] == 1
    assert facts["actions"]["orphan_terminal_acks"] == 2
    assert facts["timing"]["active_input_ticks"] == 0
    assert facts["timing"]["unassociated_ack_ticks"] == 6


def test_compares_recorded_facts_without_promoting_claims(tmp_path):
    first = journal(
        tmp_path,
        [("milestone", 200, {"id": "wood", "evidence": "model says so"})],
        name="first",
        mode="demo",
        duration=400,
    )
    second = journal(
        tmp_path,
        [
            ("action", 0, {"action": {"keys": ["attack"]}}),
            ("ack", 300, {"id": "a", "status": "completed", "start_tick": 0, "end_tick": 6}),
        ],
        name="second",
        duration=600,
    )
    comparison = compare_runs([first, second])
    assert comparison["metrics"]["actions_completed"] == [0, 1]
    assert comparison["second_minus_first"]["duration_ms"] == 200
    assert comparison["comparability"]["same_mode"] is False
    assert comparison["comparability"]["same_declared_seed"] is True
    assert comparison["comparability"]["independently_verified_outcomes"] is False
    assert comparison["milestone_claims"][0]["runs"][1] is None
    assert comparison["milestone_claims"][0]["runs"][0]["independently_verified"] is False
    with pytest.raises(ValueError, match="at least two"):
        compare_runs([first])
    json.dumps(comparison, allow_nan=False)


def test_separates_active_intervals_pauses_and_post_stop_inspection(tmp_path):
    path = journal(
        tmp_path,
        [
            ("run_started", 0, {"mode": "live"}),
            ("event", 0, {"name": "resumed"}),
            ("observation", 50, {"frame_id": "first"}),
            ("event", 100, {"name": "paused"}),
            ("event", 200, {"name": "resumed"}),
            ("error", 500, {"detail": "Repeated model abstentions"}),
            ("event", 900, {"name": "paused"}),
            ("run_finished", 1000, {}),
        ],
        duration=2000,
    )
    timing = analyze_run(path)["facts"]["timing"]
    assert timing["active_loop_recorded_ms"] == 400
    assert len(timing["active_loop_intervals"]) == 2
    assert [part["stopped_by"] for part in timing["active_loop_intervals"]] == ["paused", "error"]
    assert timing["last_loop_stop_ms"] == 500
    assert timing["post_stop_inspection_ms"] == 1500
    assert timing["recorded_duration_ms"] == 2000
    assert timing["loop_status"] == "stopped"


@pytest.mark.parametrize("stop", ["run_budget_reached", "goal_reached", "model_completion_claim"])
def test_recognizes_loop_completion_and_bounds_open_intervals(tmp_path, stop):
    path = journal(
        tmp_path, [("run_started", 10, {}), ("event", 100, {"name": stop})], name=stop, duration=500
    )
    stopped = analyze_run(path)["facts"]["timing"]
    assert stopped["active_loop_recorded_ms"] == 90
    assert stopped["post_stop_inspection_ms"] == 400
    with (path / "events.jsonl").open("a") as stream:
        stream.write(
            json.dumps({"seq": 3, "type": "event", "elapsed_ms": 200, "data": {"name": "resumed"}})
            + "\n"
        )
        stream.write(
            json.dumps({"seq": 4, "type": "observation", "elapsed_ms": 250, "data": {}}) + "\n"
        )
    open_run = analyze_run(path)["facts"]["timing"]
    assert open_run["active_loop_recorded_ms"] == 140
    assert open_run["active_loop_intervals"][-1]["open_ended"] is True
    assert open_run["loop_status"] == "open"
    assert open_run["post_stop_inspection_ms"] is None
