import asyncio
import importlib
import time
from types import SimpleNamespace

import pytest

cb = pytest.importorskip("cua_bench")
from mc_observatory.evaluator import LadderEvaluator
from mc_observatory.session import MinecraftSession, keycode
from mc_observatory.tasks import definitions, evaluate, setup, tasks


def test_decorated_task_data():
    assert tasks._td_type == "tasks_config" and setup._td_type == "setup_task" and evaluate._td_type == "evaluate_task"
    assert len(tasks()) == len(definitions()) == 3
    assert all(isinstance(task, cb.Task) for task in tasks())


def test_keycodes():
    assert keycode("W") == 87 and keycode("Escape") == 256
    with pytest.raises(ValueError):
        keycode("unknown")


async def test_task_timeout_and_uninitialized():
    class FakeSession:
        task_started = None
        evaluator = LadderEvaluator()

        async def state(self):
            self.evaluator.update({"inventory": [{"id": "minecraft:oak_log", "count": 1}]})

    session = FakeSession()
    task = tasks()[0]
    with pytest.raises(RuntimeError, match="setup"):
        await evaluate(task, session)
    session.task_started = time.monotonic() - 1000
    assert await evaluate(task, session) == [0.0]
    session.task_started = time.monotonic()
    assert await evaluate(task, session) == [1.0]


async def test_actions_are_dispatched_and_unsupported_refused():
    calls = []

    class FakeClient:
        async def click(self, *args): calls.append(("click", args))
        async def mouse(self, **kwargs): calls.append(("mouse", kwargs))
        async def key(self, *args, **kwargs): calls.append(("key", args, kwargs))
        async def type_text(self, text): calls.append(("type", text))

    session = MinecraftSession()
    session.client = FakeClient()
    for action in (cb.ClickAction(1, 2), cb.RightClickAction(3, 4), cb.MoveToAction(5, 6), cb.TypeAction("abc"), cb.KeyAction("e"), cb.WaitAction(0), cb.DoneAction()):
        await session.execute_action(action)
    assert calls[:4] == [("click", (1, 2, 0)), ("click", (3, 4, 1)), ("mouse", {"x": 5, "y": 6}), ("type", "abc")]
    with pytest.raises(NotImplementedError):
        await session.execute_action(cb.ScrollAction())
    with pytest.raises(NotImplementedError):
        await session.run_command("echo unsupported")


@pytest.mark.parametrize("task_id,item", [("craft_planks", "minecraft:oak_planks"), ("craft_table", "minecraft:crafting_table")])
@pytest.mark.parametrize("category", ["picked_up", "mined", "crafted"])
async def test_craft_tasks_require_crafted_stat(task_id, item, category):
    class FakeSession:
        task_started = time.monotonic()
        evaluator = LadderEvaluator()

        async def state(self):
            state = {"inventory": [{"id": item, "count": 1}], "stats": {category: {item: 1}}}
            self.evaluator.update(state)
            return state

    task = next(task for task in tasks() if task.task_id == task_id)
    assert await evaluate(task, FakeSession()) == [float(category == "crafted")]


async def test_setup_clock_starts_after_initial_state(monkeypatch):
    order = []

    class FakeClient:
        async def reset(self, *args): order.append("reset")

    class FakeSession:
        client = FakeClient()
        evaluator = LadderEvaluator()

        async def state(self): order.append("state")

    module = importlib.import_module("mc_observatory.tasks")
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: order.append("clock") or 123))
    session = FakeSession()
    await setup(tasks()[0], session)
    assert order == ["reset", "state", "clock"]
    assert session.task_started == 123
