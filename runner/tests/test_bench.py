import json
from types import SimpleNamespace

import httpx
import pytest

from mc_observatory.bench import run_batch
from mc_observatory.server import leaderboard
from mc_observatory.user_tasks import definitions, validate_task


async def test_batch_sequential_cross_product_and_labels():
    posts, polls = [], {}
    def handler(request):
        if request.method == "POST":
            assert not polls or all(v == 2 for v in polls.values())
            posts.append(json.loads(request.content))
            run_id = str(len(posts))
            polls[run_id] = 0
            return httpx.Response(202, json={"id": run_id})
        run_id = request.url.path.rsplit("/", 1)[1]
        polls[run_id] += 1
        return httpx.Response(200, json={"status": "running" if polls[run_id] == 1 else "finished", "summary": {"success": True}})
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler)) as client:
        result = await run_batch(client, task="get_log", models=["a", "b"], tiers=["raw", "skills"],
                                 timing="freeze", trials=2, label="baseline", poll_interval=0)
    assert len(result) == 8
    assert [(p["model"], p["tier"], p["trial_index"]) for p in posts] == [
        (m, t, i) for m in ["a", "b"] for t in ["raw", "skills"] for i in [1, 2]]
    assert all(p["label"] == "baseline" and p["timing"] == "freeze" and p["max_steps"] == 30 for p in posts)


@pytest.mark.parametrize("status", [409, 503])
async def test_batch_stops_on_backend_failure_without_launching_another_run(status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"detail": "unavailable"})
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await run_batch(client, task="get_log", models=["a"], tiers=["skills"], timing="freeze", trials=5, label="baseline")
    assert len(requests) == 1


async def test_empty_batch_rejected():
    with pytest.raises(ValueError):
        await run_batch(None, task="get_log", models=[], tiers=["skills"], timing="freeze", trials=1, label="baseline")


async def test_batch_records_terminal_error_and_continues():
    started = []
    def handler(request):
        if request.method == "POST":
            started.append(json.loads(request.content))
            return httpx.Response(202, json={"id": str(len(started))})
        return httpx.Response(200, json={"status": "error" if len(started) == 1 else "finished"})
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler)) as client:
        results = await run_batch(client, task="get_log", models=["a"], tiers=["skills"],
                                  timing="freeze", trials=2, label="baseline", poll_interval=0)
    assert [r["status"] for r in results] == ["error", "finished"]
    assert [p["trial_index"] for p in started] == [1, 2]


async def test_batch_poll_deadline_stops_before_next_trial(monkeypatch):
    clock = iter([0, 1000])
    monkeypatch.setattr("mc_observatory.bench.time", SimpleNamespace(monotonic=lambda: next(clock)))
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(202 if request.method == "POST" else 200,
                              json={"id": "stuck"} if request.method == "POST" else {"status": "running"})
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TimeoutError, match="no overlapping run"):
            await run_batch(client, task="get_log", models=["a"], tiers=["skills"], timing="freeze", trials=2, label="baseline")
    assert len(requests) == 2


def test_user_stopped_trial_is_not_model_failure():
    summary = {"score": 0, "success": False, "task": "get_log", "steps": 6,
               "config": {"model": "a", "tier": "skills", "timing": "freeze"}, "total_cost_usd": .01}
    row = leaderboard([{"summary": summary, "status": "stopped", "metadata": {"label": "baseline"}}])[0]
    assert row["runs"] == 1 and row["valid_runs"] == 0
    assert row["success_rate"] is None and row["infrastructure_failures"] == 1


def test_leaderboard_labels_timing_medians_and_infrastructure():
    def run(label, timing="freeze", success=False, steps=30, latency=300, tags=None):
        return {"status": "finished", "metadata": {"label": label}, "summary": {
            "config": {"model": "a", "tier": "skills", "timing": timing}, "task": "get_log",
            "success": success, "score": 0, "steps": steps, "wall_time_seconds": 10 if success else 40,
            "total_cost_usd": .02, "failure_tags": tags or []}, "steps": [{"api_latency_ms": latency}]}
    runs = [run("baseline", success=True, steps=10, latency=100), run("baseline"), run("after-fix", success=True),
            run("baseline", timing="realtime"), run("baseline", tags=["api_error"])]
    runs.append({"status": "error", "metadata": {"label": "baseline", "task": "get_log", "model": "a", "tier": "skills", "timing": "freeze"}})
    board = leaderboard(runs)
    assert len(board) == 3
    first, baseline = board[:2]
    assert first["label"] == "after-fix" and baseline["label"] == "baseline"
    assert baseline["runs"] == 4 and baseline["valid_runs"] == 2 and baseline["infrastructure_failures"] == 2
    assert baseline["success_rate"] == .5
    assert baseline["median_time_seconds"] == 10 and baseline["median_steps"] == 20
    assert baseline["p50_api_latency_ms"] == 300
    assert baseline["cost_per_run"] is None
    assert baseline["failure_counts"] == {"api_error": 1, "infrastructure_error": 1}


def test_builtin_spawn_schema_and_invalid_spawn():
    task = definitions()[0]
    assert task["seed"] == 12345 and task["inventory"] == []
    assert validate_task(task)["spawn"]["time_of_day"] == 6000
    for change in [{"pitch": 100}, {"x": "bad"}, {"time_of_day": -1}]:
        with pytest.raises(ValueError):
            validate_task({**task, "spawn": {**task["spawn"], **change}})
