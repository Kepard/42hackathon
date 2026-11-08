import asyncio
import json

import httpx
import pytest
from holospeed.config import Settings
from holospeed.models import Action, Decision, Observation
from holospeed.providers import HoloProvider, JevProvider, ProviderError, ReplanRequired


@pytest.fixture
def settings():
    configuration = Settings(
        holo_api_key="test-holo-secret", jev_api_key="test-jev-secret", jev_model="jev-1.14.0"
    )
    configuration.holo_compact = False
    return configuration


@pytest.fixture
def observation():
    return Observation(
        id="obs-1",
        frame_id="frame-1",
        tick=20,
        width=1280,
        height=720,
        image_base64="aW1hZ2U=",
        mime_type="image/jpeg",
    )


@pytest.fixture
def decision():
    return Decision(
        objective="Gather wood",
        strategy="Walk toward the visible oak",
        expected_effect="Approach the trunk",
        action=Action(keys=["forward", "sprint"], duration_ticks=8, yaw_delta=10),
    )


def holo_response(decision):
    return {
        "model": "holo4-27b",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "propose_decision",
                                "arguments": json.dumps(decision),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 400, "completion_tokens": 200},
    }


def jev_response():
    return {
        "model": "jev-1.14.0",
        "usage": {"input_tokens": 400, "output_tokens": 30},
        "answers": {
            "action": {
                "type": "choice",
                "choice": "continue",
                "confidence": 0.9,
                "probabilities": {"continue": 0.95, "replan": 0.05},
            },
            "danger": {"type": "noul", "noul": 0.05},
            "can_continue": {"type": "noul", "noul": 0.95},
        },
    }


async def test_holo_declares_native_tools_and_normalizes_cursor(settings, observation, decision):
    observation.screen_open = True
    raw = decision.model_dump()
    raw["action"]["keys"] = []
    raw["action"]["cursor"] = {"x": 750, "y": 250, "button": "right"}

    def handle(request):
        assert str(request.url) == "https://api.hcompany.ai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-holo-secret"
        body = json.loads(request.content)
        assert body["tool_choice"] == "required"
        assert body["reasoning_effort"] == settings.holo_reasoning_effort
        parameters = body["tools"][0]["function"]["parameters"]
        assert "$defs" not in json.dumps(parameters)
        assert "$ref" not in json.dumps(parameters)
        assert "action" not in parameters["properties"]
        assert "title" not in json.dumps(parameters)
        assert "default" not in json.dumps(parameters)
        assert parameters["properties"]["click_x"]["maximum"] == 1000
        assert body["max_tokens"] == 700
        parts = body["messages"][-1]["content"]
        assert parts[0]["text"] == "<observation>\n"
        assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64,aW1hZ2U="
        return httpx.Response(200, json=holo_response(raw))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = HoloProvider(settings, client)
        result = await provider.decide(observation, {"objective": "Gather wood"})
        await provider.aclose()
        assert not client.is_closed  # A borrowed client's lifetime belongs to its caller.
    assert result.decision.action.cursor.x == 0.75
    assert result.decision.action.cursor.y == 0.25
    assert result.usage["prompt_tokens"] == 400
    assert result.latency_ms >= 0
    assert "test-holo-secret" not in json.dumps(result.request)


@pytest.mark.parametrize(
    "change", ["duration", "opposing", "unknown", "cursor", "milestone", "truncated"]
)
async def test_holo_rejects_invalid_or_unsafe_actions(settings, observation, decision, change):
    raw = decision.model_dump()
    if change == "duration":
        raw["action"]["duration_ticks"] = 2000
    elif change == "opposing":
        raw["action"]["keys"] = ["forward", "back"]
    elif change == "unknown":
        raw["action"]["command"] = "/gamemode creative"
    elif change == "cursor":
        raw["action"]["cursor"] = {"x": 500, "y": 500}
    elif change == "milestone":
        raw["milestone"] = "wood"
        raw["evidence"] = ""
    payload = holo_response(raw)
    if change == "truncated":
        payload["choices"][0]["finish_reason"] = "length"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError) as error:
            await HoloProvider(settings, client).decide(observation, {})
    assert error.value.request["model"] == settings.holo_model
    assert error.value.response == payload


async def test_jev_image_opt_in_and_bounded_continuation(settings, observation, decision):
    def handle(request):
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["typesafe-danger-zone-images"] == "true"
        body = json.loads(request.content)
        assert body["model"] == "jev-1.14.0"
        reference = body["state"]["screenshot"]
        assert body["assets"][reference] == {
            "type": "image",
            "format": "jpg",
            "data_base64": "aW1hZ2U=",
        }
        assert body["questions"]["action"]["type"] == "choice"
        assert set(body["questions"]["action"]["criteria"]) == {"continue", "replan"}
        assert body["state"]["candidates"] == {"continue": decision.action.model_dump()}
        assert body["questions"]["danger"]["type"] == "noul"
        return httpx.Response(200, json=jev_response())

    decision.milestone = "wood"
    decision.evidence = "A log is visible in the hotbar"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await JevProvider(settings, client).decide(
            observation,
            {"previous_decision": decision.model_dump()},
        )
    assert result.decision.objective == decision.objective
    assert result.decision.strategy == decision.strategy
    assert result.decision.milestone is None
    assert result.decision.action == decision.action
    assert result.model == "jev-1.14.0"


@pytest.mark.parametrize(
    "change", ["hazard", "uncertain", "replan", "invalid", "nan", "inappropriate"]
)
async def test_jev_abstains_on_risk_or_invalid_answer(settings, observation, decision, change):
    payload = jev_response()
    if change == "hazard":
        payload["answers"]["danger"]["noul"] = 0.9
    elif change == "uncertain":
        payload["answers"]["action"]["confidence"] = 0.4
        payload["answers"]["action"]["probabilities"] = {
            "continue": 0.64,
            "replan": 0.36,
        }
    elif change == "replan":
        payload["answers"]["action"]["choice"] = "replan"
    elif change == "invalid":
        payload["answers"]["action"]["choice"] = "invented-action"
    elif change == "nan":
        payload["answers"]["danger"]["noul"] = "NaN"
    else:
        payload["answers"]["can_continue"]["noul"] = 0.2
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ReplanRequired) as error:
            await JevProvider(settings, client).decide(
                observation,
                {"previous_decision": decision.model_dump()},
            )
    assert error.value.request["assets"]
    assert error.value.response == payload
    assert error.value.latency_ms >= 0


async def test_jev_does_not_repeat_menu_actions(settings, observation, decision):
    decision.action.keys = ["inventory"]

    def forbidden(request):
        pytest.fail("A one-shot action must replan without paying for a Jev call")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ReplanRequired):
            await JevProvider(settings, client).decide(
                observation,
                {"previous_decision": decision.model_dump()},
            )


async def test_http_failure_is_redacted_and_not_retried(settings, observation):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "test-holo-secret", "api_key": "hidden"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError) as error:
            await HoloProvider(settings, client).decide(
                observation,
                {
                    "objective": "Test",
                    "failures": ["test-holo-secret"],
                },
            )
    assert len(calls) == 1
    assert error.value.response["http_status"] == 429
    assert "test-holo-secret" not in json.dumps(vars(error.value))
    assert "hidden" not in json.dumps(error.value.response)
    assert "429" in str(error.value)


async def test_transport_timeout_has_logging_evidence(settings, observation):
    def handle(request):
        raise httpx.ReadTimeout("test-holo-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ProviderError) as error:
            await HoloProvider(settings, client).decide(observation, {})
    assert "test-holo-secret" not in str(error.value)
    assert error.value.request["model"] == settings.holo_model
    assert error.value.latency_ms >= 0


@pytest.mark.parametrize("arguments", ["null", "[]", "17", '"execute_code"'])
async def test_holo_non_object_arguments_preserve_failure_evidence(
    settings,
    observation,
    decision,
    arguments,
):
    payload = holo_response(decision.model_dump())
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError) as error:
            await HoloProvider(settings, client).decide(observation, {})
    assert error.value.response == payload


async def test_model_request_is_journaled_before_network_and_response_is_redacted(
    settings,
    observation,
    decision,
):
    events = []

    def handle(request):
        assert len(events) == 1
        assert events[0][0] == "model_request"
        response = holo_response(decision.model_dump())
        response["provider_debug"] = "test-holo-secret"
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = HoloProvider(settings, client)
        provider.journal = lambda kind, data: events.append((kind, data))
        await provider.decide(observation, {})
    assert [kind for kind, data in events] == ["model_request", "model_response"]
    assert events[0][1]["call_id"] == events[1][1]["call_id"]
    assert events[1][1]["http_status"] == 200
    assert "test-holo-secret" not in json.dumps(events)


async def test_cancelled_inference_keeps_exact_request_and_cancellation_event(
    settings,
    observation,
):
    started = asyncio.Event()
    blocked = asyncio.Event()
    events = []

    async def handle(request):
        started.set()
        await blocked.wait()
        raise AssertionError("The request should have been cancelled")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = HoloProvider(settings, client)
        provider.journal = lambda kind, data: events.append((kind, data))
        task = asyncio.create_task(provider.decide(observation, {}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert [kind for kind, data in events] == ["model_request", "model_cancelled"]
    assert events[0][1]["request"]["messages"][-1]["content"][1]["image_url"]
    assert events[0][1]["call_id"] == events[1][1]["call_id"]
    assert events[1][1]["latency_ms"] >= 0


async def test_valid_80_tick_log_mining(settings, observation, decision):
    raw = decision.model_dump()
    raw["action"] = {"keys": ["attack"], "duration_ticks": 80}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=holo_response(raw)))
    ) as client:
        result = await HoloProvider(settings, client).decide(observation, {})
    assert result.decision.action.duration_ticks == 80
    assert result.decision.action.keys == ["attack"]


async def test_reject_80_tick_movement(settings, observation, decision):
    raw = decision.model_dump()
    raw["action"] = {"keys": ["forward"], "duration_ticks": 80}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=holo_response(raw)))
    ) as client:
        with pytest.raises(ProviderError):
            await HoloProvider(settings, client).decide(observation, {})


async def test_holo_answer_marks_completion_claim_without_inventing_milestone(
    settings,
    observation,
    decision,
):
    payload = holo_response(decision.model_dump())
    payload["choices"][0]["message"]["tool_calls"][0]["function"] = {
        "name": "answer",
        "arguments": json.dumps({"content": "A stone pickaxe is visible in the hotbar."}),
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        result = await HoloProvider(settings, client).decide(
            observation,
            {"run_goal": "Obtain stone tools"},
        )
    assert result.completed is True
    assert result.decision.action.keys == []
    assert result.decision.milestone is None
    assert result.decision.objective == "Obtain stone tools"
    assert result.decision.evidence == "A stone pickaxe is visible in the hotbar."


async def test_holo_live_wire_json_encoded_action_regression(settings, observation):
    # Both hosted Holo4 variants returned this nested JSON-string shape in a live probe.
    wire_decision = {
        "objective": "Hold still",
        "strategy": "Observe only",
        "expected_effect": "No input",
        "action": '{"keys":[],"duration_ticks":1}',
    }
    payload = holo_response(wire_decision)
    original_arguments = payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        result = await HoloProvider(settings, client).decide(observation, {})
    assert result.decision.action.keys == []
    assert result.decision.action.duration_ticks == 1
    assert (
        result.response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        == original_arguments
    )


async def test_holo_json_encoded_cursor_is_normalized_and_strict(settings, observation, decision):
    observation.screen_open = True
    wire_decision = decision.model_dump()
    wire_decision["action"] = json.dumps(
        {
            "keys": [],
            "duration_ticks": 1,
            "cursor": '{"x":750,"y":250,"button":"left"}',
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=holo_response(wire_decision))
        )
    ) as client:
        result = await HoloProvider(settings, client).decide(observation, {})
    assert result.decision.action.cursor.x == 0.75
    assert result.decision.action.cursor.y == 0.25


@pytest.mark.parametrize(
    "action",
    [
        '{"keys":["forward"],"duration_ticks":80}',
        '{"keys":[],"duration_ticks":"1"}',
        "__import__('os').system('echo forbidden')",
        "[]",
        "null",
    ],
)
async def test_holo_encoded_action_keeps_strict_validation(settings, observation, decision, action):
    wire_decision = decision.model_dump()
    wire_decision["action"] = action
    payload = holo_response(wire_decision)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError) as error:
            await HoloProvider(settings, client).decide(observation, {})
    assert error.value.response == payload


@pytest.mark.parametrize(
    "action",
    [
        Action(keys=["attack"], duration_ticks=80),
        Action(keys=[], duration_ticks=1),
        Action(keys=[], duration_ticks=6),
    ],
)
async def test_jev_preserves_long_mining_and_deduplicates_no_input(
    settings,
    observation,
    decision,
    action,
):
    decision.action = action

    def handle(request):
        body = json.loads(request.content)
        assert set(body["questions"]["action"]["criteria"]) == {"continue", "replan"}
        assert set(body["state"]["candidates"]) == {"continue"}
        payload = jev_response()
        payload["answers"]["action"].update(
            choice="continue",
            probabilities={"continue": 0.95, "replan": 0.05},
        )
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await JevProvider(settings, client).decide(
            observation,
            {"previous_decision": decision.model_dump()},
        )
    assert result.decision.action == action


async def test_holo_flat_wire_decision_defaults_and_gui_mapping(settings, observation):
    observation.screen_open = True
    payload = holo_response(
        {
            "objective": "Craft planks",
            "strategy": "Click the visible log stack.",
            "expected_effect": "Pick up the logs",
            "keys": [],
            "duration_ticks": 1,
            "click_x": 750,
            "click_y": 250,
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        result = await HoloProvider(settings, client).decide(observation, {})
    assert result.decision.action.cursor.x == 0.75
    assert result.decision.action.cursor.y == 0.25
    assert result.decision.action.cursor.button == "left"
    assert result.decision.action.yaw_delta == 0
    assert result.decision.action.hotbar is None
    assert result.decision.milestone is None


@pytest.mark.parametrize(
    "extra",
    [
        {"click_x": 500},
        {"click_button": "left"},
        {"click_x": 500, "click_y": 1001},
        {"click_x": "500", "click_y": 500},
        {"unknown_command": "execute"},
        {"duration_ticks": "1"},
        {"keys": ["forward"], "duration_ticks": 80},
    ],
)
async def test_holo_flat_wire_fields_remain_strict(settings, observation, extra):
    observation.screen_open = True
    raw = {
        "objective": "Observe",
        "strategy": "Hold still",
        "expected_effect": "No input",
        "keys": [],
        "duration_ticks": 1,
        **extra,
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=holo_response(raw)))
    ) as client:
        with pytest.raises(ProviderError):
            await HoloProvider(settings, client).decide(observation, {})


@pytest.mark.parametrize("gui", [False, True])
async def test_jev_validates_unexecuted_one_shot_unchanged(settings, observation, decision, gui):
    observation.screen_open = gui
    decision.action = (
        Action(keys=["sneak"], duration_ticks=1, cursor={"x": 0.6, "y": 0.4})
        if gui
        else Action(keys=["inventory"], duration_ticks=1)
    )
    decision.milestone = "wood"
    decision.evidence = "Previous planner claim"
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["state"]["evaluation_mode"] == "unexecuted_plan"
        assert body["state"]["run_goal"] == "Obtain stone tools"
        assert body["state"]["current_frame"]["frame_id"] == observation.frame_id
        assert body["state"]["plan_source_frame_id"] == "older-planning-frame"
        assert body["state"]["expected_effect"] == decision.expected_effect
        assert set(body["questions"]["action"]["criteria"]) == {"continue", "replan"}
        assert body["state"]["candidates"]["continue"] == decision.action.model_dump()
        payload = jev_response()
        payload["answers"]["action"].update(
            choice="continue",
            probabilities={"continue": 0.95, "replan": 0.05},
        )
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        adapter = JevProvider(settings, client)
        context = {
            "previous_decision": decision.model_dump(),
            "validate_unexecuted_plan": True,
            "run_goal": "Obtain stone tools",
            "plan_source_frame_id": "older-planning-frame",
        }
        result = await adapter.decide(observation, context)
        # Revalidation authorizes one proposed action; it never enables GUI repetition.
        with pytest.raises(ReplanRequired):
            await adapter.decide(observation, {"previous_decision": result.decision.model_dump()})
    assert len(calls) == 1
    assert result.decision.action == decision.action
    assert result.decision.milestone is None
    assert result.completed is False


async def test_jev_abstains_from_unexecuted_gui_proposal_in_danger(settings, observation, decision):
    observation.screen_open = True
    decision.action = Action(keys=[], duration_ticks=1, cursor={"x": 0.6, "y": 0.4})
    payload = jev_response()
    payload["answers"]["action"].update(
        choice="continue",
        probabilities={"continue": 0.95, "replan": 0.05},
    )
    payload["answers"]["danger"]["noul"] = 0.9
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ReplanRequired) as error:
            await JevProvider(settings, client).decide(
                observation,
                {
                    "previous_decision": decision.model_dump(),
                    "validate_unexecuted_plan": True,
                },
            )
    assert error.value.response == payload


@pytest.mark.parametrize("duration", [None, 60, 80])
async def test_native_mine_tool_is_stationary_and_bounded(settings, observation, duration):
    arguments = {
        "objective": "Gather a log",
        "strategy": "Break the log under the crosshair.",
        "expected_effect": "Break the aimed log",
    }
    if duration is not None:
        arguments["duration_ticks"] = duration
    payload = holo_response(arguments)
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "mine_block"

    def handle(request):
        body = json.loads(request.content)
        declared = {tool["function"]["name"]: tool["function"] for tool in body["tools"]}
        assert (
            declared["propose_decision"]["parameters"]["properties"]["duration_ticks"]["maximum"]
            == 20
        )
        mining = declared["mine_block"]["parameters"]
        assert set(mining["properties"]) == {
            "objective",
            "strategy",
            "expected_effect",
            "milestone",
            "evidence",
            "duration_ticks",
        }
        assert mining["properties"]["duration_ticks"]["minimum"] == 60
        assert mining["properties"]["duration_ticks"]["maximum"] == 80
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await HoloProvider(settings, client).decide(observation, {})
    assert result.decision.action == Action(keys=["attack"], duration_ticks=duration or 80)


@pytest.mark.parametrize(
    "extra",
    [
        {"keys": ["forward"]},
        {"yaw_delta": 0},
        {"click_x": 500},
        {"hotbar": 1},
        {"duration_ticks": 59},
        {"duration_ticks": 81},
        {"duration_ticks": "80"},
    ],
)
async def test_mine_tool_rejects_extra_controls_or_invalid_duration(settings, observation, extra):
    payload = holo_response(
        {
            "objective": "Gather a log",
            "strategy": "Mine",
            "expected_effect": "Break the log",
            **extra,
        }
    )
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "mine_block"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError):
            await HoloProvider(settings, client).decide(observation, {})


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "objective": "Start gathering wood by breaking the tree trunk in front of me",
            "strategy": "Move forward slightly and attack the tree trunk to begin logging.",
            "expected_effect": "The tree trunk will start breaking, and I'll collect wood logs.",
            "keys": ["forward", "attack"],
            "duration_ticks": 60,
        },
        {
            "objective": "Find and chop a tree to gather wood logs",
            "strategy": "Move forward out of the leafy area to locate a clear tree trunk, then begin breaking it with bare hands.",
            "expected_effect": "I will locate an accessible tree and start collecting wood logs to begin the crafting process.",
            "keys": ["forward"],
            "duration_ticks": 40,
        },
    ],
)
async def test_real_run_duration_failures_provide_actionable_feedback(
    settings, observation, arguments
):
    payload = holo_response(arguments)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError) as error:
            await HoloProvider(settings, client).decide(observation, {})
    assert "duration_ticks" in str(error.value)
    assert "1 to 20" in str(error.value)
    assert "mine_block" in str(error.value)
    assert arguments["strategy"] not in str(error.value)
    assert error.value.response == payload


async def test_pydantic_feedback_is_bounded_without_repeating_model_input(
    settings, observation, decision
):
    arguments = decision.model_dump()
    arguments["action"]["duration_ticks"] = 2000
    arguments["evidence"] = "test-holo-secret"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=holo_response(arguments))
        )
    ) as client:
        with pytest.raises(ProviderError) as error:
            await HoloProvider(settings, client).decide(observation, {})
    assert "action.duration_ticks" in str(error.value)
    assert "test-holo-secret" not in str(error.value)
    assert len(str(error.value)) < 1000


async def test_jev_rejection_explains_target_mismatch(settings, observation, decision):
    payload = jev_response()
    payload["answers"]["can_continue"]["noul"] = 0.1
    payload["answers"]["blocker"] = {"type": "choice", "choice": "target_mismatch"}

    def handle(request):
        body = json.loads(request.content)
        assert body["questions"]["blocker"]["type"] == "choice"
        assert "intermediate" in body["questions"]["can_continue"]["instructions"]
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ReplanRequired) as error:
            await JevProvider(settings, client).decide(
                observation, {"previous_decision": decision.model_dump()}
            )
    assert "crosshair or GUI target" in str(error.value)
    assert "re-aim" in str(error.value)


@pytest.mark.parametrize(
    "selected_probability,hazard,continuation,accepted",
    [
        (0.78, 0.03, 0.79, True),
        (0.64, 0.03, 0.79, False),
        (0.78, 0.35, 0.79, False),
        (0.78, 0.03, 0.69, False),
    ],
)
async def test_real_binary_choice_confidence_does_not_duplicate_probability_gate(
    settings,
    observation,
    decision,
    selected_probability,
    hazard,
    continuation,
    accepted,
):
    # Recorded second trial: p=.78, concentration confidence=.56, danger=.03,
    # continuation=.79. Confidence describes distribution concentration, not p(correct).
    payload = jev_response()
    payload["answers"]["action"].update(
        choice="continue",
        confidence=0.56,
        probabilities={"continue": selected_probability, "replan": 1 - selected_probability},
    )
    payload["answers"]["danger"]["noul"] = hazard
    payload["answers"]["can_continue"]["noul"] = continuation
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        adapter = JevProvider(settings, client)
        context = {"previous_decision": decision.model_dump(), "validate_unexecuted_plan": True}
        if not accepted:
            with pytest.raises(ReplanRequired):
                await adapter.decide(observation, context)
        else:
            result = await adapter.decide(observation, context)
            assert result.decision.action == decision.action
            assert result.response["answers"]["action"]["confidence"] == 0.56
            assert "selected probability 0.78" in result.decision.evidence


@pytest.mark.parametrize(
    "tool,arguments,screen_open",
    [
        ("propose_decision", {"keys": ["forward"], "duration_ticks": 5}, False),
        ("mine_block", {"intent": "Mine the oak log under the crosshair"}, False),
        (
            "propose_decision",
            {
                "keys": [],
                "duration_ticks": 1,
                "click_x": 600,
                "click_y": 400,
                "intent": "Pick up the visible log stack",
            },
            True,
        ),
    ],
)
async def test_compact_holo_accepts_controls_and_derives_only_input_metadata(
    settings,
    observation,
    tool,
    arguments,
    screen_open,
):
    settings.holo_compact = True
    observation.screen_open = screen_open
    payload = holo_response(arguments)
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = tool

    def handle(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 256
        tools_by_name = {item["function"]["name"]: item["function"] for item in body["tools"]}
        schema = tools_by_name[tool]["parameters"]
        assert not {"objective", "strategy", "expected_effect"} & schema["properties"].keys()
        assert {"milestone", "evidence"} <= schema["properties"].keys()
        assert not {"milestone", "evidence"} & set(schema["required"])
        assert schema["properties"]["intent"]["maxLength"] == 120
        assert "objective" not in schema["required"]
        if tool == "mine_block":
            assert "intent" in schema["required"]
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await HoloProvider(settings, client).decide(
            observation, {"objective": "Gather wood"}
        )
    assert result.decision.objective == "Gather wood"
    assert "Control-only plan:" in result.decision.strategy
    assert "Attempt inputs:" in result.decision.expected_effect
    assert "Outcome is unverified" in result.decision.expected_effect
    assert result.decision.milestone is None
    assert result.decision.evidence == ""
    assert result.completed is False
    if tool == "mine_block":
        assert result.decision.action == Action(keys=["attack"], duration_ticks=80)


async def test_compact_holo_optional_intent_is_labeled_not_progress(settings, observation):
    settings.holo_compact = True
    payload = holo_response({"keys": ["attack"], "duration_ticks": 10, "intent": "clear leaves"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        result = await HoloProvider(settings, client).decide(
            observation, {"objective": "Gather wood"}
        )
    assert result.decision.strategy.startswith("Model intent: clear leaves.")
    assert (
        result.decision.expected_effect
        == "Attempt inputs: hold attack for 10 ticks. Outcome is unverified."
    )
    assert result.decision.milestone is None


async def test_compact_holo_directs_continuous_log_mining_before_inventory_claim(
    settings, observation
):
    settings.holo_compact = True
    payload = holo_response({"intent": "Mine the aimed birch log"})
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "mine_block"

    def handle(request):
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        assert "at least60 CONTINUOUS attack ticks" in prompt
        assert "Separate 10–20-tick punches reset" in prompt
        assert "call mine_block with target intent and default80 ticks" in prompt
        assert "dropped item, breaking particles, or planned action does NOT prove wood" in prompt
        assert "verify the inventory/hotbar before claiming wood" in prompt
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await HoloProvider(settings, client).decide(
            observation, {"objective": "Gather wood"}
        )
    assert result.decision.action == Action(keys=["attack"], duration_ticks=80)
    assert result.decision.milestone is None


async def test_compact_holo_still_accepts_legacy_explicit_metadata(settings, observation, decision):
    settings.holo_compact = True
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=holo_response(decision.model_dump()))
        )
    ) as client:
        result = await HoloProvider(settings, client).decide(observation, {})
    assert result.decision == decision


@pytest.mark.parametrize(
    "arguments",
    [
        {"keys": ["forward"], "duration_ticks": 40},
        {"keys": ["attack"], "duration_ticks": 10, "intent": "x" * 121},
        {"keys": ["attack"], "duration_ticks": 10, "intent": {"instruction": "execute"}},
        {"keys": ["attack"], "duration_ticks": 10, "unknown": "execute"},
    ],
)
async def test_compact_holo_keeps_strict_controls_and_intent(settings, observation, arguments):
    settings.holo_compact = True
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=holo_response(arguments))
        )
    ) as client:
        with pytest.raises(ProviderError):
            await HoloProvider(settings, client).decide(observation, {})


@pytest.mark.parametrize(
    "evidence,accepted",
    [
        ("An oak log stack is visible in hotbar slot 1.", True),
        ("", False),
        ("   ", False),
    ],
)
async def test_compact_holo_milestone_requires_explicit_visual_evidence(
    settings,
    observation,
    evidence,
    accepted,
):
    settings.holo_compact = True
    payload = holo_response(
        {"keys": [], "duration_ticks": 1, "milestone": "wood", "evidence": evidence}
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        adapter = HoloProvider(settings, client)
        if accepted:
            result = await adapter.decide(observation, {"objective": "Gather wood"})
            assert result.decision.milestone == "wood"
            assert result.decision.evidence == evidence
        else:
            with pytest.raises(ProviderError, match="Milestone without evidence"):
                await adapter.decide(observation, {"objective": "Gather wood"})


@pytest.mark.parametrize(
    "tool,arguments,screen_open",
    [
        ("mine_block", {}, False),
        (
            "propose_decision",
            {"keys": [], "duration_ticks": 1, "click_x": 600, "click_y": 400},
            True,
        ),
        ("propose_decision", {"keys": [], "duration_ticks": 1, "hotbar": 1}, True),
        ("propose_decision", {"keys": ["inventory"], "duration_ticks": 1}, True),
    ],
)
async def test_compact_holo_target_sensitive_actions_require_intent(
    settings,
    observation,
    tool,
    arguments,
    screen_open,
):
    settings.holo_compact = True
    observation.screen_open = screen_open
    payload = holo_response(arguments)
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = tool
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError, match="require a short target intent"):
            await HoloProvider(settings, client).decide(observation, {})


async def test_compact_gui_idle_does_not_need_target_intent(settings, observation):
    settings.holo_compact = True
    observation.screen_open = True
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=holo_response({"keys": [], "duration_ticks": 1})
            )
        )
    ) as client:
        result = await HoloProvider(settings, client).decide(observation, {})
    assert result.decision.action == Action(keys=[], duration_ticks=1)


@pytest.mark.parametrize("mining", [False, True])
async def test_compact_legacy_target_descriptions_remain_compatible(
    settings,
    observation,
    decision,
    mining,
):
    settings.holo_compact = True
    if mining:
        arguments = {**decision.model_dump(exclude={"action"}), "duration_ticks": 80}
        tool = "mine_block"
    else:
        observation.screen_open = True
        arguments = decision.model_dump()
        arguments["action"] = {"keys": [], "duration_ticks": 1, "cursor": {"x": 600, "y": 400}}
        tool = "propose_decision"
    payload = holo_response(arguments)
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = tool
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        result = await HoloProvider(settings, client).decide(observation, {})
    assert result.decision.strategy == decision.strategy


async def test_jev_revalidation_rejects_gui_action_after_screen_closed(
    settings, observation, decision
):
    decision.action = Action(keys=[], duration_ticks=1, cursor={"x": 0.6, "y": 0.4})

    def forbidden(request):
        pytest.fail("An impossible screen/action combination must not call Jev")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(ReplanRequired):
            await JevProvider(settings, client).decide(
                observation,
                {
                    "previous_decision": decision.model_dump(),
                    "validate_unexecuted_plan": True,
                },
            )


@pytest.mark.parametrize(
    "action,screen_open,enabled,expected",
    [
        (Action(keys=["attack"], duration_ticks=10), False, True, True),
        (
            Action(keys=["forward"], duration_ticks=10, yaw_delta=30, pitch_delta=20),
            False,
            True,
            True,
        ),
        (Action(keys=["forward", "attack"], duration_ticks=10), False, True, True),
        (Action(keys=["forward"], duration_ticks=11), False, True, False),
        (Action(keys=["forward"], duration_ticks=10, yaw_delta=31), False, True, False),
        (Action(keys=["forward"], duration_ticks=10, pitch_delta=21), False, True, False),
        (Action(keys=["jump"], duration_ticks=5), False, True, False),
        (Action(keys=["sprint"], duration_ticks=5), False, True, False),
        (Action(keys=["use"], duration_ticks=1), False, True, False),
        (Action(keys=[], duration_ticks=1, hotbar=2), False, True, False),
        (Action(keys=[], duration_ticks=1, scroll=1), False, True, False),
        (Action(keys=[], duration_ticks=1, cursor={"x": 0.5, "y": 0.5}), True, True, False),
        (Action(keys=["attack"], duration_ticks=80), False, True, False),
        (Action(keys=["attack"], duration_ticks=10), True, True, False),
        (Action(keys=["attack"], duration_ticks=10), False, False, False),
    ],
)
async def test_exploration_profile_only_applies_to_bounded_world_actions(
    settings,
    observation,
    decision,
    action,
    screen_open,
    enabled,
    expected,
):
    settings.allow_exploration = enabled
    observation.screen_open = screen_open
    decision.action = action

    def handle(request):
        body = json.loads(request.content)
        assert body["state"]["exploration_permitted"] is expected
        instructions = body["questions"]["action"]["instructions"]
        if expected:
            assert "safe bounded experiment" in instructions
            assert "executed action with visible failure evidence" in instructions
            assert "unexecuted proposal is not evidence of physical failure" in instructions
        else:
            assert "NOT been executed" in instructions
        payload = jev_response()
        payload["answers"]["action"].update(
            choice="continue",
            probabilities={"continue": 0.95, "replan": 0.05},
        )
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await JevProvider(settings, client).decide(
            observation,
            {
                "previous_decision": decision.model_dump(),
                "validate_unexecuted_plan": True,
            },
        )
    assert result.decision.action == action


@pytest.mark.parametrize("rejection", ["probability", "danger", "continuation", "replan"])
async def test_exploration_preserves_probability_and_danger_gates(
    settings,
    observation,
    decision,
    rejection,
):
    settings.allow_exploration = True
    decision.action = Action(keys=["attack"], duration_ticks=10)
    payload = jev_response()
    payload["answers"]["action"].update(
        choice="continue",
        probabilities={"continue": 0.95, "replan": 0.05},
    )
    if rejection == "probability":
        payload["answers"]["action"]["probabilities"] = {"continue": 0.64, "replan": 0.36}
    elif rejection == "danger":
        payload["answers"]["danger"]["noul"] = 0.35
    elif rejection == "continuation":
        payload["answers"]["can_continue"]["noul"] = 0.69
    else:
        payload["answers"]["action"].update(
            choice="replan",
            probabilities={"continue": 0.01, "replan": 0.99},
        )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ReplanRequired):
            await JevProvider(settings, client).decide(
                observation,
                {
                    "previous_decision": decision.model_dump(),
                    "validate_unexecuted_plan": True,
                },
            )
