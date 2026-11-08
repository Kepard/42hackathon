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
    assert "feat/holospeed-agent" in payload["prompt"]


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
