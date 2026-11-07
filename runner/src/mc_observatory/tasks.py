import json
import time
from importlib.resources import files

import cua_bench as cb


def definitions():
    return json.loads(files("mc_observatory").joinpath("subtasks.json").read_text())


@cb.tasks_config(split="train")
def tasks():
    return [cb.Task(task_id=task["id"], description=task["description"], metadata=task)
            for task in definitions()]


@cb.setup_task(split="train")
async def setup(task_cfg, session):
    task = task_cfg.metadata
    await session.client.reset(task["seed"], task["inventory"])
    session.evaluator.reset()
    session.task_started = time.monotonic()
    await session.state()


@cb.evaluate_task(split="train")
async def evaluate(task_cfg, session):
    if session.task_started is None:
        raise RuntimeError("Call setup before evaluation")
    state = await session.state()
    task = task_cfg.metadata
    goal = task["goal"]
    achieved = (state.get("stats", {}).get("crafted", {}).get(goal["crafted"], 0) > 0 if "crafted" in goal else
                goal["milestone"] in session.evaluator.result()["completed"]
                if "milestone" in goal else session.evaluator.has_item(goal["item"]))
    within_limit = time.monotonic() - session.task_started <= task["time_limit_seconds"]
    return [float(achieved and within_limit)]
