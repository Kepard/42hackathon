import asyncio
import json
import os
from pathlib import Path

import pytest

from mc_observatory.client import MinecraftClient, ModError
from mc_observatory.session import MinecraftSession

cb = pytest.importorskip("cua_bench")
from mc_observatory.tasks import evaluate, setup, tasks

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("MC_LIVE") != "1", reason="Set MC_LIVE=1 for the running Fabric client")]
EVIDENCE = Path(__file__).resolve().parents[2] / "spike" / "core"


async def tap(client, code):
    await client.key(code)
    await asyncio.sleep(0.15)
    await client.key(code, pressed=False)
    await asyncio.sleep(0.2)


async def test_reset_freeze_gui_crafting_and_stream():
    EVIDENCE.mkdir(exist_ok=True)
    async with MinecraftSession(trace_dir=EVIDENCE / "trajectory") as session:
        client = session.client
        task = next(task for task in tasks() if task.task_id == "craft_planks")
        await setup(task, session)
        initial = await session.state()
        assert initial["in_world"] and not initial["creative"]
        assert initial["seed"] == 12345 and initial["dimension"] == "minecraft:overworld"
        assert initial["world_id"].startswith("observatory-") and initial["world_id"] != "observatory-spike"
        assert initial["inventory"] == [{"slot": 0, "id": "minecraft:oak_log", "count": 4}]
        assert await evaluate(task, session) == [0.0]
        assert session.evaluator.result()["completed"] == ["log"]
        assert all(category in initial["stats"] for category in ("mined", "picked_up", "crafted", "killed"))
        assert isinstance(initial["advancements"], list)

        with pytest.raises(ModError, match="Unknown item"):
            await client.reset(0, [{"id": "minecraft:not_an_item", "count": 1}])
        assert (await client.state())["world_id"] == initial["world_id"]
        with pytest.raises(ModError, match="Unknown command"):
            await client.call("invalid")

        await asyncio.sleep(2)
        await client.pause()
        await asyncio.sleep(0.7)
        frozen = await client.state()
        assert frozen["paused"]
        assert (await client.key(87, duration_ms=500))["ignored"]
        assert (await client.mouse(dx=100))["ignored"]
        assert (await client.type_text("ignored"))["ignored"]
        await asyncio.sleep(0.8)
        still = await client.state()
        assert still["server_time"] == frozen["server_time"]
        assert [still[k] for k in ("x", "y", "z", "yaw", "pitch")] == [frozen[k] for k in ("x", "y", "z", "yaw", "pitch")]
        (EVIDENCE / "paused.png").write_bytes(await session.screenshot())
        await client.resume()
        await asyncio.sleep(0.7)
        assert not (await client.state())["paused"]
        assert (await client.state())["server_time"] > still["server_time"]

        await tap(client, 290)  # F1: screenshot must fall back when HUD hook is skipped.
        hidden = await client.call("screenshot", timeout=7)
        assert hidden["capture_point"] == "end_frame_fallback"
        await tap(client, 290)

        await tap(client, 69)
        opened = await session.state()
        assert opened["screen"] == "InventoryScreen" and opened["screen_open"]
        (EVIDENCE / "inventory-before.png").write_bytes(await session.screenshot())
        await session.execute_action(cb.ClickAction(176, 374))  # Hotbar slot 0, with 4 logs.
        await session.execute_action(cb.ClickAction(356, 126))  # Top-left 2x2 crafting slot.
        await asyncio.sleep(0.3)
        await session.execute_action(cb.ClickAction(468, 146))  # Take the four oak planks.
        await session.execute_action(cb.ClickAction(212, 374))  # Place planks into hotbar slot 1.
        await asyncio.sleep(0.8)
        crafted = await session.state()
        (EVIDENCE / "inventory-after.png").write_bytes(await session.screenshot())
        assert any(item["id"] == "minecraft:oak_planks" and item["count"] >= 4 for item in crafted["inventory"]), crafted
        assert crafted["stats"]["crafted"]["minecraft:oak_planks"] >= 4
        assert await evaluate(task, session) == [1.0]
        assert session.evaluator.has_item("minecraft:oak_planks")
        await tap(client, 256)

        await tap(client, 84)
        assert (await client.state())["screen"] == "ChatScreen"
        await client.type_text("observatory GUI typing test")
        (EVIDENCE / "typing.png").write_bytes(await session.screenshot())
        await tap(client, 256)

        await client.subscribe()
        event_counts = {"frame": 0, "state": 0}
        for _ in range(8):
            event = await asyncio.wait_for(client.events.get(), 3)
            assert event["event"] in event_counts
            event_counts[event["event"]] += 1
            if event["event"] == "frame":
                assert event["data"]["width"] == 640
        assert event_counts == {"frame": 4, "state": 4}
        assert (await client.state())["in_world"]  # Responses interleaved with push events.
        await client.unsubscribe()
        (EVIDENCE / "states.json").write_text(json.dumps({"initial": initial, "frozen": frozen, "still": still, "crafted": crafted, "events": event_counts}, indent=2))
        print("LIVE reset seed=12345 logs=4; pause stable; hidden-HUD capture; GUI crafted oak_planks=4; typing; frame/state stream=4+4; reward=1.0")

        fresh = await client.reset(54321)
        session.evaluator.update(fresh)
        assert fresh["seed"] == 54321 and fresh["world_id"] != initial["world_id"]
        assert fresh["inventory"] == [] and fresh["stats"]["crafted"] == {}
        assert session.evaluator.result()["completed"] == []


async def test_empty_world_inputs_and_time_limits():
    async with MinecraftClient() as client:
        await client.resume()
        with pytest.raises(ModError, match="Typing requires"):
            await client.type_text("no screen")


async def test_coordinate_click_requires_gui():
    async with MinecraftClient() as client:
        await client.resume()
        assert not (await client.state())["screen_open"]
        with pytest.raises(ModError, match="crosshair"):
            await client.click(100, 100)
        await client.mouse(button=0, pressed=True)
        await client.mouse(button=0, pressed=False)
