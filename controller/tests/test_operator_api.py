"""Operator start semantics using in-process ASGI and mocked game operations."""

import asyncio
import contextlib

import httpx
import pytest
from holospeed.app import create_app
from holospeed.bridge import BridgeError
from holospeed.config import Settings


def application(tmp_path, mode="live"):
    app = create_app(Settings(mode=mode, runs_dir=tmp_path, holo_api_key="unused-test-key"))

    class Socket:
        async def send_json(self, message):
            pass

    app.state.bridge.socket = Socket()
    app.state.bridge.ready = True
    return app


async def request_start(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8768"
    ) as client:
        return await client.post("/api/start")


async def close_engine(app, task=None):
    for current in {task, app.state.engine.task} - {None}:
        current.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await current
    app.state.engine.task = None
    await app.state.engine.close()


@pytest.mark.parametrize("finished_previous_task", [False, True])
async def test_live_start_waits_for_arm_before_starting_engine(
    tmp_path, monkeypatch, finished_previous_task
):
    app = application(tmp_path)
    engine = app.state.engine
    calls = []
    prior = None
    if finished_previous_task:
        prior = asyncio.create_task(asyncio.sleep(0))
        await prior
        engine.task = prior

    async def arm():
        calls.append("arm_started")
        await asyncio.sleep(0)
        calls.append("arm_ready")

    async def run():
        calls.append("engine_start")
        await asyncio.Future()

    monkeypatch.setattr(app.state.bridge, "arm", arm)
    monkeypatch.setattr(engine, "run", run)
    try:
        response = await request_start(app)
        await asyncio.sleep(0)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert calls == ["arm_started", "arm_ready", "engine_start"]
    finally:
        await close_engine(app, prior)


async def test_rejected_live_arm_returns_conflict_and_never_starts_engine(tmp_path, monkeypatch):
    app = application(tmp_path)
    calls = []

    async def arm():
        calls.append("arm")
        raise BridgeError("Enter a focused survival world before starting")

    async def forbidden_run():
        pytest.fail("A rejected arm must not start the agent")

    monkeypatch.setattr(app.state.bridge, "arm", arm)
    monkeypatch.setattr(app.state.engine, "run", forbidden_run)
    try:
        response = await request_start(app)
        assert response.status_code == 409
        assert response.json() == {"detail": "Enter a focused survival world before starting"}
        assert calls == ["arm"]
    finally:
        await close_engine(app)


async def test_live_start_with_running_task_does_not_rearm_controls(tmp_path, monkeypatch):
    app = application(tmp_path)
    engine = app.state.engine
    waiting = asyncio.Event()
    running = asyncio.create_task(waiting.wait())
    engine.task = running
    engine.state["status"] = "running"
    calls = []

    async def forbidden_arm():
        pytest.fail("An already-running agent must not be rearmed by a duplicate start")

    async def forbidden_run():
        pytest.fail("A duplicate start must keep the existing agent task")

    monkeypatch.setattr(app.state.bridge, "arm", forbidden_arm)
    monkeypatch.setattr(engine, "run", forbidden_run)
    try:
        response = await request_start(app)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert calls == []
        assert not running.done()
    finally:
        await close_engine(app, running)
    assert running.cancelled()


async def test_demo_start_never_arms_a_real_minecraft_bridge(tmp_path, monkeypatch):
    app = application(tmp_path, "demo")
    calls = []

    async def forbidden_arm():
        pytest.fail("Demo start must never arm Minecraft")

    async def run():
        calls.append("engine_start")
        await asyncio.Future()

    monkeypatch.setattr(app.state.bridge, "arm", forbidden_arm)
    monkeypatch.setattr(app.state.engine, "run", run)
    try:
        response = await request_start(app)
        await asyncio.sleep(0)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert calls == ["engine_start"]
    finally:
        await close_engine(app)


@pytest.mark.parametrize("route,expected_status", [("pause", "paused"), ("finish", "idle")])
async def test_operator_pause_and_finish_freeze_world_after_stopping_agent(
    tmp_path, monkeypatch, route, expected_status
):
    app = application(tmp_path)
    engine = app.state.engine
    calls = []
    started = asyncio.Event()

    async def run():
        started.set()
        try:
            await asyncio.Future()
        finally:
            calls.append("agent_cancelled")

    async def send_json(message):
        if message["type"] == "stop":
            calls.append("stop")

    async def game_request(message, timeout):
        assert message == {"type": "pause_game"}
        assert engine.task.done(), "Pause the world after the agent releases control"
        calls.append("pause_game")
        return {"type": "pause_game_ack", "status": "paused"}

    monkeypatch.setattr(engine, "run", run)
    monkeypatch.setattr(app.state.bridge.socket, "send_json", send_json)
    monkeypatch.setattr(app.state.bridge, "request", game_request)
    try:
        await engine.start()
        await started.wait()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8768"
        ) as client:
            response = await client.post("/api/" + route)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert calls == ["stop", "agent_cancelled", "pause_game"]
        assert engine.state["status"] == expected_status
        assert engine.state["keys"] == [] and engine.state["action"] is None
        assert engine.recorder.closed is (route == "finish")
    finally:
        await close_engine(app)


async def test_internal_finish_for_world_launcher_does_not_add_game_pause(tmp_path, monkeypatch):
    app = application(tmp_path)

    async def forbidden_game_pause():
        pytest.fail("Internal finish must preserve the launcher's normal unload sequence")

    monkeypatch.setattr(app.state.bridge, "pause_game", forbidden_game_pause)
    try:
        await app.state.engine.finish()
        assert app.state.engine.state["status"] == "idle"
    finally:
        await close_engine(app)
