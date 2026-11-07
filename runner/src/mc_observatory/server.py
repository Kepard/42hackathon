"""Loopback observatory service. Browsers never receive mod credentials or API keys."""

import asyncio
import base64
import json
import math
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from statistics import mean
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .client import MinecraftClient
from .evaluator import LADDER
from .holo import HoloAgent, HoloConfig, RunController, utcnow
from .run import MinecraftRunner, task_definition


ROOT = Path(os.environ.get("OBS_RUNS_DIR", Path(__file__).resolve().parents[3] / "runs"))
ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"}


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def rows(path):
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except ValueError:
            continue
    return result


def leaderboard(runs):
    groups = defaultdict(list)
    for run in runs:
        s = run.get("summary")
        if s and "score" in s:
            cfg = s.get("config", {})
            groups[(cfg.get("model", "unknown"), cfg.get("tier", "unknown"), s.get("task", "unknown"))].append(run)
    result = []
    for (model, tier, task), group in groups.items():
        summaries = [r["summary"] for r in group]
        latencies = sorted(s["api_latency_ms"] for r in group for s in r.get("steps", []) if s.get("api_latency_ms", 0) > 0)
        milestones = defaultdict(list)
        tags = Counter()
        for s in summaries:
            for rung, seconds in s.get("milestones", {}).items():
                milestones[rung].append(seconds)
            tags.update(set(s.get("failure_tags", [])))
        costs = [s["total_cost_usd"] for s in summaries if s.get("total_cost_usd") is not None]
        result.append({"model": model, "tier": tier, "task": task, "runs": len(group),
                       "success_rate": mean(bool(s.get("success")) for s in summaries),
                       "mean_score": mean(s["score"] for s in summaries),
                       "time_to_milestone": {k: mean(v) for k, v in milestones.items()},
                       "mean_latency_ms": mean(latencies) if latencies else 0,
                       "p95_latency_ms": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else 0,
                       "cost_per_run": mean(costs) if len(costs) == len(summaries) else None,
                       "failure_counts": dict(tags)})
    return sorted(result, key=lambda r: (-r["success_rate"], -r["mean_score"], r["model"]))


class StartRun(BaseModel):
    task: str = "get_log"
    model: str = Field(default="holo4-27b", min_length=1, max_length=120)
    tier: Literal["raw", "skills"] = "skills"
    timing: Literal["freeze", "realtime"] = "freeze"
    max_steps: int = Field(default=30, ge=1, le=10000)
    time_budget: float = Field(default=180, gt=0, le=10800)
    max_cost_usd: float | None = Field(default=None, gt=0)


class Control(BaseModel):
    action: Literal["pause", "resume", "stop", "set_goal"]
    source: Literal["ui", "voice", "devin"] = "ui"
    text: str = Field(default="", max_length=2000)


class Confirm(BaseModel):
    token: str
    source: Literal["ui", "voice", "devin"] = "ui"


class ObservatoryService:
    def __init__(self, root=ROOT, client=None):
        self.root = Path(root)
        self.client = client or MinecraftClient()
        self.active = None
        self.worker = None
        self.controller = None
        self.listeners = set()
        self.latest_frame = None
        self.agent_frame = None
        self.overlay = {"objective": "Minecraft Observatory", "badges": "PAUSED", "completed": 0}
        self.connected = False
        self.pending = None
        self.phase = "IDLE"

    def directories(self):
        paths = {p.parent for name in ("summary.json", "run.json") for p in self.root.rglob(name)}
        return sorted(paths, key=lambda p: p.name, reverse=True)

    def interrupt_stale_runs(self):
        for path in self.directories():
            meta = read_json(path / "run.json", {})
            if meta.get("status") == "running":
                meta.update(status="interrupted", finished_at=utcnow())
                (path / "run.json").write_text(json.dumps(meta, indent=2))

    def directory(self, run_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise HTTPException(404, "Run not found")
        matches = [p for p in self.directories() if p.name == run_id]
        if len(matches) != 1:
            raise HTTPException(404, "Run not found")
        return matches[0]

    def detail(self, path, include_steps=True):
        summary = read_json(path / "summary.json")
        meta = read_json(path / "run.json", {})
        return {"id": path.name, "status": "running" if self.active == path.name and self.worker and not self.worker.done() else meta.get("status", "finished"),
                "summary": summary, "metadata": meta,
                "steps": rows(path / "steps.jsonl") if include_steps else [],
                "interventions": rows(path / "interventions.jsonl")}

    def publish(self, event, data):
        message = {"type": event, "data": data, "timestamp": utcnow()}
        for queue in self.listeners.copy():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)

    async def set_overlay(self, **fields):
        self.overlay.update(fields)
        self.publish("overlay", self.overlay.copy())
        if self.connected:
            payload = {key: value[:1024] if isinstance(value, str) else value for key, value in self.overlay.items()}
            while len(json.dumps(payload, ensure_ascii=True)) > 7000:
                key = max((key for key in payload if isinstance(payload[key], str)), key=lambda key: len(payload[key]))
                payload[key] = payload[key][:len(payload[key]) // 2]
            try:
                await self.client.call("overlay", payload=payload)
            except Exception as error:
                self.publish("overlay_error", {"error": type(error).__name__})

    async def stream(self):
        while True:
            try:
                await self.client.connect()
                await self.client.subscribe()
                self.connected = True
                await self.set_overlay()
                self.publish("connection", {"connected": True})
                while True:
                    try:
                        event = await asyncio.wait_for(self.client.events.get(), 3)
                    except TimeoutError:
                        if self.client._reader.done():
                            raise ConnectionError("Mod disconnected")
                        continue
                    if event["event"] == "frame":
                        self.latest_frame = event["data"]
                        self.publish("frame", event["data"])
            except asyncio.CancelledError:
                raise
            except Exception:
                self.connected = False
                self.publish("connection", {"connected": False})
                await asyncio.sleep(2)

    def intervention(self, action, source, **fields):
        event = {"timestamp": utcnow(), "source": source, "action": action, **fields}
        path = self.directory(self.active)
        with (path / "interventions.jsonl").open("a") as f:
            f.write(json.dumps(event) + "\n")
        with (path / "events.jsonl").open("a") as f:
            f.write(json.dumps({"type": "intervention", "data": event}) + "\n")
        self.controller.interventions.append(event)
        self.publish("intervention", event)
        return event

    async def step(self, record):
        if self.phase != "STOPPING":
            self.phase = "PAUSED" if self.controller.paused else "RUNNING"
        path = self.directory(self.active)
        with (path / "events.jsonl").open("a") as f:
            f.write(json.dumps({"type": "step", "data": record}) + "\n")
        self.publish("step", {"run_id": self.active, **record})
        completed = record.get("evaluator", {}).get("completed", [])
        rung = LADDER[len(completed)] if len(completed) < len(LADDER) else "Complete"
        await self.set_overlay(objective=self.controller.goal, completed=len(completed), current_rung=rung,
                               intent=" / ".join(record["intent"]), action=" → ".join(c["name"] for c in record["tool_calls"]),
                               api_latency_ms=round(record["api_latency_ms"]), badges="PAUSED FROZEN" if self.controller.paused else "FROZEN" if self.timing == "freeze" else "RUNNING")

    async def execute(self, run_id, spec, config):
        path = self.directory(run_id)
        meta = read_json(path / "run.json")
        service = self

        class ObservedClient:
            def __getattr__(self, name): return getattr(service.client, name)
            async def screenshot(self):
                service.phase = "THINKING"
                await service.set_overlay(badges="THINKING FROZEN" if config.timing == "freeze" else "THINKING")
                png = await service.client.screenshot()
                service.agent_frame = {"png_base64": base64.b64encode(png).decode(), "step": len(agent.records), "run_id": run_id}
                service.publish("agent_frame", service.agent_frame)
                return png
            async def resume(self):
                service.phase = "ACTING"
                await service.set_overlay(badges="ACTING")
                return await service.client.resume()

        try:
            agent = HoloAgent(config, controller=self.controller)
            summary = await MinecraftRunner(agent, ObservedClient(), path).run(spec.task)
            meta["status"] = "stopped" if not summary["success"] and self.phase == "STOPPING" else "finished"
            self.publish("summary", {"run_id": run_id, **summary})
        except asyncio.CancelledError:
            meta["status"] = "interrupted"
            raise
        except Exception as error:
            meta.update(status="error", error=type(error).__name__)
            self.publish("error", {"message": f"Run failed: {type(error).__name__}", "run_id": run_id})
        finally:
            meta["finished_at"] = utcnow()
            (path / "run.json").write_text(json.dumps(meta, indent=2))
            self.phase = "IDLE"
            self.pending = None
            await self.set_overlay(badges="PAUSED FROZEN" if config.timing == "freeze" else "PAUSED")
            self.publish("run_status", {"id": run_id, "status": meta["status"]})


def create_app(service=None):
    svc = service or ObservatoryService()

    @asynccontextmanager
    async def lifespan(app):
        svc.root.mkdir(parents=True, exist_ok=True)
        svc.interrupt_stale_runs()
        stream = asyncio.create_task(svc.stream())
        try:
            yield
        finally:
            if svc.controller:
                await svc.controller.stop()
            if svc.worker and not svc.worker.done():
                svc.worker.cancel()
                with suppress(asyncio.CancelledError): await svc.worker
            svc.interrupt_stale_runs()
            stream.cancel()
            with suppress(asyncio.CancelledError): await stream
            await svc.client.close()

    app = FastAPI(title="Minecraft Observatory", lifespan=lifespan)
    app.state.service = svc
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        if request.headers.get("origin") and request.headers["origin"] not in ORIGINS:
            return JSONResponse({"detail": "Untrusted origin"}, status_code=403)
        return await call_next(request)

    @app.get("/api/status")
    async def status():
        return {"connected": svc.connected, "active_run": svc.active, "phase": svc.phase, "overlay": svc.overlay,
                "tasks": ["ladder", "get_log", "craft_planks", "craft_table"], "ladder": LADDER}

    @app.get("/api/runs")
    async def list_runs(): return [svc.detail(p, False) for p in svc.directories()]

    @app.get("/api/runs/{run_id}")
    async def detail(run_id: str): return svc.detail(svc.directory(run_id))

    @app.get("/api/runs/{run_id}/screenshots/{step}")
    async def screenshot(run_id: str, step: int):
        path = svc.directory(run_id) / "screenshots" / f"{step:05d}.png"
        if step < 0 or not path.is_file(): raise HTTPException(404, "Screenshot not found")
        return FileResponse(path, media_type="image/png")

    @app.post("/api/runs", status_code=202)
    async def start(spec: StartRun):
        if svc.worker and not svc.worker.done(): raise HTTPException(409, "A run is already active")
        if not svc.connected: raise HTTPException(503, "Minecraft mod is offline")
        if not os.environ.get("HCOMPANY_API_KEY"): raise HTTPException(503, "Server API key is not configured")
        try:
            task_definition(spec.task)
            config = HoloConfig(**spec.model_dump(exclude={"task"}))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        path = svc.root / run_id
        path.mkdir(parents=True)
        (path / "run.json").write_text(json.dumps({"created_at": utcnow(), "status": "running", **spec.model_dump()}))
        svc.active, svc.timing, svc.phase = run_id, spec.timing, "STARTING"
        svc.pending, svc.agent_frame = None, None
        svc.controller = RunController(callback=svc.step)
        svc.controller.interventions = []
        svc.overlay = {"objective": task_definition(spec.task)["description"], "completed": 0, "current_rung": "log", "badges": "STARTING"}
        svc.publish("run_status", {"id": run_id, "status": "running"})
        svc.worker = asyncio.create_task(svc.execute(run_id, spec, config))
        return {"id": run_id}

    def active():
        if not svc.worker or svc.worker.done(): raise HTTPException(409, "No active run")

    @app.post("/api/control")
    async def control(command: Control):
        active()
        if command.action == "set_goal":
            if not svc.controller.goal:
                raise HTTPException(409, "Wait for the first model observation before changing the goal")
            if not command.text.strip(): raise HTTPException(422, "Goal cannot be empty")
            token = uuid.uuid4().hex
            svc.pending = (token, command.text, time.monotonic() + 60)
            svc.intervention("propose_goal", command.source, text=command.text)
            return {"confirmation_token": token, "text": command.text, "expires_in": 60}
        await getattr(svc.controller, command.action)()
        svc.phase = {"pause": "PAUSED", "resume": "RUNNING", "stop": "STOPPING"}[command.action]
        svc.intervention(command.action, command.source)
        await svc.set_overlay(badges=svc.phase + (" FROZEN" if svc.timing == "freeze" else ""))
        return {"status": svc.phase}

    @app.post("/api/control/confirm-goal")
    async def confirm(command: Confirm):
        active()
        if not svc.pending or command.token != svc.pending[0] or time.monotonic() > svc.pending[2]:
            raise HTTPException(409, "Goal confirmation expired or invalid")
        text = svc.pending[1]
        svc.pending = None
        await svc.controller.set_goal(text)
        svc.intervention("set_goal", command.source, text=text)
        await svc.set_overlay(objective=text)
        return {"goal": text}

    @app.get("/api/leaderboard")
    async def board(): return leaderboard([svc.detail(p) for p in svc.directories()])

    @app.websocket("/ws/live")
    async def live(ws: WebSocket):
        if ws.headers.get("origin") not in ORIGINS:
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=24)
        svc.listeners.add(queue)
        try:
            await ws.send_json({"type": "snapshot", "data": {"connected": svc.connected, "active_run": svc.active, "overlay": svc.overlay, "phase": svc.phase}})
            if svc.latest_frame: await ws.send_json({"type": "frame", "data": svc.latest_frame})
            if svc.agent_frame: await ws.send_json({"type": "agent_frame", "data": svc.agent_frame})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 15)
                except TimeoutError:
                    event = {"type": "heartbeat", "data": {}}
                await asyncio.wait_for(ws.send_json(event), 5)
        except (WebSocketDisconnect, RuntimeError, TimeoutError):
            pass
        finally:
            svc.listeners.discard(queue)

    return app


app = create_app()
