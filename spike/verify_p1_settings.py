"""Settings/layout/FPS calibration outside agent runs; artifacts remain ignored."""
import asyncio
import json
from pathlib import Path
from websockets.exceptions import WebSocketException

from mc_observatory.client import MinecraftClient
from mc_observatory.run import task_definition


async def main():
    out = Path(__file__).parent / "ui"
    client = MinecraftClient()
    for _ in range(120):
        try:
            await client.connect()
            if (await client.state()).get("in_world"):
                break
        except (OSError, ConnectionError, WebSocketException):
            pass
        await asyncio.sleep(.5)
    async with client:
        task = task_definition("get_log")
        await client.call("configure", auto_jump=True, width=854, render_distance=8)
        results = []
        await client.resume()
        for distance in (8, 6, 4, 2):
            await client.call("configure", render_distance=distance)
            samples = []
            for turn in range(3):
                await client.mouse(dx=600, dy=0)
                await asyncio.sleep(3)
                samples.append((await client.state())["fps"])
            results.append({"render_distance": distance, "fps": samples})
            if min(samples) >= 30:
                break
        await client.pause()
        (out / "p1-settings-world.png").write_bytes(await client.screenshot())
        await client.resume()
        await client.key(69, duration_ms=100)
        await asyncio.sleep(.3)
        await client.mouse(x=459, y=196, button=0, pressed=True)
        await client.mouse(button=0, pressed=False)
        await asyncio.sleep(.3)
        await client.pause()
        (out / "p1-recipe-book.png").write_bytes(await client.screenshot())
        (out / "p1-settings-evidence.json").write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2))


asyncio.run(main())
