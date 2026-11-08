"""Camera cadence, fresh-frame publication, and independent telemetry polling."""

import asyncio
import json
import time

import httpx
import pytest
from holospeed.broadcast import Frame, FramePublisher, TelemetryCache, WindowCapture, arguments


@pytest.mark.parametrize("fps", [1, 5, 10])
def test_cli_accepts_bounded_camera_rates(fps):
    args = arguments(["--site-url", "https://example.test", "--fps", str(fps)])
    assert args.fps == fps
    assert args.interval == 1
    defaults = arguments(["--site-url", "https://example.test"])
    assert defaults.fps == 1 and defaults.video_fps is None


@pytest.mark.parametrize("fps", ["0", "0.5", "11", "nan", "inf", "-1"])
def test_cli_rejects_invalid_camera_rates(fps):
    with pytest.raises(SystemExit):
        arguments(["--site-url", "https://example.test", "--fps", fps])


async def test_capture_configures_five_fps_and_atomic_jpeg_output(tmp_path, monkeypatch):
    calls = []

    class Process:
        returncode = None

        async def communicate(self):
            self.returncode = 0
            return b'0x200007 "Minecraft* 1.21.1 - Singleplayer": ()', b""

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return self.returncode

    async def create(*args, **kwargs):
        calls.append(args)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    capture = WindowCapture(":97", tmp_path, fps=5)
    await capture.ensure_started()
    ffmpeg = calls[1]
    assert ffmpeg[ffmpeg.index("-framerate") + 1] == "5"
    assert ffmpeg[ffmpeg.index("-vf") + 1] == "scale=960:-2"
    assert ffmpeg[ffmpeg.index("-q:v") + 1] == "7"
    assert ffmpeg[ffmpeg.index("-atomic_writing") + 1] == "1"
    await capture.stop()


def test_partial_jpeg_is_not_published_even_when_its_timestamp_is_fresh(tmp_path):
    class Process:
        returncode = None

    path = tmp_path / "frame.jpg"
    path.write_bytes(b"\xff\xd8incomplete")
    capture = WindowCapture(":97", tmp_path)
    capture.process = Process()
    capture.path = path
    assert capture.latest() is None


class Uploader:
    def __init__(self, results=()):
        self.files = []
        self.results = list(results)

    async def post(self, path, **kwargs):
        assert path == "/api/ingest"
        self.files.append(kwargs["files"])
        return self.results.pop(0) if self.results else True


async def test_high_rate_frames_do_not_duplicate_or_restamp_stale_capture():
    uploader = Uploader()
    publisher = FramePublisher(uploader)
    frame = Frame(b"jpeg", 1_700_000_000_000_000_000)
    state = {"status": "running"}
    assert await publisher.send(frame, state, [], True, now=1_700_000_000, monotonic=0)
    for tick in range(1, 5):
        assert not await publisher.send(
            frame, state, [], True, now=1_700_000_000 + tick / 5, monotonic=tick / 5
        )
    assert len(uploader.files) == 1
    await publisher.send(frame, state, [], True, now=1_700_000_001, monotonic=1)
    await publisher.send(frame, state, [], True, now=1_700_000_006, monotonic=6)
    assert "frame" not in uploader.files[1]
    assert "frame" not in uploader.files[2]
    first, heartbeat, stale = [json.loads(item["metadata"][1]) for item in uploader.files]
    assert first["capture_seq"] == heartbeat["capture_seq"] == stale["capture_seq"]
    assert first["captured_at"] == heartbeat["captured_at"] == stale["captured_at"]
    assert heartbeat["feed_status"] == "online"
    assert stale["feed_status"] == "stale"


async def test_failed_upload_is_replaced_by_latest_frame_without_a_backlog():
    uploader = Uploader([False, True, True])
    publisher = FramePublisher(uploader)
    first = Frame(b"first", 1_700_000_000_000_000_000)
    newest = Frame(b"newest", 1_700_000_000_200_000_000)
    assert not await publisher.send(first, {}, [], True, now=1_700_000_000, monotonic=0)
    assert publisher.last_uploaded is None
    assert await publisher.send(newest, {}, [], True, now=1_700_000_000.2, monotonic=0.2)
    assert publisher.last_uploaded is newest
    await publisher.send(first, {}, [], True, now=1_700_000_001.3, monotonic=1.3)
    assert [item["frame"][1] for item in uploader.files if "frame" in item] == [b"first", b"newest"]
    assert json.loads(uploader.files[-1]["metadata"][1])["capture_seq"] == newest.sequence


async def test_slow_telemetry_does_not_block_camera_and_cached_state_expires():
    entered = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/api/state":
            entered.set()
            await release.wait()
            return httpx.Response(
                200, json={"status": "running", "connected": True, "keys": ["forward"]}
            )
        return httpx.Response(200, json={"runs": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        cache = TelemetryCache(client, "http://127.0.0.1:8768", (), 1)
        refresh = asyncio.create_task(cache.refresh())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            state, connected = cache.snapshot(time.monotonic())
            assert not connected
            uploader = Uploader()
            publisher = FramePublisher(uploader)
            now = time.time()
            frame = Frame(b"jpeg", int(now * 1_000_000_000))
            await asyncio.wait_for(
                publisher.send(frame, state, [], connected, now=now, monotonic=time.monotonic()), 1
            )
            assert (
                json.loads(uploader.files[0]["metadata"][1])["feed_status"] == "controller-offline"
            )
            release.set()
            await refresh
            state, connected = cache.snapshot(cache.updated_at)
            assert connected and state["keys"] == ["forward"]
            for index in range(5):
                await publisher.send(
                    Frame(b"jpeg", frame.mtime_ns + (index + 1) * 200_000_000),
                    state,
                    [],
                    True,
                    now=now + (index + 1) / 5,
                    monotonic=time.monotonic(),
                )
            assert requests == ["/api/state", "/api/runs"]
            expired, connected = cache.snapshot(cache.updated_at + 5)
            assert not connected and expired["keys"] == [] and expired["status"] == "unavailable"
        finally:
            refresh.cancel()
            await asyncio.gather(refresh, return_exceptions=True)


async def test_publisher_starts_video_and_commands_while_telemetry_waits_and_cleans_up(
    tmp_path,
    monkeypatch,
):
    from holospeed import broadcast, broadcast_video

    activity = set()
    command_ready = asyncio.Event()
    never = asyncio.Event()

    class Telemetry:
        def __init__(self, *args, **kwargs):
            self.runs = []

        def snapshot(self, now):
            return {"status": "unavailable"}, False

        async def run(self, stop):
            activity.add("telemetry_started")
            try:
                await never.wait()
            finally:
                activity.add("telemetry_stopped")

    class Commands:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, stop):
            activity.add("commands_started")
            command_ready.set()
            try:
                await never.wait()
            finally:
                activity.add("commands_stopped")

    class Video:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, stop):
            activity.add("video_started")
            await command_ready.wait()
            stop.set()
            try:
                await never.wait()
            finally:
                activity.add("video_stopped")

    class Capture:
        def __init__(self, *args, **kwargs):
            assert kwargs["fps"] == 1

        async def ensure_started(self):
            await asyncio.sleep(0)

        def latest(self):
            return None

        async def stop(self):
            activity.add("capture_stopped")

    class Frames:
        def __init__(self, *args, **kwargs):
            pass

        async def send(self, *args, **kwargs):
            activity.add("fallback_updated")

    monkeypatch.setenv("HOLOSPEED_SITE_TOKEN", "test-site-private")
    monkeypatch.setenv("HOLOSPEED_INGEST_TOKEN", "test-ingest-private")
    monkeypatch.setattr(broadcast, "TelemetryCache", Telemetry)
    monkeypatch.setattr(broadcast, "CommandConsumer", Commands)
    monkeypatch.setattr(broadcast, "WindowCapture", Capture)
    monkeypatch.setattr(broadcast, "FramePublisher", Frames)
    monkeypatch.setattr(broadcast_video, "LiveVideoPublisher", Video)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    args = arguments(["--site-url", "https://example.test", "--runs-dir", str(tmp_path)])
    await asyncio.wait_for(broadcast.publish(args), 2)
    assert activity == {
        "telemetry_started",
        "telemetry_stopped",
        "commands_started",
        "commands_stopped",
        "video_started",
        "video_stopped",
        "capture_stopped",
        "fallback_updated",
    }
