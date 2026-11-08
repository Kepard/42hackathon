"""Minecraft-window publisher with authenticated recorded-run controls.

Captures only the Minecraft X11 window without planner/model calls. The separate
command consumer applies short-lived operator commands through the loopback
controller. Credentials are used only for the configured HTTPS cloud service.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import signal
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .recording import SECRET

LOG = logging.getLogger("holospeed.broadcast")
MAX_FRAME = 1_750_000
MAX_METADATA = 240_000
MAX_RUN = 8 * 1024 * 1024
MAX_RAW_LINE = 24 * 1024 * 1024
MAX_READ = 32 * 1024 * 1024
RUN_ID = re.compile(r"[0-9TZabcdef-]{1,80}\Z")
INLINE_IMAGE = re.compile(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)
BASE64 = re.compile(r"[A-Za-z0-9+/\r\n]+={0,2}\Z")
BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{1024,}={0,2}(?![A-Za-z0-9+/])")
PRIVATE_KEYS = {
    "authorization",
    "proxy_authorization",
    "oai_sites_authorization",
    "api_key",
    "apikey",
    "x_api_key",
    "token",
    "password",
    "secret",
    "client_secret",
    "bridge_token",
    "access_token",
    "refresh_token",
    "headers",
    "credentials",
}
IMAGE_KEYS = {
    "image",
    "images",
    "image_url",
    "image_base64",
    "data_base64",
    "base64",
    "b64_json",
    "assets",
    "pixels",
    "pixel_values",
    "screenshot",
    "screenshots",
}
OMITTED_IMAGE = "[image payload omitted]"


def utc_iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="milliseconds")


def capture_timestamp(mtime_ns: int) -> tuple[str, int]:
    """Use the capture file's time, never the later upload/retry time."""
    if mtime_ns <= 0:
        raise ValueError("capture timestamp must be positive")
    sequence = mtime_ns // 1_000_000
    if sequence > 2**53 - 1:
        raise ValueError("capture sequence exceeds JSON integer precision")
    return utc_iso(mtime_ns / 1_000_000_000), sequence


def sanitize(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Retain nonimage model payloads and evidence while removing credentials."""
    if isinstance(value, dict):
        kind = str(value.get("type", "")).lower()
        if kind in {"image", "image_url", "input_image", "output_image"}:
            return {"type": "image_omitted", "detail": OMITTED_IMAGE}
        result = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            safe_key = sanitize(str(key), secrets)
            if normalized in PRIVATE_KEYS or normalized.endswith(("_api_key", "_token", "_secret")):
                result[safe_key] = "[REDACTED]"
            elif normalized in IMAGE_KEYS or "base64" in normalized:
                result[safe_key] = OMITTED_IMAGE
            else:
                result[safe_key] = sanitize(item, secrets)
        return result
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        text = value
        if len(text) >= 1024 and BASE64.fullmatch(text):
            return OMITTED_IMAGE
        for secret in secrets:
            if len(secret) >= 4:
                text = text.replace(secret, "[REDACTED]")
        text = SECRET.sub("[REDACTED]", text)
        text = INLINE_IMAGE.sub(OMITTED_IMAGE, text)
        return BASE64_RUN.sub(OMITTED_IMAGE, text)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def known_secrets() -> tuple[str, ...]:
    return tuple(
        value
        for key, value in os.environ.items()
        if len(value) >= 4
        and any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
    )


def minecraft_window(output: str) -> int | None:
    """Select a named Minecraft 1.21.1 window; never fall back to the desktop."""
    for match in re.finditer(r'^\s*(0x[0-9a-fA-F]+)\s+"([^"]+)"', output, re.MULTILINE):
        if re.match(r"Minecraft\*?\s+1\.21\.1(?:\s|$)", match.group(2)):
            return int(match.group(1), 16)
    return None


@dataclass(frozen=True)
class Frame:
    content: bytes
    mtime_ns: int

    @property
    def timestamp(self) -> str:
        return capture_timestamp(self.mtime_ns)[0]

    @property
    def sequence(self) -> int:
        return capture_timestamp(self.mtime_ns)[1]

    def fresh(self, now: float, max_age: float = 4) -> bool:
        age = now - self.mtime_ns / 1_000_000_000
        return -2 <= age <= max_age


class WindowCapture:
    def __init__(self, display: str, directory: Path, fps: float = 1):
        if not math.isfinite(fps) or not 1 <= fps <= 10:
            raise ValueError("capture FPS must be between 1 and 10")
        self.display = display
        self.directory = directory
        self.fps = fps
        self.process: asyncio.subprocess.Process | None = None
        self.path: Path | None = None
        self.next_start = 0.0
        self.generation = 0
        self.started_at = 0.0

    async def ensure_started(self) -> None:
        if self.process and self.process.returncode is None:
            try:
                age = time.time() - self.path.stat().st_mtime if self.path else math.inf
            except OSError:
                age = math.inf
            if time.monotonic() - self.started_at < 10 or age < 10:
                return
            LOG.warning("window_capture_stalled")
        if time.monotonic() < self.next_start:
            return
        await self.stop()
        self.next_start = time.monotonic() + 5
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "LANG",
                "LC_ALL",
                "XAUTHORITY",
                "LD_LIBRARY_PATH",
                "TMPDIR",
            }
        }
        env["DISPLAY"] = self.display
        try:
            lookup = await asyncio.create_subprocess_exec(
                "xwininfo",
                "-root",
                "-tree",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                stdout, _ = await asyncio.wait_for(lookup.communicate(), 3)
            except TimeoutError:
                lookup.kill()
                await lookup.wait()
                raise
            window = minecraft_window(stdout.decode(errors="replace"))
            if lookup.returncode or window is None:
                LOG.warning("minecraft_window_unavailable")
                return
            self.generation += 1
            self.path = self.directory / f"live-{self.generation}.jpg"
            self.process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "x11grab",
                "-window_id",
                str(window),
                "-framerate",
                f"{self.fps:g}",
                "-draw_mouse",
                "0",
                "-i",
                self.display,
                "-vf",
                "scale=960:-2",
                "-q:v",
                "7",
                "-threads",
                "1",
                "-f",
                "image2",
                "-update",
                "1",
                "-atomic_writing",
                "1",
                str(self.path),
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.started_at = time.monotonic()
        except (OSError, TimeoutError):
            LOG.warning("window_capture_start_failed")

    def latest(self) -> Frame | None:
        if not self.path or not self.process or self.process.returncode is not None:
            return None
        try:
            # Atomic rename plus fstat on the opened descriptor keeps bytes/time paired.
            with self.path.open("rb") as stream:
                mtime_ns = os.fstat(stream.fileno()).st_mtime_ns
                content = stream.read(MAX_FRAME + 1)
            if (
                len(content) > MAX_FRAME
                or not content.startswith(b"\xff\xd8")
                or not content.endswith(b"\xff\xd9")
            ):
                return None
            return Frame(content, mtime_ns)
        except OSError:
            return None

    async def stop(self) -> None:
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self.path:
            with contextlib.suppress(OSError):
                self.path.unlink()
        self.path = None


class JournalTail:
    """Read only appended complete JSONL records; retain sanitized events in memory."""

    def __init__(self, path: Path, secrets: tuple[str, ...] = ()):
        self.path = path
        self.secrets = secrets
        self.offset = 0
        self.identity: tuple[int, int] | None = None
        self.events: list[dict] = []
        self.bytes = 0
        self.invalid_records = 0
        self.overflow = False

    def read(self) -> bool:
        try:
            info = self.path.stat()
            identity = (info.st_dev, info.st_ino)
            if self.identity != identity or info.st_size < self.offset:
                self.offset = self.bytes = self.invalid_records = 0
                self.events = []
                self.overflow = False
                self.identity = identity
            if self.overflow:
                return False
            read_bytes = 0
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                while read_bytes < MAX_READ:
                    line = stream.readline(MAX_RAW_LINE + 1)
                    if not line:
                        return True
                    if len(line) > MAX_RAW_LINE:
                        self.overflow = True
                        return False
                    if not line.endswith(b"\n"):
                        return False  # Writer may still be appending this one record.
                    self.offset += len(line)
                    read_bytes += len(line)
                    try:
                        event = sanitize(json.loads(line), self.secrets)
                        if not isinstance(event, dict):
                            raise TypeError("journal record is not an object")
                    except (ValueError, TypeError, RecursionError):
                        self.invalid_records += 1
                        continue
                    self.bytes += len(encoded(event)) + 1
                    if self.bytes > MAX_RUN:
                        self.overflow = True
                        return False
                    self.events.append(event)
            return self.offset >= info.st_size
        except OSError:
            return False


class CloudUploader:
    def __init__(
        self, client: httpx.AsyncClient, site_url: str, site_token: str, ingest_token: str
    ):
        self.client = client
        self.site_url = site_url
        self.headers = {
            "OAI-Sites-Authorization": "Bearer " + site_token,
            "Authorization": "Bearer " + ingest_token,
        }
        self.cooldown_until = 0.0
        self.failures = 0

    async def post(self, path: str, **kwargs) -> bool:
        if time.monotonic() < self.cooldown_until:
            return False
        for attempt in range(2):
            try:
                response = await self.client.post(
                    self.site_url + path,
                    headers={
                        **self.headers,
                        **({"Content-Type": "application/json"} if "content" in kwargs else {}),
                    },
                    follow_redirects=False,
                    **kwargs,
                )
                if 200 <= response.status_code < 300:
                    self.failures = 0
                    return True
                if response.status_code == 429:
                    try:
                        delay = max(1, float(response.headers.get("Retry-After", "5")))
                    except ValueError:
                        delay = 5
                    self.cooldown_until = time.monotonic() + delay
                    LOG.warning("cloud_rate_limited")
                    return False
                if response.status_code < 500:
                    LOG.warning("cloud_upload_rejected status=%d", response.status_code)
                    break
            except httpx.HTTPError:
                pass  # Never log request objects, headers, or credential-bearing exceptions.
            if attempt == 0:
                await asyncio.sleep(0.25)
        self.failures += 1
        self.cooldown_until = time.monotonic() + min(30, 2 ** min(self.failures, 5))
        LOG.warning("cloud_upload_failed")
        return False


ENGINEERING_ACTIONS = frozenset({"prepare_investigation", "investigate"})
ENGINEERING_PUBLIC_FIELDS = frozenset(
    {
        "run_id",
        "digest_hash",
        "status",
        "max_acu_limit",
        "created_at",
        "updated_at",
        "session_id",
        "url",
        "status_detail",
        "error",
        "structured_output",
    }
)
COMMAND_ACTIONS = frozenset({"start_new", "resume", "pause", "stop", "voice"}) | ENGINEERING_ACTIONS
COMMAND_STEPS = {
    "start_new": ("new-run",),
    "resume": ("start",),
    "pause": ("pause",),
    "stop": ("finish",),
}


class CommandLedger:
    """Atomic, process-locked claims; uncertain requests are never replayed."""

    def __init__(self, runs_dir: Path):
        self.directory = runs_dir.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / ".broadcast-command-state.json"
        self.lock_path = self.directory / ".broadcast-command-state.lock"

    @contextlib.contextmanager
    def locked(self):
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            os.close(descriptor)

    def read(self) -> dict[str, dict]:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
        except FileNotFoundError:
            return {}
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Command ledger exceeds its bounded size")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"version", "commands"}
            or value["version"] != 1
        ):
            raise ValueError("Invalid command ledger format")
        commands = value["commands"]
        if not isinstance(commands, dict) or len(commands) > 4096:
            raise ValueError("Invalid command ledger records")
        for command_id, record in commands.items():
            if not isinstance(record, dict) or command_id != record.get("id"):
                raise ValueError("Invalid command ledger identity")
            try:
                if str(uuid.UUID(command_id)) != command_id:
                    raise ValueError("Noncanonical command UUID")
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError("Invalid command ledger UUID") from error
            if (
                record.get("action") not in COMMAND_ACTIONS
                or record.get("status") not in {"claimed", "completed", "failed"}
                or type(record.get("reported")) is not bool
            ):
                raise ValueError("Invalid command ledger status")
            if not isinstance(record.get("created_at"), str) or not isinstance(
                record.get("expires_at"), str
            ):
                raise TypeError("Invalid command ledger timestamps")
            if record["status"] != "claimed" and (
                not isinstance(record.get("detail"), str) or len(record["detail"]) > 300
            ):
                raise ValueError("Invalid command ledger result")
        return commands

    def write(self, commands: dict[str, dict]) -> None:
        if len(commands) > 4096:
            raise ValueError("Command ledger is full; inspect its completed records")
        raw = encoded({"version": 1, "commands": commands})
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Command ledger exceeds its bounded size")
        descriptor, name = tempfile.mkstemp(prefix=".broadcast-command-state-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name)


class CommandConsumer:
    """Consume authenticated run controls, with durable at-most-once dispatch.

    A crash or timeout can leave the HTTP outcome uncertain. Such a command is
    reported failed and requires a new explicit operator command; it is never
    automatically dispatched again. No command creates or resets a game world.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        controller_url: str,
        uploader: CloudUploader,
        runs_dir: Path,
        secrets: tuple[str, ...] = (),
        poll_interval: float = 0.5,
    ):
        local = urlparse(controller_url)
        if (
            local.scheme not in {"http", "https"}
            or local.hostname not in {"localhost", "127.0.0.1", "::1"}
            or any((local.username, local.password, local.query, local.fragment))
        ):
            raise ValueError("Command controller must be a credential-free loopback URL")
        remote = urlparse(uploader.site_url)
        if (
            remote.scheme != "https"
            or not remote.hostname
            or any((remote.username, remote.password, remote.query, remote.fragment))
        ):
            raise ValueError("Command cloud service must be a credential-free HTTPS URL")
        if not math.isfinite(poll_interval) or not 0.1 <= poll_interval <= 5:
            raise ValueError("Command poll interval must be between 0.1 and 5 seconds")
        self.client = client
        self.controller_url = controller_url.rstrip("/")
        self.uploader = uploader
        self.secrets = secrets
        self.poll_interval = poll_interval
        self.ledger = CommandLedger(runs_dir)
        self._lock = asyncio.Lock()

    @staticmethod
    def _timestamp(value: Any) -> float:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("Invalid command timestamp")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("Command timestamp must be UTC")
        return parsed.timestamp()

    def _command(self, value: Any) -> dict:
        if not isinstance(value, dict):
            raise TypeError("Invalid command")
        identifier = value.get("id")
        if not isinstance(identifier, str) or str(uuid.UUID(identifier)) != identifier:
            raise ValueError("Invalid command UUID")
        if value.get("action") not in COMMAND_ACTIONS or value.get("status") != "pending":
            raise ValueError("Invalid pending command")
        created = self._timestamp(value.get("created_at"))
        expires = self._timestamp(value.get("expires_at"))
        now = time.time()
        if not created <= now < expires or not 0 < expires - created <= 30.001:
            raise ValueError("Command is expired, future-dated, or exceeds its maximum lifetime")
        result = {key: value[key] for key in ("id", "action", "created_at", "expires_at")}
        if value["action"] == "voice":
            from .voice import validate_command

            voice = validate_command(
                {
                    "command_id": identifier,
                    **{
                        key: value.get(key)
                        for key in ("text", "run_id", "session_id", "utterance_id")
                    },
                }
            )
            if "run_id" not in value:
                raise ValueError("Voice commands require an explicit run identity")
            result.update(
                {key: voice[key] for key in ("text", "run_id", "session_id", "utterance_id")}
            )
        if value["action"] in ENGINEERING_ACTIONS:
            run_id = value.get("run_id")
            if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
                raise ValueError("Invalid investigation run ID")
            result["run_id"] = run_id
            if value["action"] == "investigate":
                digest = value.get("digest_hash")
                if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError("Invalid investigation digest")
                result["digest_hash"] = digest
        return result

    async def _fetch(self) -> Any:
        try:
            response = await self.client.get(
                self.uploader.site_url + "/api/commands/next",
                headers=self.uploader.headers,
                timeout=5,
                follow_redirects=False,
            )
            if response.status_code != 200 or len(response.content) > 16_384:
                return None
            payload = response.json()
            return payload.get("command") if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError):
            LOG.warning("command_poll_failed")
            return None

    async def _report(self, record: dict) -> bool:
        result = {
            key: record[key] for key in ("status", "detail", "run_id", "data") if key in record
        }
        try:
            response = await self.client.post(
                self.uploader.site_url + f"/api/commands/{record['id']}/result",
                headers=self.uploader.headers,
                json=result,
                timeout=5,
                follow_redirects=False,
            )
            if 200 <= response.status_code < 300 and len(response.content) <= 40 * 1024:
                value = response.json()
                return isinstance(value, dict) and value.get("ok") is True
        except (httpx.HTTPError, ValueError):
            pass
        LOG.warning("command_result_upload_failed")
        return False

    async def _run_id(self) -> str | None:
        try:
            response = await self.client.get(
                self.controller_url + "/api/state",
                timeout=2,
                follow_redirects=False,
            )
            if response.status_code == 200 and len(response.content) <= MAX_METADATA:
                value = response.json().get("run_id")
                if isinstance(value, str) and RUN_ID.fullmatch(value):
                    return value
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        return None

    async def _execute(self, record: dict, commands: dict[str, dict]) -> None:
        completed_steps = []
        detail = ""
        engineering = record["action"] in ENGINEERING_ACTIONS
        voice = record["action"] == "voice"
        steps = (
            ("voice",)
            if voice
            else (
                (
                    f"devin/{record['run_id']}/{'brief' if record['action'] == 'prepare_investigation' else 'investigate'}",
                )
                if engineering
                else COMMAND_STEPS[record["action"]]
            )
        )
        for step in steps:
            remaining = self._timestamp(record["expires_at"]) - time.time()
            if remaining <= 0:
                detail = (
                    "Command expired before the next controller action; inspect the current run."
                )
                break
            record["stage"] = step
            self.ledger.write(commands)  # The next mutation is claimed durably before dispatch.
            remaining = self._timestamp(record["expires_at"]) - time.time()
            if remaining <= 0:
                detail = (
                    "Command expired while persisting its claim; no further action was dispatched."
                )
                break
            try:
                response = await self.client.post(
                    self.controller_url + "/api/" + step,
                    **(
                        {"json": {"digest_hash": record["digest_hash"]}}
                        if record["action"] == "investigate"
                        else {
                            "json": {
                                "command_id": record["id"],
                                **{
                                    key: record[key]
                                    for key in ("text", "run_id", "session_id", "utterance_id")
                                },
                            }
                        }
                        if voice
                        else {"json": {"command_id": record["id"]}}
                        if record["action"] == "start_new"
                        else {}
                    ),
                    timeout=min(8 if voice else 5, remaining),
                    follow_redirects=False,
                )
                if not 200 <= response.status_code < 300:
                    explanation = ""
                    if len(response.content) <= 16_384:
                        with contextlib.suppress(ValueError, AttributeError):
                            message = response.json().get("detail", "")
                            if isinstance(message, str):
                                explanation = ": " + str(sanitize(message, self.secrets))[:160]
                    detail = (
                        f"Controller rejected {step} (HTTP {response.status_code}){explanation}"
                    )
                    break
                if (
                    len(response.content) > (32 * 1024 if engineering else 16_384)
                    or response.json().get("ok") is not True
                ):
                    detail = (
                        "Controller response did not confirm execution; inspect the current run."
                    )
                    break
                if engineering:
                    data = response.json().get("data")
                    if not isinstance(data, dict) or data.get("run_id") != record["run_id"]:
                        raise ValueError("Invalid investigation response")
                    safe = sanitize(data, self.secrets)
                    if record["action"] == "prepare_investigation":
                        prompt = safe.get("prompt")
                        if not isinstance(prompt, str) or hashlib.sha256(
                            prompt.encode()
                        ).hexdigest() != safe.get("digest_hash"):
                            raise ValueError("Investigation preview changed during redaction")
                    elif safe.get("digest_hash") != record["digest_hash"]:
                        raise ValueError("Investigation digest mismatch")
                    record["data"] = safe
                elif voice:
                    data = response.json().get("data")
                    if (
                        not isinstance(data, dict)
                        or set(data) - {"intent", "reply", "guidance_id"}
                        or data.get("intent")
                        not in {"pause", "stop", "resume", "guidance", "question"}
                        or not isinstance(data.get("reply"), str)
                        or not 1 <= len(data["reply"]) <= 2000
                    ):
                        raise ValueError("Invalid voice command result")
                    if (
                        "guidance_id" in data
                        and str(uuid.UUID(data["guidance_id"])) != data["guidance_id"]
                    ):
                        raise ValueError("Invalid guidance identity")
                    record["data"] = sanitize(data, self.secrets)
            except (httpx.HTTPError, ValueError, AttributeError, TypeError):
                detail = "Controller response unavailable; the command may have applied. Inspect the run before retrying."
                break
            completed_steps.append(step)
        if detail:
            if completed_steps:
                detail = "Partially applied: " + ", ".join(completed_steps) + ". " + detail
            record.update(status="failed", detail=str(sanitize(detail, self.secrets))[:300])
        else:
            record.update(
                status="completed",
                detail={
                    "start_new": "Fresh-world preparation accepted. Watch launch progress before gameplay starts.",
                    "resume": "Resumed the current recorded run.",
                    "pause": "Paused gameplay and released agent controls.",
                    "stop": "Stopped and archived the current recorded run.",
                    "prepare_investigation": "Prepared the exact redacted investigation brief. Review it before creating a 2-ACU session.",
                    "investigate": "Investigation submission recorded. Session status and findings appear in this run's engineering panel.",
                    "voice": "Voice command processed. See its recorded response.",
                }[record["action"]],
            )
        if not engineering and not voice:
            record["run_id"] = None if record["action"] == "start_new" else await self._run_id()
        record["reported"] = False
        self.ledger.write(commands)

    async def poll_once(self) -> None:
        async with self._lock:
            # Keep competing publisher processes from dispatching controls while
            # this process is waiting for a local HTTP response.
            with self.ledger.locked() as acquired:
                if not acquired:
                    return
                commands = self.ledger.read()
                attempted = set()
                for record in commands.values():
                    if record["status"] == "claimed":
                        record.update(
                            status="failed",
                            reported=False,
                            run_id=record.get("run_id")
                            if record["action"] in ENGINEERING_ACTIONS
                            or record["action"] == "voice"
                            else None,
                            detail="Previous execution may have applied before interruption. Inspect the run and send a new command; it was not replayed.",
                        )
                        self.ledger.write(commands)
                    if not record["reported"]:
                        record["reported"] = await self._report(record)
                        attempted.add(record["id"])
                        self.ledger.write(commands)
                        break  # Bound result backfill so new pause commands can be received.
                value = await self._fetch()
                if value is None:
                    return
                try:
                    command = self._command(value)
                except (ValueError, TypeError, AttributeError):
                    LOG.warning("command_rejected_invalid_or_expired")
                    return
                identifier = command["id"]
                if identifier in commands:
                    record = commands[identifier]
                    if any(record[key] != command[key] for key in command):
                        LOG.warning("command_identity_conflict")
                        return
                    if identifier not in attempted:
                        record["reported"] = await self._report(record)
                        self.ledger.write(commands)
                    return
                record = {**command, "status": "claimed", "reported": False, "stage": "claimed"}
                commands[identifier] = record
                self.ledger.write(commands)
                await self._execute(record, commands)
                record["reported"] = await self._report(record)
                self.ledger.write(commands)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.poll_once()
            except (OSError, ValueError, TypeError):
                # A corrupt/unwritable ledger cannot safely establish ownership.
                LOG.error("command_consumer_disabled_persistence_error")
                await stop.wait()
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass


class RunPublisher:
    def __init__(self, directory: Path, uploader: CloudUploader, secrets: tuple[str, ...]):
        self.directory = directory.resolve()
        self.uploader = uploader
        self.secrets = secrets
        self.pending_backfill: list[str] | None = None
        self.tails: dict[str, JournalTail] = {}
        self.uploaded: dict[str, tuple] = {}
        self.previous_current: str | None = None
        self.seen_runs: set[str] = set()

    async def sync(self, summaries: list[dict], current_id: str | None) -> None:
        if not summaries:
            return
        by_id = {item.get("id"): item for item in summaries if isinstance(item, dict)}
        if self.pending_backfill is None:
            self.pending_backfill = []
        # Every advertised recent run gets a detail upload, including newly discovered runs.
        for item in summaries[:20]:
            run_id = item.get("id")
            if isinstance(run_id, str) and run_id not in self.seen_runs:
                self.pending_backfill.append(run_id)
                self.seen_runs.add(run_id)
        # Completed gameplay journals can still receive asynchronous Devin findings.
        engineering_runs = [
            item
            for item in by_id
            if isinstance(item, str)
            and RUN_ID.fullmatch(item)
            and (self.directory / item / "engineering" / "latest.json").is_file()
        ][:20]
        targets = list(
            dict.fromkeys(
                [current_id, self.previous_current, *self.pending_backfill, *engineering_runs]
            )
        )
        self.previous_current = current_id
        for run_id in targets:
            if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id) or run_id not in by_id:
                continue
            run_path = (self.directory / run_id).resolve()
            if run_path.parent != self.directory:
                continue
            tail = self.tails.setdefault(
                run_id, JournalTail(run_path / "events.jsonl", self.secrets)
            )
            complete = await asyncio.to_thread(tail.read)
            if not complete:
                if tail.overflow:
                    LOG.warning("run_journal_exceeds_upload_limit run=%s", run_id)
                    if run_id in self.pending_backfill:
                        self.pending_backfill.remove(run_id)
                continue
            summary = sanitize(by_id[run_id], self.secrets)
            engineering = None
            investigation_path = run_path / "engineering" / "latest.json"
            if investigation_path.is_file() and not investigation_path.is_symlink():
                try:
                    with investigation_path.open("rb") as stream:
                        raw_engineering = stream.read(96 * 1024 + 1)
                    if len(raw_engineering) <= 96 * 1024:
                        candidate = json.loads(raw_engineering)
                        if isinstance(candidate, dict) and candidate.get("run_id") == run_id:
                            engineering = sanitize(
                                {
                                    key: value
                                    for key, value in candidate.items()
                                    if key in ENGINEERING_PUBLIC_FIELDS
                                },
                                self.secrets,
                            )
                except (ValueError, OSError):
                    pass
            signature = (tail.offset, encoded(summary), encoded(engineering))
            if self.uploaded.get(run_id) == signature:
                if run_id in self.pending_backfill:
                    self.pending_backfill.remove(run_id)
                continue
            payload = encoded(
                {
                    "summary": summary,
                    "events": tail.events,
                    **({"engineering": engineering} if engineering else {}),
                }
            )
            if len(payload) > MAX_RUN:
                LOG.warning("run_payload_exceeds_upload_limit run=%s", run_id)
                continue
            if await self.uploader.post(
                "/api/runs/" + run_id,
                content=payload,
            ):
                self.uploaded[run_id] = signature
                if run_id in self.pending_backfill:
                    self.pending_backfill.remove(run_id)
                if run_id != current_id:
                    self.tails.pop(run_id, None)
            elif run_id not in self.pending_backfill:
                self.pending_backfill.append(run_id)
        # Only current/previous/backfill journals need an in-memory tail.
        keep = {current_id, self.previous_current, *self.pending_backfill}
        self.tails = {key: value for key, value in self.tails.items() if key in keep}


def envelope(
    state: dict,
    runs: list[dict],
    frame: Frame | None,
    feed_status: str,
    now: float,
) -> dict:
    return {
        "updated_at": utc_iso(now),
        "captured_at": frame.timestamp if frame else None,
        "capture_seq": frame.sequence if frame else None,
        "feed_status": feed_status,
        "source": "minecraft-window",
        "state": state,
        "runs": runs[:20],
    }


async def wait_for_stop(stop: asyncio.Event, delay: float) -> None:
    if delay <= 0:
        return
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass


class TelemetryCache:
    """Poll local telemetry separately so a slow controller cannot stall video."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        controller_url: str,
        secrets: tuple[str, ...],
        interval: float,
    ):
        self.client = client
        self.controller_url = controller_url
        self.secrets = secrets
        self.interval = interval
        self.state: dict = {
            "mode": "live",
            "status": "unavailable",
            "connected": False,
            "keys": [],
            "action": None,
        }
        self.runs: list[dict] = []
        self.updated_at: float | None = None
        self.controller_ok = False
        self.next_runs = 0.0

    def snapshot(self, now: float) -> tuple[dict, bool]:
        fresh = self.updated_at is not None and 0 <= now - self.updated_at <= max(
            4, self.interval * 2
        )
        if self.controller_ok and fresh:
            return self.state, True
        return {
            **self.state,
            "status": "unavailable",
            "connected": False,
            "keys": [],
            "action": None,
        }, False

    async def refresh(self) -> None:
        try:
            response = await self.client.get(self.controller_url + "/api/state", timeout=2)
            response.raise_for_status()
            fresh_state = response.json()
            if not isinstance(fresh_state, dict):
                raise TypeError("invalid controller state")
            self.state = sanitize(fresh_state, self.secrets)
            self.updated_at = time.monotonic()
            self.controller_ok = True
        except (httpx.HTTPError, ValueError, TypeError):
            self.controller_ok = False
        started = time.monotonic()
        if started >= self.next_runs:
            self.next_runs = started + 5
            try:
                response = await self.client.get(self.controller_url + "/api/runs", timeout=2)
                response.raise_for_status()
                value = response.json().get("runs", [])
                if not isinstance(value, list):
                    raise TypeError("invalid run index")
                self.runs = [
                    sanitize(item, self.secrets) for item in value[:20] if isinstance(item, dict)
                ]
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                pass

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = time.monotonic()
            await self.refresh()
            await wait_for_stop(stop, max(0, self.interval - (time.monotonic() - started)))


class FramePublisher:
    """Send newest frames only; heartbeats preserve the last capture timestamp."""

    def __init__(self, uploader: CloudUploader, heartbeat_interval: float = 1):
        self.uploader = uploader
        self.heartbeat_interval = heartbeat_interval
        self.last_uploaded: Frame | None = None
        self.next_heartbeat = 0.0
        self.stats_started = time.monotonic()
        self.uploads = 0
        self.upload_ms = 0.0
        self.upload_bytes = 0

    async def send(
        self,
        latest: Frame | None,
        state: dict,
        runs: list[dict],
        controller_ok: bool,
        *,
        now: float,
        monotonic: float,
    ) -> bool:
        fresh = latest is not None and latest.fresh(now)
        candidate = (
            latest
            if fresh
            and (self.last_uploaded is None or latest.sequence > self.last_uploaded.sequence)
            else None
        )
        if candidate is None and monotonic < self.next_heartbeat:
            return False
        shown = candidate or self.last_uploaded
        feed_status = (
            "online" if fresh and shown and shown.fresh(now) else "stale" if shown else "offline"
        )
        if not controller_ok:
            feed_status = "controller-offline"
        metadata = envelope(state, runs, shown, feed_status, now)
        body = encoded(metadata)
        if len(body) > MAX_METADATA:
            metadata["state"] = {**state, "events": [], "latency_history": {}}
            body = encoded(metadata)
        self.next_heartbeat = monotonic + self.heartbeat_interval
        if len(body) > MAX_METADATA:
            LOG.warning("metadata_exceeds_upload_limit")
            return False
        files = {"metadata": (None, body, "application/json")}
        if candidate:
            files["frame"] = ("frame.jpg", candidate.content, "image/jpeg")
        started = time.monotonic()
        uploaded = await self.uploader.post("/api/ingest", files=files)
        finished = time.monotonic()
        if uploaded and candidate:
            self.last_uploaded = candidate
            self.uploads += 1
            self.upload_ms += (finished - started) * 1000
            self.upload_bytes += len(candidate.content)
        measured = finished - self.stats_started
        if measured >= 10:
            if self.uploads:
                LOG.info(
                    "camera_upload fps=%.2f mean_ms=%.1f jpeg_bytes=%.0f",
                    self.uploads / measured,
                    self.upload_ms / self.uploads,
                    self.upload_bytes / self.uploads,
                )
            self.stats_started = finished
            self.uploads = self.upload_bytes = 0
            self.upload_ms = 0.0
        return uploaded


async def publish(args: argparse.Namespace) -> None:
    from .broadcast_video import LiveVideoPublisher

    site_token = os.environ.get("HOLOSPEED_SITE_TOKEN", "")
    ingest_token = os.environ.get("HOLOSPEED_INGEST_TOKEN", "")
    if not site_token or not ingest_token:
        raise ValueError("HOLOSPEED_SITE_TOKEN and HOLOSPEED_INGEST_TOKEN are required")
    secrets = known_secrets()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    timeout = httpx.Timeout(5, connect=3)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        uploader = CloudUploader(client, args.site_url, site_token, ingest_token)
        journals = RunPublisher(args.runs_dir, uploader, secrets)
        telemetry = TelemetryCache(client, args.controller_url, secrets, args.interval)
        frames = FramePublisher(uploader, heartbeat_interval=args.interval)
        commands = CommandConsumer(client, args.controller_url, uploader, args.runs_dir, secrets)
        video = LiveVideoPublisher(
            client, args.site_url, site_token, ingest_token, args.display, args.video_fps
        )
        with tempfile.TemporaryDirectory(prefix="holospeed-broadcast-") as directory:
            capture = WindowCapture(args.display, Path(directory), fps=args.fps)
            telemetry_task = asyncio.create_task(telemetry.run(stop))
            command_task = asyncio.create_task(commands.run(stop))
            video_task = asyncio.create_task(video.run(stop))
            next_journal = 0.0
            journal_task: asyncio.Task | None = None
            try:
                while not stop.is_set():
                    started = time.monotonic()
                    await capture.ensure_started()
                    state, controller_ok = telemetry.snapshot(time.monotonic())
                    await frames.send(
                        capture.latest(),
                        state,
                        telemetry.runs,
                        controller_ok,
                        now=time.time(),
                        monotonic=time.monotonic(),
                    )
                    if journal_task and journal_task.done():
                        with contextlib.suppress(asyncio.CancelledError):
                            error = journal_task.exception()
                            if error:
                                LOG.warning("run_publication_failed type=%s", type(error).__name__)
                        journal_task = None
                    if started >= next_journal and journal_task is None:
                        next_journal = started + 5
                        journal_task = asyncio.create_task(
                            journals.sync(telemetry.runs, state.get("run_id"))
                        )
                    # Slow uploads drop intermediate captures instead of building a queue.
                    await wait_for_stop(stop, max(0, 1 / args.fps - (time.monotonic() - started)))
            finally:
                tasks = [telemetry_task, command_task, video_task]
                if journal_task is not None:
                    tasks.append(journal_task)
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        LOG.warning("publisher_task_failed type=%s", type(result).__name__)
                await capture.stop()


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-url", default="http://127.0.0.1:8768")
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--display", default=":97")
    parser.add_argument(
        "--fps", type=float, default=1, help="JPEG fallback capture/upload rate, 1–10 FPS"
    )
    parser.add_argument(
        "--video-fps",
        type=float,
        default=None,
        help="H.264 capture rate (default: display refresh, or 60 FPS)",
    )
    parser.add_argument(
        "--interval", type=float, default=1, help="Telemetry/heartbeat interval in seconds"
    )
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    args = parser.parse_args(argv)
    local, remote = urlparse(args.controller_url), urlparse(args.site_url)
    if local.scheme not in {"http", "https"} or local.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        parser.error("--controller-url must target the loopback controller")
    if remote.scheme != "https" or not remote.hostname:
        parser.error("--site-url must use HTTPS")
    if any(url.username or url.password or url.query or url.fragment for url in (local, remote)):
        parser.error("URLs cannot contain credentials, query parameters, or fragments")
    if not math.isfinite(args.interval) or not 0.2 <= args.interval <= 60:
        parser.error("--interval must be between 0.2 and 60 seconds")
    if not math.isfinite(args.fps) or not 1 <= args.fps <= 10:
        parser.error("--fps must be between 1 and 10")
    if args.video_fps is not None and (
        not math.isfinite(args.video_fps) or not 1 <= args.video_fps <= 240
    ):
        parser.error("--video-fps must be between 1 and 240")
    args.controller_url = args.controller_url.rstrip("/")
    args.site_url = args.site_url.rstrip("/")
    return args


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = arguments()
    try:
        asyncio.run(publish(args))
    except ValueError as error:
        # Only configuration errors constructed above reach this path.
        LOG.error("%s", error)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
