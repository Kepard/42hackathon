import asyncio
import base64
import json
import os
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from .holo_tools import InvalidToolArgs, ToolExecutor, parse_calls, tool_schemas
from .vision import center_crop

try:
    from cua_bench.agents.base import BaseAgent
except ImportError:
    BaseAgent = object


PRICING = {"holo4-27b": {"input": 0.40, "cached": 0.04, "output": 3.00},
           "holo4-35b-a3b": {"input": 0.30, "cached": 0.03, "output": 2.00}}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def usage_cost(usage, model):
    tokens = {"in": int(usage.get("prompt_tokens", 0)),
              "cached": int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)),
              "out": int(usage.get("completion_tokens", 0)),
              "reasoning": int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0))}
    price = PRICING.get(model)
    cost = None if price is None else ((tokens["in"] - tokens["cached"]) * price["input"] + tokens["cached"] * price["cached"] + tokens["out"] * price["output"]) / 1_000_000
    return tokens, cost


@dataclass
class HoloConfig:
    model: str = "holo4-27b"
    tier: str = "skills"
    timing: str = "realtime"
    max_steps: int = 30
    time_budget: float = 180
    history_steps: int = 8
    history_images: int = 3
    temperature: float = 0.2
    seed: int | None = None
    max_cost_usd: float | None = None
    api_timeout: float = 45

    def __post_init__(self):
        if self.tier not in ("raw", "skills") or self.timing not in ("freeze", "realtime"):
            raise ValueError("Invalid tier/timing")
        if self.max_steps < 1 or self.time_budget <= 0 or self.history_steps < 0 or self.history_images < 1:
            raise ValueError("Invalid step, time or history limit")
        if not 0 <= self.temperature <= 2 or self.api_timeout <= 0 or (self.max_cost_usd is not None and self.max_cost_usd <= 0):
            raise ValueError("Invalid temperature, API timeout or cost limit")
        if self.max_cost_usd is not None and self.model not in PRICING:
            raise ValueError("A cost budget requires known model pricing")


class HoloAPI:
    def __init__(self, api_key):
        if not api_key:
            raise ValueError("HCOMPANY_API_KEY is required")
        self.api_key = api_key

    async def complete(self, payload):
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.hcompany.ai/v1/chat/completions",
                                    headers={"Authorization": f"Bearer {self.api_key}"}, json=payload) as response:
                if response.status != 200:
                    # Never serialize request headers or provider responses that could echo secrets.
                    raise RuntimeError(f"H Company HTTP {response.status}")
                return await response.json()


class RunController:
    """In-process hooks honored between steps; queue drops oldest on overflow."""

    def __init__(self, goal="", callback=None):
        self.goal = goal
        self.stopped = False
        self.paused = False
        self._changed = asyncio.Event()
        self.events = asyncio.Queue(maxsize=100)
        self.callback = callback

    async def pause(self):
        self.paused = True
        self._changed.set()

    async def resume(self):
        self.paused = False
        self._changed.set()

    async def stop(self):
        self.stopped = True
        self._changed.set()

    async def set_goal(self, text):
        if not text.strip():
            raise ValueError("Goal cannot be empty")
        self.goal = text

    async def checkpoint(self):
        while self.paused and not self.stopped:
            self._changed.clear()
            await self._changed.wait()
        return not self.stopped

    async def emit(self, record):
        if self.events.full():
            self.events.get_nowait()
        self.events.put_nowait(record)
        if self.callback:
            result = self.callback(record)
            if hasattr(result, "__await__"):
                await result


PRIMER = """Minecraft Java Survival, screenshots and input tools only. Goal: {goal}
CHECK -> EXPLAIN -> ACT: compare recent frames; state last action's visible effect,
a failure hypothesis if needed, and next subgoal in your intent.
Describe observable outcomes, not hidden reasoning. Current goal overrides the route.
Use update_notes for visible inventory, rung, landmarks and lessons.

REACH: crosshair aims; a black block selection outline means within 4.5 blocks. No outline:
approach/re-aim. Cracks should appear within ~0.3s; arm swinging
without cracks is not progress. Inspect the 2x center crop. At vertical FOV70 and height480,
a front-facing one-block face at reach is about 76px high (16% of height); smaller usually
means farther. Approximate only. Aim at bark, not green leaves.
Walk ~4.3 blocks/s. Short steps near cliffs; sneak at edges.
Keep aim steady while mining; logs by hand take ~3s. Walk onto drops; verify hotbar icon/count.

CONTROLS: WASD move, space jump, shift sneak, E inventory, Escape closes GUI (opens menu
if none), 1-9 hotbar, left hold mine/attack, right use/place. Look is RELATIVE: yaw right,
pitch down. GUI click_at is not world aiming. Skills aim_at takes a full-screen point;
all x/y are 0..1000 on the FULL frame, not the crop. F3 via raw key_press or skills
toggle_debug: read XYZ/Facing and Targeted Block only from pixels, then close it.
F3 targets up to20 blocks: a name is NOT reach. Compare eye/target coordinates.
Skills change scores/stuck/broke are disclosed pixel heuristics, not proof of mining or
pickup; raw has no computed feedback. Check source frames.

ROUTE / VISIBLE CHECKLIST (evidence may happen early; don't skip missing prerequisites):
1 log: log icon/count. For full ladder gather ~6 logs -> planks (4/log), table (4 planks),
sticks; recipe-book craftable filter, click recipe then shift-click output.
2 table icon, place/use it -> 3 wooden pickaxe icon (3 planks + 2 sticks).
4 cobblestone count: mine gray stone WITH pickaxe; 3 cobble -> 5 stone pickaxe icon.
Gather >=11 more cobble -> 6 furnace icon (8 ring), sword, blocks.
7 raw iron icon: search exposed cliffs/caves for tan-speckled iron, not copper; mine with
stone pick. Fallback staircase toward Y16; never dig straight down. Get ~7 iron.
8 ingot icon: furnace raw iron + coal/planks fuel, wait, take output. Budget iron:
bucket3, iron pick3, flint-and-steel1. Coal smelts8, a plank1.5.
9 hostile kill: zombie/spider in a cave; death smoke/drop, not merely a hit. Avoid creepers
and skeletons; shelter/pillar, retreat below5 hearts. At fixed noon don't wait for night.
10 flint-and-steel icon: gravel -> flint + ingot; ruined-portal/village chests help.
11 obsidian: item OR create black-purple obsidian with water over lava SOURCE (flowing lava
gives cobble). Casting counts. Chest loot also works; mining requires diamond pick, ~10s/block.
12 portal: finish/repair a 2x3 interior, 10-obsidian frame; no crying obsidian. Cast missing
blocks with bucket/mold if needed; light inner bottom with flint-and-steel; purple fill.
13 enter Nether: portal ~4s, verify red terrain/dimension in F3.

STUCK: two unchanged moves -> jump+forward or strafe/turn, not repeated forward.
Never repeat a failed action >2 times: change distance, aim or subgoal. Close unexpected
GUI first. No cracks -> move closer. Wrong tool/no drops -> change tool. Use short action
chunks; only tools listed, each with intent. {timing}
"""


class HoloAgent(BaseAgent):
    def __init__(self, config=None, api=None, controller=None):
        super().__init__()
        self.config = config or HoloConfig()
        self.api = api if api is not None else HoloAPI(os.environ.get("HCOMPANY_API_KEY"))
        self.controller = controller or RunController()
        self.terminal_tags = []
        self.records = []

    @staticmethod
    def name():
        return "holo-minecraft"

    def messages(self, images, feedback_images=(), notes="", crop=None):
        timing = ("The game is frozen during deliberation; actions run with ticks enabled. Held inputs carry across steps only during action execution."
                  if self.config.timing == "freeze" else "The game runs during deliberation. Release held controls explicitly.")
        history = self.records[-self.config.history_steps:] if self.config.history_steps else []
        summaries = [{"step": r["step"], "intent": r["intent"], "actions": r["tool_calls"], "errors": r["errors"], "tool_results": r.get("tool_results", [])} for r in history]
        content = [{"type": "text", "text": "Your recent actions and visible effects you reported; skills pixel estimates are heuristics: " + json.dumps(summaries)},
                   {"type": "text", "text": "Your scratchpad: " + (notes or "(empty)")}]
        selected = images[-self.config.history_images:]
        observations = [(f"Full screen from step {idx}", png) for idx, png in selected[:-1]]
        observations += [(f"Previous action pixel-evidence frame {idx}", png) for idx, png in enumerate(feedback_images)]
        observations += [(f"CURRENT full screen from step {selected[-1][0]}", selected[-1][1])]
        observations += [("2x CURRENT center crop; coordinates still refer to the full screen", crop or center_crop(selected[-1][1]))]
        for label, png in observations:
            content += [{"type": "text", "text": label},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]
        return [{"role": "system", "content": PRIMER.format(goal=self.controller.goal, timing=timing)}, {"role": "user", "content": content}]

    async def steps(self, client, goal, out):
        """Yield pixel-only records. The runner alone appends scoring/diagnostics."""
        config = self.config
        out = Path(out)
        (out / "screenshots").mkdir(parents=True, exist_ok=True)
        self.records, self.terminal_tags = [], []
        self.controller.goal = goal
        start, cost = time.monotonic(), 0.0
        images, executor = [], None
        feedback_images, feedback_refs = [], []
        try:
            for idx in range(config.max_steps):
                remaining = config.time_budget - (time.monotonic() - start)
                if remaining <= 0:
                    self.terminal_tags.append("timeout")
                    break
                try:
                    async with asyncio.timeout(remaining):
                        if config.timing == "freeze":
                            await client.pause()
                        if not await self.controller.checkpoint():
                            break
                except TimeoutError:
                    self.terminal_tags.append("timeout")
                    break
                record = {"step": idx, "started_at": utcnow(), "model": config.model, "goal": self.controller.goal,
                          "reasoning": "", "intent": [], "tool_calls": [], "executed_actions": [], "errors": [], "failure_tags": [],
                          "api_latency_ms": 0.0, "action_duration_ms": 0.0, "tokens": {"in": 0, "cached": 0, "out": 0, "reasoning": 0}, "cost_usd": 0.0,
                          "screenshot_ref": None}
                api_start = None
                action_start = None
                if executor is not None:
                    executor.begin_step()
                try:
                    async with asyncio.timeout(max(0.001, config.time_budget - (time.monotonic() - start))):
                        png = await client.screenshot()
                        path = out / "screenshots" / f"{idx:05d}.png"
                        path.write_bytes(png)
                        record["screenshot_ref"] = str(path.relative_to(out))
                        images.append((idx, png))
                        images = images[-config.history_images:]
                        record["image_refs"] = [f"screenshots/{image_idx:05d}.png" for image_idx, _ in images]
                        crop = center_crop(png)
                        record["crop_ref"] = f"screenshots/{idx:05d}-center.png"
                        (out / record["crop_ref"]).write_bytes(crop)
                        record["input_image_refs"] = record["image_refs"][:-1] + feedback_refs + record["image_refs"][-1:] + [record["crop_ref"]]
                        width, height = struct.unpack(">II", png[16:24])
                        if executor is None:
                            executor = ToolExecutor(client, width, height)
                        executor.width, executor.height, executor.actions = width, height, []
                        executor.begin_step(png)
                        payload = {"model": config.model, "messages": self.messages(images, feedback_images, executor.notes, crop), "tools": tool_schemas(config.tier),
                                   "temperature": config.temperature, "max_tokens": 2048}
                        if config.seed is not None:
                            payload["seed"] = config.seed
                        announce = getattr(client, "model_input", None)
                        if announce is not None:
                            await announce(idx, payload["messages"][1]["content"], record["input_image_refs"])
                        api_start = time.monotonic()
                        try:
                            async with asyncio.timeout(config.api_timeout):
                                response = await self.api.complete(payload)
                        except TimeoutError:
                            record["failure_tags"] += ["api_error", "timeout"]
                            raise
                        except Exception:
                            record["failure_tags"].append("api_error")
                            raise
                        finally:
                            record["api_latency_ms"] = (time.monotonic() - api_start) * 1000
                        record["tokens"], record["cost_usd"] = usage_cost(response.get("usage") or {}, config.model)
                        if config.max_cost_usd is not None and cost + (record["cost_usd"] or 0) >= config.max_cost_usd:
                            record["failure_tags"].append("budget_exhausted")
                        message = response["choices"][0]["message"]
                        record["reasoning"] = message.get("reasoning_content") or message.get("reasoning") or ""
                        record["raw_tool_calls"] = message.get("tool_calls") or []
                        calls = parse_calls(message, config.tier)
                        record["tool_calls"] = calls
                        record["intent"] = [call["arguments"]["intent"] for call in calls]
                        if not calls:
                            record["failure_tags"].append("no_tool_call")
                        elif "budget_exhausted" not in record["failure_tags"]:
                            action_start = time.monotonic()
                            try:
                                if config.timing == "freeze":
                                    await client.resume()
                                    await executor.restore()
                                for call in calls:
                                    await executor.execute(call, config.tier)
                            finally:
                                if config.timing == "freeze":
                                    await executor.release(retain=True)
                                    await client.pause()
                                record["action_duration_ms"] = (time.monotonic() - action_start) * 1000
                except InvalidToolArgs as error:
                    record["failure_tags"].append("invalid_tool_args")
                    record["errors"].append(str(error))
                except TimeoutError:
                    if "timeout" not in record["failure_tags"]:
                        record["failure_tags"].append("timeout")
                    record["errors"].append("Step or API time budget exceeded")
                except Exception as error:
                    if not record["failure_tags"]:
                        record["failure_tags"].append("api_error" if api_start is not None and action_start is None else "action_error")
                    record["errors"].append(f"{type(error).__name__}: {error}")
                if executor is not None:
                    record["executed_actions"] = executor.actions.copy()
                    record["tool_results"] = executor.results.copy()
                    record["notes"] = executor.notes
                    feedback_images = executor.frames.copy()
                    feedback_refs = []
                    for frame_idx, frame in enumerate(feedback_images):
                        ref = f"screenshots/{idx:05d}-action-{frame_idx:03d}.png"
                        (out / ref).write_bytes(frame)
                        feedback_refs.append(ref)
                    record["feedback_image_refs"] = feedback_refs.copy()
                record["finished_at"] = utcnow()
                record["elapsed_seconds"] = time.monotonic() - start
                cost += record["cost_usd"] or 0
                self.records.append(record)
                yield record
                if "timeout" in record["failure_tags"] or "budget_exhausted" in record["failure_tags"]:
                    break
            else:
                self.terminal_tags.append("budget_exhausted")
        finally:
            if executor is not None:
                await executor.release()
            if config.timing == "freeze":
                await client.pause()

    async def perform_task(self, task_description, session, logging_dir=None, tracer=None):
        from cua_bench.agents.base import AgentResult, FailureMode

        await session.client.call("configure", auto_jump=self.config.tier == "skills")
        out = Path(logging_dir or "runs/base-agent")
        async for record in self.steps(session.client, task_description, out):
            with (out / "steps.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            if tracer:
                tracer.record("step", record, [(out / record["screenshot_ref"]).read_bytes()] if record["screenshot_ref"] else [])
            await self.controller.emit(record)
        tags = {tag for record in self.records for tag in record["failure_tags"]} | set(self.terminal_tags)
        failure = FailureMode.API_ERROR if "api_error" in tags else FailureMode.MAX_STEPS_EXCEEDED if "budget_exhausted" in tags else FailureMode.UNKNOWN if tags else FailureMode.NONE
        return AgentResult(sum(r["tokens"]["in"] for r in self.records), sum(r["tokens"]["out"] for r in self.records), failure)
