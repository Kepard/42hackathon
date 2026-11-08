"""Devin commands preserve selected-run identity and keep review briefs private."""

import hashlib
import json

import httpx
import pytest
from holospeed.broadcast import RunPublisher
from test_broadcast_commands import RUN_ID, Peer, command, consumer

OTHER_RUN = "20261108T070001-deadbeef"
PROMPT = "Exact redacted diagnostic brief"
DIGEST = hashlib.sha256(PROMPT.encode()).hexdigest()


class EngineeringPeer(Peer):
    async def handle(self, request):
        if request.url.host != "127.0.0.1" or "/api/devin/" not in request.url.path:
            return await super().handle(request)
        self.requests.append(request)
        assert "Authorization" not in request.headers
        if self.local_handler:
            return await self.local_handler(request)
        data = {"run_id": OTHER_RUN, "digest_hash": DIGEST, "max_acu_limit": 2}
        if request.url.path.endswith("/brief"):
            data.update(prompt=PROMPT, can_submit=True, existing=None)
        else:
            assert json.loads(request.content) == {"digest_hash": DIGEST}
            data.update(
                status="submitting",
                created_at="2026-11-08T00:00:00Z",
                updated_at="2026-11-08T00:00:00Z",
            )
        return httpx.Response(200, json={"ok": True, "data": data})


@pytest.mark.parametrize(
    "action,endpoint", [("prepare_investigation", "brief"), ("investigate", "investigate")]
)
async def test_devin_commands_target_selected_archived_run_and_return_private_data(
    tmp_path, monkeypatch, action, endpoint
):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    peer = EngineeringPeer(
        command(
            action, run_id=OTHER_RUN, **({"digest_hash": DIGEST} if action == "investigate" else {})
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
        await value.poll_once()
    assert len(peer.mutations) == 1
    assert peer.mutations[0].url.path == f"/api/devin/{OTHER_RUN}/{endpoint}"
    report = peer.results[-1]
    assert report["run_id"] == OTHER_RUN != RUN_ID  # Never substitute the current game run.
    assert report["data"]["digest_hash"] == DIGEST
    assert report["status"] == "completed"


@pytest.mark.parametrize(
    "changes", [{"run_id": "../other"}, {"run_id": None}, {"digest_hash": "invalid"}]
)
async def test_bad_investigation_identity_never_reaches_local_controller(
    tmp_path, monkeypatch, changes
):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    peer = EngineeringPeer(command("investigate", run_id=OTHER_RUN, digest_hash=DIGEST) | changes)
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
    assert not peer.mutations


async def test_prompt_changed_by_redaction_is_not_forwarded_as_reviewed_brief(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    peer = EngineeringPeer(command("prepare_investigation", run_id=OTHER_RUN))
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path, secrets=("diagnostic",))
        await value.poll_once()
    assert peer.results[-1]["status"] == "failed"
    assert "data" not in peer.results[-1]


async def test_engineering_updates_republish_archived_run_without_publishing_private_brief(
    tmp_path,
):
    run = tmp_path / RUN_ID
    run.mkdir()
    summary = {"id": RUN_ID, "status": "error", "mode": "live"}
    (run / "summary.json").write_text(json.dumps(summary))
    (run / "events.jsonl").write_text('{"seq":1,"type":"error","data":{"message":"blocked"}}\n')
    directory = run / "engineering"
    directory.mkdir()
    private = "PRIVATE IMMUTABLE BRIEF"
    (directory / f"brief-{DIGEST}.json").write_text(json.dumps({"prompt": private}))
    value = {
        "run_id": RUN_ID,
        "digest_hash": DIGEST,
        "max_acu_limit": 2,
        "status": "submitting",
        "created_at": "2026-11-08T00:00:00Z",
        "updated_at": "2026-11-08T00:00:00Z",
        "prompt": "ACCIDENTAL PRIVATE FIELD",
    }
    (directory / "latest.json").write_text(json.dumps(value))

    class Uploader:
        def __init__(self):
            self.payloads = []

        async def post(self, path, content):
            self.payloads.append((path, json.loads(content)))
            return True

    uploader = Uploader()
    publisher = RunPublisher(tmp_path, uploader, ())
    await publisher.sync([summary], None)
    assert len(uploader.payloads) == 1
    first = uploader.payloads[0][1]
    assert "prompt" not in first["engineering"] and private not in json.dumps(first)
    value.update(status="running", updated_at="2026-11-08T00:00:15Z", session_id="session-1")
    (directory / "latest.json").write_text(json.dumps(value))
    await publisher.sync([summary], None)
    assert len(uploader.payloads) == 2
    assert uploader.payloads[-1][1]["engineering"]["status"] == "running"
    await publisher.sync([summary], None)
    assert len(uploader.payloads) == 2
