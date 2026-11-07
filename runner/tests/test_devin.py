import asyncio
import copy
import json
import zipfile
from email import policy
from email.parser import BytesParser

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft7Validator

from mc_observatory.devin import DEFAULT_ORG, SCHEMAS, DevinAPI, DevinError, build_bundle
from mc_observatory.run import task_definition
from mc_observatory.server import ObservatoryService, create_app
from mc_observatory.user_tasks import TASK_SCHEMA, add_task, definitions, validate_task


INVESTIGATION = {"headline": "No log acquired", "failure_category": "budget_exhausted", "root_cause": "Step cap reached; precise targeting cause remains unverified.",
    "evidence": [{"step": 29, "observation": "No log milestone recorded"}],
    "suggested_fixes": [{"area": "prompt", "change": "Check observable mining progress", "expected_effect": "Less repeated ineffective mining"}], "confidence": .6}


class Mod:
    def __init__(self): self.events = asyncio.Queue()
    async def connect(self): pass
    async def subscribe(self): pass
    async def close(self): pass
    async def call(self, *args, **kwargs): return {}


@pytest.fixture
def recorded(tmp_path):
    run = tmp_path / "fixture-run"
    (run / "screenshots").mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"success": False, "score": 0, "task": "get_log"}))
    records = []
    for i in range(30):
        records.append({"step": i, "reasoning": "PRIVATE REASONING", "intent": ["mine"], "failure_tags": ["timeout"], "executed_actions": []})
        (run / "screenshots" / f"{i:05d}.png").write_bytes(b"offline-png")
    (run / "steps.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    (run / "interventions.jsonl").write_text('{"source":"ui","action":"pause"}\n')
    return run


def test_bundle_keeps_observables_caps_screenshots_and_includes_boundaries(recorded, tmp_path):
    output = tmp_path / "evidence.zip"
    build_bundle(recorded, output)
    with zipfile.ZipFile(output) as bundle:
        pictures = [name for name in bundle.namelist() if name.endswith(".png")]
        assert len(pictures) == 20
        assert "screenshots/00000.png" in pictures and "screenshots/00029.png" in pictures
        assert "screenshots/00001.png" in pictures
        steps = bundle.read("steps.jsonl")
        assert len(steps.splitlines()) == 30 and b"PRIVATE REASONING" not in steps
        assert b'"intent"' in steps
        assert json.loads(bundle.read("manifest.json"))["run_id"] == recorded.name
        assert b'"source":"ui"' in bundle.read("interventions.jsonl")


def test_empty_missing_and_oversized_bundle(recorded, tmp_path):
    (recorded / "steps.jsonl").write_text("")
    (recorded / "interventions.jsonl").unlink()
    output = tmp_path / "empty.zip"
    build_bundle(recorded, output)
    with zipfile.ZipFile(output) as bundle:
        assert bundle.read("steps.jsonl") == bundle.read("interventions.jsonl") == b""
    (recorded / "steps.jsonl").write_text('{"step":0}\n')
    (recorded / "screenshots/00000.png").write_bytes(b"x" * (26 * 1024 * 1024))
    with pytest.raises(ValueError, match="25 MiB"): build_bundle(recorded, output)
    (recorded / "summary.json").unlink()
    with pytest.raises(ValueError, match="completed"): build_bundle(recorded, output)


@pytest.mark.parametrize("changes", [{"goal": {}}, {"id": "../escape"}, {"seed": "123"}, {"extra": True},
    {"inventory": [{"id": "minecraft:oak_log", "count": 0}]}, {"goal": {"crafted": "bad:id"}}, {"time_limit_seconds": 0}])
def test_task_rejects_invalid_schema(changes):
    task = copy.deepcopy(definitions()[1]); task.update(changes)
    with pytest.raises(ValueError): validate_task(task)


def test_user_task_is_runnable_and_cannot_override_builtin(tmp_path, monkeypatch):
    path = tmp_path / "user-tasks.json"
    monkeypatch.setenv("OBS_USER_TASKS_FILE", str(path))
    task = copy.deepcopy(definitions()[1]); task["id"] = "local_planks"
    Draft7Validator.check_schema(TASK_SCHEMA)
    for schema in SCHEMAS.values(): Draft7Validator.check_schema(schema)
    assert add_task(task) == task
    assert task_definition("local_planks") == task
    with pytest.raises(ValueError, match="exists"): add_task(task)
    with pytest.raises(ValueError, match="exists"): add_task(definitions()[0])


def test_obtain_goal_is_not_reinterpreted_from_item_name_in_description():
    task = copy.deepcopy(definitions()[0])
    task.update(id="collect_table", description="Collect a crafting table", goal={"item": "minecraft:crafting_table"})
    assert validate_task(task) == task


def test_api_shapes_workflows_persistence_poll_and_add_locally(recorded, monkeypatch):
    monkeypatch.setenv("DEVIN_API_KEY", "test-key-not-real")
    monkeypatch.delenv("DEVIN_ORG_ID", raising=False)
    calls = []; kind = "investigate"
    task = copy.deepcopy(definitions()[1]); task["id"] = "local_planks"
    results = {"investigate": INVESTIGATION, "challenge": {"task": task, "test_summary": "Offline test passed", "pr_url": "https://github.com/Kepard/42hackathon/pull/2"},
               "review_fix": {"pr_url": "https://github.com/Kepard/42hackathon/pull/3", "summary": "Fixed prompt", "tests_added": "Mock test", "risk": "Low"}}
    def respond(request):
        nonlocal kind
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer test-key-not-real"
        assert request.url.path.startswith(f"/v3/organizations/{DEFAULT_ORG}/")
        if request.url.path.endswith("/attachments"):
            mime = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + request.headers["content-type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + request.content)
            part = next(mime.iter_parts())
            assert part.get_param("name", header="content-disposition") == "file"
            assert part.get_filename() == "evidence.zip"
            assert part.get_payload(decode=True).startswith(b"PK")
            return httpx.Response(200, json={"attachment_id": "att-test", "name": "evidence.zip", "url": "https://api.devin.ai/attachment/test"})
        if request.method == "POST":
            body = json.loads(request.content)
            kind = "challenge" if "task" in body["structured_output_schema"]["properties"] else "review_fix" if "risk" in body["structured_output_schema"]["properties"] else "investigate"
            assert body["structured_output_required"] and body["memory_enabled"] is False
            assert body["max_acu_limit"] == 5
            assert body["repos"] == ([] if kind == "investigate" else ["github.com/Kepard/42hackathon"])
            assert "observable" in body["prompt"] and "hidden reasoning" in body["prompt"] and "runner/tests" in body["prompt"]
            if kind == "investigate": assert body["attachment_urls"] == ["https://api.devin.ai/attachment/test"]
            if kind == "challenge": assert "Schema:" in body["prompt"] and "Example:" in body["prompt"]
            return httpx.Response(200, json={"session_id": "devin-mock", "url": "https://app.devin.ai/sessions/mock", "status": "running"})
        return httpx.Response(200, json={"session_id": "devin-mock", "url": "https://app.devin.ai/sessions/mock", "status": "running", "status_detail": "finished", "structured_output": results[kind]})
    service = ObservatoryService(recorded.parent, Mod())
    with TestClient(create_app(service, DevinAPI(httpx.MockTransport(respond)))) as client:
        assert client.get("/api/devin/jobs").json() == []
        assert client.post("/api/devin/jobs", json={"kind": "investigate"}).status_code == 422
        assert client.post("/api/devin/jobs", json={"kind": "challenge"}).status_code == 422
        job = client.post("/api/devin/jobs", json={"kind": "investigate", "run_id": recorded.name}).json()
        done = client.get(f'/api/devin/jobs/{job["id"]}').json()
        assert done["done"] and done["result"] == INVESTIGATION
        before = len(calls)
        assert client.get(f'/api/devin/jobs/{job["id"]}').json() == done
        assert len(calls) == before
        assert client.get("/api/devin/jobs?run_id=missing").json() == []
        assert client.get("/api/devin/jobs/missing").status_code == 404
        assert client.post(f'/api/devin/jobs/{job["id"]}/add-task', json={}).status_code == 409
        fix = client.post("/api/devin/jobs", json={"kind": "review_fix", "investigation_id": job["id"], "fix_index": 0}).json()
        assert fix["run_id"] == recorded.name
        assert client.get(f'/api/devin/jobs/{fix["id"]}').json()["result"] == results["review_fix"]
        assert client.post("/api/devin/jobs", json={"kind": "review_fix", "investigation_id": job["id"], "fix_index": 99}).status_code == 422
        challenge = client.post("/api/devin/jobs", json={"kind": "challenge", "description": "Craft planks"}).json()
        client.get(f'/api/devin/jobs/{challenge["id"]}')
        assert client.post(f'/api/devin/jobs/{challenge["id"]}/add-task', json={}).json() == task
        assert "local_planks" in client.get("/api/status").json()["tasks"]
        assert client.post(f'/api/devin/jobs/{challenge["id"]}/add-task', json={}).status_code == 422
        assert client.get("/api/devin/task-schema").json() == TASK_SCHEMA
        assert (recorded.parent / "devin" / job["id"] / "job.json").is_file()


async def test_api_error_messages_credentials_message_shape(monkeypatch):
    monkeypatch.delenv("DEVIN_API_KEY", raising=False)
    api = DevinAPI()
    with pytest.raises(DevinError, match="not configured"): await api.get("devin-test")
    monkeypatch.setenv("DEVIN_API_KEY", "test-key")
    monkeypatch.setenv("DEVIN_ORG_ID", "org-custom")
    def error(request):
        assert "/org-custom/" in request.url.path
        return httpx.Response(403, json={"detail": "DO NOT EXPOSE REMOTE ERROR"})
    api = DevinAPI(httpx.MockTransport(error))
    with pytest.raises(DevinError, match="HTTP 403"): await api.get("devin-test")
    def message(request):
        assert request.url.path.endswith("/sessions/devin-test/messages")
        assert json.loads(request.content) == {"message": "hello", "attachment_urls": []}
        return httpx.Response(200, json={})
    await DevinAPI(httpx.MockTransport(message)).message("devin-test", "hello")
    with pytest.raises(DevinError, match="Invalid"): await api.get("../bad")
    def bare_id(request):
        assert "/sessions/devin-" + "a" * 32 in request.url.path
        return httpx.Response(200, json={})
    api = DevinAPI(httpx.MockTransport(bare_id))
    await api.get("a" * 32)
    await api.message("a" * 32, "hello")


def test_poll_retry_invalid_output_and_local_add_revalidation(recorded, monkeypatch):
    monkeypatch.setenv("DEVIN_API_KEY", "test-key")
    responses = [httpx.Response(429, json={}), httpx.Response(200, json={"session_id": "devin-test", "url": "https://app.devin.ai/sessions/test", "status": "running", "status_detail": "finished", "structured_output": {"headline": "incomplete"}})]
    api = DevinAPI(httpx.MockTransport(lambda request: responses.pop(0)))
    service = ObservatoryService(recorded.parent, Mod())
    with TestClient(create_app(service, api)) as client:
        devin = client.app.state.devin
        job = {"id": "a" * 32, "kind": "investigate", "run_id": recorded.name, "session_id": "devin-test", "done": False, "created_at": "now", "result": None}
        devin.save(job)
        first = client.get(f'/api/devin/jobs/{job["id"]}').json()
        assert "429" in first["error"] and not first["done"]
        second = client.get(f'/api/devin/jobs/{job["id"]}').json()
        assert second["done"] and second["result"] is None and "schema validation" in second["error"]
        job.update(kind="challenge", done=True, completed=True, result={"task": {"id": "invalid"}})
        devin.save(job)
        assert client.post(f'/api/devin/jobs/{job["id"]}/add-task', json={}).status_code == 422
        assert not devin.user_file.exists()


def test_create_failure_persisted_no_retry(recorded, monkeypatch):
    monkeypatch.setenv("DEVIN_API_KEY", "test-key")
    calls = []
    def fail(request):
        calls.append(request)
        return httpx.Response(403, json={})
    service = ObservatoryService(recorded.parent, Mod())
    with TestClient(create_app(service, DevinAPI(httpx.MockTransport(fail)))) as client:
        response = client.post("/api/devin/jobs", json={"kind": "challenge", "description": "test"})
        assert response.status_code == 502 and "HTTP 403" in response.json()["detail"]
        jobs = client.get("/api/devin/jobs").json()
        assert jobs[0]["status"] == "error"
        client.get(f'/api/devin/jobs/{jobs[0]["id"]}')
        assert len(calls) == 1


def test_valid_result_waiting_for_user_is_ready_without_rewriting_remote_status(recorded):
    service = ObservatoryService(recorded.parent, Mod())
    app = create_app(service)
    job = {"id": "b" * 32, "kind": "investigate", "run_id": recorded.name, "result": None}
    result = app.state.devin.accept(job, {"session_id": "devin-test", "url": "https://app.devin.ai/sessions/test", "status": "running", "status_detail": "waiting_for_user", "structured_output": INVESTIGATION})
    assert result["completed"] and result["done"] and result["result"] == INVESTIGATION
    assert result["status"] == "running" and result["status_detail"] == "waiting_for_user"
