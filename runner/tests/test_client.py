import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from mc_observatory.client import MinecraftClient, ModError


async def test_multiplex_events_errors_and_timeout():
    async def handler(ws):
        async for raw in ws:
            request = json.loads(raw)
            await ws.send(json.dumps({"event": "state", "data": {}}))
            command = request["command"]
            if command == "timeout":
                continue
            await ws.send(json.dumps({"id": request["id"], "ok": command != "bad", "result": {"command": command}, "error": "bad command"}))

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with MinecraftClient(f"ws://127.0.0.1:{port}", timeout=0.1) as client:
            results = await asyncio.gather(client.call("one"), client.call("two"))
            assert results == [{"command": "one"}, {"command": "two"}]
            assert (await client.events.get())["event"] == "state"
            with pytest.raises(ModError, match="bad command"):
                await client.call("bad")
            with pytest.raises(TimeoutError):
                await client.call("timeout")
            assert not client._pending


async def test_disconnect_rejects_pending_request():
    async def handler(ws):
        await ws.recv()
        await ws.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with MinecraftClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}") as client:
            with pytest.raises(ConnectionError):
                await client.call("state")


async def test_not_connected():
    with pytest.raises(ConnectionError):
        await MinecraftClient().state()


async def test_token_header_from_environment(monkeypatch):
    monkeypatch.setenv("OBS_TOKEN", "test-token")
    captured = []

    async def handler(ws):
        captured.append(ws.request.headers.get("Authorization"))
        await ws.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with MinecraftClient(f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"):
            pass
    assert captured == ["Bearer test-token"]
