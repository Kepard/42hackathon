"""Engineering-only virtual-input check and fixed natural-tree comparison starts."""
import asyncio
import json
import subprocess
from pathlib import Path

import httpx

from mc_observatory.client import MinecraftClient
from mc_observatory.holo_tools import ToolExecutor
from mc_observatory.run import task_definition
from mc_observatory.user_tasks import add_task, definitions


def fixtures():
    base = task_definition("get_log")
    return [
        base,
        {**base, "id": "get_log_leaf_close", "seed": 12345,
         "spawn": {"x": 96.5, "y": 128, "z": -10.5, "yaw": 0, "pitch": 10, "time_of_day": 6000}},
    ]


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as api:
        status = (await api.get("/api/status")).json()
        if status["active_run"]:
            run = (await api.get("/api/runs/" + status["active_run"])).json()
            assert run["status"] not in {"starting", "running"}, "Refusing to reset an active benchmark"
    subprocess.run(["xdotool", "search", "--onlyvisible", "--class", "chromium", "windowactivate", "--sync"], check=True)
    async with MinecraftClient() as client:
        for task in fixtures():
            await client.reset(task["seed"], [], task["spawn"])
            await client.resume()
            ex = ToolExecutor(client, 854, 480)
            frame = await client.screenshot()
            Path(__file__).with_name(task["id"] + "-start.png").write_bytes(frame)
            if task["id"] == "get_log":
                Path(__file__).with_name("seed7941_spawn.png").write_bytes(frame)
                await ex.execute({"name": "move", "arguments": {"direction": "forward", "seconds": 1.3}}, "skills")
            before = await client.state()
            assert not before["window_focused"] and not before["cursor_locked"]
            frame = await client.screenshot()
            Path(__file__).with_name(task["id"] + "-before-mining.png").write_bytes(frame)
            ex.begin_step(frame)
            attack = asyncio.create_task(ex.execute({"name": "attack", "arguments": {"seconds": 5}}, "skills"))
            try:
                await asyncio.sleep(.3)
                during = await client.state()
            finally:
                await attack
            await asyncio.sleep(.6)
            after = await client.state()
            assert during["cursor_locked"] and not during["window_focused"]
            assert not after["cursor_locked"] and not after["window_focused"]
            mined = after["stats"]["mined"]
            assert mined.get("minecraft:oak_log", 0) >= 1, mined
            assert bool(mined.get("minecraft:oak_leaves", 0)) == (task["id"] == "get_log_leaf_close"), mined
            await ex.execute({"name": "move", "arguments": {"direction": "forward", "seconds": 1}}, "skills")
            await asyncio.sleep(.6)
            pickup = await client.state()
            assert any(item["id"] == "minecraft:oak_log" for item in pickup["inventory"])
            Path(__file__).with_name(task["id"] + "-pickup.png").write_bytes(await client.screenshot())
            print("PASS virtual mining: " + json.dumps({"task": task["id"], "spawn": task["spawn"], "focused": after["window_focused"], "capture_before_during_after": [before["cursor_locked"], during["cursor_locked"], after["cursor_locked"]], "mined": mined, "inventory": pickup["inventory"]}), flush=True)
            existing = {t["id"]: t for t in definitions()}
            if task["id"] in existing:
                assert existing[task["id"]] == task
            else:
                add_task(task)


asyncio.run(main())
