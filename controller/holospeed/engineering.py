"""Durable, operator-requested Devin jobs; never part of the gameplay loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .devin import (
    _ARTIFACT_PATTERN,
    _ASSIGNMENT_PATTERN,
    _LOCAL_PATH_PATTERN,
    _SECRET_PATTERN,
    MAX_ACU_LIMIT,
    _without_configured_secrets,
    build_analysis_prompt,
    get_session,
    submit_prompt,
)
from .recording import read_events

RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}-[a-f0-9]{8}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
PROVIDER_STATUS = {"new", "claimed", "running", "exit", "error", "suspended", "resuming"}
PUBLIC_LIMIT = 80 * 1024


def now() -> str:
    return datetime.now(UTC).isoformat()


def _read(path: Path, limit: int = 96 * 1024) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Engineering artifact exceeds its bounded size")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Invalid engineering artifact")  # noqa: TRY004 - invalid persisted data.
    return result


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".engineering-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _claim(path: Path, value: dict) -> bool:
    """Coordinate controllers sharing the same run directory before any paid call."""
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=True, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return True


def source_revision() -> str:
    instruction = "Inspect the current default branch and account for the recorded run revision."
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        revision = result.stdout.strip()
        if re.fullmatch(r"[a-f0-9]{40}", revision):
            return f"{instruction} Controller checkout HEAD: {revision}; local uncommitted changes may differ."
    except (OSError, subprocess.SubprocessError):
        pass
    return instruction + " The controller checkout revision is unavailable."


def compact(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "structured_output"}


class Engineering:
    def __init__(self, settings: Settings, *, submit=submit_prompt, poll=get_session):
        self.settings = settings
        self.submit = submit
        self.poll = poll
        self.lock = asyncio.Lock()
        self.tasks: dict[str, asyncio.Task] = {}

    def directory(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("Unknown run")
        root = self.settings.runs_dir.resolve()
        run = (root / run_id).resolve()
        if run.parent != root or not (run / "summary.json").is_file():
            raise ValueError("Unknown run")
        directory = run / "engineering"
        if directory.is_symlink():
            raise ValueError("Invalid engineering directory")
        return directory

    def latest(self, run_id: str) -> dict | None:
        directory = self.directory(run_id)
        path = directory / "latest.json"
        if path.exists():
            return _read(path)
        claim = directory / "submission-claim.json"
        if claim.exists():
            value = _read(claim)
            value.pop("owner_pid", None)
            value.update(
                status="unknown",
                error="Submission claim exists without a final receipt. Inspect Devin history; it will not be repeated.",
            )
            return value
        return None

    def _prepare(self, run_id: str) -> dict:
        directory = self.directory(run_id)
        run = directory.parent
        summary = _read(run / "summary.json", 256 * 1024)
        events = read_events(run / "events.jsonl", exclude_model=True)
        prompt = build_analysis_prompt(
            _without_configured_secrets(summary, self.settings),
            _without_configured_secrets(events, self.settings),
            {
                "source_revision": source_revision(),
                "analysis_focus": "Investigate this recorded run, cite evidence, and propose up to two focused regression-test patches and minimal improvements. Do not apply changes or run live sessions.",
            },
        )
        if len(prompt.encode()) > 24 * 1024:
            raise ValueError("Prepared brief exceeds its bounded size")
        digest_hash = hashlib.sha256(prompt.encode()).hexdigest()
        brief = {
            "run_id": run_id,
            "prompt": prompt,
            "digest_hash": digest_hash,
            "max_acu_limit": MAX_ACU_LIMIT,
        }
        _write(directory / f"brief-{digest_hash}.json", brief)
        existing = self.latest(run_id)
        return {
            **brief,
            "can_submit": bool(self.settings.devin_api_key and self.settings.devin_org_id),
            "existing": compact(existing) if existing else None,
        }

    async def prepare(self, run_id: str) -> dict:
        async with self.lock:
            return await asyncio.to_thread(self._prepare, run_id)

    async def investigate(self, run_id: str, digest_hash: str) -> dict:
        if not isinstance(digest_hash, str) or not HASH.fullmatch(digest_hash):
            raise ValueError("Invalid brief digest")
        async with self.lock:
            directory = self.directory(run_id)
            existing = self.latest(run_id)
            if existing:
                if existing["digest_hash"] != digest_hash:
                    raise ValueError(
                        "This run already has an investigation; inspect its existing session"
                    )
                return compact(
                    existing
                )  # No second billable session, even after an ambiguous exit.
            if not self.settings.devin_api_key or not self.settings.devin_org_id:
                raise ValueError("Devin credentials and organization must be configured")
            path = directory / f"brief-{digest_hash}.json"
            if not path.is_file():
                raise ValueError("Prepare and review this run's investigation brief first")
            brief = _read(path, 32 * 1024)
            if (
                brief.get("run_id") != run_id
                or brief.get("digest_hash") != digest_hash
                or brief.get("max_acu_limit") != MAX_ACU_LIMIT
                or not isinstance(brief.get("prompt"), str)
                or hashlib.sha256(brief["prompt"].encode()).hexdigest() != digest_hash
            ):
                raise ValueError("Prepared investigation brief changed; prepare it again")
            stamp = now()
            receipt = {
                "run_id": run_id,
                "digest_hash": digest_hash,
                "status": "submitting",
                "max_acu_limit": MAX_ACU_LIMIT,
                "created_at": stamp,
                "updated_at": stamp,
            }
            if not _claim(
                directory / "submission-claim.json", {**receipt, "owner_pid": os.getpid()}
            ):
                raise ValueError(
                    "This run's submission was already claimed. Inspect its existing investigation before retrying."
                )
            _write(
                directory / "latest.json", receipt
            )  # Durable claim BEFORE the external side effect.
            self.tasks[run_id] = asyncio.create_task(self._submit(run_id, brief["prompt"], receipt))
            return receipt

    def _text(self, value, limit: int) -> str:
        text = str(_without_configured_secrets(value, self.settings))
        for pattern in (
            _SECRET_PATTERN,
            _ASSIGNMENT_PATTERN,
            _ARTIFACT_PATTERN,
            _LOCAL_PATH_PATTERN,
        ):
            text = pattern.sub("[OMITTED]", text)
        return text[:limit]

    def _findings(self, value: dict) -> dict:
        findings = {"summary": self._text(value.get("summary", ""), 8000)}
        for key in ("likely_causes", "proposed_tests", "proposed_fixes", "missing_evidence"):
            findings[key] = (
                [
                    self._text(item, 2000)
                    for item in value.get(key, [])[:12]
                    if isinstance(item, str)
                ]
                if isinstance(value.get(key), list)
                else []
            )
        findings["test_patches"] = []
        for index, item in enumerate(
            value.get("test_patches", [])[:2] if isinstance(value.get("test_patches"), list) else []
        ):
            if not isinstance(item, dict):
                continue
            patch = {
                key: self._text(item.get(key, ""), limit)
                for key, limit in (
                    ("purpose", 2000),
                    ("code", 16000),
                    ("validation_status", 2000),
                )
            }
            # A relative repository path is useful evidence, unlike private absolute paths.
            candidate = _without_configured_secrets(item.get("relative_path", ""), self.settings)
            if (
                isinstance(candidate, str)
                and re.fullmatch(r"[A-Za-z0-9_.-][A-Za-z0-9_./-]{0,239}", candidate)
                and not any(part in {"", ".", ".."} for part in candidate.split("/"))
                and not _SECRET_PATTERN.search(candidate)
            ):
                patch["relative_path"] = candidate
            else:
                patch["relative_path"] = f"proposed-tests/proposal-{index + 1}.txt"
            findings["test_patches"].append(patch)
        while len(json.dumps(findings).encode()) > PUBLIC_LIMIT - 4096:
            arrays = [
                findings[key]
                for key in (
                    "test_patches",
                    "likely_causes",
                    "proposed_tests",
                    "proposed_fixes",
                    "missing_evidence",
                )
            ]
            longest = max(arrays, key=lambda array: len(json.dumps(array)))
            if not longest:
                findings["summary"] = findings["summary"][:2000]
                break
            longest.pop()
        return findings

    def _response(self, base: dict, response: dict) -> dict:
        result = {**base, "updated_at": now()}
        result.pop("error", None)
        session_id = response.get("session_id", base.get("session_id"))
        if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
            raise ValueError("Devin returned an invalid session identifier")
        result["session_id"] = session_id
        url = response.get("url", base.get("url"))
        parsed = urlparse(url) if isinstance(url, str) else None
        if (
            parsed
            and parsed.scheme == "https"
            and parsed.netloc == "app.devin.ai"
            and parsed.path == f"/sessions/{session_id}"
            and not parsed.query
            and not parsed.fragment
        ):
            result["url"] = url
        status = response.get("status")
        result["status"] = status if status in PROVIDER_STATUS else "created"
        if isinstance(response.get("status_detail"), str):
            result["status_detail"] = self._text(response["status_detail"], 100)
        if isinstance(response.get("structured_output"), dict):
            result["structured_output"] = self._findings(response["structured_output"])
        return result

    async def _submit(self, run_id: str, prompt: str, receipt: dict) -> None:
        directory = self.directory(run_id)
        try:
            response = await self.submit(self.settings, prompt)
            _write(
                directory / "creation-response.json",
                _without_configured_secrets(response, self.settings),
            )
            receipt = self._response(receipt, response)
        except asyncio.CancelledError:
            receipt.update(
                status="unknown",
                error="Controller stopped during submission. Inspect Devin history; this request will not be repeated.",
                updated_at=now(),
            )
            _write(directory / "latest.json", receipt)
            raise
        except Exception as error:  # noqa: BLE001 - every ambiguous creation needs a durable receipt.
            receipt.update(
                status="unknown",
                error=self._text(
                    f"Submission outcome needs review: {type(error).__name__}. Inspect Devin history before retrying.",
                    500,
                ),
                updated_at=now(),
            )
        _write(directory / "latest.json", receipt)

    async def poll_once(self) -> None:
        paths = sorted(self.settings.runs_dir.glob("*/engineering/latest.json"), reverse=True)[:100]
        for path in paths:
            run_id = path.parent.parent.name
            try:
                record = self.latest(run_id)
                if record["status"] == "submitting":
                    elsewhere_active = False
                    if run_id not in self.tasks:
                        claim = path.parent / "submission-claim.json"
                        if claim.is_file():
                            owner = _read(claim).get("owner_pid")
                            if type(owner) is int and owner > 0:
                                try:
                                    os.kill(owner, 0)
                                    elsewhere_active = True
                                except (ProcessLookupError, PermissionError):
                                    pass
                    if not elsewhere_active and (
                        run_id not in self.tasks or self.tasks[run_id].done()
                    ):
                        record.update(
                            status="unknown",
                            error="Submission was interrupted. Inspect Devin history; it will not be repeated.",
                            updated_at=now(),
                        )
                        _write(path, record)
                    continue
                if (
                    not record.get("session_id")
                    or record.get("status_detail") == "finished"
                    or record["status"] in {"exit", "error"}
                ):
                    continue
                response = await self.poll(self.settings, record["session_id"])
                _write(
                    path.parent / "session-response.json",
                    _without_configured_secrets(response, self.settings),
                )
                _write(path, self._response(record, response))
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
                continue  # Keep last recorded evidence; do not restamp failed polls as fresh.

    async def run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(15)

    async def close(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        # A provider suppressing cancellation must not stall controller shutdown.
        if self.tasks:
            done, _ = await asyncio.wait(self.tasks.values(), timeout=2)
            for task in done:
                if not task.cancelled():
                    task.exception()
