import asyncio
import base64
import json
import os
import re
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from .holo_tools import InvalidToolArgs, ToolExecutor, parse_calls, tool_schemas, observation_prefix
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
    api_timeout: float = 15
    reflex: str = "none"
    jev_threshold: float = .5
    reasoning_effort: str | None = None

    def __post_init__(self):
        if self.reflex not in ("none", "jev") or (self.reflex == "jev" and self.tier != "skills"):
            raise ValueError("Jev reflex requires skills tier")
        if not 0 <= self.jev_threshold <= 1 or self.reasoning_effort not in (None, "low"):
            raise ValueError("Invalid Jev threshold or reasoning effort")
        if self.reasoning_effort is not None and self.model != "holo4-27b":
            raise ValueError("Reasoning effort setting is only verified for holo4-27b")
        if self.tier not in ("raw", "skills") or self.timing not in ("freeze", "realtime"):
            raise ValueError("Invalid tier/timing")
        if self.max_steps < 1 or self.time_budget <= 0 or self.history_steps < 0 or self.history_images < 1:
            raise ValueError("Invalid step, time or history limit")
        if not 0 <= self.temperature <= 2 or self.api_timeout <= 0 or (self.max_cost_usd is not None and self.max_cost_usd <= 0):
            raise ValueError("Invalid temperature, API timeout or cost limit")
        if self.max_cost_usd is not None and self.model not in PRICING:
            raise ValueError("A cost budget requires known model pricing")


class HoloAPIError(RuntimeError):
    def __init__(self, errors, retry_after=None):
        self.errors = errors
        self.retry_after = retry_after
        super().__init__(f"H Company HTTP {errors[-1]['status']}")


class HoloAPI:
    _locks = {}
    _next = {}
    short_timeout = 20

    def __init__(self, api_key, api_timeout=15):
        if not api_key:
            raise ValueError("HCOMPANY_API_KEY is required")
        self.api_key = api_key
        self.api_timeout = api_timeout

    @property
    def total_timeout(self):
        return min(self.short_timeout, self.api_timeout) + 2 * self.api_timeout + 67

    async def complete(self, payload):
        errors = []
        self.last_errors = errors
        timeout_retried = False
        async with aiohttp.ClientSession() as session:
            for attempt in range(3):
                self.last_attempts = attempt + 1
                model = payload["model"]
                lock = self._locks.setdefault(model, asyncio.Lock())
                async with lock:
                    await asyncio.sleep(max(0, self._next.get(model, 0) - time.monotonic()))
                    self._next[model] = time.monotonic() + 2.05
                try:
                    request_timeout = min(self.short_timeout, self.api_timeout) if attempt == 0 else self.api_timeout
                    async with asyncio.timeout(request_timeout):
                        return await self._request(session, payload, errors, request_timeout)
                except TimeoutError:
                    errors.append({"status": None, "type": "request_timeout", "timeout_s": request_timeout, "attempt": attempt + 1})
                    if timeout_retried or attempt == 2:
                        raise
                    timeout_retried = True
                except HoloAPIError as error:
                    if errors[-1]["status"] != 429 or attempt == 2:
                        raise
                    try:
                        delay = max(1, min(30, float(error.retry_after or 2 ** (attempt + 1))))
                    except ValueError:
                        delay = 2 ** (attempt + 1)
                    await asyncio.sleep(delay)

    async def _request(self, session, payload, errors, request_timeout):
        async with session.post("https://api.hcompany.ai/v1/chat/completions",
                                headers={"Authorization": f"Bearer {self.api_key}"}, json=payload,
                                timeout=aiohttp.ClientTimeout(total=request_timeout)) as response:
            if response.status == 200:
                result = await response.json()
                result["_api_errors"] = errors
                return result
            try:
                raw = await response.content.readexactly(65537)
            except asyncio.IncompleteReadError as error:
                raw = error.partial
            body = raw[:65536].decode("utf-8", errors="replace")
            secrets = [self.api_key] + [value for key, value in os.environ.items()
                                       if re.search(r"(?:KEY|TOKEN|SECRET)$", key) and len(value) >= 8]
            for secret in secrets:
                body = body.replace(secret, "[REDACTED]")
            body = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+", "Bearer [REDACTED]", body)
            errors.append({"status": response.status, "body": body, "truncated": len(raw) > 65536})
            raise HoloAPIError(errors, response.headers.get("Retry-After"))


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


REACH_GUIDANCE = "At854x480/FOV70, trunk width >=85px usually means reach. Sprint+jump; sneak near cliffs."
TREE_GUIDANCE = """TREE: streaked brown bark, NOT speckled dirt -> approach -> clear intervening green leaves
(~0.35s; hold ~0.6s) -> OBSERVE -> re-aim bark -> mine(max_s=4), ~3.3s/log by hand ->
collect(max_s=0.8) -> confirm LOG icon/count. Leaf gone is progress, NOT a log.
Reflex outline/break/hotbar pixel cues are fallible, not semantic guarantees. Get4 logs.
No cracks: change ONE variable (closer/re-aim/pitch/strafe); don't blindly retreat from leaves."""
JEV_REACH_GUIDANCE = "Always walk right up to the block before mining (~1.5–2blocks). Seek until close, then attack; mining checks close size before any outline/progress probe. Never back away merely to find an outline. not_close/not_in_reach -> seek(target) first, not another attack. No centre change means no progress; scene_changed alone is not cracking. Collect only drops. Avoid cliffs/lava."
JEV_TREE_GUIDANCE = "TREE: seek('oak log trunk',max_s=8) -> attack_until_broken(max_s=4) -> collect(max_s=0.8,item='log') -> confirm LOG icon. Seek is the ONLY block approach tool. If leaves obstruct bark, seek('oak leaves'), attack_until_broken(max_s=0.6), then seek bark again. Leaf gone is progress, not a log. Scan with look when not_visible; reposition only to retreat/sidestep. Seek/collect use fast TypeSafe Jev on screenshots; mining uses outline/progress pixels and hotbar scores are advisory."

PRIMER = """Minecraft Java Survival, screenshots and input tools only. Goal: {goal}
CHECK -> EXPLAIN -> ACT: intent names visible outcome, failure hypothesis, next subgoal.
CURRENT pixels override old frames/notes. After milestones update_notes: inventory,
completed subgoals, next waypoint. Don't recraft visible tools. Goal overrides route.

REACH: always walk right up to the block before mining (~1.5–2blocks).
Visible black block selection outline confirms ~4.5-block reach, not close size; absence is ambiguous
when edges are offscreen. Cracks appear within ~0.3s; swinging isn't progress.
{reach_guidance}
{tree_guidance}

CONTROLS: WASD, space jump, shift sneak, E inventory, Escape closes GUI (opens menu if none),
1-9 hotbar, left mine, right use. Look is RELATIVE: yaw right, pitch down. click_at is GUI-only;
{aim_guidance} Lower-right cube is your HELD ITEM,
not a world block: select EMPTY hotbar slot before locating stations.
F3 via raw key_press/skills toggle_debug:
read XYZ/Facing/Targeted Block only from pixels then close. Target up to20 blocks is NOT reach;
use yaw NUMBER, not just cardinal Facing: relative turn=desired yaw-current yaw.
Skills scene_changed/stuck are pixel heuristics, NOT
block-gone or item-collected; raw has no computed feedback. Check frames.
SKILLS BATCHING: {batch_guidance} Stop after mining/attack,
reflex moves, or moves >0.5s: later calls are discarded, not queued. Observe before mining.

ROUTE / VISIBLE CHECKLIST (don't skip prerequisites):
1 log: log icon/count. Full ladder: 4 logs -> planks (4/log), table (4 planks), sticks.
Craft: click recipe-book search, Ctrl+A, type the item name FIRST; observe filtered results, then click the matching result. Never use unsearched icon positions.
Observe the output slot: shift-click ONLY if visibly nonempty. If empty, re-search or obtain missing ingredients; never repeat empty-output clicks.
2 table icon, place/use it -> 3 wooden pickaxe icon (3 planks + 2 sticks).
4 cobblestone count: mine gray stone WITH pickaxe; 3 cobble -> 5 stone pickaxe icon.
Gather >=11 more cobble -> 6 furnace icon (8 ring), sword, blocks.
7 raw iron icon: cliffs/caves, tan-speckled iron, not copper; mine with
stone pick. Staircase toward Y16, never straight down; ~7 iron.
8 ingot icon: furnace raw iron + coal/planks fuel; take output. Iron:
bucket3, iron pick3, flint-and-steel1. Coal smelts8, a plank1.5.
9 hostile kill: cave zombie/spider, death smoke/drop, not a hit. Avoid creepers
and skeletons; shelter/pillar, retreat below5 hearts. At fixed noon don't wait for night.
10 flint-and-steel icon: gravel -> flint + ingot; ruined-portal/village chests help.
11 obsidian: item OR create black-purple obsidian with water over lava SOURCE (flowing lava
gives cobble). Casting counts. Chest loot also works; mining requires diamond pick, ~10s/block.
12 portal: finish/repair a 2x3 interior, 10-obsidian frame; no crying obsidian. Cast missing
blocks with bucket/mold if needed; light inner bottom with flint-and-steel; purple fill.
13 Nether: portal ~4s, verify red terrain/dimension in F3.

STUCK: two unchanged moves -> jump+forward or strafe/turn, not repeated forward.
Never repeat failure >2 times: change one variable/subgoal. Close unexpected GUI.
Wrong tool/no drops -> change tool. Short actions; each with intent. {timing}
"""


class HoloAgent(BaseAgent):
    def __init__(self, config=None, api=None, controller=None, jev_client=None):
        super().__init__()
        self.config = config or HoloConfig()
        self.api = api if api is not None else HoloAPI(os.environ.get("HCOMPANY_API_KEY"), self.config.api_timeout)
        if isinstance(self.api, HoloAPI):
            self.api.api_timeout = self.config.api_timeout
        self.controller = controller or RunController()
        self.terminal_tags = []
        self.records = []
        self.jev_client = jev_client

    @staticmethod
    def name():
        return "holo-minecraft"

    def messages(self, images, feedback_images=(), notes="", crop=None):
        timing = ("The game is frozen during deliberation; actions run with ticks enabled. Held inputs carry across steps only during action execution."
                  if self.config.timing == "freeze" else "The game runs during deliberation. Release held controls explicitly.")
        history = self.records[-self.config.history_steps:] if self.config.history_steps else []
        summaries = [{"step": r["step"], "intent": r["intent"], "actions": r["tool_calls"], "skipped_tool_calls": r.get("skipped_tool_calls", []), "errors": r["errors"], "tool_results": r.get("tool_results", [])} for r in history]
        content = [{"type": "text", "text": "Your recent actions and visible effects you reported; skills pixel estimates are heuristics: " + json.dumps(summaries)},
                   {"type": "text", "text": "Your scratchpad (may be stale; reconcile with CURRENT pixels): " + (notes or "(empty)")}]
        selected = images[-min(3, self.config.history_images):]
        observations = [(f"Full screen from step {idx}", png) for idx, png in selected[:-1]]
        observations += [(f"Previous action pixel-evidence frame {idx}", png) for idx, png in enumerate(feedback_images)]
        observations += [(f"CURRENT full screen from step {selected[-1][0]}", selected[-1][1])]
        observations = observations[-3:]
        observations += [("2x CURRENT center crop; coordinates still refer to the full screen", crop or center_crop(selected[-1][1]))]
        for label, png in observations:
            content += [{"type": "text", "text": label},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]
        jev = self.config.reflex == "jev"
        primer = PRIMER.format(goal=self.controller.goal, timing=timing,
            reach_guidance=JEV_REACH_GUIDANCE if jev else REACH_GUIDANCE,
            tree_guidance=JEV_TREE_GUIDANCE if jev else TREE_GUIDANCE,
            aim_guidance="Use seek for blocks and look for scanning." if jev else "aim_at x/y=0..1000 on CURRENT FULL screen, not crop.",
            batch_guidance="GUI clicks/typing may batch." if jev else "GUI clicks/typing and look->move may batch.")
        return [{"role": "system", "content": primer}, {"role": "user", "content": content}]

    async def steps(self, client, goal, out):
        """Yield pixel-only records. The runner alone appends scoring/diagnostics."""
        config = self.config
        out = Path(out)
        (out / "screenshots").mkdir(parents=True, exist_ok=True)
        self.records, self.terminal_tags = [], []
        self.controller.goal = goal
        start, cost = time.monotonic(), 0.0
        images, executor = [], None
        jev = None
        if config.reflex == "jev":
            from .jev import JevVision
            jev = JevVision(self.jev_client)
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
                        images = images[-min(3, config.history_images):]
                        record["image_refs"] = [f"screenshots/{image_idx:05d}.png" for image_idx, _ in images]
                        crop = center_crop(png)
                        record["crop_ref"] = f"screenshots/{idx:05d}-center.png"
                        (out / record["crop_ref"]).write_bytes(crop)
                        record["input_image_refs"] = (record["image_refs"][:-1] + feedback_refs + record["image_refs"][-1:])[-3:] + [record["crop_ref"]]
                        width, height = struct.unpack(">II", png[16:24])
                        if executor is None:
                            executor = ToolExecutor(client, width, height, jev, config.jev_threshold)
                        executor.width, executor.height, executor.actions = width, height, []
                        executor.begin_step(png)
                        if jev is not None:
                            jev.calls = []
                        payload = {"model": config.model, "messages": self.messages(images, feedback_images, executor.notes, crop), "tools": tool_schemas(config.tier, config.reflex),
                                   "temperature": config.temperature, "max_tokens": 4096}
                        if config.reasoning_effort is not None:
                            payload["reasoning_effort"] = config.reasoning_effort
                        if config.seed is not None:
                            payload["seed"] = config.seed
                        announce = getattr(client, "model_input", None)
                        if announce is not None:
                            await announce(idx, payload["messages"][1]["content"], record["input_image_refs"])
                        api_start = time.monotonic()
                        try:
                            async with asyncio.timeout(getattr(self.api, "total_timeout", config.api_timeout)):
                                response = await self.api.complete(payload)
                                record["api_errors"] = response.get("_api_errors", [])
                        except TimeoutError:
                            record["failure_tags"] += ["api_error", "api_timeout"]
                            raise
                        except Exception:
                            record["failure_tags"].append("api_error")
                            raise
                        finally:
                            record["api_latency_ms"] = (time.monotonic() - api_start) * 1000
                            record["api_attempts"] = getattr(self.api, "last_attempts", 1)
                        record["tokens"], record["cost_usd"] = usage_cost(response.get("usage") or {}, config.model)
                        if config.max_cost_usd is not None and cost + (record["cost_usd"] or 0) >= config.max_cost_usd:
                            record["failure_tags"].append("budget_exhausted")
                        choice = response["choices"][0]
                        record["finish_reason"] = choice.get("finish_reason")
                        if record["finish_reason"] == "length":
                            record["failure_tags"].append("completion_truncated")
                            record["errors"].append("Completion token budget exhausted; choose one short tool action next turn.")
                        message = choice["message"]
                        record["reasoning"] = message.get("reasoning_content") or message.get("reasoning") or ""
                        record["raw_tool_calls"] = message.get("tool_calls") or []
                        record["skipped_tool_calls"] = []
                        pending = record["raw_tool_calls"]
                        if config.tier == "skills":
                            pending, record["skipped_tool_calls"] = observation_prefix(pending, config.reflex)
                        calls = parse_calls({"tool_calls": pending}, config.tier, config.reflex)
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
                    if api_start is not None:
                        record["api_errors"] = getattr(self.api, "last_errors", [])
                    if "api_timeout" not in record["failure_tags"]:
                        record["failure_tags"].append("timeout")
                    record["errors"].append("API request timed out; observe and retry next turn" if "api_timeout" in record["failure_tags"] else "Step time budget exceeded")
                except Exception as error:
                    if isinstance(error, HoloAPIError):
                        record["api_errors"] = error.errors
                    error_tag = "api_error" if api_start is not None and action_start is None else "action_error"
                    if error_tag not in record["failure_tags"]:
                        record["failure_tags"].append(error_tag)
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
                record["holo_cost_usd"] = record["cost_usd"]
                record["jev_calls"] = jev.calls.copy() if jev is not None else []
                for vision_call in record["jev_calls"]:
                    frame_index = vision_call.get("frame_index")
                    if frame_index is not None and 0 <= frame_index < len(feedback_refs):
                        vision_call["screenshot_ref"] = feedback_refs[frame_index]
                record["jev_cost_usd"] = sum(call["cost_usd"] for call in record["jev_calls"])
                if record["cost_usd"] is not None:
                    record["cost_usd"] += record["jev_cost_usd"]
                if jev is not None:
                    jev.calls = []
                if config.max_cost_usd is not None and cost + (record["cost_usd"] or 0) >= config.max_cost_usd:
                    record["failure_tags"].append("budget_exhausted")
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
            if jev is not None:
                await jev.close()

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
