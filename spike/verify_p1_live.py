"""Engineering-only calibration. Privileged reads here are never model feedback."""
import asyncio
import json
import math
from pathlib import Path

from websockets.exceptions import WebSocketException

from mc_observatory.client import MinecraftClient
from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.run import task_definition


async def main():
    out = Path(__file__).parent / "ui"
    c = MinecraftClient()
    for _ in range(160):
        try:
            await c.connect()
            if (await c.state()).get("in_world"):
                break
        except (OSError, ConnectionError, WebSocketException):
            pass
        await asyncio.sleep(.5)
    async with c:
        task = task_definition("get_log")
        await c.call("configure", width=854, render_distance=2, auto_jump=True)
        await c.reset(task["seed"], [], task["spawn"])
        await c.resume()
        ex = ToolExecutor(c, 854, 480)
        await ex.execute({"name": "set_pitch", "arguments": {"deg": 0}}, "skills")
        await c.mouse(dx=20/.15)
        before = await c.state()
        aim_before = await c.screenshot()
        (out / "p1-aim-before.png").write_bytes(aim_before)
        ex.begin_step(aim_before)
        focal = 240 / math.tan(math.radians(35))
        target_x = (.5 - focal * math.tan(math.radians(20)) / 853) * 1000
        result = await ex.execute({"name": "aim_at", "arguments": {"x": target_x, "y": 500}}, "skills")
        after = await c.state()
        (out / "p1-aim-after.png").write_bytes(await c.screenshot())
        assert abs(after["yaw"]) < .2 and abs(after["pitch"]) < .2
        print("PASS aim calibration:", json.dumps({"target_x": target_x, "yaw_before": before["yaw"], "yaw_after": after["yaw"], "pitch_after": after["pitch"], "pixel_result": result}))
        await c.reset(task["seed"], [{"id": "minecraft:water_bucket", "count": 1}, {"id": "minecraft:lava_bucket", "count": 1}], task["spawn"])
        await c.resume()
        ex.begin_step(await c.screenshot())
        await ex.execute({"name": "set_pitch", "arguments": {"deg": 90}}, "skills")
        await ex.execute({"name": "use", "arguments": {}}, "skills")
        await ex.execute({"name": "select_hotbar", "arguments": {"n": 2}}, "skills")
        await ex.execute({"name": "use", "arguments": {}}, "skills")
        state = await c.state()
        for _ in range(60):
            if state.get("obsidian_created"):
                break
            await asyncio.sleep(.1)
            state = await c.state()
        (out / "p1-cast-obsidian.png").write_bytes(await c.screenshot())
        evidence = {"obsidian_created": state.get("obsidian_created"), "inventory": state["inventory"]}
        print("CAST EVIDENCE:", json.dumps(evidence))
        assert state.get("obsidian_created") is True
        assert not any(s["id"] == "minecraft:obsidian" for s in state["inventory"])
        print("PASS live cast obsidian credited without an obsidian item")
        await c.reset(task["seed"], [], task["spawn"])
        assert not (await c.state())["obsidian_created"]
        print("PASS new world clears cast evidence")


asyncio.run(main())
