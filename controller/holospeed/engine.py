from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import time
from collections import deque

from PIL import Image

from .bridge import GameBridge
from .config import Settings
from .demo import DemoBridge, demo_decision
from .models import Decision
from .providers import HoloProvider, JevProvider, ProviderError, ReplanRequired
from .recording import RunRecorder, redact


class AgentEngine:
    def __init__(self, settings: Settings, bridge: GameBridge):
        self.settings = settings
        self.live_bridge = bridge
        self.bridge = DemoBridge() if settings.mode == "demo" else bridge
        self.recorder: RunRecorder | None = None
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.subscribers: set[asyncio.Queue] = set()
        self.holo = HoloProvider(settings)
        self.jev = JevProvider(settings) if settings.jev_api_key else None
        self.holo.journal = self.log
        if self.jev:
            self.jev.journal = self.log
        self.generation = 0
        self.previous: Decision | None = None
        self.failures: deque = deque(maxlen=8)
        self.recent: deque = deque(maxlen=8)
        self.events: deque = deque(maxlen=80)
        self.state = self.initial_state()

    def initial_state(self) -> dict:
        return {
            "mode": self.settings.mode,
            "status": "idle",
            "connected": self.bridge.connected,
            "run_id": None,
            "elapsed_ms": 0,
            "objective": "Ready for an early-game run",
            "strategy": "Observe, plan, act, verify.",
            "expected_effect": "",
            "keys": [],
            "action": None,
            "holo_latency_ms": None,
            "jev_latency_ms": None,
            "metrics": {
                "actions": 0,
                "observations": 0,
                "recoveries": 0,
                "errors": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            },
            "milestones": [],
            "events": [],
            "frame_url": None,
            "latency_history": {"holo": [], "jev": []},
        }

    def snapshot(self) -> dict:
        self.state["connected"] = self.bridge.connected
        if self.recorder:
            self.state["elapsed_ms"] = self.recorder.elapsed_ms
        self.state["events"] = list(self.events)
        return json.loads(json.dumps(self.state))

    def broadcast(self) -> None:
        state = self.snapshot()
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(state)

    def log(self, kind: str, data: dict) -> dict | None:
        if self.recorder and not self.recorder.closed:
            event = self.recorder.log(kind, data)
            # Large provider request bodies stay on disk, never on the live websocket.
            if not kind.startswith("model_"):
                self.events.append(event)
            return event
        return None

    async def start(self) -> None:
        async with self.lock:
            if self.task and not self.task.done():
                return
            if not self.bridge.connected:
                raise ValueError("Connect the Fabric mod before starting a live run")
            if self.settings.mode == "live" and not self.settings.holo_api_key:
                raise ValueError("HCOMPANY_API_KEY is required for live mode")
            if self.recorder is None or self.recorder.closed:
                self.state = self.initial_state()
                self.events.clear()
                self.recent.clear()
                self.failures.clear()
                if self.settings.mode == "demo":
                    self.bridge = DemoBridge()
                self.recorder = RunRecorder(
                    self.settings.runs_dir,
                    self.settings.mode,
                    {
                        "holo_model": self.settings.holo_model,
                        "jev_model": self.settings.jev_model,
                        "jev_enabled": bool(self.jev),
                        "observation_mode": "visual_only",
                        "planner_interval": self.settings.planner_interval,
                        "minecraft": "1.21.1",
                        "goal": "wood_to_stone_tools",
                        "validate_holo_plans": self.settings.validate_holo_plans,
                        "model_timeout_s": self.settings.model_timeout_s,
                        "max_actions": self.settings.max_actions,
                        "max_run_seconds": self.settings.max_run_seconds,
                        "operator_declared_seed": self.settings.declared_seed,
                    },
                )
                self.state["run_id"] = self.recorder.id
            self.previous = None  # Resume always replans from a fresh observation.
            self.generation += 1
            self.state["status"] = "running"
            self.log(
                "event", {"name": "resumed", "detail": "Fresh observation and Holo4 plan required"}
            )
            self.task = asyncio.create_task(self.run(), name="holospeed-run")
            self.broadcast()

    async def pause(self, reason: str = "Operator paused") -> None:
        async with self.lock:
            self.generation += 1
            if self.state["status"] != "idle":
                self.state["status"] = "paused"
            await self.bridge.stop(reason)
            if self.task and not self.task.done() and self.task is not asyncio.current_task():
                self.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.task
            self.state.update(keys=[], action=None)
            self.previous = None
            self.log("event", {"name": "paused", "detail": reason})
            self.checkpoint()
            await self.hud()
            self.broadcast()

    async def finish(self) -> None:
        await self.pause("Run ended by operator")
        if self.recorder and not self.recorder.closed:
            self.recorder.finish(
                "finished", metrics=self.state["metrics"], milestones=self.state["milestones"]
            )
        self.state["status"] = "idle"
        self.broadcast()

    def checkpoint(self) -> None:
        if self.recorder and not self.recorder.closed:
            self.recorder.checkpoint(
                status=self.state["status"],
                metrics=self.state["metrics"],
                milestones=self.state["milestones"],
            )

    async def hud(self) -> None:
        if self.bridge.connected:
            with contextlib.suppress(Exception):
                snapshot = self.snapshot()
                metrics = snapshot["metrics"]
                detail = f"{metrics['actions']} actions / {metrics['observations']} frames / {metrics['recoveries']} recoveries"
                if self.events and self.events[-1]["type"] in {"error", "recovery", "event"}:
                    data = self.events[-1]["data"]
                    detail = str(
                        data.get("detail") or data.get("message") or data.get("reason") or detail
                    )
                await self.bridge.send(
                    {
                        "type": "status",
                        "event": detail,
                        **{
                            k: snapshot[k]
                            for k in (
                                "status",
                                "objective",
                                "strategy",
                                "holo_latency_ms",
                                "jev_latency_ms",
                                "elapsed_ms",
                                "milestones",
                            )
                        },
                    }
                )

    def recovery(self, reason: str, rejected: Decision | None = None) -> None:
        self.state["metrics"]["recoveries"] += 1
        prior = rejected or self.previous
        memory = {
            "reason": reason,
            "objective": self.state["objective"],
            "previous_action": prior.action.model_dump() if prior else None,
            "expected_effect": prior.expected_effect if prior else None,
        }
        self.failures.append(memory)
        self.log("recovery", memory)
        self.previous = None
        # Persistent evidence-linked mistakes are advice, never current-world state.
        if self.settings.mode == "live":
            path = self.settings.runs_dir / "memory.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(redact({"run_id": self.state["run_id"], **memory})) + "\n")
                stream.flush()

    def prior_mistakes(self) -> list:
        path = self.settings.runs_dir / "memory.jsonl"
        if not path.exists():
            return []
        try:
            return [json.loads(line) for line in path.read_text().splitlines()[-5:]]
        except (ValueError, OSError):
            return []

    def timing(self, provider: str, latency: float, usage: dict) -> None:
        self.log("inference_metrics", {"provider": provider, "latency_ms": latency, "usage": usage})
        self.state[f"{provider}_latency_ms"] = round(latency, 1)
        history = self.state["latency_history"][provider]
        history.append(round(latency, 1))
        del history[:-40]
        for target, names in [
            ("input_tokens", ("prompt_tokens", "input_tokens")),
            ("output_tokens", ("completion_tokens", "output_tokens")),
        ]:
            value = usage.get(names[0], usage.get(names[1], 0))
            if isinstance(value, int) and value >= 0:
                self.state["metrics"][target] += value

    async def run(self) -> None:
        generation = self.generation
        repeat = 0
        last_signature = None
        last_pixels = None
        repeated_ticks = 0
        errors = 0
        steps_since_plan = self.settings.planner_interval
        try:
            while self.state["status"] == "running" and generation == self.generation:
                if (
                    self.state["metrics"]["actions"] >= self.settings.max_actions
                    or self.recorder.elapsed_ms >= self.settings.max_run_seconds * 1000
                ):
                    self.state["status"] = "paused"
                    self.log(
                        "event",
                        {
                            "name": "run_budget_reached",
                            "detail": "Configured action or time budget reached",
                        },
                    )
                    self.checkpoint()
                    break
                observation = await self.bridge.observe()
                if generation != self.generation or self.state["status"] != "running":
                    break
                observed_at = time.monotonic()
                event = self.recorder.save_observation(observation)
                self.events.append(event)
                self.state["frame_url"] = event["data"]["frame_url"]
                self.state["metrics"]["observations"] += 1
                self.broadcast()
                context = {
                    "run_goal": "Gather logs, craft a crafting table and wooden pickaxe, then visibly obtain stone tools. The current objective is only a subgoal.",
                    "objective": self.state["objective"],
                    "strategy": self.state["strategy"],
                    "recent_actions": list(self.recent),
                    "failures": list(self.failures),
                    "previous_decision": self.previous.model_dump() if self.previous else None,
                    "prior_run_mistakes_not_current_world_facts": self.prior_mistakes(),
                }
                provider = (
                    "holo"
                    if self.previous is None
                    or not self.jev
                    or steps_since_plan >= self.settings.planner_interval
                    else "jev"
                )
                if self.previous:
                    prior = self.previous.action
                    if (
                        observation.screen_open
                        or prior.cursor
                        or prior.hotbar
                        or prior.scroll
                        or set(prior.keys) & {"inventory", "drop", "swap", "escape"}
                    ):
                        provider = "holo"
                if self.settings.mode == "demo":
                    provider = "holo" if self.bridge.step % 4 == 0 else "jev"
                planned_this_step = provider == "holo"
                proposed: Decision | None = None
                try:
                    completed = False
                    if self.settings.mode == "demo":
                        start = time.monotonic()
                        decision = await demo_decision(self.bridge.step)
                        latency = (time.monotonic() - start) * 1000
                        usage = {}
                        self.log(
                            "model_call",
                            {
                                "provider": provider,
                                "simulated": True,
                                "request": {"frame_id": observation.frame_id},
                                "response": decision.model_dump(),
                                "latency_ms": latency,
                            },
                        )
                    else:
                        adapter = self.holo if provider == "holo" else self.jev
                        result = await adapter.decide(observation, context)
                        decision, latency, usage = result.decision, result.latency_ms, result.usage
                        proposed = decision
                        completed = result.completed
                        self.log(
                            "model_call",
                            {
                                "provider": provider,
                                "model": result.model,
                                "frame_id": observation.frame_id,
                                "request": result.request,
                                "response": result.response,
                                "latency_ms": latency,
                                "usage": usage,
                            },
                        )
                    self.timing(provider, latency, usage)
                    if (
                        self.settings.mode == "live"
                        and provider == "holo"
                        and not completed
                        and self.jev
                        and self.settings.validate_holo_plans
                    ):
                        if generation != self.generation or self.state["status"] != "running":
                            break
                        source_frame = observation.frame_id
                        self.log(
                            "plan",
                            {
                                "provider": "holo",
                                "decision": decision.model_dump(),
                                "latency_ms": latency,
                                "usage": usage,
                                "frame_id": source_frame,
                            },
                        )
                        self.state.update(
                            objective=decision.objective,
                            strategy=decision.strategy,
                            expected_effect="Jev is validating this plan on a fresh screenshot",
                        )
                        await self.bridge.stop("Fresh visual validation before executing Holo plan")
                        observation = await self.bridge.observe()
                        observed_at = time.monotonic()
                        event = self.recorder.save_observation(observation)
                        self.events.append(event)
                        self.state["frame_url"] = event["data"]["frame_url"]
                        self.state["metrics"]["observations"] += 1
                        self.broadcast()
                        if generation != self.generation or self.state["status"] != "running":
                            break
                        context.update(
                            previous_decision=decision.model_dump(),
                            validate_unexecuted_plan=True,
                            plan_source_frame_id=source_frame,
                            validation_frame_id=observation.frame_id,
                        )
                        provider = "jev"
                        validated = await self.jev.decide(observation, context)
                        self.log(
                            "model_call",
                            {
                                "provider": provider,
                                "model": validated.model,
                                "request": validated.request,
                                "response": validated.response,
                                "latency_ms": validated.latency_ms,
                                "usage": validated.usage,
                                "frame_id": observation.frame_id,
                                "plan_frame_id": source_frame,
                            },
                        )
                        # A visual claim is attributed to the actual Holo observation, never to Jev.
                        if (
                            decision.milestone
                            and decision.evidence
                            and not any(
                                m["id"] == decision.milestone for m in self.state["milestones"]
                            )
                        ):
                            milestone = {
                                "id": decision.milestone,
                                "elapsed_ms": self.recorder.elapsed_ms,
                                "evidence": decision.evidence,
                                "frame_id": source_frame,
                                "verification": "model_visual_claim",
                            }
                            self.state["milestones"].append(milestone)
                            self.log("milestone", milestone)
                        decision, latency, usage = (
                            validated.decision,
                            validated.latency_ms,
                            validated.usage,
                        )
                        self.timing(provider, latency, usage)
                except ProviderError as error:
                    self.log(
                        "model_call",
                        {
                            "provider": provider,
                            "request": error.request,
                            "response": error.response,
                            "latency_ms": error.latency_ms,
                            "error": str(error),
                            "frame_id": observation.frame_id,
                        },
                    )
                    self.recovery(str(error), rejected=proposed)
                    if error.latency_ms > 0:
                        self.timing(provider, error.latency_ms, error.response.get("usage", {}))
                    if not isinstance(error, ReplanRequired):
                        errors += 1
                        self.state["metrics"]["errors"] += 1
                    if errors >= 3:
                        raise RuntimeError(
                            "Three model failures; inspect logs before resuming"
                        ) from error
                    if self.state["metrics"]["recoveries"] >= 8:
                        raise RuntimeError(
                            "Repeated model abstentions; inspect the run before resuming"
                        ) from error
                    await self.bridge.stop("Replanning after provider rejection")
                    self.broadcast()
                    await asyncio.sleep(0.5)
                    continue
                if generation != self.generation or self.state["status"] != "running":
                    self.log(
                        "event",
                        {
                            "name": "late_decision_discarded",
                            "detail": "Run paused during inference",
                        },
                    )
                    break
                if (
                    not completed
                    and (time.monotonic() - observed_at) * 1000 >= self.settings.frame_ttl_ms - 500
                ):
                    self.recovery("Model result arrived after the frame freshness budget")
                    errors += 1
                    if errors >= 3:
                        raise RuntimeError(
                            "Repeated stale observations; lower inference latency before resuming"
                        )
                    continue
                self.log(
                    "decision",
                    {
                        "provider": provider,
                        "decision": decision.model_dump(),
                        "latency_ms": latency,
                        "frame_id": observation.frame_id,
                        "usage": usage,
                    },
                )
                if completed:
                    self.state.update(
                        status="paused", objective=decision.objective, strategy=decision.strategy
                    )
                    self.log(
                        "event",
                        {
                            "name": "model_completion_claim",
                            "detail": decision.evidence,
                            "frame_id": observation.frame_id,
                        },
                    )
                    self.checkpoint()
                    break
                with Image.open(io.BytesIO(base64.b64decode(observation.image_base64))) as image:
                    pixels = image.convert("L").resize((32, 18)).tobytes()
                difference = (
                    sum(abs(a - b) for a, b in zip(pixels, last_pixels)) / len(pixels)
                    if last_pixels
                    else 255
                )
                signature = json.dumps(decision.action.model_dump(), sort_keys=True)
                repeating = signature == last_signature and difference <= 4
                repeat = repeat + 1 if repeating else 0
                repeated_ticks = repeated_ticks + decision.action.duration_ticks if repeating else 0
                last_signature = signature
                last_pixels = pixels
                stuck = repeated_ticks >= 240 if "attack" in decision.action.keys else repeat >= 5
                if self.settings.mode == "live" and stuck:
                    self.recovery(
                        "Same visual scene and action repeatedly failed to produce an observable change; choose a different approach"
                    )
                    repeat = 0
                    await self.bridge.stop("Stuck recovery")
                    if self.state["metrics"]["recoveries"] >= 8:
                        raise RuntimeError("Recovery budget exhausted; operator review required")
                    continue
                self.previous = decision
                steps_since_plan = 0 if planned_this_step else steps_since_plan + 1
                self.state.update(
                    objective=decision.objective,
                    strategy=decision.strategy,
                    expected_effect=decision.expected_effect,
                )
                if (
                    decision.milestone
                    and (provider == "holo" or self.settings.mode == "demo")
                    and decision.evidence
                    and not any(m["id"] == decision.milestone for m in self.state["milestones"])
                ):
                    milestone = {
                        "id": decision.milestone,
                        "elapsed_ms": self.recorder.elapsed_ms,
                        "evidence": decision.evidence,
                        "frame_id": observation.frame_id,
                        "verification": "simulated"
                        if self.settings.mode == "demo"
                        else "model_visual_claim",
                    }
                    self.state["milestones"].append(milestone)
                    self.log("milestone", milestone)
                if self.settings.mode == "live" and any(
                    m["id"] == "stone_tools" for m in self.state["milestones"]
                ):
                    self.state["status"] = "paused"
                    self.log(
                        "event",
                        {
                            "name": "goal_reached",
                            "detail": "Stone tools visually claimed; review recorded evidence",
                        },
                    )
                    self.checkpoint()
                    break
                action = decision.action.model_dump()
                action_event = self.log(
                    "action",
                    {"action": action, "frame_id": observation.frame_id, "phase": "requested"},
                )
                self.state.update(keys=decision.action.keys, action=action)
                await self.hud()
                self.broadcast()
                if generation != self.generation or self.state["status"] != "running":
                    break
                ack = await self.bridge.execute(
                    decision.action, observation.frame_id, self.settings.frame_ttl_ms
                )
                ack.update(action_seq=action_event["seq"], frame_id=observation.frame_id)
                self.log("ack", ack)
                self.state.update(keys=[], action=None)
                if ack["status"] == "completed":
                    self.state["metrics"]["actions"] += 1
                    self.recent.append(
                        {
                            "action": action,
                            "expected_effect": decision.expected_effect,
                            "frame_id": observation.frame_id,
                            "ack": ack,
                        }
                    )
                    errors = 0
                else:
                    self.recovery(f"Action {ack['status']}: {ack.get('reason')}")
                    errors += 1
                    if errors >= 3:
                        raise RuntimeError("Three actions rejected or cancelled")
                self.checkpoint()
                self.broadcast()
                if any(m["id"] == "stone_tools" for m in self.state["milestones"]):
                    self.state["status"] = "paused"
                    self.log(
                        "event",
                        {
                            "name": "goal_reached",
                            "detail": "Review the visual milestone evidence before claiming success",
                        },
                    )
                    self.checkpoint()
                    break
                await asyncio.sleep(0.15 if self.settings.mode == "demo" else 0)
        except asyncio.CancelledError:
            self.log(
                "event",
                {"name": "inference_or_action_cancelled", "detail": "Operator pause or shutdown"},
            )
            raise
        except Exception as error:  # noqa: BLE001 - every unexpected failure must release game inputs.
            self.state["status"] = "error"
            self.state["metrics"]["errors"] += 1
            self.log("error", {"message": str(error), "class": type(error).__name__})
            self.checkpoint()
        finally:
            await self.bridge.stop("Agent loop stopped")
            self.state.update(keys=[], action=None)
            await self.hud()
            self.broadcast()

    async def close(self) -> None:
        terminal_status = "error" if self.state["status"] == "error" else "interrupted"
        await self.pause("Controller shutting down")
        if self.recorder and not self.recorder.closed:
            self.recorder.finish(
                terminal_status, metrics=self.state["metrics"], milestones=self.state["milestones"]
            )
        await self.holo.aclose()
        if self.jev:
            await self.jev.aclose()
