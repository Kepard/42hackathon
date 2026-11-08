"""Window-only H.264 live video; ordered fMP4 HLS publication for the dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import math
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from .broadcast import minecraft_window, wait_for_stop

LOG = logging.getLogger("holospeed.broadcast.video")
MAX_ASSET = 4 * 1024 * 1024
MAX_PLAYLIST = 64 * 1024
MAX_VIDEO_AGE = 8
UPLOAD_CONCURRENCY = 6
ASSET = re.compile(r"(?:init-([a-f0-9]{32})\.mp4|segment-([a-f0-9]{32})-(\d{1,20})\.m4s)\Z")


def display_refresh(output: str) -> float:
    """Use xrandr's active refresh marker; virtual displays often report zero."""
    for match in re.finditer(r"(?:^|\s)(\d+(?:\.\d+)?)\*", output):
        value = float(match.group(1))
        if 1 <= value <= 240:
            return value
    return 60.0


@dataclass(frozen=True)
class Playlist:
    content: bytes
    generation: str
    assets: tuple[str, ...]
    captured_at: float


def parse_playlist(content: bytes) -> Playlist:
    """Validate local ffmpeg output before turning any filename into a request."""
    if not content or len(content) > MAX_PLAYLIST:
        raise ValueError("invalid playlist size")
    text = content.decode("utf-8")
    if not text.startswith("#EXTM3U\n"):
        raise ValueError("invalid HLS header")
    generation = None
    assets = []
    timestamp = duration = None
    captured_at = 0.0
    max_duration = 0.0
    target_duration = None
    segments = 0
    for line in text.splitlines():
        if line.startswith("#EXT-X-TARGETDURATION:"):
            if target_duration is not None or not line.split(":", 1)[1].isdigit():
                raise ValueError("invalid HLS target duration")
            target_duration = int(line.split(":", 1)[1])
            continue
        if line.startswith("#EXT-X-MAP:"):
            match = re.fullmatch(r'#EXT-X-MAP:URI="([^"\r\n]+)"', line)
            if not match:
                raise ValueError("invalid HLS initialization reference")
            name = match.group(1)
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            date = datetime.fromisoformat(line.split(":", 1)[1])
            if date.tzinfo is None:
                raise ValueError("HLS timestamps require a timezone")
            timestamp = date.timestamp()
            continue
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
            if not math.isfinite(duration) or not 0 < duration <= 3:
                raise ValueError("invalid HLS segment duration")
            max_duration = max(max_duration, duration)
            continue
        elif line and not line.startswith("#"):
            name = line
            if timestamp is None or duration is None:
                raise ValueError("each HLS segment needs a capture timestamp and duration")
            captured_at = max(captured_at, timestamp + duration)
            timestamp = duration = None
            segments += 1
        else:
            continue
        match = ASSET.fullmatch(name)
        if not match:
            raise ValueError("invalid HLS asset name")
        asset_generation = match.group(1) or match.group(2)
        if generation is not None and generation != asset_generation:
            raise ValueError("mixed HLS generations")
        generation = asset_generation
        assets.append(name)
    if not 1 <= segments <= 12 or not assets or not assets[0].startswith("init-"):
        raise ValueError("incomplete HLS playlist")
    if len(set(assets)) != len(assets) or len(assets) != segments + 1:
        raise ValueError("duplicate or missing HLS initialization")
    if target_duration is None:
        raise ValueError("missing HLS target duration")
    # ffmpeg rounds exact 0.5-second segments down to TARGETDURATION:0.
    # Browser HLS players require a positive bound; capture timestamps stay intact.
    required_duration = max(1, math.ceil(max_duration))
    if target_duration < required_duration:
        text = re.sub(
            r"^#EXT-X-TARGETDURATION:\d+$",
            f"#EXT-X-TARGETDURATION:{required_duration}",
            text,
            flags=re.MULTILINE,
        )
    return Playlist(text.encode(), generation, tuple(assets), captured_at)


def ready_playlist(playlist: Playlist, uploaded: set[str]) -> Playlist | None:
    """Publish the newest contiguous uploaded window, never missing references."""
    init = playlist.assets[0]
    if init not in uploaded:
        return None
    current: list[tuple[str, list[str]]] = []
    best: list[tuple[str, list[str]]] = []
    pending: list[str] = []
    for line in playlist.content.decode().splitlines():
        if line.startswith(("#EXTINF:", "#EXT-X-PROGRAM-DATE-TIME:")):
            pending.append(line)
        elif line and not line.startswith("#"):
            if line in uploaded:
                current.append((line, [*pending, line]))
                best = list(current)
            else:
                current = []
            pending = []
    if not best:
        return None
    first = ASSET.fullmatch(best[0][0])
    target = next(
        line
        for line in playlist.content.decode().splitlines()
        if line.startswith("#EXT-X-TARGETDURATION:")
    )
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        target,
        f"#EXT-X-MEDIA-SEQUENCE:{int(first.group(3))}",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        f'#EXT-X-MAP:URI="{init}"',
    ]
    for _, block in best:
        lines.extend(block)
    return parse_playlist(("\n".join(lines) + "\n").encode())


class HlsWindowCapture:
    def __init__(self, display: str, directory: Path, fps: float | None = None):
        if fps is not None and (not math.isfinite(fps) or not 1 <= fps <= 240):
            raise ValueError("video FPS must be between 1 and 240")
        self.display = display
        self.directory = directory
        self.requested_fps = fps
        self.fps = fps or 60.0
        self.generation = ""
        self.process: asyncio.subprocess.Process | None = None
        self.path: Path | None = None
        self.started_at = 0.0
        self.next_start = 0.0

    async def _output(self, command: str, *args: str, env: dict) -> str:
        process = await asyncio.create_subprocess_exec(
            command,
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 3)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        return output.decode(errors="replace") if not process.returncode else ""

    async def ensure_started(self) -> None:
        if self.process and self.process.returncode is None:
            try:
                age = time.time() - self.path.stat().st_mtime if self.path else math.inf
            except OSError:
                age = math.inf
            if time.monotonic() - self.started_at < 8 or age < 5:
                return
            LOG.warning("video_capture_stalled")
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
        env.update(DISPLAY=self.display, TZ="UTC")
        try:
            window = minecraft_window(await self._output("xwininfo", "-root", "-tree", env=env))
            if window is None:
                LOG.warning("video_minecraft_window_unavailable")
                return
            if self.requested_fps is None:
                try:
                    self.fps = display_refresh(await self._output("xrandr", "--current", env=env))
                except (OSError, TimeoutError):
                    self.fps = 60.0
            self.generation = uuid.uuid4().hex
            self.path = self.directory / "index.m3u8"
            gop = str(max(1, round(self.fps)))
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
                "-an",
                "-vf",
                "scale=960:-2",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "28",
                "-maxrate",
                "2500k",
                "-bufsize",
                "2500k",
                "-threads",
                "2",
                "-g",
                gop,
                "-keyint_min",
                gop,
                "-sc_threshold",
                "0",
                "-f",
                "hls",
                "-hls_time",
                "1",
                "-hls_list_size",
                "8",
                "-hls_delete_threshold",
                "12",
                "-hls_segment_type",
                "fmp4",
                "-hls_fmp4_init_filename",
                f"init-{self.generation}.mp4",
                "-hls_segment_filename",
                str(self.directory / f"segment-{self.generation}-%09d.m4s"),
                "-hls_flags",
                "delete_segments+independent_segments+program_date_time+temp_file",
                str(self.path),
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.started_at = time.monotonic()
            LOG.info("video_capture_started target_fps=%.3f width=960 segment_seconds=1", self.fps)
        except (OSError, TimeoutError):
            LOG.warning("video_capture_start_failed")

    def latest(self) -> Playlist | None:
        if not self.path or not self.process or self.process.returncode is not None:
            return None
        try:
            with self.path.open("rb") as stream:
                value = parse_playlist(stream.read(MAX_PLAYLIST + 1))
            return value if value.generation == self.generation else None
        except (OSError, ValueError, UnicodeError):
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
        for path in self.directory.iterdir():
            if path.is_file():
                with contextlib.suppress(OSError):
                    path.unlink()
        self.path = None


class LiveVideoPublisher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        site_url: str,
        site_token: str,
        ingest_token: str,
        display: str,
        fps: float | None = None,
    ):
        self.client = client
        self.site_url = site_url
        self.headers = {
            "OAI-Sites-Authorization": "Bearer " + site_token,
            "Authorization": "Bearer " + ingest_token,
        }
        self.display = display
        self.fps = fps
        self.generation = ""
        self.uploaded: set[str] = set()
        self.playlist_hash: bytes | None = None
        self.published_sequence: int | None = None
        self.cooldown_until = 0.0
        self.stats_started = time.monotonic()
        self.segment_uploads = 0
        self.upload_ms = 0.0
        self.upload_slots = asyncio.Semaphore(UPLOAD_CONCURRENCY)

    async def _upload(self, name: str, content: bytes, fps: float) -> bool:
        if time.monotonic() < self.cooldown_until:
            return False
        playlist = name == "index.m3u8"
        try:
            response = await self.client.post(
                self.site_url + "/api/live/upload",
                params={"name": name},
                content=content,
                headers={
                    **self.headers,
                    "Content-Type": "application/vnd.apple.mpegurl" if playlist else "video/mp4",
                    "X-HoloSpeed-Target-Fps": f"{fps:g}",
                },
                timeout=httpx.Timeout(6, connect=3),
                follow_redirects=False,
            )
            if 200 <= response.status_code < 300:
                return True
            self.cooldown_until = time.monotonic() + (2 if response.status_code == 429 else 1)
            LOG.warning("video_upload_rejected status=%d", response.status_code)
        except httpx.HTTPError:
            self.cooldown_until = time.monotonic() + 1
            LOG.warning("video_upload_failed")
        return False

    async def _asset(self, capture: HlsWindowCapture, name: str) -> bool:
        async with self.upload_slots:
            try:
                with (capture.directory / name).open("rb") as stream:
                    content = stream.read(MAX_ASSET + 1)
            except OSError:
                return False
            if not content or len(content) > MAX_ASSET:
                LOG.warning("video_asset_invalid_size")
                return False
            started = time.monotonic()
            if not await self._upload(name, content, capture.fps):
                return False
            self.uploaded.add(name)
            if name.startswith("segment-"):
                self.segment_uploads += 1
                self.upload_ms += (time.monotonic() - started) * 1000
            return True

    async def sync(self, capture: HlsWindowCapture) -> bool:
        playlist = capture.latest()
        if playlist is None or not -2 <= time.time() - playlist.captured_at <= MAX_VIDEO_AGE:
            return False
        if playlist.generation != self.generation:
            self.generation = playlist.generation
            self.uploaded.clear()
            self.playlist_hash = None
            self.published_sequence = None
        # Prioritize current captures over historical backfill. A bounded parallel
        # batch avoids sequential upload backlog when network RTT exceeds a GOP.
        order = [playlist.assets[0], *reversed(playlist.assets[1:])]
        missing = [
            name
            for name in order
            if name not in self.uploaded
            and (
                name.startswith("init-")
                or self.published_sequence is None
                or int(ASSET.fullmatch(name).group(3)) > self.published_sequence
            )
        ][:UPLOAD_CONCURRENCY]
        await asyncio.gather(*(self._asset(capture, name) for name in missing))
        latest = capture.latest()
        if latest is None or latest.generation != playlist.generation:
            return False
        ready = ready_playlist(latest, self.uploaded)
        if ready is None or not -2 <= time.time() - ready.captured_at <= MAX_VIDEO_AGE:
            return False
        sequence = int(ASSET.fullmatch(ready.assets[-1]).group(3))
        if self.published_sequence is not None and sequence <= self.published_sequence:
            return False
        digest = hashlib.sha256(ready.content).digest()
        if digest == self.playlist_hash:
            return False
        # Commit only the freshest complete window; capture timestamps are original.
        if not await self._upload("index.m3u8", ready.content, capture.fps):
            return False
        self.playlist_hash = digest
        self.published_sequence = sequence
        self.uploaded.intersection_update(set(latest.assets) | set(ready.assets))
        elapsed = time.monotonic() - self.stats_started
        if elapsed >= 10 and self.segment_uploads:
            LOG.info(
                "video_upload segments_per_second=%.2f mean_ms=%.1f target_fps=%.3f",
                self.segment_uploads / elapsed,
                self.upload_ms / self.segment_uploads,
                capture.fps,
            )
            self.stats_started = time.monotonic()
            self.segment_uploads = 0
            self.upload_ms = 0.0
        return True

    async def run(self, stop: asyncio.Event) -> None:
        with tempfile.TemporaryDirectory(prefix="holospeed-video-") as directory:
            capture = HlsWindowCapture(self.display, Path(directory), self.fps)
            try:
                while not stop.is_set():
                    await capture.ensure_started()
                    await self.sync(capture)
                    await wait_for_stop(stop, 0.05)
            finally:
                await capture.stop()
