"""Offline, evidence-based analysis of HoloSpeed journals.

Reads JSONL in bounded chunks, eliding large strings before JSON decoding. Image
contents and model request/response bodies are never retained by the analyzer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .recording import redact

_STRING_LIMIT = 8192
_RECORD_LIMIT = 1_048_576
_CHUNK_SIZE = 65536
_STRING_SPECIAL = re.compile(r'["\\]')
_ACK_TYPES = {"ack", "bridge_ack", "raw_bridge_ack"}
_TERMINAL = {"completed", "rejected", "cancelled"}
_PORTAL_STEPS = frozenset(
    {
        "portal_located",
        "chest_looted",
        "obsidian_acquired",
        "ignition_acquired",
        "portal_repaired",
        "portal_lit",
    }
)
_ACTION_FIELDS = (
    "keys",
    "duration_ticks",
    "yaw_delta",
    "pitch_delta",
    "hotbar",
    "cursor",
    "scroll",
)


class _CompactRecord:
    """Preserve JSON syntax while bounding strings, including across chunks."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.string: list[str] = []
        self.string_size = 0
        self.size = 0
        self.in_string = False
        self.escaped = False
        self.oversized = False
        self.omitted_strings = 0

    def _append(self, value: str) -> None:
        self.size += len(value)
        if self.size <= _RECORD_LIMIT:
            self.parts.append(value)
        else:
            self.parts.clear()
            self.oversized = True

    def _string_append(self, value: str) -> None:
        self.string_size += len(value)
        if self.string_size <= _STRING_LIMIT:
            self.string.append(value)
        else:
            self.string.clear()

    def feed(self, chunk: str) -> None:
        if self.oversized:
            return
        i = 0
        while i < len(chunk):
            if not self.in_string:
                end = chunk.find('"', i)
                if end < 0:
                    self._append(chunk[i:])
                    return
                self._append(chunk[i:end])
                self.in_string = True
                self.string = []
                self.string_size = 0
                i = end + 1
            elif self.escaped:
                self._string_append(chunk[i])
                self.escaped = False
                i += 1
            else:
                match = _STRING_SPECIAL.search(chunk, i)
                if match is None:
                    self._string_append(chunk[i:])
                    return
                self._string_append(chunk[i : match.start()])
                if match.group() == "\\":
                    self._string_append("\\")
                    self.escaped = True
                else:
                    if self.string_size > _STRING_LIMIT:
                        self._append('"[oversized string omitted]"')
                        self.omitted_strings += 1
                    else:
                        self._append('"' + "".join(self.string) + '"')
                    self.in_string = False
                    self.string = []
                i = match.end()

    def decode(self) -> Any:
        if self.oversized or self.in_string:
            raise ValueError("incomplete or oversized JSON record")
        return json.loads("".join(self.parts), parse_constant=lambda _: None)


def _events(path: Path, diagnostics: dict) -> Iterator[dict]:
    record = _CompactRecord()
    line = 1
    pending = False
    with path.open(encoding="utf-8", errors="replace") as stream:
        while chunk := stream.readline(_CHUNK_SIZE):
            pending = True
            complete = chunk.endswith("\n")
            record.feed(chunk[:-1] if complete else chunk)
            if not complete:
                continue
            diagnostics["oversized_strings_omitted"] += record.omitted_strings
            try:
                event = record.decode()
                if not isinstance(event, dict) or not isinstance(event.get("data", {}), dict):
                    raise TypeError("event must be an object")
                diagnostics["valid_events"] += 1
                yield {**event, "_line": line}
            except (ValueError, TypeError, RecursionError):
                diagnostics["invalid_lines"] += 1
                if record.oversized:
                    diagnostics["oversized_records_skipped"] += 1
            record = _CompactRecord()
            line += 1
            pending = False
        if pending:
            diagnostics["oversized_strings_omitted"] += record.omitted_strings
            try:
                event = record.decode()
                if not isinstance(event, dict) or not isinstance(event.get("data", {}), dict):
                    raise TypeError("event must be an object")
                diagnostics["valid_events"] += 1
                yield {**event, "_line": line}
            except (ValueError, TypeError, RecursionError):
                diagnostics["invalid_lines"] += 1
                diagnostics["truncated_last_line"] = not record.oversized
                if record.oversized:
                    diagnostics["oversized_records_skipped"] += 1


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _text(value: Any, limit: int = 1000) -> str:
    return str(redact(value))[:limit] if value is not None else ""


def _usage(data: dict) -> dict:
    usage = data.get("usage")
    if not isinstance(usage, dict) or not usage:
        response = data.get("response", {})
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    inputs = _number(usage.get("prompt_tokens", usage.get("input_tokens")))
    outputs = _number(usage.get("completion_tokens", usage.get("output_tokens")))
    total = _number(usage.get("total_tokens"))
    return {
        "input_tokens": int(inputs or 0),
        "output_tokens": int(outputs or 0),
        "total_tokens": int(total if total is not None else (inputs or 0) + (outputs or 0)),
        "reported": any(value is not None for value in (inputs, outputs, total)),
    }


def _distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, **dict.fromkeys(("min", "max", "mean", "p50", "p90", "p95", "p99"))}

    def percentile(fraction: float) -> float:
        index = fraction * (len(ordered) - 1)
        lower = int(index)
        upper = min(lower + 1, len(ordered) - 1)
        return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 3)

    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
        "mean": round(sum(ordered) / len(ordered), 3),
        **{f"p{p}": percentile(p / 100) for p in (50, 90, 95, 99)},
    }


def _union_ms(intervals: list[tuple[float, float]], duration: float) -> float:
    total = 0.0
    end = 0.0
    for start, finish in sorted(intervals):
        start, finish = max(0, start), min(duration, finish)
        if finish > max(start, end):
            total += finish - max(start, end)
        end = max(end, finish)
    return round(total, 3)


def _action(data: dict) -> dict:
    raw = data.get("action", data)
    return {key: raw[key] for key in _ACTION_FIELDS if key in raw} if isinstance(raw, dict) else {}


def _signature(action: dict) -> str:
    normalized = {
        "keys": [],
        "duration_ticks": 6,
        "yaw_delta": 0,
        "pitch_delta": 0,
        "hotbar": None,
        "cursor": None,
        "scroll": 0,
        **action,
    }
    if isinstance(normalized["keys"], list):
        normalized["keys"] = sorted(str(key) for key in normalized["keys"])
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()[:16]


def _no_input(action: dict) -> bool:
    return not any(
        action.get(key)
        for key in ("keys", "yaw_delta", "pitch_delta", "hotbar", "cursor", "scroll")
    )


def _inventory_counts(value: Any) -> dict[str, int]:
    """Read only player inventory, never GUI slots or nearby dropped entities."""
    counts: Counter = Counter()
    if isinstance(value, dict):
        stacks = [{"item": item, "count": count} for item, count in list(value.items())[:256]]
    elif isinstance(value, list):
        stacks = value[:256]
    else:
        return {}
    seen_slots = set()
    for stack in stacks:
        if not isinstance(stack, dict):
            continue
        item, count, slot = stack.get("item"), stack.get("count"), stack.get("slot")
        if slot is not None:
            if type(slot) is not int or slot in seen_slots or not 0 <= slot <= 40:
                continue
            seen_slots.add(slot)
        if isinstance(item, str) and item != "minecraft:air" and type(count) is int and count > 0:
            counts[_text(item, 160)] += count
    return dict(counts)


def _portal_steps(value: Any) -> list[str]:
    """Accept only bounded client-recorded step identifiers, never arbitrary claims."""
    if not isinstance(value, list) or len(value) > len(_PORTAL_STEPS):
        return []
    if any(not isinstance(item, str) or item not in _PORTAL_STEPS for item in value):
        return []
    if len(set(value)) != len(value):
        return []
    return value.copy()


def _ready_milestone_state(milestone: dict, state: dict | None) -> bool:
    return bool(
        state
        and state["world_ready"] is True
        and state["cache_age_ms"] is not None
        and 0 <= state["cache_age_ms"] <= 1000
        and state["player_health"] is not None
        and state["player_health"] > 0
        and milestone["elapsed_ms"] is not None
        and state["elapsed_ms"] <= milestone["elapsed_ms"]
        and (
            milestone["event_seq"] is None
            or not isinstance(state["seq"], int)
            or state["seq"] < milestone["event_seq"]
        )
    )


class _SkillJournal:
    """Keep skill requests, immediate ACKs, and verified outcomes distinct."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.acks: dict[str, dict] = {}
        self.outcomes: list[dict] = []
        self.seen_outcomes: set[tuple] = set()
        self.duplicate_acks = self.conflicting_acks = self.duplicate_outcomes = 0

    @staticmethod
    def identity(data: dict) -> tuple:
        return tuple(data.get(key) for key in ("skill", "item", "target_count", "max_ticks"))

    def start(self, seq: Any, elapsed: float, data: dict, dispatched: bool = False) -> dict:
        request = {
            "seq": seq,
            "elapsed_ms": elapsed,
            "candidate_id": _text(data.get("candidate_id" if dispatched else "id"), 200),
            "id": _text(data.get("id"), 200) if dispatched else "",
            "skill": _text(data.get("skill"), 30),
            "item": _text(data.get("item"), 160),
            "target_count": _number(data.get("target_count")),
            "max_ticks": _number(data.get("max_ticks")),
            "postcondition": _text(data.get("postcondition", "inventory"), 40),
            "expected_dimension": _text(data.get("expected_dimension"), 100) or None,
            "dispatched_ms": elapsed if dispatched else None,
            "ack": None,
            "outcome": None,
        }
        self.requests.append(request)
        if request["id"]:
            self.by_id[request["id"]] = request
        return request

    def dispatch(self, seq: Any, elapsed: float, data: dict) -> None:
        identifier = _text(data.get("id"), 200)
        request = self.by_id.get(identifier) or next(
            (
                item
                for item in reversed(self.requests)
                if item["dispatched_ms"] is None
                and item["outcome"] is None
                and self.identity(item) == self.identity(data)
            ),
            None,
        )
        if request is None:
            request = self.start(seq, elapsed, data, dispatched=True)
        request.update(id=identifier, dispatched_ms=elapsed)
        if identifier:
            self.by_id[identifier] = request

    def acknowledge(self, seq: Any, elapsed: float, packet: dict) -> None:
        if packet.get("status") not in {"accepted", "completed", "rejected"}:
            return
        identifier = _text(packet.get("id"), 200)
        request = self.by_id.get(identifier) if identifier else None
        key = identifier or f"orphan:{seq}"
        if key in self.acks:
            self.duplicate_acks += 1
            self.conflicting_acks += self.acks[key]["status"] != packet["status"]
            return
        ack = {
            "id": identifier or None,
            "seq": seq,
            "elapsed_ms": elapsed,
            "status": packet["status"],
            "skill_id": _text(packet.get("skill_id"), 200) or None,
            "request_seq": request["seq"] if request else None,
        }
        self.acks[key] = ack
        if request:
            request["ack"] = ack

    def finish(self, kind: str, seq: Any, elapsed: float, data: dict, states: dict) -> None:
        candidate = _text(data.get("candidate_id"), 200)
        state_id = _text(data.get("state_id"), 200)
        identity = (kind, candidate, state_id)
        if state_id and identity in self.seen_outcomes:
            self.duplicate_outcomes += 1
            return
        self.seen_outcomes.add(identity)
        request = next(
            (
                item
                for item in reversed(self.requests)
                if candidate and item["candidate_id"] == candidate and item["outcome"] is None
            ),
            None,
        )
        item = _text(data.get("item"), 160)
        target = data.get("target_count")
        inventory = _inventory_counts(data.get("inventory"))
        state = states.get(state_id)
        linked = (
            kind == "skill_completed"
            and request is not None
            and type(target) is int
            and 1 <= target <= 64
            and (item, target) == (request["item"], request["target_count"])
            and state is not None
            and state["elapsed_ms"] >= request["elapsed_ms"]
            and (
                not isinstance(state["seq"], int)
                or not isinstance(request["seq"], int)
                or state["seq"] > request["seq"]
            )
        )
        postcondition = _text(data.get("postcondition", "inventory"), 40)
        inventory_verified = bool(
            linked
            and postcondition == request["postcondition"] == "inventory"
            and inventory.get(item, 0) >= target
            and state["inventory_counts"].get(item, 0) >= target
        )
        expected_dimension = _text(data.get("expected_dimension"), 100) or None
        dimension = _text(data.get("dimension"), 100) or None
        dimension_verified = bool(
            linked
            and postcondition == request["postcondition"] == "dimension"
            and item == "minecraft:nether_portal"
            and expected_dimension == "minecraft:the_nether"
            and expected_dimension == request["expected_dimension"]
            and dimension == expected_dimension == state["dimension"]
            and state["world_ready"] is True
            and state["cache_age_ms"] is not None
            and 0 <= state["cache_age_ms"] <= 1000
            and state["player_health"] is not None
            and state["player_health"] > 0
        )
        result = {
            "candidate_id": candidate,
            "seq": seq,
            "elapsed_ms": elapsed,
            "status": "completed" if kind == "skill_completed" else "failed",
            "request_seq": request["seq"] if request else None,
            "item": item,
            "target_count": _number(target),
            "inventory_count": inventory.get(item, 0),
            "postcondition": postcondition,
            "inventory_verified": inventory_verified,
            "dimension_verified": dimension_verified,
            "postcondition_verified": inventory_verified or dimension_verified,
            "expected_dimension": expected_dimension,
            "dimension": dimension,
            "state_ref": state,
            "reason": _text(data.get("reason")),
        }
        self.outcomes.append(result)
        if request:
            request["outcome"] = result

    def report(self) -> dict:
        statuses = Counter(ack["status"] for ack in self.acks.values())
        return {
            "requests": len(self.requests),
            "dispatched": sum(item["dispatched_ms"] is not None for item in self.requests),
            "acknowledged": len(self.acks),
            "accepted_acks": statuses["accepted"],
            "already_satisfied_acks": statuses["completed"],
            "rejected_acks": statuses["rejected"],
            "duplicate_acks": self.duplicate_acks,
            "conflicting_acks": self.conflicting_acks,
            "orphan_acks": sum(ack["request_seq"] is None for ack in self.acks.values()),
            "completion_events": sum(item["status"] == "completed" for item in self.outcomes),
            "completed": sum(item["postcondition_verified"] for item in self.outcomes),
            "inventory_verified": sum(item["inventory_verified"] for item in self.outcomes),
            "dimension_verified": sum(item["dimension_verified"] for item in self.outcomes),
            "unverified_completions": sum(
                item["status"] == "completed" and not item["postcondition_verified"]
                for item in self.outcomes
            ),
            "failed": sum(item["status"] == "failed" for item in self.outcomes),
            "duplicate_outcomes": self.duplicate_outcomes,
            "orphan_outcomes": sum(item["request_seq"] is None for item in self.outcomes),
            "unacknowledged_requests": sum(item["ack"] is None for item in self.requests),
            "unresolved_requests": sum(
                item["outcome"] is None
                and (item["ack"] is None or item["ack"]["status"] != "rejected")
                for item in self.requests
            ),
            "outcome_samples": self.outcomes[:25],
            "unacknowledged_samples": [
                {key: item[key] for key in ("seq", "id", "candidate_id", "skill", "item")}
                for item in self.requests
                if item["ack"] is None
            ][:25],
        }


def analyze_run(path: Path) -> dict:
    """Analyze one run directory or events.jsonl file without reading images.

    Terminal ACKs establish execution of controls, never success in Minecraft.
    Reported tick durations use nominal 20 TPS; wall-clock command windows also
    include dispatch/ACK overhead and are reported separately.
    """
    path = Path(path)
    directory = path.parent if path.is_file() else path
    journal = path if path.is_file() else path / "events.jsonl"
    if not journal.is_file():
        raise FileNotFoundError(journal)
    summary_path = directory / "summary.json"
    summary_error = None
    try:
        if summary_path.exists() and summary_path.stat().st_size > _RECORD_LIMIT:
            raise ValueError("summary exceeds metadata size limit")
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        if not isinstance(summary, dict):
            raise TypeError("summary must be an object")
    except (ValueError, TypeError, OSError) as error:
        summary, summary_error = {}, type(error).__name__
    diagnostics = {
        "valid_events": 0,
        "invalid_lines": 0,
        "truncated_last_line": False,
        "oversized_strings_omitted": 0,
        "oversized_records_skipped": 0,
        "duplicate_sequences": 0,
        "duplicate_model_records": 0,
        "summary_error": summary_error,
    }
    event_counts: Counter = Counter()
    seen_sequences: set = set()
    frames: dict[str, dict] = {}
    states: dict[str, dict] = {}
    skills = _SkillJournal()
    requests: list[dict] = []
    request_by_seq: dict[Any, dict] = {}
    request_by_id: dict[str, dict] = {}
    acks: dict[str, dict] = {}
    calls: list[dict] = []
    latest_call: dict[str, dict] = {}
    active_call_ids: dict[str, str] = {}
    transport_calls: dict[str, dict] = {}
    legacy_decisions: list[dict] = []
    veto_records: list[dict] = []
    latest_veto: dict | None = None
    milestones: list[dict] = []
    last_elapsed = 0.0
    ack_duplicates = 0
    ack_conflicts = 0
    loop_intervals: list[dict] = []
    active_loop: dict | None = None
    lifecycle_observed = False

    def make_request(event: dict, data: dict, dispatched: bool) -> dict:
        action = _action(data)
        result = {
            "seq": event.get("seq", f"line-{event['_line']}"),
            "elapsed_ms": _number(event.get("elapsed_ms")) or 0,
            "dispatched_ms": (_number(event.get("elapsed_ms")) or 0) if dispatched else None,
            "frame_id": _text(data.get("frame_id"), 200),
            "action": action,
            "signature": _signature(action),
            "id": _text(data.get("id"), 200),
            "terminal": None,
            "origin": "bridge_only" if dispatched else "controller",
        }
        requests.append(result)
        request_by_seq[result["seq"]] = result
        if result["id"]:
            request_by_id[result["id"]] = result
        return result

    for event in _events(journal, diagnostics):
        seq = event.get("seq", f"line-{event['_line']}")
        # The recorder assigns unique sequence numbers; duplicated copies are not new work.
        if isinstance(seq, (str, int)):
            if seq in seen_sequences:
                diagnostics["duplicate_sequences"] += 1
                continue
            seen_sequences.add(seq)
        else:
            seq = f"line-{event['_line']}"
            event["seq"] = seq
        kind = event.get("type", "unknown")
        if not isinstance(kind, str):
            kind = "unknown"
        event_counts[kind] += 1
        data = event.get("data", {})
        elapsed = _number(event.get("elapsed_ms")) or 0
        last_elapsed = max(last_elapsed, elapsed)
        starts_loop = kind == "run_started" or (
            kind == "event" and data.get("name") in {"resumed", "started"}
        )
        stop_reason = (
            kind
            if kind
            in {
                "error",
                "run_finished",
                "run_budget_reached",
                "goal_reached",
                "model_completion_claim",
            }
            else data.get("name")
            if kind == "event"
            and data.get("name")
            in {
                "paused",
                "emergency_stop",
                "world_changed",
                "disconnected",
                "bridge_disconnected",
                "inference_or_action_cancelled",
                "run_budget_reached",
                "goal_reached",
                "model_completion_claim",
            }
            else None
        )
        if starts_loop:
            lifecycle_observed = True
            if active_loop is None:
                active_loop = {
                    "start_elapsed_ms": elapsed,
                    "start_seq": seq,
                    "started_by": kind if kind == "run_started" else data["name"],
                }
        if stop_reason:
            lifecycle_observed = True
            if active_loop is not None:
                loop_intervals.append(
                    {
                        **active_loop,
                        "stop_elapsed_ms": elapsed,
                        "stop_seq": seq,
                        "stopped_by": stop_reason,
                        "observed_through_ms": elapsed,
                        "observed_duration_ms": round(
                            max(0, elapsed - active_loop["start_elapsed_ms"]), 3
                        ),
                        "open_ended": False,
                    }
                )
                active_loop = None
        if kind == "run_started" and not summary.get("mode"):
            summary["mode"] = data.get("mode", "unknown")
        if kind == "observation":
            frame_id = _text(data.get("frame_id"), 200)
            frames[frame_id] = {
                "frame_id": frame_id,
                "seq": seq,
                "sha256": _text(data.get("sha256"), 64),
                "frame_url": _text(data.get("frame_url"), 500),
            }
        elif kind == "game_state":
            identifier = _text(data.get("id"), 200)
            if identifier:
                states[identifier] = {
                    "id": identifier,
                    "seq": seq,
                    "elapsed_ms": elapsed,
                    "tick": _number(data.get("tick")),
                    "path": _text(data.get("path"), 500),
                    "sha256": _text(data.get("sha256"), 64),
                    "inventory_counts": _inventory_counts(data.get("inventory")),
                    "world_ready": data.get("world_ready") is True,
                    "cache_age_ms": _number(data.get("cache_age_ms")),
                    "sampled_at_ms": _number(data.get("sampled_at_ms")),
                    "dimension": _text(data.get("dimension"), 100) or None,
                    "player_health": _number(data.get("player", {}).get("health"))
                    if isinstance(data.get("player"), dict)
                    else None,
                    "verified_steps": _portal_steps(data.get("skill", {}).get("verified_steps"))
                    if isinstance(data.get("skill"), dict)
                    else [],
                }
        elif kind == "model_request":
            provider = _text(data.get("provider", "unknown"), 100).lower()
            active_call_ids[provider] = _text(data.get("call_id"), 200)
            # A new HTTP request separates calls even if their rounded latencies match.
            latest_call.pop(provider, None)
        elif kind in {"model_response", "model_error", "model_cancelled"}:
            provider = _text(data.get("provider", "unknown"), 100).lower()
            identifier = _text(data.get("call_id"), 200)
            if identifier:
                transport_calls[identifier] = {
                    "provider": provider,
                    "latency_ms": _number(data.get("latency_ms")),
                    "usage": _usage(data),
                    "elapsed_ms": elapsed,
                    "seq": seq,
                    "source": kind,
                    "raw_seq": None,
                    "error": kind != "model_response"
                    or (_number(data.get("http_status")) or 0) >= 400,
                    "model": _text(data.get("model"), 200),
                    "simulated": bool(data.get("simulated")) or summary.get("mode") == "demo",
                    "call_id": identifier,
                    "candidate_id": None,
                }
                active_call_ids[provider] = identifier
        elif kind == "strategy_fallback":
            provider = _text(data.get("provider", "unknown"), 100).lower()
            failed = transport_calls.get(active_call_ids.get(provider, ""))
            if failed:
                failed["error"] = True
        elif kind in {"model_call", "inference_metrics", "decision"}:
            provider = _text(data.get("provider", "unknown"), 100).lower()
            call = {
                "provider": provider,
                "latency_ms": _number(data.get("latency_ms")),
                "usage": _usage(data),
                "elapsed_ms": elapsed,
                "seq": seq,
                "source": kind,
                "raw_seq": seq if kind == "model_call" else None,
                "error": bool(data.get("error")),
                "model": _text(data.get("model"), 200),
                "simulated": bool(data.get("simulated")) or summary.get("mode") == "demo",
                "call_id": _text(data.get("call_id"), 200) or active_call_ids.get(provider),
                "candidate_id": _text(data.get("candidate_id"), 200) or None,
            }
            previous = latest_call.get(provider)
            same_identity = (
                previous and previous["call_id"] and previous["call_id"] == call["call_id"]
            )
            same_latency = (
                previous
                and call["latency_ms"] is not None
                and previous["latency_ms"] == call["latency_ms"]
                and not (
                    previous["call_id"]
                    and call["call_id"]
                    and previous["call_id"] != call["call_id"]
                )
            )
            pair = previous and (
                same_identity or (same_latency and 0 <= elapsed - previous["elapsed_ms"] <= 2000)
            )
            if kind == "decision":
                legacy_decisions.append(call)
            elif kind == "model_call":
                if (
                    pair
                    and previous["source"] == "inference_metrics"
                    and previous["raw_seq"] is None
                ):
                    # The skill loop records metrics first; retain that sample once
                    # while enriching it with the actual raw selection/error metadata.
                    previous.update(
                        raw_seq=seq,
                        error=call["error"],
                        model=call["model"],
                        candidate_id=call["candidate_id"],
                        call_id=previous["call_id"] or call["call_id"],
                    )
                    if not previous["usage"]["reported"]:
                        previous["usage"] = call["usage"]
                else:
                    calls.append(call)
                    latest_call[provider] = call
                reason = _text(data.get("error"))
                if provider.startswith("jev") and any(
                    word in reason.lower()
                    for word in ("abstain", "veto", "replan", "unsafe", "uncertain")
                ):
                    latest_veto = {
                        "reason": reason,
                        "seq": seq,
                        "elapsed_ms": elapsed,
                        "source": "model_call",
                    }
                    veto_records.append(latest_veto)
            else:
                # The visual loop records raw calls first. Consume each pair once.
                if pair and previous["source"] == "model_call":
                    previous.update(source="inference_metrics", elapsed_ms=elapsed, seq=seq)
                    previous["call_id"] = previous["call_id"] or call["call_id"]
                    if call["usage"]["reported"]:
                        previous["usage"] = call["usage"]
                else:
                    calls.append(call)
                    latest_call[provider] = call
        elif kind == "recovery":
            reason = _text(data.get("reason", data.get("detail", "")))
            if "jev" in reason.lower() and any(
                word in reason.lower()
                for word in ("abstain", "veto", "replan", "unsafe", "uncertain")
            ):
                duplicate = (
                    latest_veto
                    and latest_veto["source"] == "model_call"
                    and latest_veto["reason"] == reason
                    and 0 <= elapsed - latest_veto["elapsed_ms"] <= 2000
                )
                if not duplicate:
                    latest_veto = {
                        "reason": reason,
                        "seq": seq,
                        "elapsed_ms": elapsed,
                        "source": "recovery",
                    }
                    veto_records.append(latest_veto)
        elif kind == "skill_started":
            skills.start(seq, elapsed, data)
        elif kind == "bridge_command" and data.get("type") == "skill":
            skills.dispatch(seq, elapsed, data)
        elif kind in {"skill_completed", "skill_failed"}:
            skills.finish(kind, seq, elapsed, data, states)
        elif kind == "action":
            make_request(event, data, False)
        elif kind == "bridge_command" and data.get("type") == "action":
            packet_id = _text(data.get("id"), 200)
            frame_id = _text(data.get("frame_id"), 200)
            action = _action(data)
            request = request_by_id.get(packet_id) if packet_id else None
            if request is None:
                request = next(
                    (
                        item
                        for item in reversed(requests)
                        if item["dispatched_ms"] is None
                        and item["terminal"] is None
                        and item["frame_id"] == frame_id
                        and item["signature"] == _signature(action)
                    ),
                    None,
                )
            if request is None:
                request = make_request(event, data, True)
            request["dispatched_ms"] = elapsed
            request["id"] = packet_id
            if packet_id:
                request_by_id[packet_id] = request
        elif kind in _ACK_TYPES or kind == "skill_ack":
            packet = data.get("ack", data.get("message", data))
            if not isinstance(packet, dict):
                continue
            packet_id = _text(packet.get("id"), 200)
            if (
                kind == "skill_ack"
                or packet.get("type") == "skill_ack"
                or packet_id in skills.by_id
            ):
                skills.acknowledge(seq, elapsed, packet)
                continue
            if packet.get("status") not in _TERMINAL:
                continue
            request = request_by_id.get(packet_id) if packet_id else None
            action_seq = packet.get("action_seq")
            if request is None and isinstance(action_seq, (str, int)):
                request = request_by_seq.get(action_seq)
            if request is None and not packet_id and action_seq is None:
                candidates = [item for item in requests if item["terminal"] is None]
                if len(candidates) == 1:
                    request = candidates[0]
            key = (
                f"id:{packet_id}"
                if packet_id
                else f"action:{request['seq']}"
                if request
                else f"orphan:{seq}"
            )
            existing = acks.get(key) or (request["terminal"] if request else None)
            if existing:
                ack_duplicates += 1
                ack_conflicts += existing["status"] != packet["status"]
                existing["sources"].add(kind)
                if request and existing["request_seq"] is None:
                    existing["request_seq"] = request["seq"]
                    request["terminal"] = existing
                continue
            ack = {
                "id": packet_id or None,
                "seq": seq,
                "elapsed_ms": elapsed,
                "request_seq": request["seq"] if request else None,
                "status": packet["status"],
                "start_tick": _number(packet.get("start_tick")),
                "end_tick": _number(packet.get("end_tick")),
                "sources": {kind},
            }
            acks[key] = ack
            if request:
                request["terminal"] = ack
        elif kind == "milestone":
            milestones.append(
                {
                    "id": _text(data.get("id", data.get("milestone")), 100),
                    "elapsed_ms": elapsed,
                    "event_seq": seq,
                    "evidence": _text(data.get("evidence"), 2000),
                    "frame_id": _text(data.get("frame_id"), 200),
                    "verification": _text(data.get("verification", "unverified_model_claim"), 100),
                    "state_id": _text(data.get("state_id"), 200) or None,
                    "tick": _number(data.get("tick")),
                    "state_inventory_evidence": _inventory_counts(
                        data.get("evidence", {}).get("inventory")
                    )
                    if isinstance(data.get("evidence"), dict)
                    else None,
                    "state_dimension_evidence": _text(
                        data.get("evidence", {}).get("dimension"), 100
                    )
                    if isinstance(data.get("evidence"), dict)
                    else None,
                    "state_steps_evidence": _portal_steps(data["evidence"]["verified_steps"])
                    if isinstance(data.get("evidence"), dict)
                    and "verified_steps" in data["evidence"]
                    else None,
                    "source": "journal",
                }
            )

    if not calls:
        calls = legacy_decisions
    # Identity remains definitive when a copied record gets a new journal seq.
    unique_calls: list[dict] = []
    call_by_id: dict[str, dict] = {}
    for call in calls:
        identifier = call["call_id"]
        previous = call_by_id.get(identifier) if identifier else None
        if previous:
            diagnostics["duplicate_model_records"] += 1
            previous["error"] = previous["error"] or call["error"]
            previous["candidate_id"] = previous["candidate_id"] or call["candidate_id"]
            if not previous["usage"]["reported"]:
                previous["usage"] = call["usage"]
        else:
            unique_calls.append(call)
            if identifier:
                call_by_id[identifier] = call
    calls = unique_calls
    represented = {call["call_id"] for call in calls if call["call_id"]}
    for call in calls:
        transport = transport_calls.get(call["call_id"])
        if transport:
            call["error"] = call["error"] or transport["error"]
            call["elapsed_ms"] = transport["elapsed_ms"]
            if call["latency_ms"] is None:
                call["latency_ms"] = transport["latency_ms"]
            if not call["usage"]["reported"]:
                call["usage"] = transport["usage"]
    # Rejected strategy parses, transport failures, cancellation, and crashes
    # can have a complete HTTP journal without a higher-level model_call.
    calls.extend(call for key, call in transport_calls.items() if key not in represented)
    duration = max(_number(summary.get("duration_ms")) or 0, last_elapsed)
    if active_loop is not None:
        loop_intervals.append(
            {
                **active_loop,
                "stop_elapsed_ms": None,
                "stop_seq": None,
                "stopped_by": None,
                "observed_through_ms": last_elapsed,
                "observed_duration_ms": round(
                    max(0, last_elapsed - active_loop["start_elapsed_ms"]), 3
                ),
                "open_ended": True,
            }
        )
    active_loop_ms = (
        round(sum(interval["observed_duration_ms"] for interval in loop_intervals), 3)
        if loop_intervals
        else None
    )
    last_loop_stop = next(
        (
            interval["stop_elapsed_ms"]
            for interval in reversed(loop_intervals)
            if not interval["open_ended"]
        ),
        None,
    )
    providers: dict[str, dict] = {}
    model_intervals = []
    for provider in sorted({call["provider"] for call in calls}):
        group = [call for call in calls if call["provider"] == provider]
        measured = [call["latency_ms"] for call in group if call["latency_ms"] is not None]
        providers[provider] = {
            "calls": len(group),
            "skill_selections": sum(call["candidate_id"] is not None for call in group),
            "simulated_calls": sum(call["simulated"] for call in group),
            "recorded_error_calls": sum(call["error"] for call in group),
            "sources": dict(Counter(call["source"] for call in group)),
            "latency_ms": _distribution(measured),
            "usage": {
                **{
                    key: sum(call["usage"][key] for call in group)
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                },
                "calls_with_reported_usage": sum(call["usage"]["reported"] for call in group),
            },
        }
        model_intervals.extend(
            (call["elapsed_ms"] - call["latency_ms"], call["elapsed_ms"])
            for call in group
            if call["latency_ms"] is not None
        )
    terminals = list(acks.values())
    statuses = Counter(ack["status"] for ack in terminals)
    action_intervals = []
    tick_total = active_ticks = deliberate_wait_ticks = unassociated_ticks = 0.0
    tick_measurements = 0
    for request in requests:
        ack = request["terminal"]
        if ack:
            start = (
                request["dispatched_ms"]
                if request["dispatched_ms"] is not None
                else request["elapsed_ms"]
            )
            action_intervals.append((start, ack["elapsed_ms"]))
    for ack in terminals:
        if (
            ack["status"] != "rejected"
            and ack["start_tick"] is not None
            and ack["end_tick"] is not None
            and ack["end_tick"] >= ack["start_tick"]
        ):
            ticks = ack["end_tick"] - ack["start_tick"]
            tick_total += ticks
            tick_measurements += 1
            request = request_by_seq.get(ack["request_seq"])
            if request is None:
                unassociated_ticks += ticks
            elif _no_input(request["action"]):
                deliberate_wait_ticks += ticks
            else:
                active_ticks += ticks
    pattern_groups: dict[str, dict] = {}
    streak = longest_streak = identical_frame_repeats = 0
    previous_signature = previous_frame_hash = None
    for request in requests:
        signature = request["signature"]
        streak = streak + 1 if signature == previous_signature else 1
        longest_streak = max(longest_streak, streak)
        frame_hash = frames.get(request["frame_id"], {}).get("sha256")
        if signature == previous_signature and frame_hash and frame_hash == previous_frame_hash:
            identical_frame_repeats += 1
        previous_signature, previous_frame_hash = signature, frame_hash
        group = pattern_groups.setdefault(
            signature,
            {
                "signature": signature,
                "action": redact(request["action"]),
                "requests": 0,
                "longest_consecutive_streak": 0,
                "first_seq": request["seq"],
                "last_seq": request["seq"],
            },
        )
        group["requests"] += 1
        group["longest_consecutive_streak"] = max(group["longest_consecutive_streak"], streak)
        group["last_seq"] = request["seq"]
    veto_counts = Counter(record["reason"] for record in veto_records)
    if not milestones:
        for milestone in (
            summary.get("milestones", []) if isinstance(summary.get("milestones"), list) else []
        ):
            if isinstance(milestone, dict):
                milestones.append(
                    {
                        "id": _text(milestone.get("id"), 100),
                        "elapsed_ms": _number(milestone.get("elapsed_ms")),
                        "event_seq": None,
                        "evidence": _text(milestone.get("evidence"), 2000),
                        "frame_id": _text(milestone.get("frame_id"), 200),
                        "verification": _text(
                            milestone.get("verification", "unverified_model_claim"), 100
                        ),
                        "state_id": _text(milestone.get("state_id"), 200) or None,
                        "tick": _number(milestone.get("tick")),
                        "state_inventory_evidence": _inventory_counts(
                            milestone.get("evidence", {}).get("inventory")
                        )
                        if isinstance(milestone.get("evidence"), dict)
                        else None,
                        "state_dimension_evidence": _text(
                            milestone.get("evidence", {}).get("dimension"), 100
                        )
                        if isinstance(milestone.get("evidence"), dict)
                        else None,
                        "state_steps_evidence": _portal_steps(
                            milestone["evidence"]["verified_steps"]
                        )
                        if isinstance(milestone.get("evidence"), dict)
                        and "verified_steps" in milestone["evidence"]
                        else None,
                        "source": "summary",
                    }
                )
    for milestone in milestones:
        frame = frames.get(milestone["frame_id"])
        milestone["observation_ref"] = frame
        milestone["independently_verified"] = False
        milestone["state_ref"] = states.get(milestone["state_id"])
        milestone["state_evidence_correlated"] = bool(
            milestone["state_ref"]
            and (milestone["tick"] is None or milestone["tick"] == milestone["state_ref"]["tick"])
            and milestone["state_inventory_evidence"] is not None
            and milestone["state_inventory_evidence"] == milestone["state_ref"]["inventory_counts"]
        )
        dimension = milestone["state_dimension_evidence"]
        steps = milestone["state_steps_evidence"]
        state = milestone["state_ref"]
        if milestone["id"] == "nether" or dimension:
            milestone["state_evidence_correlated"] = bool(
                milestone["state_evidence_correlated"]
                and dimension == state["dimension"]
                and (milestone["id"] != "nether" or dimension == "minecraft:the_nether")
                and _ready_milestone_state(milestone, state)
            )
        if milestone["id"] in _PORTAL_STEPS:
            name = milestone["id"]
            inventory = state["inventory_counts"] if state else {}
            held_resource = (
                name == "obsidian_acquired" and inventory.get("minecraft:obsidian", 0) > 0
            ) or (
                name == "ignition_acquired"
                and any(
                    inventory.get(item, 0) > 0
                    for item in ("minecraft:fire_charge", "minecraft:flint_and_steel")
                )
            )
            recorded_step = bool(
                state and name in state["verified_steps"] and name in (steps or [])
            )
            milestone["state_evidence_correlated"] = bool(
                milestone["state_evidence_correlated"]
                and _ready_milestone_state(milestone, state)
                and (held_resource or recorded_step)
            )
            milestone["portal_step_evidence"] = (
                None
                if not milestone["state_evidence_correlated"]
                else "current_inventory"
                if held_resource
                else "client_verified_step"
                if recorded_step
                else None
            )
        if milestone["verification"] == "game_state":
            # Table confirmation can refer to a placed table, not an inventory
            # stack. Preserve controller provenance without pretending to replay
            # its complete geometry checks from the bounded journal metadata.
            milestone["evidence"] = {"inventory": milestone["state_inventory_evidence"] or {}}
            if dimension:
                milestone["evidence"]["dimension"] = dimension
            if steps is not None:
                milestone["evidence"]["verified_steps"] = steps
        milestone.pop("state_inventory_evidence")
        milestone.pop("state_dimension_evidence")
        milestone.pop("state_steps_evidence")
    state_milestones = [item for item in milestones if item["verification"] == "game_state"]
    model_milestones = [item for item in milestones if item["verification"] != "game_state"]
    model_wait = _union_ms(model_intervals, duration)
    action_wait = _union_ms(action_intervals, duration)
    accounted = _union_ms(model_intervals + action_intervals, duration)
    config = summary.get("config", {}) if isinstance(summary.get("config"), dict) else {}
    return {
        "schema_version": 1,
        "run": {
            "id": _text(summary.get("id", directory.name), 200),
            "mode": _text(summary.get("mode", "unknown"), 30),
            "status": _text(summary.get("status", "unknown"), 50),
            "started_at": _text(summary.get("started_at"), 100),
            "operator_declared_seed": _text(config.get("operator_declared_seed"), 100) or None,
            "gameplay_mode": _text(config.get("gameplay_mode", "legacy_or_unspecified"), 50),
        },
        "facts": {
            "journal": diagnostics,
            "event_counts": dict(event_counts),
            "observations": event_counts["observation"],
            "state_observations": event_counts["game_state"],
            "recoveries": event_counts["recovery"],
            "strategy_fallbacks": event_counts["strategy_fallback"],
            "error_events": event_counts["error"],
            "providers": providers,
            "skills": skills.report(),
            "state_confirmed_milestones": [
                item for item in state_milestones if item["state_evidence_correlated"]
            ],
            "uncorroborated_state_milestones": [
                item for item in state_milestones if not item["state_evidence_correlated"]
            ],
            "actions": {
                "requests": len(requests),
                "dispatched": sum(item["dispatched_ms"] is not None for item in requests),
                "bridge_only_requests": sum(item["origin"] == "bridge_only" for item in requests),
                "terminal_acks": len(terminals),
                "completed": statuses["completed"],
                "rejected": statuses["rejected"],
                "cancelled": statuses["cancelled"],
                "duplicate_terminal_acks": ack_duplicates,
                "conflicting_terminal_acks": ack_conflicts,
                "unacknowledged_requests": sum(item["terminal"] is None for item in requests),
                "orphan_terminal_acks": sum(ack["request_seq"] is None for ack in terminals),
                "ack_source_counts": dict(
                    Counter(source for ack in terminals for source in ack["sources"])
                ),
                "unacknowledged_samples": [
                    {"seq": item["seq"], "id": item["id"] or None, "frame_id": item["frame_id"]}
                    for item in requests
                    if item["terminal"] is None
                ][:25],
            },
            "timing": {
                "recorded_duration_ms": round(duration, 3),
                "last_event_ms": round(last_elapsed, 3),
                "active_loop_intervals": loop_intervals,
                "active_loop_recorded_ms": active_loop_ms,
                "loop_status": "open"
                if active_loop is not None
                else "stopped"
                if lifecycle_observed
                else "unobserved",
                "last_loop_stop_ms": last_loop_stop,
                "post_stop_inspection_ms": round(max(0, duration - last_loop_stop), 3)
                if last_loop_stop is not None and active_loop is None
                else None,
                "model_wait_wall_ms": model_wait,
                "command_round_trip_wall_ms": action_wait,
                "model_command_overlap_ms": round(model_wait + action_wait - accounted, 3),
                "outside_model_or_command_ms": round(max(0, duration - accounted), 3),
                "acknowledged_action_ticks": tick_total,
                "active_input_ticks": active_ticks,
                "deliberate_wait_ticks": deliberate_wait_ticks,
                "unassociated_ack_ticks": unassociated_ticks,
                "nominal_input_execution_ms_at_20_tps": active_ticks * 50,
                "acks_with_tick_measurements": tick_measurements,
            },
        },
        "patterns": {
            "no_input_requests": sum(_no_input(item["action"]) for item in requests),
            "longest_identical_action_streak": longest_streak,
            "same_action_on_identical_frame_repeats": identical_frame_repeats,
            "most_repeated_actions": sorted(
                (group for group in pattern_groups.values() if group["requests"] > 1),
                key=lambda group: (-group["requests"], group["signature"]),
            )[:20],
        },
        "model_claims": {
            "jev_vetoes": {
                "count": len(veto_records),
                "reasons": [
                    {
                        "reason": reason,
                        "count": count,
                        "event_seqs": [
                            record["seq"] for record in veto_records if record["reason"] == reason
                        ][:20],
                    }
                    for reason, count in veto_counts.most_common()
                ],
            },
            "milestones": model_milestones,
        },
        "interpretation": [
            "Raw action ACKs confirm input execution, not gameplay progress. Skill ACKs are separate request acknowledgments and do not establish skill completion.",
            "Completed skill counts require a matching skill_completed event and post-start game_state: inventory skills meet their requested target in both records; dimension skills match the requested dimension in a ready world with a live player. Missing or contradictory evidence remains unverified.",
            "Game-state milestones preserve recorded controller verification with matching linked state inventory and, for Nether entry, ready-world dimension evidence; full snapshots and the world were not independently revalidated. Model claims and missing or contradictory evidence remain separate; screenshots were not inspected.",
            "Portal substeps require matching step identifiers in milestone evidence and the linked ready/live client state; obsidian and ignition acquisition may instead use current inventory. Retained client verification can record an earlier acquisition after items were consumed; model response flags are not evidence.",
            "Identical actions or images are patterns, not proof of being stuck; mining and deliberate waiting may be valid.",
            "Model wait is inferred from recorded latency ending at the call event. Command windows include scheduling and acknowledgment overhead.",
            "Tick execution uses nominal 20 TPS and may differ from wall time. Unattributed time includes observations, pauses, idle time, logging, and unresolved commands.",
            "Active loop intervals use explicit start/resume and stop/error events. Open intervals are lower bounds ending at the last journal event, not the archive checkpoint; post-stop inspection is separate.",
            "Exact call metrics pair inference_metrics and model_call in either order, once, by request identity or provider/order/measured latency. New model_request events separate calls; missing latency alone is not a match. Decision metrics are a legacy fallback only.",
        ],
    }


def compare_runs(paths: list[Path]) -> dict:
    """Compare two or more run analyses without treating model claims as wins."""
    if len(paths) < 2:
        raise ValueError("Select at least two runs to compare")
    analyses = [analyze_run(Path(path)) for path in paths]
    metrics: dict[str, list] = defaultdict(list)
    milestone_ids = []
    state_milestone_ids = []
    for analysis in analyses:
        facts = analysis["facts"]
        metrics["duration_ms"].append(facts["timing"]["recorded_duration_ms"])
        metrics["active_loop_recorded_ms"].append(facts["timing"]["active_loop_recorded_ms"])
        metrics["post_stop_inspection_ms"].append(facts["timing"]["post_stop_inspection_ms"])
        for key in ("completed", "rejected", "cancelled", "unacknowledged_requests"):
            metrics[f"actions_{key}"].append(facts["actions"][key])
        for key in ("completed", "failed", "unverified_completions", "unacknowledged_requests"):
            metrics[f"skills_{key}"].append(facts["skills"][key])
        for key in ("observations", "recoveries", "error_events"):
            metrics[key].append(facts[key])
        metrics["state_observations"].append(facts["state_observations"])
        metrics["state_confirmed_milestones"].append(len(facts["state_confirmed_milestones"]))
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            metrics[key].append(
                sum(provider["usage"][key] for provider in facts["providers"].values())
            )
        metrics["jev_vetoes"].append(analysis["model_claims"]["jev_vetoes"]["count"])
        for milestone in analysis["model_claims"]["milestones"]:
            if milestone["id"] not in milestone_ids:
                milestone_ids.append(milestone["id"])
        for milestone in facts["state_confirmed_milestones"]:
            if milestone["id"] not in state_milestone_ids:
                state_milestone_ids.append(milestone["id"])
    seeds = [analysis["run"]["operator_declared_seed"] for analysis in analyses]
    return {
        "schema_version": 1,
        "runs": [analysis["run"] for analysis in analyses],
        "metrics": dict(metrics),
        "second_minus_first": {
            key: round(values[1] - values[0], 3) if None not in values else None
            for key, values in metrics.items()
        }
        if len(paths) == 2
        else None,
        "milestone_claims": [
            {
                "id": identifier,
                "runs": [
                    next(
                        (
                            claim
                            for claim in analysis["model_claims"]["milestones"]
                            if claim["id"] == identifier
                        ),
                        None,
                    )
                    for analysis in analyses
                ],
            }
            for identifier in milestone_ids
        ],
        "state_confirmed_milestones": [
            {
                "id": identifier,
                "runs": [
                    next(
                        (
                            item
                            for item in analysis["facts"]["state_confirmed_milestones"]
                            if item["id"] == identifier
                        ),
                        None,
                    )
                    for analysis in analyses
                ],
            }
            for identifier in state_milestone_ids
        ],
        "comparability": {
            "same_mode": len({analysis["run"]["mode"] for analysis in analyses}) == 1,
            "same_gameplay_mode": len({analysis["run"]["gameplay_mode"] for analysis in analyses})
            == 1,
            "same_declared_seed": len(set(seeds)) == 1 if all(seeds) else None,
            "independently_verified_outcomes": False,
        },
        "notes": [
            "Values are recorded evidence, not a benchmark ranking. Gameplay modes, world conditions, pause time, and evidence quality affect comparisons. Raw completed actions and postcondition-verified skills are separate measures."
        ],
    }
