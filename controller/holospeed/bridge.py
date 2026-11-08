from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import WebSocket

from .models import Action, Observation


class BridgeError(RuntimeError):
    pass


class GameBridge:
    def __init__(self) -> None:
        self.socket: WebSocket | None = None
        self.pending: dict[str, asyncio.Future] = {}
        self.lock = asyncio.Lock()
        self.frames: dict[str, float] = {}
        self.ready = False
        self.journal = None

    @property
    def connected(self) -> bool:
        return self.socket is not None and self.ready

    async def attach(self, socket: WebSocket) -> bool:
        async with self.lock:
            if self.socket is not None:
                await socket.close(code=1008, reason="A Minecraft client is already connected")
                return False
            await socket.accept()
            self.socket = socket
            return True

    async def detach(self, socket: WebSocket) -> None:
        if self.socket is not socket:
            return
        self.socket = None
        self.ready = False
        self.frames.clear()
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(BridgeError("Minecraft disconnected"))
        self.pending.clear()

    async def receive(self, message: dict[str, Any]) -> None:
        if message.get("type") == "ack" and self.journal:
            self.journal("bridge_ack", message)
        if message.get("type") == "hello":
            if message.get("protocol") != 1 or message.get("minecraft") != "1.21.1":
                raise BridgeError("This controller requires protocol 1 / Minecraft 1.21.1")
            self.ready = True
            return
        future = self.pending.get(message.get("id", ""))
        if future and not future.done():
            future.set_result(message)

    async def send(self, message: dict[str, Any]) -> None:
        if self.socket is None:
            raise BridgeError("Minecraft is not connected")
        async with self.lock:
            if message.get("type") != "status" and self.journal:
                self.journal("bridge_command", message)
            await self.socket.send_json(message)

    async def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not self.connected:
            raise BridgeError("Minecraft has not completed the bridge handshake")
        if self.pending:
            raise BridgeError("Only one game request may be in flight")
        message["id"] = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[message["id"]] = future
        try:
            await self.send(message)
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as error:
            await self.stop("bridge response timed out")
            raise BridgeError("Minecraft response timed out; controls released") from error
        finally:
            self.pending.pop(message["id"], None)

    async def observe(self) -> Observation:
        message = await self.request({"type": "observe"}, timeout=8)
        if message.get("type") != "observation":
            raise BridgeError("Unexpected observation response")
        observation = Observation.model_validate(message)
        self.frames = {observation.frame_id: time.monotonic()}
        return observation

    async def execute(self, action: Action, frame_id: str, ttl_ms: int) -> dict[str, Any]:
        captured = self.frames.get(frame_id)
        if captured is None or (time.monotonic() - captured) * 1000 >= ttl_ms:
            raise BridgeError("Action rejected: observation expired")
        message = await self.request(
            {
                "type": "action",
                "frame_id": frame_id,
                "ttl_ms": ttl_ms,
                "action": action.model_dump(),
            },
            timeout=action.duration_ticks / 20 + 4,
        )
        if message.get("type") != "ack" or message.get("status") not in {
            "completed",
            "rejected",
            "cancelled",
        }:
            raise BridgeError("Invalid action acknowledgment")
        return message

    async def stop(self, reason: str) -> None:
        if self.socket:
            try:
                await self.send({"type": "stop", "reason": reason})
            except (RuntimeError, OSError):
                logging.getLogger(__name__).warning(
                    "Could not send stop; mod watchdog must release controls"
                )
