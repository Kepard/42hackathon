"""Historical seed12345 A/B; use --expect-fixed to verify current virtual capture."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from mc_observatory.client import MinecraftClient
from mc_observatory.holo_tools import ToolExecutor


async def main():
    out = Path(__file__).parent
    subprocess.run(["xdotool", "search", "--onlyvisible", "--class", "chromium", "windowactivate", "--sync"], check=True)
    async with MinecraftClient() as c:
        t = {"seed": 12345, "spawn": {"x": 96.5, "y": 128, "z": -16.5, "yaw": 0, "pitch": 10, "time_of_day": 6000}}
        await c.reset(t["seed"], [], t["spawn"])
        await c.resume()
        ex = ToolExecutor(c, 854, 480)
        await ex.execute({"name": "move", "arguments": {"direction": "forward", "seconds": 2}}, "skills")
        before = await c.state()
        (out / "mining-unfocused-before.png").write_bytes(await c.screenshot())
        await ex.execute({"name": "attack", "arguments": {"seconds": 4.5}}, "skills")
        await asyncio.sleep(.6)
        unfocused = await c.state()
        (out / "mining-unfocused-after.png").write_bytes(await c.screenshot())
        print("UNFOCUSED:", json.dumps({"before": before["stats"]["mined"], "after": unfocused["stats"]["mined"], "focused": unfocused["window_focused"]}), flush=True)
        assert not unfocused["window_focused"]
        if "--expect-fixed" in sys.argv:
            assert unfocused["stats"]["mined"].get("minecraft:oak_log", 0) >= 1
        else:
            assert unfocused["stats"]["mined"] == before["stats"]["mined"]
        windows = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--name", r"^Minecraft\*? 1\.20\.4"], text=True).split()
        window = windows[-1]
        subprocess.run(["xdotool", "windowactivate", "--sync", window], check=True)
        subprocess.run(["xdotool", "windowraise", window], check=True)
        subprocess.run(["xdotool", "mousemove", "--window", window, "427", "240", "click", "1"], check=True)
        await asyncio.sleep(.3)
        ex.begin_step(await c.screenshot())
        await ex.execute({"name": "attack", "arguments": {"seconds": 4.5}}, "skills")
        await asyncio.sleep(.6)
        focused = await c.state()
        (out / "mining-focused-after.png").write_bytes(await c.screenshot())
        print("FOCUSED:", json.dumps({"mined": focused["stats"]["mined"], "focused": focused["window_focused"], "inventory": focused["inventory"]}), flush=True)
        assert focused["window_focused"] and focused["stats"]["mined"].get("minecraft:oak_log", 0) >= 1
        await ex.execute({"name": "move", "arguments": {"direction": "forward", "seconds": .4}}, "skills")
        await asyncio.sleep(.6)
        pickup = await c.state()
        print("PICKUP:", json.dumps(pickup["inventory"]), flush=True)
        assert any(item["id"] == "minecraft:oak_log" for item in pickup["inventory"])
        print("PASS unfocused synthetic mining and pickup" if "--expect-fixed" in sys.argv else "PASS pre-fix A/B: no unfocused mining; captured mouse mines leaves/log and picks up")


asyncio.run(main())
