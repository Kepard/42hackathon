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
from .engine import AgentEngine
from .recording import read_events

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
    bridge = GameBridge()
    engine = AgentEngine(settings, bridge)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.runs_dir.mkdir(parents=True, exist_ok=True)

        async def heartbeat():
            while True:
                await engine.hud()
                engine.broadcast()
                await asyncio.sleep(0.5)

        ticker = asyncio.create_task(heartbeat())
        try:
            yield
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
            await engine.close()

    app = FastAPI(title="HoloSpeed", lifespan=lifespan)
    app.state.engine = engine
    app.state.bridge = bridge

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
            await engine.start()
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"ok": True}

    @app.post("/api/pause")
    async def pause():
        await engine.pause()
        return {"ok": True}

    @app.post("/api/finish")
    async def finish():
        await engine.finish()
        return {"ok": True}

    def run_path(run_id: str) -> Path:
        if not run_id or any(c not in "0123456789TZ-abcdef" for c in run_id):
            raise HTTPException(404, "Unknown run")
        path = settings.runs_dir / run_id
        if not (path / "summary.json").is_file():
            raise HTTPException(404, "Unknown run")
        return path

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
