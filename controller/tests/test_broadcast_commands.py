"""Authenticated dashboard command delivery, expiry, and crash-safe idempotence."""

import asyncio
import copy
import json

import httpx
import pytest
from holospeed.broadcast import CloudUploader, CommandConsumer, utc_iso

SITE = "https://observer.test"
LOCAL = "http://127.0.0.1:8768"
SITE_TOKEN = "private-site-test-token"
INGEST_TOKEN = "private-ingest-test-token"
COMMAND_ID = "9c2657d4-cc76-4b73-bf9d-409ab041b89a"
RUN_ID = "20261108T070000-deadbeef"
NOW = 1_700_000_000.0


def command(action="resume", **changes):
    return {
        "id": COMMAND_ID,
        "action": action,
        "status": "pending",
        "created_at": utc_iso(NOW - 1),
        "expires_at": utc_iso(NOW + 29),
        **changes,
    }


@pytest.fixture
def clock(monkeypatch):
    value = [NOW]
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: value[0])
    return value


class Peer:
    def __init__(self, pending):
        self.pending = pending
        self.requests = []
        self.results = []
        self.fail_results = False
        self.local_handler = None

    @property
    def mutations(self):
        return [
            request
            for request in self.requests
            if request.url.host == "127.0.0.1" and request.method == "POST"
        ]

    async def handle(self, request):
        self.requests.append(request)
        if request.url.host == "observer.test":
            assert request.headers["Authorization"] == f"Bearer {INGEST_TOKEN}"
            assert request.headers["OAI-Sites-Authorization"] == f"Bearer {SITE_TOKEN}"
            if request.method == "GET" and request.url.path == "/api/commands/next":
                return httpx.Response(200, json={"command": copy.deepcopy(self.pending)})
            if request.method == "POST" and request.url.path.endswith("/result"):
                payload = json.loads(request.content)
                self.results.append(payload)
                if self.fail_results:
                    return httpx.Response(503, json={"error": "temporary unavailable"})
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "command": {
                            **(self.pending or {}),
                            **payload,
                        },
                    },
                )
            pytest.fail(f"Unexpected cloud request: {request.method} {request.url.path}")
        assert request.url.host == "127.0.0.1"
        assert "Authorization" not in request.headers
        assert "OAI-Sites-Authorization" not in request.headers
        if request.method == "GET" and request.url.path == "/api/state":
            return httpx.Response(200, json={"run_id": RUN_ID, "status": "running"})
        assert request.method == "POST"
        assert request.url.path in {"/api/start", "/api/pause", "/api/finish", "/api/new-run"}
        if self.local_handler:
            return await self.local_handler(request)
        return httpx.Response(200, json={"ok": True})


def consumer(client, directory, *, secrets=()):
    uploader = CloudUploader(client, SITE, SITE_TOKEN, INGEST_TOKEN)
    value = CommandConsumer(client, LOCAL, uploader, directory, secrets=secrets)
    return value, uploader


@pytest.mark.parametrize(
    ("action", "paths"),
    [
        ("start_new", ["/api/new-run"]),
        ("resume", ["/api/start"]),
        ("pause", ["/api/pause"]),
        ("stop", ["/api/finish"]),
    ],
)
async def test_commands_map_to_local_endpoints_with_cloud_auth_kept_remote(
    tmp_path, clock, action, paths
):
    peer = Peer(command(action))
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
    assert [request.url.path for request in peer.mutations] == paths
    assert peer.results[-1]["status"] == "completed"
    assert peer.results[-1].get("run_id") == (None if action == "start_new" else RUN_ID)
    if action == "start_new":
        assert json.loads(peer.mutations[0].content) == {"command_id": COMMAND_ID}
        assert "preparation accepted" in peer.results[-1]["detail"]
    assert isinstance(peer.results[-1]["detail"], str)
    assert len(peer.results[-1]["detail"]) <= 300
    persisted = (tmp_path / ".broadcast-command-state.json").read_text()
    assert COMMAND_ID in persisted
    assert SITE_TOKEN not in persisted and INGEST_TOKEN not in persisted


async def test_command_claim_is_atomically_persisted_before_any_local_action(tmp_path, clock):
    peer = Peer(command("start_new"))

    async def inspect_claim(request):
        path = tmp_path / ".broadcast-command-state.json"
        assert path.is_file()
        payload = json.loads(path.read_text())
        assert COMMAND_ID in json.dumps(payload)
        return httpx.Response(200, json={"ok": True})

    peer.local_handler = inspect_claim
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
    assert len(peer.mutations) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"expires_at": utc_iso(NOW - 0.001)},
        {"created_at": utc_iso(NOW + 10), "expires_at": utc_iso(NOW + 30)},
        {"created_at": utc_iso(NOW - 1), "expires_at": utc_iso(NOW + 60)},
        {"created_at": "not-a-date"},
        {"expires_at": "2026-01-01T00:00:00"},
        {"id": "../../start"},
        {"action": "teleport"},
        {"status": "completed"},
    ],
)
async def test_expired_future_long_lived_and_malformed_commands_never_execute(
    tmp_path, clock, changes
):
    peer = Peer(command(**changes))
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        try:
            await value.poll_once()
        except ValueError:
            pass
    assert peer.mutations == []


async def test_duplicate_command_id_never_repeats_completed_action(tmp_path, clock):
    peer = Peer(command())
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
        await value.poll_once()
        # Immutable IDs cannot be reused to smuggle a different action.
        peer.pending = command("pause")
        try:
            await value.poll_once()
        except ValueError:
            pass
    assert [request.url.path for request in peer.mutations] == ["/api/start"]


@pytest.mark.parametrize("restart", [False, True])
async def test_failed_result_upload_retries_without_repeating_local_action(
    tmp_path, clock, restart
):
    peer = Peer(command())
    peer.fail_results = True
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, uploader = consumer(client, tmp_path)
        await value.poll_once()
        assert len(peer.mutations) == 1
        peer.fail_results = False
        if restart:
            value, uploader = consumer(client, tmp_path)
        uploader.cooldown_until = 0
        await value.poll_once()
    assert len(peer.mutations) == 1
    assert len(peer.results) >= 2
    assert all(result["status"] == "completed" for result in peer.results)


async def test_restart_after_uncertain_local_execution_reports_failure_without_replay(
    tmp_path, clock
):
    peer = Peer(command())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def interrupted_action(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"ok": True})

    peer.local_handler = interrupted_action
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        task = asyncio.create_task(value.poll_once())
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (tmp_path / ".broadcast-command-state.json").is_file()
        peer.local_handler = None
        restarted, _ = consumer(client, tmp_path)
        await restarted.poll_once()
    assert len(peer.mutations) == 1
    assert peer.results[-1]["status"] == "failed"
    assert peer.results[-1]["detail"]


async def test_start_new_is_one_idempotent_launch_request_even_when_ack_arrives_at_expiry(
    tmp_path, clock
):
    peer = Peer(command("start_new"))

    async def expire_after_acceptance(request):
        assert request.url.path == "/api/new-run"
        clock[0] = NOW + 30
        return httpx.Response(202, json={"ok": True})

    peer.local_handler = expire_after_acceptance
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
    assert [request.url.path for request in peer.mutations] == ["/api/new-run"]
    assert peer.results[-1]["status"] == "completed"


async def test_local_http_failure_is_sanitized_persisted_and_never_retried(tmp_path, clock):
    secret = "opaque-private-operator-secret"
    peer = Peer(command())

    async def reject(request):
        return httpx.Response(409, json={"detail": f"Rejected with {secret} Bearer hidden-token"})

    peer.local_handler = reject
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path, secrets=(secret,))
        await value.poll_once()
        await value.poll_once()
    assert len(peer.mutations) == 1
    assert peer.results[-1]["status"] == "failed"
    serialized = json.dumps(peer.results) + (tmp_path / ".broadcast-command-state.json").read_text()
    assert secret not in serialized
    assert "hidden-token" not in serialized


async def test_corrupt_persistent_ledger_fails_closed(tmp_path, clock):
    (tmp_path / ".broadcast-command-state.json").write_text("{invalid json")
    peer = Peer(command())
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        try:
            value, _ = consumer(client, tmp_path)
            await value.poll_once()
        except (ValueError, OSError, RuntimeError):
            pass
    assert peer.mutations == []


async def test_no_pending_command_does_not_write_or_touch_controller(tmp_path, clock):
    peer = Peer(None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
    assert peer.mutations == []
    assert peer.results == []


async def test_competing_consumers_do_not_dispatch_or_reclassify_an_inflight_command(
    tmp_path, clock
):
    peer = Peer(command())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_action(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"ok": True})

    peer.local_handler = delayed_action
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        first, _ = consumer(client, tmp_path)
        second, _ = consumer(client, tmp_path)
        task = asyncio.create_task(first.poll_once())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            await asyncio.wait_for(second.poll_once(), timeout=1)
            assert len(peer.mutations) == 1
            assert peer.results == []  # The other process must not call this a crashed claim.
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=1)
        await second.poll_once()
    assert len(peer.mutations) == 1
    assert all(result["status"] == "completed" for result in peer.results)


async def test_command_expiring_while_claim_is_persisted_is_not_dispatched(
    tmp_path, clock, monkeypatch
):
    peer = Peer(command())
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        write = value.ledger.write

        def slow_fsync(commands):
            write(commands)
            if any(
                record.get("stage") == "start" and record["status"] == "claimed"
                for record in commands.values()
            ):
                clock[0] = NOW + 30

        monkeypatch.setattr(value.ledger, "write", slow_fsync)
        await value.poll_once()
    assert peer.mutations == []
    assert peer.results[-1]["status"] == "failed"
