"""Operator start semantics using in-process ASGI and mocked game operations."""

import asyncio
import contextlib

import httpx
import pytest
from holospeed.app import create_app
from holospeed.bridge import BridgeError
from holospeed.config import Settings


def application(tmp_path, mode="live"):
    return create_app(Settings(mode=mode, runs_dir=tmp_path, holo_api_key="unused-test-key"))


async def request_start(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8768"
    ) as client:
        return await client.post("/api/start")


async def close_engine(app, task=None):
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
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

    async def start():
        calls.append("engine_start")

    monkeypatch.setattr(app.state.bridge, "arm", arm)
    monkeypatch.setattr(engine, "start", start)
    try:
        response = await request_start(app)
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

    async def forbidden_start():
        pytest.fail("A rejected arm must not start the agent")

    monkeypatch.setattr(app.state.bridge, "arm", arm)
    monkeypatch.setattr(app.state.engine, "start", forbidden_start)
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

    async def start():
        calls.append("engine_start")

    monkeypatch.setattr(app.state.bridge, "arm", forbidden_arm)
    monkeypatch.setattr(engine, "start", start)
    try:
        response = await request_start(app)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert calls == ["engine_start"]
        assert not running.done()
    finally:
        await close_engine(app, running)
    assert running.cancelled()


async def test_demo_start_never_arms_a_real_minecraft_bridge(tmp_path, monkeypatch):
    app = application(tmp_path, "demo")
    calls = []

    async def forbidden_arm():
        pytest.fail("Demo start must never arm Minecraft")

    async def start():
        calls.append("engine_start")

    monkeypatch.setattr(app.state.bridge, "arm", forbidden_arm)
    monkeypatch.setattr(app.state.engine, "start", start)
    try:
        response = await request_start(app)
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert calls == ["engine_start"]
    finally:
        await close_engine(app)
