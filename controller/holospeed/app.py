from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bridge import BridgeError, GameBridge
from .config import Settings
from .demo_launch import DemoLauncher
from .engine import AgentEngine
from .engineering import Engineering
from .recording import read_events
from .voice import VoiceControl

STATIC = Path(__file__).parent / "static"


def local_origin(origin: str | None) -> bool:
    if origin is None:
        return True  # CLI and authenticated Fabric requests do not send Origin.
    parsed = urlparse(origin)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.mode not in {"demo", "live"}:
        raise ValueError("HOLOSPEED_MODE must be demo or live")
    if settings.run_goal not in {"early_game", "nether"}:
        raise ValueError("RUN_GOAL must be early_game or nether")
    bridge = GameBridge()
    engine = AgentEngine(settings, bridge)
    launcher = DemoLauncher(engine, settings)
    engineering = Engineering(settings)
    voice = VoiceControl(engine, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.runs_dir.mkdir(parents=True, exist_ok=True)

        async def heartbeat():
            while True:
                await engine.hud()
                engine.broadcast()
                await asyncio.sleep(0.5)

        ticker = asyncio.create_task(heartbeat())
        investigations = asyncio.create_task(engineering.run())
        try:
            yield
        finally:
            ticker.cancel()
            investigations.cancel()
            await engine.close()
            await launcher.close()
            await voice.close()
            await engineering.close()
            done, _ = await asyncio.wait((ticker, investigations), timeout=2)
            for task in done:
                if not task.cancelled():
                    task.exception()

    app = FastAPI(title="HoloSpeed", lifespan=lifespan)
    app.state.engine = engine
    app.state.launcher = launcher
    app.state.bridge = bridge
    app.state.engineering = engineering
    app.state.voice = voice

    @app.middleware("http")
    async def protect_local_control(request: Request, call_next):
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "HoloSpeed is a local-only controller"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            not local_origin(request.headers.get("origin"))
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            return JSONResponse(
                {"detail": "Cross-origin control requests are rejected"}, status_code=403
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/api/state")
    async def state():
        return engine.snapshot()

    @app.post("/api/start")
    async def start():
        try:
            await engine.start(arm=settings.mode == "live")
        except (ValueError, BridgeError) as error:
            raise HTTPException(409, str(error)) from error
        return {"ok": True}

    @app.post("/api/new-run", status_code=202)
    async def new_run(request: Request):
        raw = await request.body()
        if len(raw) > 512:
            raise HTTPException(400, "New run request is too large")
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"command_id"}:
                raise ValueError("New run requires only a command_id")
            return {"ok": True, "launch": await launcher.begin(value["command_id"])}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except OSError as error:
            raise HTTPException(
                409, "Could not persist the launch request; no new run was started"
            ) from error

    @app.post("/api/pause")
    async def pause():
        await engine.pause(pause_world=True)
        return {"ok": True}

    @app.post("/api/finish")
    async def finish():
        await engine.finish(pause_world=True)
        return {"ok": True}

    @app.post("/api/voice")
    async def voice_command(request: Request):
        raw = await request.body()
        if len(raw) > 8192:
            raise HTTPException(400, "Voice command exceeds its bounded size")
        try:
            value = json.loads(raw)
            return {"ok": True, "data": await voice.apply(value)}
        except (ValueError, BridgeError) as error:
            raise HTTPException(409, str(error)) from error

    def run_path(run_id: str) -> Path:
        if not run_id or any(c not in "0123456789TZ-abcdef" for c in run_id):
            raise HTTPException(404, "Unknown run")
        path = settings.runs_dir / run_id
        if not (path / "summary.json").is_file():
            raise HTTPException(404, "Unknown run")
        return path

    @app.post("/api/devin/{run_id}/brief")
    async def investigation_brief(run_id: str):
        run_path(run_id)
        try:
            return {"ok": True, "data": await engineering.prepare(run_id)}
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/devin/{run_id}/investigate")
    async def investigate(run_id: str, request: Request):
        run_path(run_id)
        raw = await request.body()
        if len(raw) > 512:
            raise HTTPException(400, "Invalid investigation request")
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"digest_hash"}:
                raise ValueError("Supply only the reviewed digest_hash")
            return {"ok": True, "data": await engineering.investigate(run_id, value["digest_hash"])}
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/devin/{run_id}")
    async def investigation_status(run_id: str):
        run_path(run_id)
        try:
            return {"ok": True, "engineering": engineering.latest(run_id)}
        except (OSError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/runs")
    async def runs():
        summaries = []
        for path in sorted(settings.runs_dir.glob("*/summary.json"), reverse=True):
            try:
                summary = json.loads(path.read_text())
                if summary["status"] == "running" and (
                    not engine.recorder or summary["id"] != engine.recorder.id
                ):
                    summary["status"] = "interrupted"
                summaries.append(summary)
            except (ValueError, OSError, KeyError):
                continue
        return {"runs": summaries}

    @app.get("/api/runs/{run_id}")
    async def run(run_id: str):
        path = run_path(run_id)
        # Exact model payloads are in the downloadable journal; omit image copies from UI.
        events = await asyncio.to_thread(read_events, path / "events.jsonl", exclude_model=True)
        return {"summary": json.loads((path / "summary.json").read_text()), "events": events}

    @app.get("/api/runs/{run_id}/events")
    async def journal(run_id: str):
        return FileResponse(
            run_path(run_id) / "events.jsonl",
            media_type="application/x-ndjson",
            filename=f"{run_id}.jsonl",
        )

    @app.get("/api/runs/{run_id}/frames/{filename}")
    async def frame(run_id: str, filename: str):
        if Path(filename).name != filename or not filename.endswith((".png", ".jpg")):
            raise HTTPException(404, "Unknown frame")
        path = run_path(run_id) / "frames" / filename
        if not path.is_file():
            raise HTTPException(404, "Unknown frame")
        return FileResponse(path)

    @app.websocket("/events")
    async def events(socket: WebSocket):
        if not local_origin(socket.headers.get("origin")):
            await socket.close(code=1008)
            return
        await socket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        engine.subscribers.add(queue)
        try:
            await socket.send_json(engine.snapshot())
            while True:
                try:
                    snapshot = await asyncio.wait_for(queue.get(), timeout=1)
                except TimeoutError:
                    snapshot = engine.snapshot()
                await socket.send_json(snapshot)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            engine.subscribers.discard(queue)

    @app.websocket("/bridge")
    async def minecraft(socket: WebSocket):
        authorization = socket.headers.get("authorization", "")
        if not secrets.compare_digest(authorization, "Bearer " + settings.bridge_token):
            await socket.close(code=1008, reason="Invalid bridge token")
            return
        if not await bridge.attach(socket):
            return
        try:
            while True:
                message = (
                    await socket.receive_json()
                    if bridge.ready
                    else await asyncio.wait_for(socket.receive_json(), timeout=10)
                )
                if not isinstance(message, dict):
                    raise BridgeError("Bridge message must be an object")
                if message.get("type") == "event":
                    engine.log("event", message)
                    if message.get("name") == "start_requested" and settings.mode == "live":
                        try:
                            await engine.start()
                        except ValueError as error:
                            engine.log("error", {"message": str(error)})
                    if message.get("name") in {"emergency_stop", "world_changed", "disconnected"}:
                        if message.get("name") == "world_changed" and (
                            launcher.active
                            or (
                                engine.state["status"] == "running"
                                and (engine.state.get("action") or {}).get("skill")
                                in {"portal", "cast_portal"}
                            )
                        ):
                            # The skill releases controls during normal portal transfer;
                            # only a fresh Nether snapshot can confirm its postcondition.
                            continue
                        # Pause cancels an outstanding action; process asynchronously so ACKs can drain.
                        await engine.pause(message.get("detail", "Game paused"))
                else:
                    await bridge.receive(message)
                engine.broadcast()
        except (WebSocketDisconnect, RuntimeError, ValueError, TimeoutError, BridgeError) as error:
            engine.log("event", {"name": "bridge_disconnected", "detail": type(error).__name__})
        finally:
            await bridge.detach(socket)
            if settings.mode == "live":
                await engine.pause("Minecraft disconnected; controls released")
            engine.broadcast()
            with contextlib.suppress(RuntimeError, OSError):
                await socket.close()

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
