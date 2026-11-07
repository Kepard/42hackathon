"""Privileged scoring/diagnostics boundary, kept out of HoloAgent's prompts."""

import argparse
import asyncio
import json
import math
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .client import MinecraftClient
from .evaluator import LadderEvaluator
from .holo import HoloAPI, HoloAgent, HoloConfig, RunController, utcnow
from .user_tasks import definitions


class FailureDetector:
    def __init__(self, window=5):
        if window < 2:
            raise ValueError("Failure window must be at least 2")
        self.window = window
        self.gui_steps = 0
        self.samples = []
        self.signatures = []
        self.progress = None
        self.held = set()

    def update(self, record, state, progress):
        tags = []
        advanced = self.progress != progress
        self.progress = progress
        self.gui_steps = self.gui_steps + 1 if state.get("screen_open") and not advanced else 0
        if self.gui_steps >= self.window:
            tags.append("gui_stuck")
        if state.get("health", 20) <= 0 or state.get("screen") == "DeathScreen":
            tags.append("death")
        moving = bool(self.held)
        for action in record["executed_actions"]:
            if action["action"] == "key" and action["key"] in ("w", "a", "s", "d", "space"):
                if action["pressed"]:
                    self.held.add(action["key"])
                    moving = True
                else:
                    self.held.discard(action["key"])
        position = tuple(state.get(key, 0) for key in ("x", "y", "z"))
        self.samples.append((position, moving))
        self.samples = self.samples[-self.window:]
        if len(self.samples) == self.window and all(m for _, m in self.samples):
            if max(math.dist(self.samples[0][0], p) for p, _ in self.samples) < 0.2:
                tags.append("stuck_position")
        signature = json.dumps([{ "name": c["name"], "arguments": {k: v for k, v in c["arguments"].items() if k != "intent"}}
                                for c in record["tool_calls"]], sort_keys=True)
        self.signatures = [] if advanced else self.signatures
        self.signatures.append(signature)
        self.signatures = self.signatures[-self.window:]
        if signature != "[]" and len(self.signatures) == self.window and len(set(self.signatures)) == 1:
            tags.append("repeated_action_loop")
        return tags


def task_definition(task_id, user_file=None):
    if task_id == "ladder":
        development = task_definition("get_log", user_file)
        return {"id": "ladder", "description": "Complete these Survival milestones in order: log, crafting table, wooden pickaxe, cobblestone, stone pickaxe, furnace, iron ore, iron ingot, kill a hostile mob, flint and steel, obsidian, light or enter a Nether portal, enter the Nether.",
                "seed": development["seed"], "spawn": development["spawn"], "inventory": [], "goal": {"milestone": "enter_nether"}}
    for task in definitions(user_file):
        if task["id"] == task_id:
            return task
    raise ValueError(f"Unknown task {task_id}")


def summarize(records, evaluator, reached, success, elapsed, tags, config):
    latencies = sorted(r["api_latency_ms"] for r in records if r["api_latency_ms"] > 0)
    known_cost = all(r["cost_usd"] is not None for r in records)
    return {"success": success, "score": evaluator.result()["fraction"], "milestones": reached,
            "steps": len(records), "wall_time_seconds": elapsed,
            "mean_api_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
            "p95_api_latency_ms": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else 0,
            "total_cost_usd": sum(r["cost_usd"] for r in records) if known_cost else None,
            "tokens": {key: sum(r["tokens"][key] for r in records) for key in ("in", "cached", "out", "reasoning")},
            "failure_tags": sorted(tags), "config": asdict(config)}


class MinecraftRunner:
    def __init__(self, agent, client, out, controller=None, failure_window=5, tracing=True):
        self.agent, self.client, self.out = agent, client, Path(out)
        self.controller = controller or agent.controller
        self.agent.controller = self.controller
        self.detector = FailureDetector(failure_window)
        self.tracing = tracing

    @staticmethod
    def achieved(task, evaluator, state):
        goal = task["goal"]
        return (state.get("stats", {}).get("crafted", {}).get(goal["crafted"], 0) > 0 if "crafted" in goal else
                goal["milestone"] in evaluator.result()["completed"] if "milestone" in goal else
                evaluator.has_item(goal["item"]))

    async def run(self, task_id, task=None):
        task = task_definition(task_id) if task is None else task
        self.out.mkdir(parents=True, exist_ok=True)
        if (self.out / "steps.jsonl").exists():
            raise ValueError("Run output already exists; choose a new directory")
        tracer = None
        if self.tracing:
            from cua_bench.tracing import Tracing
            tracer = Tracing(self)
            tracer.start()
        await self.client.call("configure", auto_jump=self.agent.config.tier == "skills")
        initial = await self.client.reset(task["seed"], task["inventory"], task.get("spawn"))
        (self.out / "initial_state.json").write_text(json.dumps({"task": task, "state": initial}, indent=2))
        if self.agent.config.timing == "realtime":
            await self.client.resume()
        evaluator = LadderEvaluator()
        evaluator.update(initial)
        start = time.monotonic()
        reached = {rung: 0.0 for rung in evaluator.result()["completed"]}
        records, tags, success = [], set(), False
        iterator = self.agent.steps(self.client, task["description"], self.out)
        try:
            async for record in iterator:
                try:
                    state = await self.client.state()
                    snapshot = evaluator.update(state)
                    final_step = record["step"] + 1 == self.agent.config.max_steps or bool(
                        {"timeout", "budget_exhausted"} & set(record["failure_tags"]))
                    if final_step and not self.achieved(task, evaluator, state):
                        try:
                            if self.agent.config.timing == "freeze":
                                await self.client.resume()
                            await asyncio.sleep(.6)
                        finally:
                            if self.agent.config.timing == "freeze":
                                await self.client.pause()
                        state = await self.client.state()
                        snapshot = evaluator.update(state)
                        record["final_settle_ms"] = 600
                    for rung in snapshot["completed"]:
                        reached.setdefault(rung, time.monotonic() - start)
                    success = self.achieved(task, evaluator, state)
                    success = success and time.monotonic() - start <= task["time_limit_seconds"]
                    record["evaluator"] = snapshot
                    record["failure_tags"] += self.detector.update(record, state, (tuple(snapshot["completed"]), len(evaluator.obtained)))
                    record["metrics"] = {key: state.get(key) for key in ("x", "y", "z", "health", "screen", "screen_open")}
                except Exception as error:
                    record["errors"].append(f"Scoring: {type(error).__name__}: {error}")
                    record["evaluator"] = evaluator.result()
                    record["failure_tags"].append("metrics_error")
                if not success and record["step"] + 1 == self.agent.config.max_steps:
                    record["failure_tags"].append("budget_exhausted")
                record["failure_tags"] = sorted(set(record["failure_tags"]))
                tags.update(record["failure_tags"])
                records.append(record)
                with (self.out / "steps.jsonl").open("a") as stream:
                    stream.write(json.dumps(record, allow_nan=False) + "\n")
                if tracer:
                    png = (self.out / record["screenshot_ref"]).read_bytes() if record["screenshot_ref"] else None
                    tracer.record("step", record, [png] if png else [])
                await self.controller.emit(record)
                if success or "death" in record["failure_tags"]:
                    await self.controller.stop()
                    break
        finally:
            await iterator.aclose()
            tags.update(self.agent.terminal_tags)
            summary = summarize(records, evaluator, reached, success, time.monotonic() - start, tags, self.agent.config)
            summary.update({"task": task_id, "goal": self.controller.goal, "finished_at": utcnow()})
            (self.out / "summary.json").write_text(json.dumps(summary, indent=2))
            if tracer and records:
                for intervention in getattr(self.controller, "interventions", []):
                    tracer.record("intervention", intervention)
                tracer.record("summary", summary)
                tracer.save_to_disk(str(self.out / "trajectory"), save_pngs=True)
        return summary


async def run_cli(args):
    config = HoloConfig(model=args.model, tier=args.tier, timing=args.timing, max_steps=args.max_steps,
                        time_budget=args.time_budget, history_steps=args.history_steps,
                        history_images=args.history_images, temperature=args.temperature,
                        seed=args.seed, max_cost_usd=args.max_cost_usd)
    api = HoloAPI(os.environ.get("HCOMPANY_API_KEY"))
    out = Path(args.out) / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    controller = RunController()
    async with MinecraftClient(args.url) as client:
        agent = HoloAgent(config, api, controller)
        summary = await MinecraftRunner(agent, client, out, failure_window=args.failure_window, tracing=not args.no_trace).run(args.task)
    print(json.dumps({"out": str(out), **summary}, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="obs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run a screenshot-only Minecraft Holo agent")
    run.add_argument("--task", default="get_log")
    run.add_argument("--model", default="holo4-27b")
    run.add_argument("--tier", choices=("raw", "skills"), default="skills")
    run.add_argument("--timing", choices=("freeze", "realtime"), default="realtime")
    run.add_argument("--max-steps", type=int, default=30)
    run.add_argument("--time-budget", type=float, default=180)
    run.add_argument("--history-steps", type=int, default=8)
    run.add_argument("--history-images", type=int, default=3)
    run.add_argument("--temperature", type=float, default=.2)
    run.add_argument("--seed", type=int)
    run.add_argument("--max-cost-usd", type=float)
    run.add_argument("--failure-window", type=int, default=5)
    run.add_argument("--out", default="runs")
    run.add_argument("--url", default="ws://127.0.0.1:8765")
    run.add_argument("--no-trace", action="store_true", help="Skip optional cua-bench trajectory export")
    args = parser.parse_args()
    asyncio.run(run_cli(args))
