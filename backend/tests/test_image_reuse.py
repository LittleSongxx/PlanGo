"""Synthetic OCR replies exercise uploaded-file reuse through the real API and graph entry."""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import ImageReading
from plango.outcomes import read_outcome
from plango.task import DeliveryDecision, TaskDecision, task_context
from plango_harness.agent.graph import GraphDeps
from plango_harness.agent.model_adapter import ModelAdapter, ModelProviderUnavailable
from test_browser_harness import TOKEN, settings, wait_for

IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j/a0AAAAASUVORK5CYII="


def test_same_uploaded_image_on_second_user_turn_reuses_original_source_without_new_ocr(tmp_path):
    app = create_app(settings(tmp_path).model_copy(update={"openai_api_key": "synthetic-adapter-only"}), token=TOKEN)
    async def classify_or_read(schema, **kwargs):
        if schema is ImageReading:
            return ImageReading(text="合成OCR样本：套餐128元，仅为图片文字")
        assert schema in {TaskDecision, DeliveryDecision}
        return TaskDecision(operation="answer", answer="已读取图片文字：套餐128元，仅为图片文字。")
    model = AsyncMock(side_effect=classify_or_read)
    app.state.runtime.model.structured = model
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "读取上传图片中的文字", "image": IMAGE, "browser_session_id": "fixture-desktop"}).json()["run_id"]
        first = wait_for(client, run_id, lambda value: value["phase"] in {"SUCCEEDED", "FAILED"})
        assert first["phase"] == "SUCCEEDED", first
        original = first["state"]["browser_artifacts"][0]
        response = client.post(f"/api/v1/runs/{run_id}/messages", json={"text": "再次读取这张图片中的文字", "image": IMAGE})
        assert response.status_code == 202, response.text
        second = wait_for(client, run_id, lambda value: value["state"].get("turn_id", 1) > 1 and value["phase"] in {"SUCCEEDED", "FAILED", "PARTIAL_FAILED"})
        assert second["phase"] == "SUCCEEDED", second
        assert second["state"]["browser_artifacts"] == [original]
        assert second["state"]["browser_image_turn_id"] == second["state"]["turn_id"]
        assert second["state"]["tool_call_count"] == first["state"]["tool_call_count"]
        outcome = second["state"]["execution_outcome"]
        assert outcome["kind"] == "task_answer" and outcome["data"]["business_completed"] is False
        assert outcome["summary"].startswith("已读取图片文字")
        assert [call.args[0] for call in model.await_args_list] == [ImageReading, TaskDecision, TaskDecision], "Each user turn is answered once; the same image uses OCR only once"
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
        assert len([event for event in events if event["event_type"] == "image_extracted"]) == 1
        assert len([event for event in events if event["event_type"] == "image_reused"]) == 1


def test_image_provider_rejection_asks_for_text_instead_of_failing_the_run(tmp_path):
    app = create_app(settings(tmp_path).model_copy(update={"openai_api_key": "synthetic-adapter-only"}), token=TOKEN)

    async def reject_image(schema, **kwargs):
        if schema is ImageReading:
            raise ModelProviderUnavailable("request")
        return TaskDecision(operation="answer", answer="已记下你补充的图片文字。")

    app.state.runtime.model.structured = AsyncMock(side_effect=reject_image)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "读取上传图片中的文字", "image": IMAGE, "browser_session_id": "fixture-desktop"}).json()["run_id"]
        paused = wait_for(client, run_id, lambda value: bool(value.get("interrupt_id")) or value["phase"] in {"SUCCEEDED", "FAILED", "PARTIAL_FAILED"})
        assert paused["phase"] not in {"FAILED", "PARTIAL_FAILED"}, paused
        assert str(paused.get("interrupt_id") or "").startswith("clarification:")
        question = ((paused.get("state") or {}).get("clarification") or {}).get("question") or ""
        events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
        interrupted = [event for event in events if event["event_type"] == "GRAPH_INTERRUPTED"]
        asked = question or " ".join(str(item.get("payload") or "") for item in interrupted)
        assert "补充图片" in asked or "支持图像" in asked or interrupted, paused
        assert paused["state"].get("clarification", {}).get("question", "").startswith("截图未能识别")


async def test_image_entry_treats_provider_rejection_as_unreadable(tmp_path, monkeypatch):
    import plango.graph as module

    model = ModelAdapter(settings(tmp_path))
    model.structured = AsyncMock(side_effect=ModelProviderUnavailable("request"))
    runtime = SimpleNamespace(bridge=SimpleNamespace(binding=AsyncMock(return_value={"input_image": IMAGE})))
    deps = GraphDeps(model=model, tools=SimpleNamespace(schemas=lambda: []), world=SimpleNamespace(), planner=None, memory=None, runs=None, action_provider=None)
    actual = module.build_graph
    nodes = {}

    def build(deps, *, extension, **kwargs):
        def capture(graph):
            extension(graph)
            nodes["image"] = graph.nodes["image_entry"].runnable
        return actual(deps, extension=capture, **kwargs)

    monkeypatch.setattr(module, "build_graph", build)
    monkeypatch.setattr(module, "interrupt", lambda payload: {"text": "套餐128元，来自用户补充"})
    module.build_desktop_graph(runtime, deps, None)
    result = await nodes["image"].ainvoke({"run_id": "fixture", "turn_id": 1, "input_text": "读取图片文字"})
    assert result["browser_image_context"] == "套餐128元，来自用户补充"
    assert result["browser_artifacts"][0]["data"]["text"] == "套餐128元，来自用户补充"
    model.structured.assert_awaited_once()


@pytest.mark.parametrize("case", ["old_cached_image", "empty_text", "missing_artifact", "wrong_source", "wrong_artifact"])
async def test_hash_alone_does_not_skip_ocr_and_static_image_age_is_not_live_freshness(tmp_path, monkeypatch, case):
    import plango.graph as module

    model = ModelAdapter(settings(tmp_path))
    model.structured = AsyncMock(return_value=ImageReading(text="新的合成OCR结果"))
    runtime = SimpleNamespace(bridge=SimpleNamespace(binding=AsyncMock(return_value={"input_image": IMAGE})))
    deps = GraphDeps(model=model, tools=SimpleNamespace(schemas=lambda: []), world=SimpleNamespace(), planner=None, memory=None, runs=None, action_provider=None)
    actual = module.build_graph
    nodes = {}

    def build(deps, *, extension, **kwargs):
        def capture(graph):
            extension(graph)
            nodes["image"] = graph.nodes["image_entry"].runnable
        return actual(deps, extension=capture, **kwargs)

    monkeypatch.setattr(module, "build_graph", build)
    module.build_desktop_graph(runtime, deps, None)
    digest = hashlib.sha256(IMAGE.encode()).hexdigest()
    original = {"artifact_id": "image:" + digest, "type": "image", "source": "user", "observed_at": "2000-01-01T00:00:00+00:00", "data": {"text": "已有图片文字"}}
    state = {"run_id": "fixture", "turn_id": 2, "input_text": "再次读取图片文字", "processed_image_hash": digest, "browser_image_context": "已有图片文字",
             "browser_image_turn_id": 1, "browser_artifacts": [original]}
    if case == "empty_text":
        state["browser_image_context"] = ""
    elif case == "missing_artifact":
        state["browser_artifacts"] = []
    elif case == "wrong_source":
        original["source"] = "browser"
    elif case == "wrong_artifact":
        original["artifact_id"] = "image:another-upload"
        state["browser_artifacts"].append({"artifact_id": "page:previous", "type": "browser_page", "data": {"text": "上一份网页资料"}})
    result = await nodes["image"].ainvoke(state)
    if case == "old_cached_image":
        model.structured.assert_not_awaited()
        assert result["browser_artifacts"] == [original]
        assert "tool_call_count" not in result
    else:
        model.structured.assert_awaited_once()
        fresh = next(item for item in result["browser_artifacts"] if item["artifact_id"] == "image:" + digest)
        assert fresh["data"]["text"] == "新的合成OCR结果"
        assert fresh["observed_at"] != original["observed_at"]
    outcome = read_outcome({**state, **result})
    assert outcome.status == "satisfied" and outcome.data["scope"] == "image_text"
    sources = task_context({**state, **result})['sources']
    assert sum(source['current'] for source in sources) == 1
    if case == 'wrong_artifact':
        assert {source['artifact_id'] for source in sources} == {'image:another-upload', 'page:previous', 'image:' + digest}

