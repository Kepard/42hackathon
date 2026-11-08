from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

from fastapi import WebSocket

from .game_state import GameState
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
        if message.get("type") in {"ack", "skill_ack", "world_ack"} and self.journal:
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

    async def game_state(self) -> GameState:
        message = await self.request({"type": "state"}, timeout=5)
        if message.get("type") != "game_state":
            raise BridgeError("The Fabric mod does not support structured game state")
        state = GameState.model_validate(message)
        if not state.is_fresh(time.time() * 1000):
            raise BridgeError("Structured game state is stale")
        return state

    async def arm(self) -> None:
        """Explicit operator start can rearm F8 and resume the pause menu."""
        message = await self.request({"type": "arm"}, timeout=5)
        if message.get("type") != "arm_ack" or message.get("status") != "ready":
            raise BridgeError(str(message.get("reason") or "Minecraft cannot be armed"))

    async def _world_request(self, operation: str, expected: str, **fields) -> dict:
        message = await self.request(
            {"type": "world_" + operation, **fields}, timeout=5 if operation == "cancel" else 125
        )
        if (
            message.get("type") != "world_ack"
            or message.get("operation") != operation
            or message.get("status") != expected
            or message.get("controls_armed") is not False
            or (operation == "load" and message.get("save_name") != fields["save_name"])
        ):
            raise BridgeError("Minecraft did not confirm the requested world lifecycle operation")
        return message

    async def unload_world(self) -> dict:
        """Return only after the integrated server's final save has completed."""
        return await self._world_request("unload", "unloaded")

    async def load_world(self, save_name: str) -> dict:
        if not re.fullmatch(r"holospeed-demo-[A-Za-z0-9_-]{1,80}", save_name):
            raise ValueError("Invalid fresh-world save name")
        return await self._world_request("load", "ready", save_name=save_name)

    async def cancel_world(self) -> dict:
        return await self._world_request("cancel", "cancelled")

    async def pause_game(self) -> bool:
        """Freeze a local singleplayer world after final goal evidence is recorded."""
        message = await self.request({"type": "pause_game"}, timeout=5)
        if message.get("type") != "pause_game_ack":
            raise BridgeError("Invalid game pause acknowledgment")
        return message.get("status") == "paused"

    async def start_skill(self, candidate: dict[str, Any]) -> dict[str, Any]:
        if candidate.get("skill") not in {"mine", "craft", "smelt", "portal", "cast_portal"}:
            raise BridgeError("Unsupported normal-mechanics skill")
        packet = {key: candidate[key] for key in ("skill", "item", "target_count", "max_ticks")}
        if type(packet["target_count"]) is not int or not 1 <= packet["target_count"] <= 64:
            raise BridgeError("Invalid skill inventory target")
        if type(packet["max_ticks"]) is not int or not 20 <= packet["max_ticks"] <= 1800:
            raise BridgeError("Invalid skill deadline")
        if packet["skill"] == "smelt" and packet["item"] != "minecraft:iron_ingot":
            raise BridgeError("Unsupported smelting item")
        if packet["skill"] in {"portal", "cast_portal"}:
            if packet["item"] != "minecraft:nether_portal" or packet["target_count"] != 1:
                raise BridgeError("Invalid portal objective")
            target = candidate.get("target")
            if not isinstance(target, dict) or set(target) != {"x", "y", "z"}:
                raise BridgeError("Portal requires a bounded landmark")
            if any(type(value) is not int for value in target.values()) or not (
                -64 <= target["y"] <= 320
                and abs(target["x"]) <= 29_999_984
                and abs(target["z"]) <= 29_999_984
            ):
                raise BridgeError("Invalid portal landmark")
            if packet["skill"] == "cast_portal" and not (
                -63 <= target["y"] <= 313
                and -29_999_968 <= target["x"] <= 29_999_967
                and -29_999_968 <= target["z"] <= 29_999_967
            ):
                raise BridgeError("Portal construction needs room within world bounds")
            packet["target"] = target.copy()
        message = await self.request({"type": "skill", **packet}, timeout=5)
        if message.get("type") != "skill_ack" or message.get("status") not in {
            "accepted",
            "completed",
            "rejected",
        }:
            raise BridgeError("Invalid skill acknowledgment")
        return message

    async def stop(self, reason: str) -> None:
        if self.socket:
            try:
                await self.send({"type": "stop", "reason": reason})
            except (RuntimeError, OSError):
                logging.getLogger(__name__).warning(
                    "Could not send stop; mod watchdog must release controls"
                )
