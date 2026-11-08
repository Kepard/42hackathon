"""Exact reviewed Devin briefs, durable submission ownership, and public projection."""

import asyncio
import hashlib
import json
import threading

import httpx
import pytest
from holospeed.app import create_app
from holospeed.config import Settings
from holospeed.engineering import Engineering

RUN = "20261108T093147-8080fe49"
SESSION = "session-demo"


@pytest.fixture
def settings(tmp_path):
    run = tmp_path / RUN
    run.mkdir()
    (run / "summary.json").write_text(json.dumps({"id": RUN, "status": "error", "mode": "live"}))
    (run / "events.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "type": "error",
                "data": {
                    "message": "No eligible skill /private/a.txt hk-test-key",
                    "inventory": {"minecraft:cobblestone": 6},
                },
            }
        )
        + "\n"
    )
    return Settings(runs_dir=tmp_path, devin_api_key="hk-test-key", devin_org_id="org-test")


def result(**extra):
    return {
        "session_id": SESSION,
        "url": f"https://app.devin.ai/sessions/{SESSION}",
        "status": "running",
        **extra,
    }


async def test_preview_is_exact_redacted_persisted_and_never_submits(settings):
    async def forbidden(*args):
        pytest.fail("Preparing a brief must not call Devin")

    jobs = Engineering(settings, submit=forbidden)
    brief = await jobs.prepare(RUN)
    assert brief["max_acu_limit"] == 2 and brief["can_submit"]
    assert brief["existing"] is None and jobs.latest(RUN) is None
    assert "hk-test-key" not in brief["prompt"] and "/private/a.txt" not in brief["prompt"]
    assert hashlib.sha256(brief["prompt"].encode()).hexdigest() == brief["digest_hash"]
    saved = json.loads(
        (settings.runs_dir / RUN / "engineering" / f"brief-{brief['digest_hash']}.json").read_text()
    )
    assert saved["prompt"] == brief["prompt"]


async def test_duplicate_create_uses_reviewed_snapshot_once_even_if_live_run_changes(settings):
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def submit(config, prompt):
        assert config is settings
        assert jobs.latest(RUN)["status"] == "submitting"  # Durable before API side effect.
        calls.append(prompt)
        entered.set()
        await release.wait()
        return result()

    jobs = Engineering(settings, submit=submit)
    brief = await jobs.prepare(RUN)
    (settings.runs_dir / RUN / "events.jsonl").write_text(
        '{"type":"error","data":{"message":"NEW WORLD EVIDENCE"}}\n'
    )
    receipt = await jobs.investigate(RUN, brief["digest_hash"])
    await entered.wait()
    assert receipt["status"] == "submitting"
    assert await jobs.investigate(RUN, brief["digest_hash"]) == receipt
    assert calls == [brief["prompt"]] and "NEW WORLD EVIDENCE" not in calls[0]
    release.set()
    await jobs.tasks[RUN]
    assert jobs.latest(RUN)["session_id"] == SESSION
    assert "prompt" not in jobs.latest(RUN)
    newer = await jobs.prepare(RUN)
    assert newer["existing"]["session_id"] == SESSION
    with pytest.raises(ValueError, match="already has"):
        await jobs.investigate(RUN, newer["digest_hash"])
    await jobs.close()


async def test_changed_saved_brief_and_missing_configuration_fail_before_submission(settings):
    calls = []

    async def submit(*args):
        calls.append(args)

    jobs = Engineering(settings, submit=submit)
    brief = await jobs.prepare(RUN)
    path = settings.runs_dir / RUN / "engineering" / f"brief-{brief['digest_hash']}.json"
    saved = json.loads(path.read_text())
    saved["prompt"] += "changed"
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="changed"):
        await jobs.investigate(RUN, brief["digest_hash"])
    settings.devin_api_key = ""
    assert not (await jobs.prepare(RUN))["can_submit"]
    with pytest.raises(ValueError, match="configured"):
        await jobs.investigate(RUN, brief["digest_hash"])
    assert not calls


async def test_unknown_submission_is_persisted_and_never_replayed(settings):
    calls = []

    async def submit(*args):
        calls.append(args)
        raise httpx.ReadTimeout("private error hk-test-key")

    jobs = Engineering(settings, submit=submit)
    brief = await jobs.prepare(RUN)
    await jobs.investigate(RUN, brief["digest_hash"])
    await jobs.tasks[RUN]
    assert jobs.latest(RUN)["status"] == "unknown"
    assert "hk-test-key" not in jobs.latest(RUN)["error"]
    restarted = Engineering(settings, submit=submit)
    existing = await restarted.investigate(RUN, brief["digest_hash"])
    await restarted.poll_once()
    assert existing["status"] == "unknown" and len(calls) == 1


async def test_shutdown_cancellation_marks_ambiguous_creation_without_retry(settings):
    entered = asyncio.Event()

    async def submit(*args):
        entered.set()
        await asyncio.Event().wait()

    jobs = Engineering(settings, submit=submit)
    brief = await jobs.prepare(RUN)
    await jobs.investigate(RUN, brief["digest_hash"])
    await entered.wait()
    await jobs.close()
    assert jobs.latest(RUN)["status"] == "unknown"


async def test_poll_projects_actual_findings_and_never_publishes_prompt_or_private_fields(settings):
    calls = []

    async def submit(*args):
        return result()

    async def poll(config, session_id):
        calls.append(session_id)
        return result(
            status_detail="finished",
            prompt="PRIVATE PROMPT",
            headers={"Authorization": "secret"},
            structured_output={
                "summary": "Review hk-test-key at /workspace/private/file.py",
                "likely_causes": ["table not reachable"],
                "proposed_tests": [],
                "proposed_fixes": [],
                "missing_evidence": [],
                "test_patches": [
                    {
                        "relative_path": "tests/test_table.py",
                        "purpose": "inventory target",
                        "code": "def test_target():\n    assert 6 == 6",
                        "validation_status": "Proposed, not run against current repository",
                    }
                ],
            },
        )

    jobs = Engineering(settings, submit=submit, poll=poll)
    brief = await jobs.prepare(RUN)
    await jobs.investigate(RUN, brief["digest_hash"])
    await jobs.tasks[RUN]
    await jobs.poll_once()
    public = jobs.latest(RUN)
    assert public["status_detail"] == "finished" and public["session_id"] == SESSION
    assert "prompt" not in public and "headers" not in public
    assert "hk-test-key" not in json.dumps(public) and "/workspace/private" not in json.dumps(
        public
    )
    assert public["structured_output"]["test_patches"][0]["code"].startswith("def test_target")
    assert public["structured_output"]["test_patches"][0]["relative_path"] == "tests/test_table.py"
    await jobs.poll_once()
    assert calls == [SESSION]  # Completed status is not repolled indefinitely.


async def test_interrupted_receipt_is_not_resubmitted_on_restart(settings):
    jobs = Engineering(settings)
    directory = jobs.directory(RUN)
    directory.mkdir()
    (directory / "latest.json").write_text(
        json.dumps(
            {
                "run_id": RUN,
                "digest_hash": "a" * 64,
                "status": "submitting",
                "max_acu_limit": 2,
                "created_at": "2026-11-08T00:00:00+00:00",
                "updated_at": "2026-11-08T00:00:00+00:00",
            }
        )
    )
    await jobs.poll_once()
    assert jobs.latest(RUN)["status"] == "unknown"


async def test_operator_api_routes_reviewed_digest_and_refuses_unrelated_payloads(settings):
    app = create_app(settings)

    async def submit(*args):
        return result()

    app.state.engineering.submit = submit
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.post(f"/api/devin/{RUN}/brief")
        brief = response.json()["data"]
        assert response.status_code == 200
        assert (
            await client.post(
                f"/api/devin/{RUN}/investigate",
                json={"digest_hash": brief["digest_hash"], "prompt": "replace"},
            )
        ).status_code == 409
        assert (
            await client.post(
                f"/api/devin/{RUN}/investigate",
                headers={"Origin": "https://untrusted.test"},
                json={"digest_hash": brief["digest_hash"]},
            )
        ).status_code == 403
        response = await client.post(
            f"/api/devin/{RUN}/investigate", json={"digest_hash": brief["digest_hash"]}
        )
        assert response.status_code == 200 and response.json()["data"]["status"] == "submitting"
        await app.state.engineering.tasks[RUN]
        status = (await client.get(f"/api/devin/{RUN}")).json()["engineering"]
        assert status["session_id"] == SESSION and "prompt" not in status
    await app.state.engineering.close()
    await app.state.engine.close()


def test_run_ids_and_symlinked_artifacts_cannot_read_outside_run_root(settings, tmp_path):
    jobs = Engineering(settings)
    for value in ("../secret", "https://host", RUN + "/../other"):
        with pytest.raises(ValueError, match="Unknown"):
            jobs.directory(value)
    directory = settings.runs_dir / RUN / "engineering"
    directory.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="directory"):
        jobs.directory(RUN)


async def test_corrupt_poll_receipt_and_failed_background_task_do_not_break_game_cleanup(settings):
    jobs = Engineering(settings)
    directory = jobs.directory(RUN)
    directory.mkdir()
    (directory / "latest.json").write_text("{}")
    await jobs.poll_once()  # A broken investigation cannot terminate the background supervisor.

    async def failed_disk_write():
        raise OSError("disk unavailable")

    jobs.tasks[RUN] = asyncio.create_task(failed_disk_write())
    await asyncio.sleep(0)
    await jobs.close()  # Must not prevent the caller from subsequently releasing game inputs.


async def test_separate_controller_process_claims_cannot_duplicate_paid_submission(settings):
    brief = await Engineering(settings).prepare(RUN)
    barrier = threading.Barrier(2)
    calls = []

    async def submit(*args):
        calls.append(args)
        return result()

    def controller():
        async def run():
            jobs = Engineering(settings, submit=submit)
            original = jobs.latest
            first = True

            def overlapping_read(run_id):
                nonlocal first
                value = original(run_id)
                if first:
                    first = False
                    barrier.wait(timeout=5)  # Both controllers see no existing receipt.
                return value

            jobs.latest = overlapping_read
            try:
                await jobs.investigate(RUN, brief["digest_hash"])
                await jobs.tasks[RUN]
                return "submitted"
            except ValueError as error:
                assert "already claimed" in str(error)
                return "claimed"

        return asyncio.run(run())

    outcomes = await asyncio.gather(asyncio.to_thread(controller), asyncio.to_thread(controller))
    assert sorted(outcomes) == ["claimed", "submitted"]
    assert len(calls) == 1
    jobs = Engineering(settings, submit=submit)
    receipt = jobs.latest(RUN)
    assert receipt["session_id"] == SESSION
    (jobs.directory(RUN) / "latest.json").unlink()
    recovered = await jobs.investigate(RUN, brief["digest_hash"])
    assert recovered["status"] == "unknown" and "owner_pid" not in recovered
    assert len(calls) == 1


async def test_game_controls_close_before_cancellation_suppressing_engineering_poll(settings):
    app = create_app(settings)
    entered, released = asyncio.Event(), asyncio.Event()
    original_close = app.state.engine.close

    async def poller():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await released.wait()

    async def close_game():
        await original_close()
        released.set()

    app.state.engineering.run = poller
    app.state.engine.close = close_game
    async with app.router.lifespan_context(app):
        await entered.wait()
    assert released.is_set()


def test_private_or_malformed_test_paths_are_safe_public_placeholders(settings):
    jobs = Engineering(settings)
    for path in (
        "/workspace/private/test.java",
        "../../private.java",
        "[OMITTED].java",
        "hk-test-key.py",
    ):
        findings = jobs._findings({"test_patches": [{"relative_path": path}]})
        assert findings["test_patches"][0]["relative_path"] == "proposed-tests/proposal-1.txt"
