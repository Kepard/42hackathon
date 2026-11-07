"""Validated local tasks, separate from the versioned benchmark definitions."""

import json
import os
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft7Validator

from .evaluator import LADDER

ITEM = {"type": "string", "pattern": "^minecraft:[a-z0-9_]+$", "maxLength": 120}
TASK_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "additionalProperties": False,
    "required": ["id", "description", "seed", "time_limit_seconds", "inventory", "goal"],
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,79}$"},
        "description": {"type": "string", "minLength": 1, "maxLength": 2000},
        "seed": {"type": "integer", "minimum": -(2**63), "maximum": 2**63 - 1},
        "spawn": {"type": "object", "additionalProperties": False,
            "required": ["x", "y", "z", "yaw", "pitch", "time_of_day"],
            "properties": {
                "x": {"type": "number", "minimum": -29999984, "maximum": 29999984},
                "y": {"type": "number", "minimum": -64, "maximum": 320},
                "z": {"type": "number", "minimum": -29999984, "maximum": 29999984},
                "yaw": {"type": "number", "minimum": -180, "maximum": 180},
                "pitch": {"type": "number", "minimum": -90, "maximum": 90},
                "time_of_day": {"type": "integer", "minimum": 0, "maximum": 23999}}},
        "time_limit_seconds": {"type": "integer", "minimum": 1, "maximum": 10800},
        "inventory": {"type": "array", "maxItems": 36, "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "count"],
            "properties": {"id": ITEM, "count": {"type": "integer", "minimum": 1, "maximum": 64,
                "description": "Total quantity; reset splits by Minecraft's item stack limit. Expanded inventory must fit 36 slots."}}}},
        "goal": {"oneOf": [
            {"type": "object", "additionalProperties": False, "required": [key], "properties": {key: spec}}
            for key, spec in [("crafted", ITEM), ("item", ITEM), ("milestone", {"enum": list(LADDER)})]
        ]},
    },
}


def user_tasks_path():
    root = Path(os.environ.get("OBS_RUNS_DIR", Path(__file__).resolve().parents[3] / "runs"))
    return Path(os.environ.get("OBS_USER_TASKS_FILE", root / "user-tasks.json"))


def validate_task(task):
    errors = sorted(Draft7Validator(TASK_SCHEMA).iter_errors(task), key=lambda e: str(e.path))
    if errors:
        raise ValueError("Invalid task: " + "; ".join(e.message for e in errors))
    if task["id"] == "ladder":
        raise ValueError("The ladder task id is reserved")
    return task


def definitions(user_file=None):
    builtin = json.loads(files("mc_observatory").joinpath("subtasks.json").read_text())
    path = Path(user_file) if user_file is not None else user_tasks_path()
    local = json.loads(path.read_text()) if path.exists() else []
    ids = {task["id"] for task in builtin}
    for task in local:
        validate_task(task)
        if task["id"] in ids:
            raise ValueError("Duplicate task id: " + task["id"])
        ids.add(task["id"])
    return builtin + local


def add_task(task, user_file=None):
    validate_task(task)
    path = Path(user_file) if user_file is not None else user_tasks_path()
    if task["id"] in {t["id"] for t in definitions(path)}:
        raise ValueError("Task id already exists")
    local = json.loads(path.read_text()) if path.exists() else []
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(local + [task], indent=2) + "\n")
    temporary.replace(path)
    return task
