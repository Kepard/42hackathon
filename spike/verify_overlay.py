"""Live overlay isolation/focus regression; outputs remain ignored under spike/ui."""
import asyncio
import base64
import json
from pathlib import Path

from PIL import Image, ImageChops
from mc_observatory.client import MinecraftClient
from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.run import task_definition


async def main():
    out = Path(__file__).parent / "ui"
    out.mkdir(exist_ok=True)
    async with MinecraftClient() as client:
        task = task_definition("get_log")
        await client.reset(task["seed"], task["inventory"], task["spawn"])
        if (await client.state())["screen_open"]:
            await client.resume()
            await client.key(256, duration_ms=1)
            await asyncio.sleep(.15)
        await client.pause()
        await client.call("overlay", payload={})
        capture = await client.call("screenshot")
        assert capture["capture_point"] == "after_hud_before_overlay"
        (out / "agent-baseline.png").write_bytes(await client.screenshot())
        await client.call("overlay", payload={"objective": "OVERLAY EXCLUSION TEST", "completed": 4, "current_rung": "stone pickaxe",
                                             "intent": "Viewer-only telemetry; never sent to the model.", "action": "attack · 3 seconds",
                                             "api_latency_ms": 1234, "badges": "THINKING PAUSED LISTENING FROZEN"})
        await client.subscribe()
        (out / "agent-overlay-on.png").write_bytes(await client.screenshot())
        frame = await asyncio.wait_for(client.events.get(), 5)
        while frame["event"] != "frame": frame = await asyncio.wait_for(client.events.get(), 5)
        (out / "viewer-overlay-on.jpg").write_bytes(base64.b64decode(frame["data"]["jpeg_base64"]))
        a, b = (Image.open(out / name).convert("RGB") for name in ("agent-baseline.png", "agent-overlay-on.png"))
        diff = ImageChops.difference(a, b)
        unchanged = diff.getbbox() is None
        world_changed = sum(pixel != (0, 0, 0) for pixel in diff.get_flattened_data())
        assert world_changed < a.width * a.height * .005, f"Agent overlay contamination: {world_changed} pixels"
        viewer = Image.open(out / "viewer-overlay-on.jpg").convert("RGB")
        assert ImageChops.difference(b.crop((12, 12, 620, 130)), viewer.crop((12, 12, 620, 130))).getbbox()
        await client.unsubscribe()
        await client.resume()
        await client.key(69, duration_ms=1)
        await asyncio.sleep(.15)
        await client.pause()
        (out / "agent-gui-overlay-on.png").write_bytes(await client.screenshot())
        await client.call("overlay", payload={})
        (out / "agent-gui-overlay-off.png").write_bytes(await client.screenshot())
        c, d = (Image.open(out / name).convert("RGB") for name in ("agent-gui-overlay-on.png", "agent-gui-overlay-off.png"))
        changed = sum(pixel != (0, 0, 0) for pixel in ImageChops.difference(c, d).get_flattened_data())
        assert changed < c.width * c.height * .005, f"Overlay leaked into GUI screenshot: {changed} changed pixels"
        await client.resume()
        await client.key(256, duration_ms=1)
        await asyncio.sleep(1.2)
        state = await client.state()
        assert state["pause_on_lost_focus"] is False
        assert not state["screen_open"], "Unexpected pause menu while unfocused"
        await client.pause()
        await client.resume()
        executor = ToolExecutor(client, 640, 480)
        await executor.execute({"name": "key_press", "arguments": {"key": "f3", "ms": 100}}, "raw")
        await client.pause()
        (out / "agent-f3-raw.png").write_bytes(await client.screenshot())
        await client.resume()
        await executor.execute({"name": "toggle_debug", "arguments": {}}, "skills")
        await client.pause()
        (out / "agent-f3-skills-off.png").write_bytes(await client.screenshot())
        result = {"agent_pixels_identical": unchanged, "world_changed_pixels": world_changed, "gui_changed_pixels": changed, "viewer_format": frame["data"]["capture_point"],
                  "pause_on_lost_focus": state["pause_on_lost_focus"], "window_focused": state["window_focused"], "screen": state["screen"]}
        (out / "overlay-proof.json").write_text(json.dumps(result, indent=2))
        print("PASS vanilla-HUD capture before overlay; agent world/GUI pixel deltas <0.5%; viewer includes overlay; F3 raw/skills screenshots captured")
        print(json.dumps(result))


asyncio.run(main())
