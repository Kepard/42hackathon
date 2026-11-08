"""Local HTTP and websocket integration tests; no external model calls."""

import hashlib
import io
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from holospeed.app import create_app
from holospeed.config import Settings
from PIL import Image
from starlette.websockets import WebSocketDisconnect


def settings_for(tmp_path, mode="demo"):
    return Settings(
        mode=mode,
        runs_dir=tmp_path,
        bridge_token="bridge-private-test-value",
        holo_api_key="hk-private-test-value",
        jev_api_key="apikey_private-test-value",
        devin_api_key="cog_private-test-value",
    )


def assert_no_credentials(value, settings):
    serialized = json.dumps(value)
    for credential in (
        settings.bridge_token,
        settings.holo_api_key,
        settings.jev_api_key,
        settings.devin_api_key,
    ):
        assert credential not in serialized


def test_demo_endpoints_preserve_a_real_run_and_serve_replay_artifacts(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "HoloSpeed" in index.text
        assert index.headers["cache-control"] == "no-store"
        assert index.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in index.headers["content-security-policy"]
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/runs").json() == {"runs": []}
        assert client.get("/api/state").json()["status"] == "idle"

        with client.websocket_connect(
            "/events", headers={"Origin": "http://localhost:8765"}
        ) as updates:
            initial = updates.receive_json()
            assert_no_credentials(initial, settings)
            assert client.post("/api/start").json() == {"ok": True}
            # Wait for one actual demo action/ACK through the live websocket,
            # rather than sleeping through the entire 20-step demonstration.
            deadline = time.monotonic() + 5
            state = initial
            while state["metrics"]["actions"] < 1 and time.monotonic() < deadline:
                state = updates.receive_json()
                assert_no_credentials(state, settings)
            assert state["metrics"]["actions"] >= 1
            run_id = state["run_id"]
            assert state["mode"] == "demo"
            assert state["frame_url"]
            assert all(not event["type"].startswith("model_") for event in state["events"])

            assert client.post("/api/pause").json() == {"ok": True}
            paused = client.get("/api/state").json()
            assert paused["status"] == "paused"
            assert paused["keys"] == [] and paused["action"] is None
            assert paused["run_id"] == run_id
            assert client.post("/api/start").status_code == 200
            assert client.get("/api/state").json()["run_id"] == run_id
            assert client.post("/api/finish").json() == {"ok": True}
            assert client.get("/api/state").json()["status"] == "idle"

        summaries = client.get("/api/runs").json()["runs"]
        assert len(summaries) == 1
        assert summaries[0]["id"] == run_id
        assert summaries[0]["status"] == "finished"
        assert summaries[0]["metrics"]["actions"] >= 1
        assert_no_credentials(summaries, settings)

        replay_response = client.get(f"/api/runs/{run_id}")
        assert replay_response.status_code == 200
        replay = replay_response.json()
        assert replay["summary"]["id"] == run_id
        assert all(not event["type"].startswith("model_") for event in replay["events"])
        assert any(event["type"] == "ack" for event in replay["events"])
        observation = next(event for event in replay["events"] if event["type"] == "observation")
        screenshot = client.get(observation["data"]["frame_url"])
        assert screenshot.status_code == 200
        assert screenshot.headers["content-type"] == "image/png"
        assert hashlib.sha256(screenshot.content).hexdigest() == observation["data"]["sha256"]
        with Image.open(io.BytesIO(screenshot.content)) as image:
            assert image.size == (observation["data"]["width"], observation["data"]["height"])

        journal = client.get(f"/api/runs/{run_id}/events")
        assert journal.status_code == 200
        assert journal.headers["content-type"].startswith("application/x-ndjson")
        assert run_id + ".jsonl" in journal.headers["content-disposition"]
        events = [json.loads(line) for line in journal.text.splitlines()]
        assert any(event["type"] == "model_call" for event in events)
        assert events[-1]["type"] == "run_finished"
        assert_no_credentials(events, settings)
        assert client.get(f"/api/runs/{run_id}/frames/missing.png").status_code == 404
        assert client.get(f"/api/runs/{run_id}/frames/summary.json").status_code == 404
        assert client.get("/api/runs/not-a-valid-run").status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://untrusted.example"},
        {"Origin": "http://localhost.untrusted.example"},
        {"Origin": "null"},
        {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_origin_control_is_rejected_without_starting_a_run(tmp_path, headers):
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        for route in ("/api/start", "/api/pause", "/api/finish"):
            assert client.post(route, headers=headers).status_code == 403
        assert client.get("/api/state").json()["status"] == "idle"
        assert client.get("/api/runs").json() == {"runs": []}


def test_nonlocal_host_and_cross_origin_spectator_socket_are_rejected(tmp_path):
    app = create_app(settings_for(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/state", headers={"Host": "untrusted.example"}).status_code == 403
        with (
            pytest.raises(WebSocketDisconnect) as error,
            client.websocket_connect("/events", headers={"Origin": "https://untrusted.example"}),
        ):
            pytest.fail("Cross-origin spectator connection was accepted")
        assert error.value.code == 1008


@pytest.mark.parametrize("authorization", [None, "Bearer incorrect", "private-test-value"])
def test_bridge_requires_matching_bearer_token(tmp_path, authorization):
    app = create_app(settings_for(tmp_path, mode="live"))
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDisconnect) as error,
            client.websocket_connect("/bridge", headers=headers),
        ):
            pytest.fail("Unauthenticated bridge connection was accepted")
        assert error.value.code == 1008
        assert not app.state.bridge.connected
        assert client.post("/api/start").status_code == 409
        assert client.get("/api/runs").json() == {"runs": []}


@pytest.mark.parametrize(
    "hello",
    [
        {"type": "hello", "protocol": 2, "minecraft": "1.21.1"},
        {"type": "hello", "protocol": 1, "minecraft": "1.16.1"},
        ["not", "a", "message"],
    ],
)
def test_unsupported_bridge_handshake_is_closed(tmp_path, hello):
    settings = settings_for(tmp_path, mode="live")
    app = create_app(settings)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/bridge",
            headers={"Authorization": f"Bearer {settings.bridge_token}"},
        ) as socket:
            socket.send_json(hello)
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()
        assert not app.state.bridge.connected
        assert app.state.bridge.pending == {}


def test_single_bridge_f9_start_and_f8_emergency_pause(tmp_path, monkeypatch):
    settings = settings_for(tmp_path, mode="live")
    app = create_app(settings)
    started = threading.Event()
    starts = []

    async def fake_start():
        assert app.state.bridge.connected
        starts.append("start")
        app.state.engine.state["status"] = "running"
        started.set()

    # Keep actual websocket routing and pause/stop behavior, but do not invoke
    # cloud providers or request a Minecraft frame when the F9 event arrives.
    monkeypatch.setattr(app.state.engine, "start", fake_start)
    headers = {"Authorization": f"Bearer {settings.bridge_token}"}
    with TestClient(app) as client:
        with client.websocket_connect("/bridge", headers=headers) as socket:
            assert not app.state.bridge.connected  # Accepted is not handshaken.
            socket.send_json({"type": "hello", "protocol": 1, "minecraft": "1.21.1"})
            socket.send_json({"type": "event", "name": "start_requested", "detail": "F9 pressed"})
            assert started.wait(timeout=2)
            assert starts == ["start"]
            assert client.get("/api/state").json()["status"] == "running"

            with (
                pytest.raises(WebSocketDisconnect) as error,
                client.websocket_connect("/bridge", headers=headers),
            ):
                pytest.fail("A second Minecraft client was accepted")
            assert error.value.code == 1008
            assert app.state.bridge.connected

            socket.send_json({"type": "event", "name": "emergency_stop", "detail": "F8 pressed"})
            # Periodic HUD heartbeats may already be queued before the stop.
            # Bound the search so a missing stop cannot pass on endless status.
            for _ in range(12):
                stop = socket.receive_json()
                if stop["type"] == "stop":
                    break
                assert stop["type"] == "status"
            else:
                pytest.fail("F8 did not send a stop within 12 bridge messages")
            assert stop == {"type": "stop", "reason": "F8 pressed"}
            for _ in range(12):
                hud = socket.receive_json()
                assert hud["type"] == "status"
                if hud["status"] == "paused":
                    break
            else:
                pytest.fail("Paused status did not reach the HUD after F8")
            paused = client.get("/api/state").json()
            assert paused["status"] == "paused"
            assert paused["keys"] == [] and paused["action"] is None
            assert_no_credentials(paused, settings)

            started.clear()
            socket.send_json({"type": "event", "name": "start_requested", "detail": "F9 pressed"})
            assert started.wait(timeout=2)
            assert starts == ["start", "start"]
            assert client.get("/api/state").json()["status"] == "running"

        # The real disconnection handler pauses the engine and clears readiness.
        assert not app.state.bridge.connected
        assert client.get("/api/state").json()["status"] == "paused"


def test_abandoned_run_is_reported_interrupted_without_rewriting_its_journal(tmp_path):
    run_id = "20261108T070000-abcdef12"
    path = tmp_path / run_id
    path.mkdir()
    summary = {"id": run_id, "mode": "live", "status": "running", "duration_ms": 1200}
    (path / "summary.json").write_text(json.dumps(summary))
    (path / "events.jsonl").write_text('{"seq":1,"type":"run_started","data":{}}\n')
    with TestClient(create_app(settings_for(tmp_path))) as client:
        runs = client.get("/api/runs").json()["runs"]
        assert runs[0]["status"] == "interrupted"
        assert json.loads((path / "summary.json").read_text())["status"] == "running"
        assert client.get(f"/api/runs/{run_id}/events").text.endswith("\n")
