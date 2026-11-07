import asyncio
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from mc_observatory.jev_eval import Budget, RESERVE_PER_CALL, binary_metrics, evaluate, iou, percentile, prepare


def fixture_run(tmp_path):
    run = tmp_path / "runs" / "example"
    run.mkdir(parents=True)
    Image.new("RGB", (640, 480), "green").save(run / "frame.png")
    record = {"step": 0, "screenshot_ref": "frame.png", "input_image_refs": ["frame.png"], "tokens": {"in": 10}}
    (run / "steps.jsonl").write_text(json.dumps(record) + "\n")
    (run / "initial_state.json").write_text(json.dumps({"state": {"inventory": []}}))
    return run, record


@pytest.mark.parametrize("inventory,expected", [([], False), ([{"slot": 0, "id": "minecraft:oak_log", "count": 1}], True),
                                                ([{"slot": 9, "id": "minecraft:oak_log", "count": 1}], False)])
def test_prepare_proves_exact_api_input_and_aligns_initial_inventory(tmp_path, inventory, expected):
    run, record = fixture_run(tmp_path)
    (run / "initial_state.json").write_text(json.dumps({"state": {"inventory": inventory}}))
    root = tmp_path / "dataset"
    rows = prepare(run.parent, root, count=1)
    assert (root / rows[0]["image"]).read_bytes() == (run / "frame.png").read_bytes()
    assert rows[0]["source_ref"] in rows[0]["source_input_refs"]
    assert rows[0]["labels"]["log_in_hotbar"] is expected
    assert rows[0]["labels"]["crosshair_on_log"] is None
    with pytest.raises(ValueError, match="existing dataset"):
        prepare(run.parent, root, count=1)
    record["step"] = 1
    (run / "steps.jsonl").write_text(json.dumps(record))
    assert prepare(run.parent, tmp_path / "other", count=1)[0]["labels"]["log_in_hotbar"] is None


@pytest.mark.parametrize("change", [{"input_image_refs": []}, {"tokens": {"in": 0}}, {"screenshot_ref": "../outside.png"}])
def test_prepare_rejects_unproven_or_failed_requests(tmp_path, change):
    run, record = fixture_run(tmp_path)
    record.update(change)
    (run / "steps.jsonl").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Only 0"):
        prepare(run.parent, tmp_path / "dataset", count=1)


def test_metrics_empty_single_class_ties_and_boxes():
    assert binary_metrics([])["accuracy"] is None
    assert binary_metrics([(.8, True)])["auc"] is None
    assert binary_metrics([(.8, True), (.8, False)])["auc"] == .5
    assert binary_metrics([(.9, True), (.1, False)])["accuracy"] == 1
    assert binary_metrics([(.2, True), (.1, False)])["recall"] == 0
    assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1
    assert iou([0, 0, 0, 0], [0, 0, 0, 0]) == 0
    assert percentile([], .9) is None
    assert percentile([0, 10], .9) == 9


async def test_budget_reserves_inflight_and_charges_unknown_failures():
    budget = Budget(limit=RESERVE_PER_CALL * 1.5)
    await budget.reserve()
    with pytest.raises(RuntimeError, match="Budget"):
        await budget.reserve()
    await budget.settle()
    assert budget.spent == RESERVE_PER_CALL
    assert budget.reserved == 0
    with pytest.raises(ValueError):
        Budget(2)


async def test_evaluation_sends_only_pixels_and_static_questions(tmp_path):
    pytest.importorskip("typesafe_sdk")
    run, _ = fixture_run(tmp_path)
    root = tmp_path / "dataset"
    prepare(run.parent, root, count=1)
    calls = []

    class Client:
        async def system_one(self, **kwargs):
            calls.append(kwargs)
            assert set(kwargs["state"]) == {"screenshot"}
            assert set(kwargs["questions"]) == {"crosshair_on_log", "log_in_hotbar", "nearest_trunk", "next_move"}
            return SimpleNamespace(model="jev-1.14.0", usage=SimpleNamespace(input_tokens=100, output_tokens=5),
                                   nouls={key: SimpleNamespace(noul=.2) for key in ("crosshair_on_log", "log_in_hotbar")},
                                   boxes={"nearest_trunk": SimpleNamespace(box=SimpleNamespace(left=0, top=0, right=1, bottom=1))},
                                   choices={"next_move": SimpleNamespace(choice="stop")})

    result = await evaluate(root, client=Client())
    assert len(calls) == 2
    assert [r["successes"] for r in result] == [1, 1]
    assert result[0]["log_in_hotbar"]["accuracy"] == 1
    with pytest.raises(ValueError, match="overwrite"):
        await evaluate(root, client=Client())
    rows = [json.loads((root / "manifest.jsonl").read_text())]
    (root / rows[0]["image"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        await evaluate(root, client=Client())


async def test_first_api_failure_aborts_instead_of_flooding_service(tmp_path, monkeypatch):
    pytest.importorskip("typesafe_sdk")
    run, _ = fixture_run(tmp_path)
    root = tmp_path / "dataset"
    prepare(run.parent, root, count=1)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")

    class Client:
        async def system_one(self, **kwargs):
            raise RuntimeError("connection refused test-secret")

    result = await evaluate(root, client=Client())
    assert result[0]["failures"] == 1 and result[1]["calls"] == 0
    assert "test-secret" not in (root / "responses.jsonl").read_text()


async def test_owned_sdk_client_header_and_async_cleanup(tmp_path, monkeypatch):
    sdk = pytest.importorskip("typesafe_sdk")
    run, _ = fixture_run(tmp_path)
    root = tmp_path / "dataset"
    prepare(run.parent, root, count=1)
    created, closed = [], []

    class Client:
        def __init__(self, **kwargs):
            created.append(kwargs)
        async def system_one(self, **kwargs):
            raise RuntimeError("probe failed")
        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(sdk, "AsyncTypeSafeClient", Client)
    await evaluate(root)
    assert created[0]["headers"] == {"TypeSafe-Danger-Zone-Images": "true"}
    assert created[0]["retry"].max_retries == 0
    assert closed == [True]
    with pytest.raises(ValueError, match="overwrite"):
        await evaluate(root)
    assert len(created) == 1


async def test_budget_rejection_waits_for_paid_inflight_results(tmp_path, monkeypatch):
    sdk = pytest.importorskip("typesafe_sdk")
    run, _ = fixture_run(tmp_path)
    root = tmp_path / "dataset"
    row = prepare(run.parent, root, count=1)[0]
    (root / "manifest.jsonl").write_text("".join(json.dumps({**row, "id": str(i)}) + "\n" for i in range(3)))
    active, completed = [], []

    class Client:
        def __init__(self, **kwargs):
            pass
        async def system_one(self, **kwargs):
            active.append(True)
            await asyncio.sleep(.01)
            active.pop()
            completed.append(True)
            return SimpleNamespace(model="jev-1.14.0", usage=SimpleNamespace(input_tokens=0, output_tokens=0),
                                   nouls={key: SimpleNamespace(noul=.2) for key in ("crosshair_on_log", "log_in_hotbar")},
                                   boxes={"nearest_trunk": SimpleNamespace(box=SimpleNamespace(left=0, top=0, right=1, bottom=1))},
                                   choices={"next_move": SimpleNamespace(choice="stop")})
        async def aclose(self):
            assert not active

    monkeypatch.setattr(sdk, "AsyncTypeSafeClient", Client)
    result = await evaluate(root, limit=RESERVE_PER_CALL * 1.5)
    assert len(completed) == 4
    assert [r["successes"] for r in result] == [3, 1]
    assert len((root / "responses.jsonl").read_text().splitlines()) == 4
