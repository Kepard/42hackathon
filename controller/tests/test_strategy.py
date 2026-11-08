import asyncio
import copy
import json

import httpx
import pytest
from holospeed.config import Settings
from holospeed.models import Observation
from holospeed.providers import ProviderError, ReplanRequired
from holospeed.strategy import StrategyProvider, summarize_state


@pytest.fixture
def settings():
    return Settings(holo_api_key="test-holo-secret", jev_api_key="test-jev-secret")


@pytest.fixture
def candidates():
    return [
        {
            "id": "mine_birch",
            "skill": "mine",
            "item": "minecraft:birch_log",
            "target_count": 4,
            "max_ticks": 1200,
            "position": {"x": 2, "y": 64, "z": 0},
            "reason": "Reachable birch tree",
        },
        {
            "id": "craft_table",
            "skill": "craft",
            "item": "minecraft:crafting_table",
            "target_count": 1,
            "max_ticks": 200,
            "reason": "Four planks are available",
        },
    ]


@pytest.fixture
def state():
    return {
        "id": "state-1",
        "tick": 42,
        "sampled_at_ms": 1000,
        "cache_age_ms": 0,
        "world_ready": True,
        "screen_open": False,
        "dimension": "minecraft:overworld",
        "player": {"position": {"x": 0, "y": 64, "z": 0}, "health": 20, "hunger": 18},
        "inventory": [
            {"slot": 0, "item": "minecraft:birch_log", "count": 2},
            {"slot": 1, "item": "minecraft:birch_log", "count": 3},
        ],
        "nearby_blocks": [
            {
                "id": "minecraft:birch_log",
                "position": {"x": 2, "y": 64, "z": 0},
                "solid": True,
                "fluid": "minecraft:empty",
                "hardness": 2,
            }
        ],
        "local_blocks": [
            {
                "id": "minecraft:air",
                "position": {"x": i, "y": 64, "z": 0},
                "solid": False,
                "fluid": "minecraft:empty",
            }
            for i in range(729)
        ],
        "local_bounds": {
            "min": {"x": -4, "y": 60, "z": -4},
            "max": {"x": 4, "y": 68, "z": 4},
            "complete": True,
        },
        "nearby_entities": [
            {
                "id": 3,
                "type": "minecraft:item",
                "position": {"x": 1, "y": 64, "z": 0},
                "stack": {"item": "minecraft:birch_log", "count": 9},
            }
        ],
    }


def holo_response(arguments=None):
    return {
        "model": "holo4-35b-a3b",
        "usage": {"prompt_tokens": 250, "completion_tokens": 30},
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "select_skill",
                                "arguments": json.dumps(
                                    arguments
                                    or {
                                        "candidate_id": "mine_birch",
                                        "rationale": "Gather the reachable birch logs before crafting.",
                                    }
                                ),
                            },
                        }
                    ]
                },
            }
        ],
    }


def test_state_summary_keeps_inventory_and_geometry_meaning_without_raw_arrays(state):
    original = copy.deepcopy(state)
    state["screen"] = {
        "type": "InventoryScreen",
        "title": "Inventory",
        "slots": [{"item": "minecraft:birch_log", "count": 5}],
        "cursor": {"item": "minecraft:birch_log", "count": 1},
    }
    result = summarize_state(state)
    assert result["inventory_counts"] == {"minecraft:birch_log": 5}
    assert result["nearby_entity_summary"]["nearest"][0]["dropped_stack"]["count"] == 9
    assert result["screen"]["visible_slot_items_not_player_inventory"]["minecraft:birch_log"] == 5
    assert result["screen"]["cursor_stack"]["count"] == 1
    assert result["local_sample"]["records"] == 729
    assert result["nearby_resources"][0]["nearest"][0]["distance"] == 2
    assert "local_blocks" not in result and "nearby_blocks" not in result
    assert len(json.dumps(result)) < 5000
    assert state["local_blocks"] == original["local_blocks"]


async def test_holo_selects_only_ids_and_preserves_candidate_parameters(
    settings, state, candidates
):
    original = copy.deepcopy(candidates)
    recorded = []

    def handle(request):
        assert str(request.url) == "https://api.hcompany.ai/v1/chat/completions"
        body = json.loads(request.content)
        assert body["tool_choice"] == "required"
        parameters = body["tools"][0]["function"]["parameters"]
        assert parameters["properties"]["candidate_id"]["enum"] == ["mine_birch", "craft_table"]
        assert set(parameters["properties"]) == {"candidate_id", "rationale"}
        assert parameters["additionalProperties"] is False
        payload = json.loads(body["messages"][1]["content"])
        assert payload["game_state"]["inventory_counts"]["minecraft:birch_log"] == 5
        assert payload["candidates"][0]["target_count"] == 4
        assert payload["context"]["phase"] == "wood"
        assert len(body["messages"]) == 2
        assert "local_blocks" not in json.dumps(body)
        return httpx.Response(200, json=holo_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        planner = StrategyProvider(
            settings, client, lambda kind, data: recorded.append((kind, data))
        )
        result = await planner.choose(None, state, candidates, {"phase": "wood"})
        await planner.aclose()
        assert not client.is_closed
    assert result.candidate_id == "mine_birch"
    assert result.provider == "holo"
    assert result.usage["prompt_tokens"] == 250
    assert result.latency_ms >= 0
    assert candidates == original
    assert [kind for kind, _ in recorded] == ["model_request", "model_response"]
    assert recorded[0][1]["call_id"] == recorded[1][1]["call_id"]


async def test_holo_accepts_optional_screenshot_without_putting_it_in_state(
    settings, state, candidates
):
    observation = Observation(
        id="obs",
        frame_id="frame",
        tick=42,
        width=64,
        height=64,
        image_base64="aW1hZ2U=",
        mime_type="image/png",
    )

    def handle(request):
        body = json.loads(request.content)
        assert len(body["messages"]) == 3
        assert (
            body["messages"][2]["content"][1]["image_url"]["url"]
            == "data:image/png;base64,aW1hZ2U="
        )
        return httpx.Response(200, json=holo_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await StrategyProvider(settings, client).choose(observation, state, candidates, {})
    assert result.candidate_id == "mine_birch"


async def test_holo_reserves_room_for_tool_arguments_after_reasoning(settings, state, candidates):
    """The observed 203 reasoning tokens left too little room in a 256-token cap."""
    response = holo_response()
    response["usage"].update(
        completion_tokens=320, completion_tokens_details={"reasoning_tokens": 203}
    )

    def handle(request):
        body = json.loads(request.content)
        assert body["reasoning_effort"] == settings.holo_reasoning_effort
        assert body["max_tokens"] == 512
        if body["max_tokens"] < response["usage"]["completion_tokens"]:
            pytest.fail("Reasoning and required tool arguments must fit the output ceiling")
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await StrategyProvider(settings, client).choose(None, state, candidates, {})
    assert result.candidate_id == "mine_birch"
    assert result.usage["completion_tokens"] == 320
    assert result.rationale == "Gather the reachable birch logs before crafting."


@pytest.mark.parametrize("complete_arguments", [False, True])
async def test_holo_truncation_requires_fallback_even_with_a_known_candidate(
    settings, state, candidates, complete_arguments
):
    arguments = {"candidate_id": "mine_birch"}
    if complete_arguments:
        arguments["rationale"] = "Gather nearby logs."
    response = holo_response(arguments)
    response["choices"][0]["finish_reason"] = "length"
    response["usage"].update(
        completion_tokens=256, completion_tokens_details={"reasoning_tokens": 203}
    )
    calls = 0

    def handle(_):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError, match="invalid skill selection") as raised:
            await StrategyProvider(settings, client).choose(None, state, candidates, {})
    assert calls == 1, "A provider retry must not bypass the engine's planning deadline"
    assert raised.value.response["choices"][0]["finish_reason"] == "length"
    assert raised.value.response["usage"]["completion_tokens_details"]["reasoning_tokens"] == 203


@pytest.mark.parametrize(
    "arguments",
    [
        {"candidate_id": "invented_skill", "rationale": "Try this"},
        {"candidate_id": "mine_birch", "rationale": "Try this", "target_count": 999},
        {"candidate_id": "mine_birch", "rationale": "Try this", "keys": ["attack"]},
        {"candidate_id": "mine_birch", "rationale": "   "},
    ],
)
async def test_holo_refuses_unknown_ids_or_parameter_changes(
    settings, state, candidates, arguments
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=holo_response(arguments)))
    ) as client:
        with pytest.raises(ProviderError, match="invalid skill selection") as raised:
            await StrategyProvider(settings, client).choose(None, state, candidates, {})
    assert raised.value.provider == "holo"
    assert raised.value.response["choices"]


async def test_jev_uses_structured_choice_without_images_or_confidence_veto(
    settings, state, candidates
):
    def handle(request):
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert "TypeSafe-Danger-Zone-Images" not in request.headers
        body = json.loads(request.content)
        assert "assets" not in body
        assert set(body["questions"]["skill"]["criteria"]) == {
            "mine_birch",
            "craft_table",
            "__replan__",
        }
        assert body["state"]["game_state"]["inventory_counts"] == {"minecraft:birch_log": 5}
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "usage": {"input_tokens": 70, "output_tokens": 5},
                "answers": {
                    "skill": {
                        "type": "choice",
                        "choice": "mine_birch",
                        "confidence": 0.01,
                        "probabilities": {
                            "mine_birch": 0.34,
                            "craft_table": 0.33,
                            "__replan__": 0.33,
                        },
                    }
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await StrategyProvider(settings, client).choose(
            None, state, candidates, {}, provider="jev"
        )
    assert result.candidate_id == "mine_birch"
    assert result.provider == "jev"


@pytest.mark.parametrize(
    "answer,error",
    [
        ({"type": "choice", "choice": "__replan__"}, ReplanRequired),
        ({"type": "choice", "choice": "unknown"}, ProviderError),
        ({"type": "choice", "choice": "mine_birch", "confidence": "high"}, ProviderError),
        ({"type": "noul", "noul": 1}, ProviderError),
    ],
)
async def test_jev_replan_and_malformed_responses_require_explicit_fallback(
    settings, state, candidates, answer, error
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"answers": {"skill": answer}})
        )
    ) as client:
        with pytest.raises(error) as raised:
            await StrategyProvider(settings, client).choose(
                None, state, candidates, {}, provider="jev"
            )
    assert raised.value.provider == "jev"


async def test_secret_redaction_in_journal_and_result(settings, state, candidates):
    records = []

    def handle(request):
        assert settings.jev_api_key not in request.content.decode()
        return httpx.Response(
            200,
            json=holo_response(
                {
                    "candidate_id": "mine_birch",
                    "rationale": f"Choose nearby logs {settings.jev_api_key}",
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = StrategyProvider(
            settings, client, lambda kind, data: records.append((kind, data))
        )
        result = await provider.choose(
            None,
            state,
            candidates,
            {"note": settings.jev_api_key, "api_key": settings.holo_api_key},
        )
    serialized = json.dumps([records, result.model_dump()])
    assert settings.jev_api_key not in serialized
    assert settings.holo_api_key not in serialized


async def test_jev_fresh_state_validation_is_an_actual_singleton_choice_with_replan(
    settings, state, candidates
):
    def handle(request):
        body = json.loads(request.content)
        question = body["questions"]["skill"]
        assert set(question["criteria"]) == {"mine_birch", "__replan__"}
        assert "freshly sampled" in question["instructions"]
        assert "proposal, not evidence" in question["instructions"]
        assert "already achieved its postcondition" in question["instructions"]
        assert body["state"]["context"]["previous_holo_plan"]["candidate_id"] == "mine_birch"
        assert "assets" not in body
        return httpx.Response(
            200, json={"answers": {"skill": {"type": "choice", "choice": "mine_birch"}}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await StrategyProvider(settings, client).choose(
            None,
            state,
            candidates[:1],
            {
                "decision_role": "fresh_state_validation",
                "previous_holo_plan": {"candidate_id": "mine_birch", "rationale": "Gather wood"},
            },
            provider="jev",
        )
    assert "Jev accepted" in result.rationale
    assert result.candidate_id == "mine_birch"


async def test_cancellation_is_journaled_without_retry(settings, state, candidates):
    calls = 0
    records = []

    async def handle(_):
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = StrategyProvider(
            settings, client, lambda kind, data: records.append((kind, data))
        )
        with pytest.raises(asyncio.CancelledError):
            await provider.choose(None, state, candidates, {})
    assert calls == 1
    assert [kind for kind, _ in records] == ["model_request", "model_cancelled"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda items: [],
        lambda items: [items[0], items[0]],
        lambda items: [{**items[0], "id": "__replan__"}],
    ],
)
async def test_invalid_candidate_sets_never_make_requests(settings, state, candidates, mutate):
    def handle(_):
        pytest.fail("Invalid candidate sets must not call providers")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError):
            await StrategyProvider(settings, client).choose(None, state, mutate(candidates), {})
