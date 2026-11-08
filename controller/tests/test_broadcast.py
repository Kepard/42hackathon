"""Privacy, freshness, and incremental archive contracts for the read-only publisher."""

import json
import os
from pathlib import Path

import httpx
import pytest
from holospeed.broadcast import (
    OMITTED_IMAGE,
    CloudUploader,
    Frame,
    JournalTail,
    RunPublisher,
    WindowCapture,
    arguments,
    capture_timestamp,
    envelope,
    minecraft_window,
    sanitize,
)


def test_sanitization_preserves_text_decisions_usage_and_removes_secret_image_payloads():
    opaque_secret = "opaque-test-credential-value"
    event = {
        "type": "model_call",
        "data": {
            "request": {
                "headers": {"Authorization": "Bearer secret"},
                "messages": [
                    {
                        "content": [
                            {"type": "text", "text": "Find a tree"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AAAA"},
                            },
                        ]
                    }
                ],
                "assets": {"frame-id": {"data_base64": "A" * 10000}},
                "custom": opaque_secret,
            },
            "response": {"text": "Move forward six ticks", "api_key": "hk-example"},
            "usage": {"input_tokens": 100, "output_tokens": 40},
            "action": {"keys": ["forward"], "duration_ticks": 6},
            "latency_ms": 72.5,
        },
    }
    result = sanitize(event, (opaque_secret,))
    serialized = json.dumps(result)
    assert opaque_secret not in serialized and "hk-example" not in serialized
    assert "data:image" not in serialized and "A" * 1000 not in serialized
    assert result["data"]["request"]["messages"][0]["content"][0]["text"] == "Find a tree"
    assert result["data"]["response"]["text"] == "Move forward six ticks"
    assert result["data"]["usage"] == {"input_tokens": 100, "output_tokens": 40}
    assert result["data"]["action"]["keys"] == ["forward"]
    assert result["data"]["latency_ms"] == 72.5


def test_sanitization_catches_unknown_inline_base64_and_secret_dictionary_keys():
    secret = "opaque-secret-key"
    result = sanitize({secret: "test", "nested": 'before "' + "A" * 2048 + '" after'}, (secret,))
    assert secret not in json.dumps(result)
    assert result["nested"] == 'before "' + OMITTED_IMAGE + '" after'
    assert sanitize({"completion_tokens": 30})["completion_tokens"] == 30


def test_repeated_heartbeat_never_restamps_captured_frame():
    frame = Frame(b"jpeg", 1_700_000_000_123_456_789)
    first = envelope({"mode": "live"}, [], frame, "online", 1_700_000_001)
    retry = envelope({"mode": "live"}, [], frame, "stale", 1_700_000_030)
    assert first["captured_at"] == retry["captured_at"] == "2023-11-14T22:13:20.123+00:00"
    assert first["capture_seq"] == retry["capture_seq"] == 1_700_000_000_123
    assert retry["updated_at"] != first["updated_at"]
    assert not frame.fresh(1_700_000_030)
    assert not frame.fresh(1_699_999_990)  # A future-dated file is not live evidence.


def test_initial_offline_metadata_has_nullable_capture_pair():
    result = envelope({"mode": "live", "status": "unavailable"}, [], None, "offline", 1_700_000_001)
    assert result["captured_at"] is None and result["capture_seq"] is None
    assert result["source"] == "minecraft-window"
    with pytest.raises(ValueError):
        capture_timestamp(0)


def test_window_resolution_never_selects_browser_or_desktop():
    windows = """
      0x21f (the root window) (has no name)
      0x111 "Chrome - Minecraft 1.21.1 guide": () 1280x720+0+0
      0x200007 "Minecraft* 1.21.1 - Singleplayer": () 1280x720+0+0
    """
    # A title that merely mentions Minecraft must not be selected.
    assert minecraft_window(windows) == 0x200007
    assert minecraft_window('0x123 "Desktop": ()') is None
    assert minecraft_window('0x123 "Minecraft 1.20.1": ()') is None


def test_capture_uses_open_file_mtime_and_rejects_dead_process(tmp_path):
    class Process:
        returncode = None

    path = tmp_path / "frame.jpg"
    path.write_bytes(b"\xff\xd8pixels\xff\xd9")
    mtime_ns = 1_700_000_000_123_456_789
    os.utime(path, ns=(mtime_ns, mtime_ns))
    capture = WindowCapture(":97", tmp_path)
    capture.process = Process()
    capture.path = path
    assert capture.latest().mtime_ns == mtime_ns
    capture.process.returncode = 1
    assert capture.latest() is None


def test_journal_tail_reads_appends_without_duplication_and_waits_for_complete_records(tmp_path):
    path = tmp_path / "events.jsonl"
    first = (
        json.dumps({"seq": 1, "type": "model_call", "data": {"response": {"text": "Plan"}}}) + "\n"
    )
    path.write_text(first)
    tail = JournalTail(path)
    assert tail.read()
    assert tail.offset == len(first.encode())
    with path.open("a") as stream:
        stream.write('{"seq":2,"type":"ack"')
    assert not tail.read()
    assert tail.offset == len(first.encode())
    with path.open("a") as stream:
        stream.write(',"data":{"status":"completed"}}\n')
    assert tail.read()
    assert [item["seq"] for item in tail.events] == [1, 2]
    assert tail.read()
    assert [item["seq"] for item in tail.events] == [1, 2]
    assert tail.events[0]["data"]["response"]["text"] == "Plan"


def test_journal_rotation_resets_cached_sequence(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"seq":1,"type":"old"}\n')
    tail = JournalTail(path)
    assert tail.read()
    replacement = tmp_path / "new.jsonl"
    replacement.write_text('{"seq":1,"type":"new"}\n')
    replacement.replace(path)
    assert tail.read()
    assert tail.events == [{"seq": 1, "type": "new"}]


async def test_archive_upload_uses_json_auth_and_incremental_updates(tmp_path):
    run_id = "20261108T070000-deadbeef"
    path = tmp_path / run_id
    path.mkdir()
    journal = path / "events.jsonl"
    journal.write_text('{"seq":1,"type":"model_call","data":{"request":{"text":"Find wood"}}}\n')
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        uploader = CloudUploader(client, "https://example.test", "site-private", "ingest-private")
        publisher = RunPublisher(tmp_path, uploader, ())
        summary = {"id": run_id, "mode": "live", "status": "running"}
        await publisher.sync([summary], run_id)
        await publisher.sync([summary], run_id)
        assert len(requests) == 1
        with journal.open("a") as stream:
            stream.write('{"seq":2,"type":"ack","data":{"status":"completed"}}\n')
        await publisher.sync([summary], run_id)
    assert len(requests) == 2
    assert requests[0].headers["Content-Type"] == "application/json"
    assert requests[0].headers["Authorization"] == "Bearer ingest-private"
    assert requests[0].headers["OAI-Sites-Authorization"] == "Bearer site-private"
    payload = json.loads(requests[1].content)
    assert [event["seq"] for event in payload["events"]] == [1, 2]
    assert payload["events"][0]["data"]["request"]["text"] == "Find wood"


async def test_backfill_covers_every_advertised_run_not_only_first_seven(tmp_path):
    class Uploader:
        def __init__(self):
            self.paths = []

        async def post(self, path, **kwargs):
            self.paths.append(path)
            return True

    summaries = []
    for index in range(9):
        run_id = f"20261108T070000-{index:08x}"
        (tmp_path / run_id).mkdir()
        (tmp_path / run_id / "events.jsonl").write_text('{"seq":1,"type":"run_started"}\n')
        summaries.append({"id": run_id, "mode": "live", "status": "finished"})
    uploader = Uploader()
    publisher = RunPublisher(tmp_path, uploader, ())
    await publisher.sync([], None)  # Controller may be offline during startup.
    await publisher.sync(summaries, None)
    assert len(uploader.paths) == 9
    await publisher.sync(summaries, None)
    assert len(uploader.paths) == 9


async def test_failed_final_archive_is_retried_after_new_run_starts(tmp_path):
    old_id, new_id = "20261108T070000-deadbeef", "20261108T080000-cafebabe"
    for run_id in (old_id, new_id):
        (tmp_path / run_id).mkdir()
        (tmp_path / run_id / "events.jsonl").write_text('{"seq":1,"type":"run_started"}\n')

    class Uploader:
        fail_old = False

        async def post(self, path, **kwargs):
            return not (self.fail_old and path.endswith(old_id))

    uploader = Uploader()
    publisher = RunPublisher(tmp_path, uploader, ())
    old = {"id": old_id, "mode": "live", "status": "running"}
    await publisher.sync([old], old_id)
    old["status"] = "finished"
    new = {"id": new_id, "mode": "live", "status": "running"}
    uploader.fail_old = True
    await publisher.sync([new, old], new_id)
    assert old_id in publisher.pending_backfill
    uploader.fail_old = False
    await publisher.sync([new, old], new_id)
    assert old_id not in publisher.pending_backfill
    assert b'"status":"finished"' in publisher.uploaded[old_id][1]


@pytest.mark.parametrize(
    "url",
    ["http://example.test", "https://secret@example.test", "https://example.test/?token=private"],
)
def test_cli_requires_https_without_embedded_credentials(url):
    with pytest.raises(SystemExit):
        arguments(["--site-url", url])


def test_cli_defaults_and_no_remote_controller():
    args = arguments(["--site-url", "https://example.test/"])
    assert args.controller_url == "http://127.0.0.1:8768"
    assert args.site_url == "https://example.test"
    assert args.display == ":97" and args.interval == 1 and args.runs_dir == Path("runs")
    with pytest.raises(SystemExit):
        arguments(["--site-url", "https://example.test", "--controller-url", "http://remote.test"])
