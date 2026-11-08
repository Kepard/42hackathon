import json

import httpx
import pytest
from holospeed.config import Settings
from holospeed.devin import (
    DevinAPIError,
    build_analysis_prompt,
    get_identity,
    get_session,
    submit_analysis,
)


def test_digest_preserves_diagnostics_but_omits_credentials_and_artifacts():
    prompt = build_analysis_prompt(
        {"run_id": "run-1", "status": "failed", "api_key": "arbitrary-private-value"},
        [
            {
                "seq": 7,
                "type": "error",
                "data": {
                    "message": "timeout using Bearer example-token and hk-example-secret",
                    "action_id": "action-6",
                    "frame_id": "frame-3",
                    "image_base64": "c2NyZWVuc2hvdA==",
                    "request": {"headers": {"Authorization": "private-header"}},
                    "response": {"body": "private-body"},
                    "screenshot_path": "/private/run/image.png",
                    "evidence": "data:image/png;base64,YQ== https://private.test/file?token=x",
                },
            }
        ],
    )
    assert "action-6" in prompt and "frame-3" in prompt and "timeout" in prompt
    for secret in (
        "arbitrary-private-value",
        "example-token",
        "hk-example-secret",
        "c2NyZWVuc2hvdA==",
        "private-header",
        "private-body",
        "/private/run",
        "data:image",
        "private.test",
    ):
        assert secret not in prompt
    assert "Do not merge, deploy" in prompt


def test_digest_is_bounded_valid_json_and_reports_omissions():
    events = [
        {"seq": i, "type": "decision", "data": {"strategy": "text " * 500}} for i in range(300)
    ]
    prompt = build_analysis_prompt({"run_id": "run-1"}, events)
    digest = json.loads(prompt.split("RUN_DIGEST_JSON\n", 1)[1])
    assert len(prompt) < 20_000
    assert digest["total_event_count"] == 300
    assert digest["omitted_event_count"] > 260
    assert digest["recent_events"][-1]["seq"] == 299
    assert digest["omitted_event_count"] + len(digest["recent_events"]) == 300


def test_digest_preserves_public_planning_and_validation_evidence_amid_telemetry():
    events = [
        {
            "seq": 1,
            "type": "inference",
            "data": {
                "task": "choose_skill",
                "request_id": "holo-request-1",
                "decision_role": "strategic_planner",
                "rationale": "Gather sufficient cobblestone before returning to the table.",
                "candidate": {"candidate_id": "mine-cobblestone", "target_count": 6},
                "private_reasoning": "must not be included",
            },
        },
        {
            "seq": 2,
            "type": "strategy_rejected",
            "data": {
                "inference_request_id": "holo-request-1",
                "validation_provider": "jev",
                "validation_request_id": "jev-request-2",
                "reason": "Current candidate is outside interaction range",
            },
        },
    ] + [{"seq": n, "type": "telemetry", "data": {"fps": 30}} for n in range(3, 100)]
    prompt = build_analysis_prompt({}, events)
    digest = json.loads(prompt.split("RUN_DIGEST_JSON\n", 1)[1])
    inference = next(item for item in digest["recent_events"] if item["type"] == "inference")
    assert inference["data"]["candidate"]["target_count"] == 6
    assert inference["data"]["decision_role"] == "strategic_planner"
    assert "Gather sufficient cobblestone" in prompt and "jev-request-2" in prompt
    assert "private_reasoning" not in prompt and "must not be included" not in prompt


@pytest.mark.parametrize("settings", [Settings(), Settings(devin_api_key="test-key")])
async def test_submit_requires_configuration_without_network(settings):
    def unexpected_call(request):
        pytest.fail("Missing configuration must fail before a network request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_call)) as client:
        with pytest.raises(ValueError, match="DEVIN_"):
            await submit_analysis(settings, {}, [], client)


async def test_submit_sends_one_bounded_v3_request_with_configured_secrets_redacted():
    settings = Settings(
        devin_api_key="cog_test",
        devin_org_id="org-example",
        holo_api_key="unusual-secret",
        bridge_token="bridge-private",
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"session_id": "session-1", "status": "new"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await submit_analysis(
            settings,
            {"error": "unusual-secret"},
            [{"type": "failure", "data": {"reason": "bridge-private"}}],
            client,
        )
        assert not client.is_closed
    assert result["session_id"] == "session-1"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.devin.ai/v3/organizations/org-example/sessions"
    assert request.headers["Authorization"] == "Bearer cog_test"
    payload = json.loads(request.content)
    assert payload["max_acu_limit"] == 2
    assert payload["memory_enabled"] is False
    assert payload["secret_ids"] == []
    assert "bypass_approval" not in payload
    assert "attachment_urls" not in payload
    assert "unusual-secret" not in payload["prompt"]
    assert "bridge-private" not in payload["prompt"]
    assert "current default branch" in payload["prompt"]


async def test_timeout_does_not_retry_or_expose_request_details():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private response with cog_sensitive", request=request)

    settings = Settings(devin_api_key="cog_test", devin_org_id="org-example")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DevinAPIError, match="check session history") as error:
            await submit_analysis(settings, {}, [], client)
    assert calls == 1
    assert "cog_sensitive" not in str(error.value)


async def test_http_error_omits_response_body_and_refuses_redirect():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            302,
            headers={"Location": "https://untrusted.example"},
            text="sensitive response content",
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        with pytest.raises(DevinAPIError, match="HTTP 302"):
            await get_identity(Settings(devin_api_key="test"), client)
    assert len(calls) == 1


async def test_identity_does_not_need_org_and_session_read_uses_org():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await get_identity(Settings(devin_api_key="test"), client)
        await get_session(
            Settings(devin_api_key="test", devin_org_id="org-example"), "session-1", client
        )
    assert paths == ["/v3/self", "/v3/organizations/org-example/sessions/session-1"]


async def test_session_identifier_cannot_change_endpoint():
    with pytest.raises(ValueError, match="session_id"):
        await get_session(Settings(devin_api_key="test", devin_org_id="org-example"), "../self")


def digest_of(summary, events, context=None):
    return json.loads(
        build_analysis_prompt(summary, events, context).split("RUN_DIGEST_JSON\n", 1)[1]
    )


def test_skill_history_survives_many_state_polls_and_keeps_inventory_facts():
    events = [
        {
            "seq": 1,
            "type": "skill_started",
            "data": {"skill": "mine", "item": "minecraft:cobblestone", "target_count": 6},
        },
        *[{"seq": i, "type": "skill_progress", "data": {"tick": i}} for i in range(2, 202)],
        {
            "seq": 202,
            "type": "skill_completed",
            "data": {
                "candidate_id": "mine-stone-6",
                "state_id": "state-202",
                "inventory": {"minecraft:cobblestone": 6},
            },
        },
        {
            "seq": 203,
            "type": "state_summary",
            "data": {
                "player": {"position": {"x": 36.5, "y": 52, "z": 35.6}, "health": 20},
                "inventory": {"minecraft:stick": 6},
                "known_table": {"x": 32, "y": 67, "z": 41},
            },
        },
        {"seq": 204, "type": "error", "data": {"message": "No eligible skill"}},
    ]
    digest = digest_of({}, events)
    selected = digest["recent_events"]
    assert selected[0]["seq"] == 1
    assert len(selected) <= 40
    assert digest["omitted_event_count"] + len(selected) == len(events)
    completed = next(event for event in selected if event["type"] == "skill_completed")
    assert completed["data"]["inventory"] == {"minecraft:cobblestone": 6}
    assert completed["data"]["state_id"] == "state-202"
    state = next(event for event in selected if event["type"] == "state_summary")
    assert state["data"]["player"]["position"]["z"] == 35.6
    assert state["data"]["known_table"] == {"x": 32, "y": 67, "z": 41}


def test_dynamic_inventory_keys_are_strict_and_private_paths_are_scrubbed():
    digest = digest_of(
        {},
        [
            {
                "type": "game_state",
                "data": {
                    "inventory": {
                        "minecraft:stone_pickaxe": 1,
                        "api_key": "secret",
                        "minecraft:oak_log": True,
                        "minecraft:dirt": -1,
                        "minecraft:stone": 9000,
                    },
                    "message": "inspect /workspace/private/file.json /mnt/private.json /secret.txt or C:\\Users\\name\\secret.txt",
                    "path": "/workspace/private/state.json.gz",
                },
            }
        ],
    )
    data = digest["recent_events"][0]["data"]
    assert data["inventory"] == {"minecraft:stone_pickaxe": 1}
    assert "path" not in data
    assert "/workspace" not in data["message"] and "C:" not in data["message"]
    assert "/mnt" not in data["message"] and "/secret.txt" not in data["message"]


def test_inventory_slot_shape_cannot_forward_extra_data():
    slots = [
        {"slot": 0, "item": "minecraft:birch_log", "count": 3, "secret": "sensitive"},
        {"slot": 1, "item": "cog_private", "count": 1},
        {"slot": "secret-slot", "item": "minecraft:dirt", "count": 1},
    ]
    digest = digest_of({}, [{"type": "game_state", "data": {"inventory": slots}}])
    assert digest["recent_events"][0]["data"]["inventory"] == [
        {"slot": 0, "item": "minecraft:birch_log", "count": 3}
    ]


def test_current_engineering_context_is_bounded_and_only_explicit_fields_survive():
    context = {
        "current_changes": "Known table navigation implemented",
        "verification": "Stone tools acquired in live continuation",
        "analysis_focus": "regression tests " * 1000,
        "headers": {"secret": "do-not-forward"},
    }
    digest = digest_of({}, [], context)
    assert digest["engineering_context"]["current_changes"] == context["current_changes"]
    assert len(digest["engineering_context"]["analysis_focus"]) < 750
    assert "headers" not in digest["engineering_context"]


def test_digest_budget_removes_large_poll_samples_before_skill_history():
    events = [
        {
            "seq": 1,
            "type": "skill_started",
            "data": {"skill": "mine", "item": "minecraft:cobblestone", "target_count": 6},
        }
    ]
    inventory = [{"slot": i, "item": "minecraft:dirt", "count": 64} for i in range(41)]
    events.extend(
        {"seq": i, "type": "game_state", "data": {"inventory": inventory}} for i in range(2, 80)
    )
    events.extend(
        [
            {
                "seq": 80,
                "type": "skill_completed",
                "data": {"inventory": {"minecraft:cobblestone": 6}, "state_id": "state-80"},
            },
            {"seq": 81, "type": "error", "data": {"message": "No eligible skill"}},
        ]
    )
    digest = digest_of({}, events)
    assert len(json.dumps(digest, ensure_ascii=True)) <= 18_000
    assert digest["recent_events"][0]["seq"] == 1
    assert [e["seq"] for e in digest["recent_events"]][-2:] == [80, 81]
    assert digest["omitted_event_count"] + len(digest["recent_events"]) == len(events)


async def test_submission_redacts_context_secrets_and_requests_test_source():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"session_id": "test-session"})

    settings = Settings(devin_api_key="nonstandard-private-secret", devin_org_id="org-example")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await submit_analysis(
            settings,
            {},
            [],
            client,
            engineering_context={"current_changes": "nonstandard-private-secret"},
        )
    payload = requests[0]
    assert "nonstandard-private-secret" not in payload["prompt"]
    assert "test_patches" in payload["structured_output_schema"]["required"]
    schema = payload["structured_output_schema"]["properties"]["test_patches"]["items"]
    assert "code" in schema["required"] and "validation_status" in schema["required"]
    assert "proposed/unrun unless actually tested" in payload["prompt"]
