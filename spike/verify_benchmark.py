"""Offline-evidence probe: two fresh task resets and screenshot/pose comparison."""

import asyncio
import io
import json
from pathlib import Path

from PIL import Image, ImageChops

from mc_observatory.client import MinecraftClient
from mc_observatory.run import task_definition


async def main():
    task = task_definition("get_log")
    root = Path(__file__).parent / "ui"
    states, images = [], []
    async with MinecraftClient() as client:
        for trial in range(2):
            state = await client.reset(task["seed"], task["inventory"], task["spawn"])
            await asyncio.sleep(3)
            png = await client.screenshot()
            states.append(await client.state())
            images.append(Image.open(io.BytesIO(png)).convert("RGB"))
            (root / f"benchmark-reset-{trial + 1}.png").write_bytes(png)
    pose = ["x", "y", "z", "yaw", "pitch", "time_of_day", "health", "food", "raining", "thundering", "inventory"]
    evidence = {"task": task, "states": [{k: s.get(k) for k in pose} for s in states]}
    diff = ImageChops.difference(*images)
    r, g, b = diff.split()
    changed = sum(ImageChops.lighter(ImageChops.lighter(r, g), b).histogram()[1:])
    evidence.update(changed_pixels=changed, changed_fraction=changed / (images[0].width * images[0].height))
    (root / "benchmark-reset-evidence.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))
    assert evidence["states"][0] == evidence["states"][1], "Reset states differ"
    for k, value in task["spawn"].items():
        assert abs(states[0][k] - value) < 1e-5, f"Incorrect spawn {k}"
    assert states[0]["health"] == 20 and states[0]["inventory"] == []
    assert states[0]["food"] == 20 and not states[0]["raining"] and not states[0]["thundering"]
    assert evidence["changed_fraction"] < .05, "Screenshots differ by at least 5%"
    print("PASS identical reset poses, fixed time, full health, empty inventory; screenshots differ <5%")


if __name__ == "__main__":
    asyncio.run(main())
