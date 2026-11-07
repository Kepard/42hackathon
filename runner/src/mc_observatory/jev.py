"""Optional pixel-only Jev proposals. No game-state client is accepted here."""
import hashlib
import time
from datetime import datetime, timezone

from .jev_eval import PRICE_PER_INPUT_TOKEN, RESERVE_PER_CALL


class JevVision:
    def __init__(self, client=None):
        self.client = client
        self.owned = client is None
        self.calls = []

    async def ask(self, png, target, *, hotbar=False):
        from typesafe_sdk import AsyncTypeSafeClient, Image, Box, Noul, RetryPolicy
        if self.client is None:
            self.client = AsyncTypeSafeClient(model="jev-1.14.0", headers={"TypeSafe-Danger-Zone-Images": "true"},
                                             retry=RetryPolicy(max_retries=0), timeout=2)
        questions = {"visible": Noul(instructions=(f"Is a {target} item icon in one of the nine hotbar slots? Include the inventory GUI bottom hotbar row. Exclude recipe icons, main inventory, and held-item sprites."
                                                   if hotbar else f"Is a {target} block visibly exposed in the world in this Minecraft screenshot? Exclude held items and GUI icons. GUI screens mean no."))}
        if not hotbar:
            questions["target"] = Box(instructions=f"Tight box around the nearest visible {target} block in the world. Exclude held items, GUI icons and foliage obscuring the target. Use apparent size for nearness.")
        event = {"target": target, "kind": "hotbar" if hotbar else "seek", "sha256": hashlib.sha256(png).hexdigest(), "model": "jev-1.14.0", "started_at": datetime.now(timezone.utc).isoformat()}
        started = time.monotonic()
        try:
            response = await self.client.system_one(model="jev-1.14.0", state={"screenshot": Image(png)}, questions=questions)
            event.update(input_tokens=response.usage.input_tokens, cost_usd=response.usage.input_tokens * PRICE_PER_INPUT_TOKEN)
            event["noul"] = float(response.nouls["visible"].noul)
            if not 0 <= event["noul"] <= 1:
                raise ValueError("Invalid probability")
            if not hotbar:
                b = response.boxes["target"].box
                event["box"] = [float(b.left), float(b.top), float(b.right), float(b.bottom)]
                l, t, r, b = event["box"]
                if not 0 <= l < r <= 1 or not 0 <= t < b <= 1:
                    raise ValueError("Invalid target box")
        except BaseException as error:
            event["error"] = type(error).__name__
            event.setdefault("cost_usd", RESERVE_PER_CALL)
            event["cost_estimated_upper_bound"] = "input_tokens" not in event
            raise
        finally:
            event["latency_ms"] = (time.monotonic() - started) * 1000
            self.calls.append(event)
        return event

    async def close(self):
        if self.client is not None and self.owned:
            await self.client.aclose()
