"""Deterministic simulated bridge for integration tests and stage rehearsals."""

from __future__ import annotations

import asyncio
import base64
import io
import uuid

from PIL import Image, ImageDraw

from .models import Action, Decision, Observation


class DemoBridge:
    connected = True

    def __init__(self) -> None:
        self.step = 0

    async def observe(self) -> Observation:
        image = Image.new("RGB", (960, 540), "#80b8cb")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 260, 960, 540), fill="#526b3e")
        for x, y, w, h in [(0, 220, 220, 100), (680, 180, 280, 180), (170, 290, 250, 100)]:
            draw.rectangle((x, y, x + w, y + h), fill="#789453")
            draw.rectangle((x, y + 24, x + w, y + h), fill="#5c4937")
        offset = (self.step % 4) * 7
        for x, y in [(130 + offset, 220), (470 + offset, 190), (790 + offset, 250)]:
            draw.rectangle((x, y, x + 44, y + 150), fill="#5e4231")
            draw.rectangle((x + 12, y, x + 18, y + 150), fill="#775a3c")
            draw.rectangle((x - 66, y - 105, x + 100, y + 18), fill="#325e39")
            draw.rectangle((x - 40, y - 140, x + 73, y - 45), fill="#467843")
        draw.line((469, 270, 491, 270), fill="white", width=2)
        draw.line((480, 259, 480, 281), fill="white", width=2)
        draw.rectangle((320, 488, 640, 530), fill="#18251f", outline="#97b69a", width=2)
        for i in range(9):
            draw.rectangle((323 + 35 * i, 491, 353 + 35 * i, 527), outline="#526254")
        draw.rectangle((0, 0, 960, 32), fill="#111e22")
        draw.text(
            (14, 10), "SIMULATED BRIDGE FRAME / no Minecraft or provider calls", fill="#d0e3d8"
        )
        draw.text((14, 510), f"fixture tick {self.step * 6}", fill="white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return Observation(
            id=str(uuid.uuid4()),
            frame_id=str(uuid.uuid4()),
            tick=self.step * 6,
            width=960,
            height=540,
            image_base64=base64.b64encode(stream.getvalue()).decode(),
        )

    async def execute(self, action: Action, frame_id: str, ttl_ms: int) -> dict:
        tick = self.step * 6
        await asyncio.sleep(action.duration_ticks / 20)
        self.step += 1
        return {
            "type": "ack",
            "id": str(uuid.uuid4()),
            "status": "completed",
            "start_tick": tick,
            "end_tick": tick + action.duration_ticks,
            "reason": "simulated controls",
        }

    async def stop(self, reason: str) -> None:
        pass

    async def send(self, message: dict) -> None:
        pass


async def demo_decision(step: int) -> Decision:
    await asyncio.sleep(0.25 if step % 4 == 0 else 0.08)
    phases = [
        (
            "Find a reachable tree",
            "Scan the treeline; approach the nearest unobstructed trunk.",
            "The trunk fills the crosshair.",
            Action(keys=["forward", "sprint"], duration_ticks=6),
        ),
        (
            "Gather oak logs",
            "Keep the crosshair on the trunk and hold attack in bounded bursts.",
            "A log breaks and enters the hotbar.",
            Action(keys=["attack"], duration_ticks=20),
        ),
        (
            "Craft a workbench",
            "Inspect the inventory, convert logs to planks, and arrange four planks.",
            "A crafting table appears in the inventory.",
            Action(keys=["inventory"], duration_ticks=1),
        ),
        (
            "Craft a wooden pickaxe",
            "Place the workbench and use the visible crafting grid.",
            "A wooden pickaxe is visible in the hotbar.",
            Action(keys=["use"], duration_ticks=2),
        ),
        (
            "Reach stone tools",
            "Mine exposed stone, then return to the workbench for stone tools.",
            "A stone pickaxe is visible.",
            Action(keys=["attack"], duration_ticks=20),
        ),
    ]
    index = min(step // 4, len(phases) - 1)
    objective, strategy, expected, action = phases[index]
    milestones = {7: "wood", 11: "crafting_table", 15: "wooden_pickaxe", 19: "stone_tools"}
    return Decision(
        objective=objective,
        strategy=strategy,
        expected_effect=expected,
        action=action,
        milestone=milestones.get(step),
        evidence="Synthetic fixture event; not real gameplay.",
    )
