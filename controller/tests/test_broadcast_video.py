"""Bounded HLS assets, capture timestamps, and ordered video publication."""

import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
import pytest
from holospeed.broadcast import arguments
from holospeed.broadcast_video import (
    UPLOAD_CONCURRENCY,
    HlsWindowCapture,
    LiveVideoPublisher,
    display_refresh,
    parse_playlist,
    ready_playlist,
)

GENERATION = "a" * 32


def playlist(*, generation=GENERATION, timestamp=None, sequence=0):
    timestamp = time.time() - 0.5 if timestamp is None else timestamp
    date = datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="milliseconds")
    return (
        "#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:1\n"
        f'#EXT-X-MEDIA-SEQUENCE:{sequence}\n#EXT-X-MAP:URI="init-{generation}.mp4"\n'
        "#EXT-X-INDEPENDENT-SEGMENTS\n#EXTINF:0.500000,\n"
        f"#EXT-X-PROGRAM-DATE-TIME:{date}\nsegment-{generation}-{sequence:09d}.m4s\n"
    ).encode()


def playlist_window(start, count, timestamp):
    header = playlist(timestamp=timestamp, sequence=start).decode().split("#EXTINF:", 1)[0]
    blocks = []
    for index in range(count):
        date = datetime.fromtimestamp(timestamp + index, UTC).isoformat(timespec="milliseconds")
        blocks.append(
            f"#EXTINF:1.000000,\n#EXT-X-PROGRAM-DATE-TIME:{date}\n"
            f"segment-{GENERATION}-{start + index:09d}.m4s\n"
        )
    return (header + "".join(blocks)).encode()


def test_refresh_is_active_display_mode_or_sixty_fps_fallback():
    assert display_refresh("1920x1080 60.00 + 144.00* 120.00") == 144
    assert display_refresh("1280x720 59.94*+") == 59.94
    assert display_refresh("1280x720 0.00*") == 60
    assert display_refresh("display unavailable") == 60


@pytest.mark.parametrize("value", ["0", "241", "nan", "inf"])
def test_invalid_video_fps_is_rejected(value):
    with pytest.raises(SystemExit):
        arguments(["--site-url", "https://example.test", "--video-fps", value])


def test_default_video_is_auto_refresh_with_one_fps_jpeg_fallback():
    args = arguments(["--site-url", "https://example.test"])
    assert args.video_fps is None
    assert args.fps == 1
    assert arguments(["--site-url", "https://example.test", "--video-fps", "60"]).video_fps == 60


def test_playlist_keeps_generation_assets_and_original_capture_time():
    parsed = parse_playlist(playlist(timestamp=1_700_000_000, sequence=3))
    assert parsed.generation == GENERATION
    assert parsed.captured_at == 1_700_000_000.5
    assert parsed.assets == (f"init-{GENERATION}.mp4", f"segment-{GENERATION}-000000003.m4s")


def test_ffmpeg_half_second_zero_target_duration_is_normalized_without_restamping():
    raw = playlist(timestamp=1_700_000_000).replace(
        b"#EXT-X-TARGETDURATION:1", b"#EXT-X-TARGETDURATION:0"
    )
    parsed = parse_playlist(raw)
    assert b"#EXT-X-TARGETDURATION:1\n" in parsed.content
    assert parsed.captured_at == 1_700_000_000.5
    assert parsed.content.replace(b"#EXT-X-TARGETDURATION:1", b"#EXT-X-TARGETDURATION:0") == raw


@pytest.mark.parametrize(
    "replace",
    [
        lambda value: value.replace(b"segment-", b"https://example.test/segment-"),
        lambda value: value.replace(b"segment-", b"../segment-"),
        lambda value: value.replace(b"segment-" + GENERATION.encode(), b"segment-" + b"b" * 32),
        lambda value: value.replace(b"#EXT-X-PROGRAM-DATE-TIME:", b"#REMOVED:"),
        lambda value: value.replace(b"#EXTINF:0.500000", b"#EXTINF:nan"),
        lambda value: value.replace(b"#EXTINF:0.500000", b"#EXTINF:10.0"),
    ],
)
def test_playlist_rejects_unsafe_names_and_missing_capture_evidence(replace):
    with pytest.raises(ValueError):
        parse_playlist(replace(playlist()))


async def test_video_capture_is_window_only_h264_with_one_second_gop(tmp_path, monkeypatch):
    calls = []

    class Process:
        returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return self.returncode

    async def output(self, command, *args, **kwargs):
        if command == "xwininfo":
            return '0x123 "Minecraft* 1.21.1 - Singleplayer": ()'
        return "1280x720 60.00*+"

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr(HlsWindowCapture, "_output", output)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    capture = HlsWindowCapture(":97", tmp_path)
    await capture.ensure_started()
    command, options = calls[0]
    assert command[command.index("-window_id") + 1] == str(0x123)
    assert command[command.index("-framerate") + 1] == "60"
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-g") + 1] == "60"
    assert command[command.index("-hls_time") + 1] == "1"
    assert command[command.index("-hls_segment_type") + 1] == "fmp4"
    assert "program_date_time" in command[command.index("-hls_flags") + 1]
    assert options["env"]["TZ"] == "UTC"
    first = capture.generation
    await capture.stop()
    capture.next_start = 0
    await capture.ensure_started()
    assert capture.generation != first
    await capture.stop()


async def test_video_capture_never_falls_back_to_an_unrelated_window(tmp_path, monkeypatch):
    async def output(self, *args, **kwargs):
        return '0x123 "Chrome - Minecraft": ()'

    monkeypatch.setattr(HlsWindowCapture, "_output", output)
    capture = HlsWindowCapture(":97", tmp_path)
    await capture.ensure_started()
    assert capture.process is None and capture.path is None


class Capture:
    def __init__(self, directory, body=None):
        self.directory = directory
        self.fps = 60
        self.body = body or playlist()
        for name in parse_playlist(self.body).assets:
            (directory / name).write_bytes(b"video-fragment")

    def latest(self):
        return parse_playlist(self.body)


async def test_assets_upload_before_public_playlist_and_unchanged_playlist_is_skipped(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    capture = Capture(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = LiveVideoPublisher(
            client, "https://example.test", "site-secret", "ingest-secret", ":97"
        )
        assert await publisher.sync(capture)
        assert not await publisher.sync(capture)
    assert [r.url.params["name"] for r in requests] == [*capture.latest().assets, "index.m3u8"]
    assert requests[0].headers["content-type"] == "video/mp4"
    assert requests[-1].headers["content-type"] == "application/vnd.apple.mpegurl"
    assert requests[-1].headers["X-HoloSpeed-Target-Fps"] == "60"
    assert requests[-1].headers["Authorization"] == "Bearer ingest-secret"
    published = parse_playlist(requests[-1].content)
    assert published.assets == capture.latest().assets
    assert published.captured_at == capture.latest().captured_at


async def test_failed_segment_does_not_publish_a_broken_playlist(tmp_path):
    names = []

    async def handler(request):
        name = request.url.params["name"]
        names.append(name)
        return httpx.Response(500 if name.startswith("segment-") else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = LiveVideoPublisher(client, "https://example.test", "s", "i", ":97")
        assert not await publisher.sync(Capture(tmp_path))
    assert "index.m3u8" not in names


async def test_stale_video_does_not_get_a_new_publication_timestamp(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = LiveVideoPublisher(client, "https://example.test", "s", "i", ":97")
        capture = Capture(tmp_path, playlist(timestamp=time.time() - 30))
        assert not await publisher.sync(capture)
    assert requests == []


async def test_rate_limit_does_not_log_credentials_or_retry_old_segment_immediately(
    tmp_path, caplog
):
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(429, json={"secret": "opaque-key-private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = LiveVideoPublisher(
            client, "https://example.test", "opaque-key-private", "i", ":97"
        )
        capture = Capture(tmp_path)
        assert not await publisher.sync(capture)
        assert not await publisher.sync(capture)
    assert len(requests) == 1
    assert "opaque-key-private" not in caplog.text
    assert "video_upload_rejected status=429" in caplog.text
    json.dumps({"logs": caplog.text})


def test_ready_playlist_uses_newest_contiguous_uploaded_range_without_restamping():
    original = parse_playlist(playlist_window(0, 6, 1_700_000_000))
    uploaded = {original.assets[index] for index in (0, 1, 2, 4, 5)}
    ready = ready_playlist(original, uploaded)
    assert ready.assets == (original.assets[0], original.assets[4], original.assets[5])
    assert b"#EXT-X-MEDIA-SEQUENCE:3\n" in ready.content
    assert ready.captured_at == 1_700_000_005
    assert original.captured_at == 1_700_000_006
    assert ready_playlist(original, set(original.assets[1:])) is None


async def test_missing_assets_upload_in_one_bounded_parallel_batch_before_playlist(tmp_path):
    all_started = asyncio.Event()
    release = asyncio.Event()
    names = []
    in_flight = 0
    peak = 0

    async def handler(request):
        nonlocal in_flight, peak
        name = request.url.params["name"]
        names.append(name)
        if name != "index.m3u8":
            in_flight += 1
            peak = max(peak, in_flight)
            if in_flight == UPLOAD_CONCURRENCY:
                all_started.set()
            await release.wait()
            in_flight -= 1
        else:
            assert in_flight == 0
        return httpx.Response(200)

    capture = Capture(tmp_path, playlist_window(0, 8, time.time() - 8))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = LiveVideoPublisher(client, "https://example.test", "s", "i", ":97")
        task = asyncio.create_task(publisher.sync(capture))
        try:
            await asyncio.wait_for(all_started.wait(), 1)
            assert len(names) == UPLOAD_CONCURRENCY and not task.done()
            assert set(names) == {capture.latest().assets[0], *capture.latest().assets[-5:]}
            release.set()
            assert await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert peak == UPLOAD_CONCURRENCY and names[-1] == "index.m3u8"


async def test_published_window_does_not_backfill_passed_segments_or_recommit_same_end(tmp_path):
    requests = []

    async def handler(request):
        requests.append(request.url.params["name"])
        return httpx.Response(200)

    capture = Capture(tmp_path, playlist_window(0, 8, time.time() - 8))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        publisher = LiveVideoPublisher(client, "https://example.test", "s", "i", ":97")
        assert await publisher.sync(capture)
        initial = list(requests)
        assert publisher.published_sequence == 7
        # Older omitted segments are behind the committed end and must not be
        # uploaded later: the Worker correctly rejects those passed identities.
        assert not await publisher.sync(capture)
        assert requests == initial
        current = capture.latest()
        capture.body = playlist_window(1, 7, current.captured_at - 7)
        assert not await publisher.sync(capture)
        assert requests == initial


async def test_sustained_two_second_upload_rtt_keeps_stream_advancing_without_backlog(
    tmp_path,
    monkeypatch,
):
    from holospeed import broadcast_video

    clock = [1000.0]
    monkeypatch.setattr(broadcast_video.time, "time", lambda: clock[0])
    (tmp_path / f"init-{GENERATION}.mp4").write_bytes(b"init")
    for sequence in range(100):
        (tmp_path / f"segment-{GENERATION}-{sequence:09d}.m4s").write_bytes(b"segment")

    class AdvancingCapture:
        directory = tmp_path
        fps = 60

        def latest(self):
            last = int(clock[0] - 992)
            first = max(0, last - 8)
            return parse_playlist(playlist_window(first, last - first, 992 + first))

    committed = []
    batches = []
    pending = []
    expected_uploads = [0]

    async with httpx.AsyncClient() as client:
        publisher = LiveVideoPublisher(client, "https://example.test", "s", "i", ":97")

        async def upload(name, content, fps):
            if name == "index.m3u8":
                clock[0] += 2  # Playlist commit has the same observed network RTT.
                value = parse_playlist(content)
                committed.append((value.assets[-1], clock[0] - value.captured_at))
                return True
            pending.append(name)
            if len(pending) == expected_uploads[0]:
                batches.append(list(pending))
                pending.clear()
                clock[0] += 2  # Parallel asset uploads finish together.
            await asyncio.sleep(0)
            return True

        monkeypatch.setattr(publisher, "_upload", upload)
        for _ in range(5):
            expected_uploads[0] = min(
                UPLOAD_CONCURRENCY,
                len(set(AdvancingCapture().latest().assets) - publisher.uploaded),
            )
            assert await publisher.sync(AdvancingCapture())
    assert len(committed) == len(batches) == 5
    assert [age for _, age in committed] == [4, 4, 4, 4, 4]
    assert len({name for name, _ in committed}) == 5
    assert all(1 <= len(batch) <= UPLOAD_CONCURRENCY for batch in batches)
