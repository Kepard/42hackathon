"""Fresh-copy world launches; never overwrite a world or change Minecraft state."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import math
import os
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from nbt import nbt

from .bridge import BridgeError
from .game_state import item_counts

MAX_WORLD_BYTES = 2 * 1024**3
MAX_WORLD_FILES = 50_000
FIELDS = ("id", "status", "stage", "started_at", "updated_at", "world_name", "run_id", "detail")
DETAILS = {
    "saving": "Saving the previous world and releasing all controls.",
    "copying": "Making a fresh copy of the prepared survival world.",
    "loading": "Loading the fresh world. Gameplay has not started.",
    "verifying": "Verifying the new world's seed, empty inventory, and health.",
    "starting": "Starting the autonomous run from the verified fresh world.",
    "running": "Fresh world ready. Autonomous gameplay has started.",
    "failed": "World preparation failed. Previous worlds are preserved; inspect the local launch log.",
    "cancelled": "Launch cancelled. Controls remain released and no automatic start will occur.",
    "interrupted": "Previous launch was interrupted. Start a new run explicitly.",
}


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path: Path, limit: int = 12 * 1024**2) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Launch metadata is too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Invalid launch metadata")  # noqa: TRY004 - invalid persisted content.
    return result


def metadata(world: Path) -> dict:
    """Read saved NBT without changing inventory, position, time, or generation."""
    level = world / "level.dat"
    if level.is_symlink() or not level.is_file() or level.stat().st_size > 16 * 1024**2:
        raise ValueError("A bounded saved level.dat is required")
    data = nbt.NBTFile(filename=str(level))["Data"]
    player = data["Player"]
    generation = data["WorldGenSettings"]
    position = [float(value.value) for value in player["Pos"].tags]
    valid = (
        data["DataVersion"].value == 3955
        and data["GameType"].value == 0
        and not data["allowCommands"].value
        and not generation["bonus_chest"].value
        and not player["Inventory"].tags
        and not player["EnderItems"].tags
        and player["Dimension"].value == "minecraft:overworld"
        and player["Health"].value == 20
        and player["foodLevel"].value == 20
        and len(position) == 3
        and all(math.isfinite(value) for value in position)
        and not player["abilities"]["instabuild"].value
        and not player["abilities"]["flying"].value
        and not player["abilities"]["invulnerable"].value
    )
    if not valid:
        raise ValueError("Template must be an empty, healthy, command-free 1.21.1 survival world")
    return {
        "seed": str(generation["seed"].value),
        "position": position,
        "dimension": "minecraft:overworld",
        "game_mode": "survival",
        "health": 20,
        "hunger": 20,
        "inventory_empty": True,
        "level_sha256": hashlib.sha256(level.read_bytes()).hexdigest(),
    }


def file_hashes(world: Path) -> dict[str, str]:
    files = {}
    total = 0
    for directory, names, filenames in os.walk(world, followlinks=False):
        for name in (*names, *filenames):
            if (Path(directory) / name).is_symlink():
                raise ValueError("World templates cannot contain symbolic links")
        for name in filenames:
            path = Path(directory) / name
            relative = path.relative_to(world).as_posix()
            if relative == "session.lock":
                continue
            total += path.stat().st_size
            if total > MAX_WORLD_BYTES or len(files) >= MAX_WORLD_FILES:
                raise ValueError("Prepared world exceeds the bounded copy size")
            with path.open("rb") as stream:
                files[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    return files


def verify_template(directory: Path, expected_seed: str) -> dict:
    manifest = read_json(directory / "manifest.json")
    world = directory / "world"
    if directory.is_symlink() or world.is_symlink() or not world.is_dir():
        raise ValueError("Prepared world directory is unavailable")
    info = metadata(world)
    if manifest.get("version") != 1 or manifest.get("world") != info:
        raise ValueError("Prepared world metadata changed")
    if info["seed"] != expected_seed:
        raise ValueError("Prepared world seed does not match the configured demonstration")
    if manifest.get("files") != file_hashes(world):
        raise ValueError("Prepared world files changed; prepare a new immutable template")
    return manifest


def prepare_template(source: Path, destination: Path, expected_seed: str) -> dict:
    """Explicit offline preparation, after the game's normal save-and-unload."""
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError("Choose a new template directory outside the saved world")
    lock = source / "session.lock"
    if not lock.is_file():
        raise ValueError("The source must be a normally saved Minecraft world")
    with lock.open("r+b") as stream:
        try:
            fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            raise ValueError("Save and unload the source world before preparing it") from error
        info = metadata(source)
        if info["seed"] != expected_seed:
            raise ValueError("Source world has the wrong demonstration seed")
        hashes = file_hashes(source)
        destination.mkdir(parents=True, exist_ok=False)
        shutil.copytree(
            source, destination / "world", ignore=shutil.ignore_patterns("session.lock")
        )
        if file_hashes(destination / "world") != hashes:
            raise ValueError("Prepared world copy verification failed")
        manifest = {"version": 1, "prepared_at": stamp(), "world": info, "files": hashes}
        write_json(destination / "manifest.json", manifest)
    return manifest


class DemoLauncher:
    def __init__(self, engine, settings):
        self.engine = engine
        self.settings = settings
        self.directory = settings.runs_dir / ".demo-launches"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.task: asyncio.Task | None = None
        self.record: dict | None = None
        self.abort = threading.Event()
        self.lock = asyncio.Lock()
        self.owner_fd: int | None = None
        self.launch_started = False
        self.engine.launcher = self
        self.engine.state["launch"] = None
        # A restarted process never replays a previous launch or calls a game API.
        records = sorted(self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime)
        if records:
            with contextlib.suppress(OSError, ValueError, KeyError):
                previous = read_json(records[-1], 16 * 1024)
                if (
                    any(key not in previous for key in FIELDS)
                    or previous["status"]
                    not in {"preparing", "running", "failed", "cancelled", "interrupted"}
                    or str(uuid.UUID(previous["id"])) != previous["id"]
                ):
                    raise ValueError("Invalid saved launch receipt")
                self.record = previous
                if self.record["status"] in {"preparing", "running"}:
                    self._publish("interrupted")
                else:
                    self.engine.state["launch"] = self.snapshot()

    @property
    def active(self) -> bool:
        return bool(self.record and self.record["status"] == "preparing")

    def snapshot(self) -> dict | None:
        return {key: self.record[key] for key in FIELDS} if self.record else None

    def _publish(self, stage: str) -> None:
        self.record.update(
            stage=stage,
            status="preparing"
            if stage in {"saving", "copying", "loading", "verifying", "starting"}
            else stage,
            detail=DETAILS[stage],
            updated_at=stamp(),
        )
        try:
            write_json(self.directory / f"{self.record['id']}.json", self.record)
        finally:
            self.engine.state["launch"] = self.snapshot()
            self.engine.broadcast()

    def guard_start(self, launch_id: str | None) -> None:
        if launch_id is not None:
            if not self.active or launch_id != self.record["id"] or self.abort.is_set():
                raise ValueError("This world launch is no longer active")
        elif self.active or (self.task and not self.task.done()):
            raise ValueError("A fresh world is still preparing; wait or cancel the launch")

    def interrupt(self, reason: str, launch_id: str | None = None) -> None:
        if self.active and launch_id != self.record["id"]:
            self.abort.set()
            if (
                self.launch_started
                and self.task
                and self.task is not asyncio.current_task()
                and not self.task.done()
            ):
                self.task.cancel()
            with contextlib.suppress(OSError):
                self._publish("cancelled")

    async def begin(self, identifier: str) -> dict:
        try:
            if str(uuid.UUID(identifier)) != identifier:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("New run requires a canonical launch UUID") from error
        async with self.lock:
            path = self.directory / f"{identifier}.json"
            if path.exists():
                previous = read_json(path, 16 * 1024)
                return {key: previous[key] for key in FIELDS}
            if self.active or (self.task and not self.task.done()):
                raise ValueError("Another fresh-world launch is already preparing or stopping")
            if self.settings.mode != "live":
                raise ValueError("Fresh-world launching requires live Minecraft")
            if not self.engine.bridge.connected:
                raise ValueError("Connect Minecraft before launching a fresh world")
            if not self.settings.demo_template_dir or not self.settings.minecraft_saves_dir:
                raise ValueError("Prepare and configure the demonstration world template first")
            descriptor = os.open(self.directory / "launcher.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                os.close(descriptor)
                raise ValueError("Another controller is preparing a fresh world") from error
            self.owner_fd = descriptor
            if path.exists():  # Another process may have finished while we acquired the lock.
                os.close(descriptor)
                self.owner_fd = None
                previous = read_json(path, 16 * 1024)
                return {key: previous[key] for key in FIELDS}
            self.abort = threading.Event()
            self.launch_started = False
            now = stamp()
            self.record = {
                "id": identifier,
                "status": "preparing",
                "stage": "saving",
                "started_at": now,
                "updated_at": now,
                "world_name": "holospeed-demo-" + uuid.UUID(identifier).hex,
                "run_id": None,
                "detail": DETAILS["saving"],
            }
            try:
                self._publish("saving")  # Durable receipt precedes every world side effect.
            except BaseException:
                os.close(descriptor)
                self.owner_fd = None
                self.record = None
                raise
            self.task = asyncio.create_task(self._launch(identifier), name="holospeed-world-launch")
            return self.snapshot()

    def _check(self, identifier: str) -> None:
        if self.abort.is_set():
            raise asyncio.CancelledError
        self.guard_start(identifier)

    def _copy(self, identifier: str) -> dict:
        manifest = verify_template(self.settings.demo_template_dir, self.settings.declared_seed)
        source = self.settings.demo_template_dir / "world"
        saves = self.settings.minecraft_saves_dir.resolve(strict=True)
        destination = saves / ("holospeed-demo-" + uuid.UUID(identifier).hex)
        if destination.exists():
            raise ValueError("The unique destination already exists; it will never be overwritten")

        def copy_file(source_file, destination_file):
            if self.abort.is_set():
                raise OSError("Launch copy cancelled")
            return shutil.copy2(source_file, destination_file)

        shutil.copytree(source, destination, copy_function=copy_file)
        if self.abort.is_set():
            raise OSError("Launch copy cancelled")
        if file_hashes(destination) != manifest["files"]:
            raise ValueError("Fresh world copy verification failed")
        return manifest

    async def _launch(self, identifier: str) -> None:
        self.launch_started = True
        started_run_id = None
        try:
            self._check(identifier)
            await self.engine.finish(launch_id=identifier)
            self._check(identifier)
            # The ACK proves the previous integrated server has finished saving.
            await self.engine.bridge.unload_world()
            self._check(identifier)
            self._publish("copying")
            copying = asyncio.create_task(asyncio.to_thread(self._copy, identifier))
            try:
                manifest = await asyncio.shield(copying)
            except asyncio.CancelledError:
                self.abort.set()
                with contextlib.suppress(Exception):
                    await copying  # Do not overlap a cancelled copy with a new launch.
                raise
            self._check(identifier)
            self._publish("loading")
            loaded = await self.engine.bridge.load_world(self.record["world_name"])
            self._check(identifier)
            self._publish("verifying")
            if (
                loaded.get("seed") != manifest["world"]["seed"]
                or loaded.get("save_name") != self.record["world_name"]
                or loaded.get("dimension") != "minecraft:overworld"
                or loaded.get("game_mode") != "survival"
                or loaded.get("health") != 20
                or loaded.get("inventory_empty") is not True
                or loaded.get("controls_armed") is not False
            ):
                raise ValueError("Loaded world did not match the prepared starting state")
            state = await self.engine.bridge.game_state()
            if (
                not state.is_fresh(time.time() * 1000)
                or state.dimension != "minecraft:overworld"
                or state.player is None
                or state.player.health != 20
                or state.player.hunger != 20
                or item_counts(state)
            ):
                raise ValueError("Fresh world state must be healthy and empty before gameplay")
            self._check(identifier)
            self._publish("starting")
            await self.engine.start(launch_id=identifier, arm=True)
            started_run_id = self.engine.state["run_id"]
            self.record["run_id"] = started_run_id
            self.engine.recorder.save_game_state(state.model_dump())
            self.engine.log(
                "launch_verified",
                {
                    "launch_id": identifier,
                    "world_name": self.record["world_name"],
                    "seed": loaded["seed"],
                    "template_level_sha256": manifest["world"]["level_sha256"],
                    "initial_state_id": state.id,
                    "inventory_empty": True,
                    "health": 20,
                },
            )
            self._publish("running")
        except asyncio.CancelledError:
            if self.active:
                with contextlib.suppress(OSError):
                    self._publish("cancelled")
            with contextlib.suppress(BridgeError, TimeoutError):
                await asyncio.wait_for(self.engine.bridge.cancel_world(), 5)
        except Exception as error:  # noqa: BLE001 - retain a failed receipt, never replay a launch.
            if self.record and self.record["id"] == identifier:
                self.record["error_class"] = type(error).__name__
                self.record["error_stage"] = self.record["stage"]
                with contextlib.suppress(OSError):
                    self._publish("failed")
            if started_run_id is not None:
                with contextlib.suppress(Exception):
                    await self.engine.finish(expected_run_id=started_run_id, launch_id=identifier)
            with contextlib.suppress(BridgeError, TimeoutError):
                await self.engine.bridge.stop("Fresh world launch failed; controls released")
                await asyncio.wait_for(self.engine.bridge.cancel_world(), 5)
        finally:
            if self.owner_fd is not None:
                os.close(self.owner_fd)
                self.owner_fd = None

    async def close(self) -> None:
        self.interrupt("Controller shutting down")
        if self.task and not self.task.done():
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(asyncio.shield(self.task), 3)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare a saved empty world for fresh demo launches"
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--seed", required=True)
    args = parser.parse_args()
    manifest = prepare_template(args.source, args.destination, args.seed)
    print(
        json.dumps(
            {"prepared": True, "seed": manifest["world"]["seed"], "files": len(manifest["files"])}
        )
    )


if __name__ == "__main__":
    main()
