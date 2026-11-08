from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from .models import Observation

SECRET = re.compile(r"(?:Bearer\s+\S+|hk-[\w-]+|cog_[\w-]+|apikey_[\w-]+|sk_[\w-]+)", re.IGNORECASE)
SECRET_KEYS = {"authorization", "api_key", "apikey", "token", "password", "bridge_token"}


def redact(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return f"[nonfinite:{value}]"
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if k.lower() in SECRET_KEYS else redact(v) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return SECRET.sub("[REDACTED]", value)
    return value


class RunRecorder:
    """Append and flush every event before permitting the next side effect."""

    def __init__(self, root: Path, mode: str, config: dict[str, Any]):
        self.id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.path = root / self.id
        (self.path / "frames").mkdir(parents=True)
        self.started = time.monotonic()
        self.sequence = 0
        self.closed = False
        self.stream = (self.path / "events.jsonl").open("a", encoding="utf-8")
        self.summary = {
            "id": self.id,
            "mode": mode,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
            "duration_ms": 0,
            "metrics": {},
            "milestones": [],
            "config": redact(config),
            "protocol": 1,
        }
        self.checkpoint()
        self.log("run_started", {"mode": mode, "config": config})

    @property
    def elapsed_ms(self) -> int:
        return (
            self.summary["duration_ms"]
            if self.closed
            else round((time.monotonic() - self.started) * 1000)
        )

    def log(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        if self.closed:
            raise RuntimeError("run is already closed")
        self.sequence += 1
        event = {
            "seq": self.sequence,
            "type": kind,
            "elapsed_ms": self.elapsed_ms,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": redact(data),
        }
        self.stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        return event

    def save_observation(self, observation: Observation) -> dict[str, Any]:
        if len(observation.image_base64) > 16_000_000:
            raise ValueError("screenshot exceeds 12 MB")
        raw = base64.b64decode(observation.image_base64, validate=True)
        with Image.open(io.BytesIO(raw)) as im:
            if im.size != (observation.width, observation.height):
                raise ValueError("screenshot dimensions do not match metadata")
            if im.format not in {"PNG", "JPEG"}:
                raise ValueError("unsupported screenshot format")
            if (im.format == "PNG") != (observation.mime_type == "image/png"):
                raise ValueError("screenshot MIME type does not match image")
            im.verify()
        digest = hashlib.sha256(raw).hexdigest()
        filename = f"{self.sequence + 1:06d}-{digest[:12]}.{'png' if observation.mime_type == 'image/png' else 'jpg'}"
        with (self.path / "frames" / filename).open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        metadata = observation.model_dump(exclude={"image_base64"})
        metadata.update({"sha256": digest, "frame_url": f"/api/runs/{self.id}/frames/{filename}"})
        return self.log("observation", metadata)

    def checkpoint(self, **updates: Any) -> None:
        self.summary.update(redact(updates))
        self.summary["duration_ms"] = self.elapsed_ms
        temp = self.path / "summary.json.tmp"
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(self.summary, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(self.path / "summary.json")

    def finish(self, status: str, **updates: Any) -> None:
        if self.closed:
            return
        self.log("run_finished", {"status": status})
        self.checkpoint(status=status, **updates)
        self.closed = True
        self.stream.close()


def read_events(path: Path, *, exclude_model: bool = False) -> list[dict[str, Any]]:
    events = []
    if not path.exists():
        return events
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
                if not exclude_model or not event.get("type", "").startswith("model_"):
                    events.append(event)
            except json.JSONDecodeError:
                # A process may have died during its last append; preserve prior evidence.
                continue
    return events
