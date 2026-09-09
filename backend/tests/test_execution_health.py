"""Offline configuration/probe fixtures; no main service, credentials or real model calls."""

import json

import httpx
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.health import check_model, execution_summary, provider_origin
from plango_harness.persistence.database import agent_run
from sqlalchemy import update
from test_browser_harness import TOKEN, settings


def test_execution_summary_and_saved_model_are_safe(tmp_path):
    config = settings(tmp_path).model_copy(update={
        "openai_api_key": "secret-fixture-key",
        "openai_base_url": "https://user:password@model.invalid/v1?key=secret#private",
        "openai_model": "service-model", "amap_webservice_key": "secret-amap-key",
    })
    app = create_app(config, token=TOKEN)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        response = client.get("/api/v1/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] and data["input_delivery_version"] == 1
        assert data["execution"]["model"] == {
            "name": "service-model", "provider_origin": "https://model.invalid", "key_configured": True,
            "check": {"status": "not_checked"},
        }
        assert data["execution"]["capabilities"]["browser_vision_enabled"] is False
        assert data["execution"]["capabilities"]["transit"] == "same_city"
        assert not any(secret in response.text for secret in ["secret", "password", "/v1", "user:"])
        assert "execution" not in client.get("/health/ready").json()
        assert client.get("/api/v1/health/ready", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.post("/api/v1/health/model-check", headers={"Authorization": "Bearer wrong"}).status_code == 401

        async def save_record():
            await app.state.runtime.runs.create("fixture-history", "desktop", "private task text")
            async with app.state.runtime.database.session() as session:
                await session.execute(update(agent_run).where(agent_run.c.run_id == "fixture-history").values(
                    state_json={"model_calls": [{"model": "actual-worker-model", "status": "ok", "error": "private error"}]}
                ))
                await session.commit()

        client.portal.call(save_record)
        data = client.get("/api/v1/health/ready").json()
        assert data["execution"]["recent_task_model"]["name"] == "actual-worker-model"
        assert data["execution"]["model"]["name"] == "service-model", "Historical worker evidence cannot replace current API configuration"
        assert "private" not in json.dumps(data)

    service = execution_summary(config.model_copy(update={"runtime_profile": "service", "browser_vision_enabled": True}))
    assert service["runtime_profile"] == "service"
    assert service["capabilities"]["browser_vision_enabled"] is True
    assert provider_origin("https://[::1]:8443/private?key=x") == "https://[::1]:8443"
    assert provider_origin("https://host:invalid/private") == "invalid"


async def test_bounded_probe_distinguishes_configuration_failures_and_offline(tmp_path, monkeypatch):
    config = settings(tmp_path)
    assert (await check_model(config))["status"] == "not_configured"
    config = config.model_copy(update={"openai_api_key": "fixture-only", "openai_model": "fixture-model", "openai_base_url": "https://model.invalid/v1"})
    real_client = httpx.AsyncClient
    response_code = 200
    failure = None
    choices = [{"message": {"content": "ok"}}]
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer fixture-only"
        body = json.loads(request.content)
        assert body["model"] == "fixture-model" and body["max_tokens"] == 8
        if failure:
            raise failure
        return httpx.Response(response_code, json={"choices": choices, "usage": {"total_tokens": 9}, "error": "secret-fixture-key"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)))
    passed = await check_model(config)
    assert passed["status"] == "passed" and passed["total_tokens"] == 9
    for code, category in [(401, "authentication"), (404, "model_or_endpoint"), (429, "rate_limit"), (503, "provider")]:
        response_code = code
        result = await check_model(config)
        assert result["status"] == "failed" and result["category"] == category
        assert "secret" not in json.dumps(result)
    failure = httpx.ConnectError("secret endpoint was offline")
    assert (await check_model(config))["category"] == "connection"
    failure = httpx.ReadTimeout("secret endpoint timed out")
    assert (await check_model(config))["category"] == "timeout"
    failure = None
    response_code = 200
    for choices in [[], [{}], [{"message": {"content": " "}}]]:
        assert (await check_model(config))["category"] == "invalid_response"
    assert len(calls) == 10
