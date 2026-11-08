"""Gladia final text follows the authenticated, durable command queue exactly once."""

import json
import uuid

import httpx
import pytest
from test_broadcast_commands import RUN_ID, Peer, command, consumer

SESSION = "45463597-20b7-4af7-b3b3-f5fb778203ab"


def voice_command(**changes):
    return (
        command(
            "voice",
            text="Please pause the game",
            run_id=RUN_ID,
            session_id=SESSION,
            utterance_id="00-00000011",
        )
        | changes
    )


class VoicePeer(Peer):
    async def handle(self, request):
        if request.url.host != "127.0.0.1" or request.url.path != "/api/voice":
            return await super().handle(request)
        self.requests.append(request)
        assert "Authorization" not in request.headers
        if self.local_handler:
            return await self.local_handler(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": {"intent": "pause", "reply": "Gameplay paused; agent controls released."},
            },
        )


async def test_final_transcript_is_forwarded_once_with_immutable_run_and_speech_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    pending = voice_command()
    peer = VoicePeer(pending)
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
        await value.poll_once()
    assert len(peer.mutations) == 1
    assert json.loads(peer.mutations[0].content) == {
        "command_id": pending["id"],
        **{k: pending[k] for k in ("text", "run_id", "session_id", "utterance_id")},
    }
    assert peer.results[-1]["run_id"] == RUN_ID
    assert peer.results[-1]["data"]["intent"] == "pause"
    assert peer.results[-1]["status"] == "completed"


@pytest.mark.parametrize(
    "change",
    [
        {"text": ""},
        {"text": "x" * 1001},
        {"session_id": "bad"},
        {"run_id": "../world"},
        {"utterance_id": "bad/path"},
    ],
)
async def test_invalid_voice_payloads_never_reach_controller(tmp_path, monkeypatch, change):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    peer = VoicePeer(voice_command(**change))
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
    assert not peer.mutations


async def test_nullable_run_identity_is_preserved_for_typed_idle_questions(tmp_path, monkeypatch):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    peer = VoicePeer(
        voice_command(
            run_id=None,
            session_id="typed",
            utterance_id=str(uuid.uuid4()),
            text="What are you doing?",
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
    assert peer.results[-1]["run_id"] is None


async def test_stale_run_rejection_does_not_substitute_current_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    peer = VoicePeer(voice_command())

    async def reject(request):
        return httpx.Response(
            409, json={"detail": "The run changed before this voice command arrived; speak again"}
        )

    peer.local_handler = reject
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path)
        await value.poll_once()
    assert peer.results[-1]["status"] == "failed" and peer.results[-1]["run_id"] == RUN_ID
    assert "run changed" in peer.results[-1]["detail"]


async def test_ambiguous_voice_call_never_replays_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    peer = VoicePeer(voice_command())

    async def lost(request):
        raise httpx.ReadTimeout("response lost", request=request)

    peer.local_handler = lost
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        first, _ = consumer(client, tmp_path)
        await first.poll_once()
        second, _ = consumer(client, tmp_path)
        await second.poll_once()
    assert len(peer.mutations) == 1
    assert (
        peer.results[-1]["status"] == "failed" and "may have applied" in peer.results[-1]["detail"]
    )


async def test_bad_or_credential_bearing_response_is_never_publicly_forwarded(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("holospeed.broadcast.time.time", lambda: 1_700_000_000.0)
    peer = VoicePeer(voice_command())

    async def respond(request):
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": {
                    "intent": "guidance",
                    "reply": "Recorded private-example-key guidance.",
                    "guidance_id": str(uuid.uuid4()),
                },
            },
        )

    peer.local_handler = respond
    async with httpx.AsyncClient(transport=httpx.MockTransport(peer.handle)) as client:
        value, _ = consumer(client, tmp_path, secrets=("private-example-key",))
        await value.poll_once()
    assert "private-example-key" not in json.dumps(peer.results)
    assert peer.results[-1]["status"] == "completed"
