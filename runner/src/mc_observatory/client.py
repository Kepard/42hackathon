import asyncio
import base64
import json
import os
import inspect
from contextlib import suppress

import websockets


class ModError(RuntimeError):
    pass


class MinecraftClient:
    """Multiplex request responses and bounded dashboard events on one socket."""

    def __init__(self, url="ws://127.0.0.1:8765", timeout=10, token=None):
        self.url = url
        self.timeout = timeout
        self.token = os.environ.get("OBS_TOKEN") if token is None else token
        self._socket = None
        self._reader = None
        self._pending = {}
        self._sequence = 0
        self.events = asyncio.Queue(maxsize=8)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def connect(self):
        if self._socket is not None:
            if self._reader is not None and not self._reader.done() and getattr(self._socket, "close_code", None) is None:
                return
            await self.close()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        header_arg = "additional_headers" if "additional_headers" in inspect.signature(websockets.connect).parameters else "extra_headers"
        self._socket = await websockets.connect(self.url, max_size=16 * 1024 * 1024, **{header_arg: headers})
        self._reader = asyncio.create_task(self._read())

    async def _read(self):
        failure = ConnectionError("Mod connection closed")
        try:
            async for raw in self._socket:
                message = json.loads(raw)
                if "event" in message:
                    if self.events.full():
                        self.events.get_nowait()
                    self.events.put_nowait(message)
                else:
                    future = self._pending.get(message.get("id"))
                    if future is not None and not future.done():
                        if message.get("ok"):
                            future.set_result(message["result"])
                        else:
                            future.set_exception(ModError(message.get("error", "Unknown mod error")))
        except Exception as error:
            failure = error
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(failure)

    async def call(self, command, *, timeout=None, **fields):
        if self._socket is None or self._reader.done():
            raise ConnectionError("Connect before sending requests")
        self._sequence += 1
        request_id = self._sequence
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._socket.send(json.dumps({"id": request_id, "command": command, **fields}))
            return await asyncio.wait_for(future, self.timeout if timeout is None else timeout)
        finally:
            self._pending.pop(request_id, None)

    async def state(self):
        return await self.call("state")

    async def screenshot(self):
        result = await self.call("screenshot")
        return base64.b64decode(result["png_base64"], validate=True)

    async def reset(self, seed, inventory=None):
        return await self.call("reset", seed=seed, inventory=inventory or [], timeout=150)

    async def pause(self):
        return await self.call("pause")

    async def resume(self):
        return await self.call("resume")

    async def key(self, code, pressed=True, duration_ms=None):
        fields = {"code": code, "pressed": pressed}
        if duration_ms is not None:
            fields["duration_ms"] = duration_ms
        return await self.call("key", **fields)

    async def mouse(self, **fields):
        return await self.call("mouse", **fields)

    async def click(self, x, y, button=0):
        await self.mouse(x=x, y=y, button=button, pressed=True)
        return await self.mouse(button=button, pressed=False)

    async def type_text(self, text):
        return await self.call("type", text=text)

    async def subscribe(self):
        return await self.call("subscribe")

    async def unsubscribe(self):
        return await self.call("unsubscribe")

    async def close(self):
        if self._socket is not None:
            await self._socket.close()
        if self._reader is not None:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
        self._socket = self._reader = None
