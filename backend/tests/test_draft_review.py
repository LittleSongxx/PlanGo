"""Synthetic protocol fixtures verify draft delivery and bounded preparation, never merchant success."""

import copy
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import BrowserDecision
from plango.outcomes import draft_review
from plango.task import DeliveryDecision, TaskDecision
from plango_harness.agent.contracts import ConstraintCheck, Evidence, VerifierResult
from plango_harness.agent.decisions import RequirementOutput
from plango_harness.agent.graph import GraphDeps
from plango_harness.agent.model_adapter import ModelAdapter
from test_browser_harness import TOKEN, settings, wait_for
from test_browser_navigation import browser_driver
from test_preparation_outcome import prepared_state


@pytest.mark.parametrize("decision", ["save", "prepare", "edit", "blocked_click", "tab_closed"])
def test_unknown_supply_has_explicit_draft_decision_and_preparation_scope(tmp_path, decision):
    app = create_app(settings(tmp_path), token=TOKEN)

    async def choose(schema, *, fallback, **kwargs):
        if schema not in {TaskDecision, DeliveryDecision}:
            return fallback
        context = json.loads(kwargs['user'])
        if context.get('execution_goal'):
            step = BrowserDecision(operation='snapshot') if decision == 'tab_closed' else BrowserDecision(operation='click', idx=3)
            return TaskDecision(operation='read', browser=step)
        return TaskDecision(operation='plan', requirements=RequirementOutput(
            party_size=4 if context['turn_id'] > 1 else 3, budget=300, time_window_start='18:30',
            visit_date=date.fromisoformat(context['local_date']), required_activities=['餐厅']))

    app.state.runtime.model.structured = choose
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "今天18:30，3人吃饭，帮我规划一个餐厅行程，总预算300元", "browser_session_id": "fixture-desktop",
                                                "location_context": {"city": "重庆", "longitude": 106.57, "latitude": 29.56, "source": "manual", "granularity": "point"}}).json()["run_id"]
        command, respond, _ = browser_driver(client, run_id)
        provider = {"places": [{"place_id": "browser:fixture", "name": "雾岚餐厅", "address": "重庆市渝中区邹容路1号", "category": "餐厅", "latitude": 29.56, "longitude": 106.57, "average_price": 50, "open_minute": 0, "close_minute": 1440}],
                    "routes": {"browser:fixture": {"driving_min": 0, "walking_min": 0, "transit_min": 0, "distance_km": 0}}}
        respond(command("extract"), fields=provider)
        paused = wait_for(client, run_id, lambda value: value.get("draft_review") or value["phase"] in {"FAILED", "INFEASIBLE"})
        assert paused.get("draft_review"), paused
        review = paused["draft_review"]
        assert paused["state"]["outcome"] is None and review["unknowns"] and review["can_prepare"]
        assert paused["state"]["verifier"]["executable"] is False
        body = {"decision": "save" if decision == "save" else "prepare", **{key: review[key] for key in ("interrupt_id", "plan_id", "plan_version")}}
        assert client.post(f"/api/v1/runs/{run_id}/resume", json={"decision": "approve", "interrupt_id": review["interrupt_id"]}).status_code == 409
        assert client.post(f"/api/v1/runs/{run_id}/draft-decision", json={**body, "plan_version": body["plan_version"] + 1}).status_code == 409
        if decision == "edit":
            assert client.post(f"/api/v1/runs/{run_id}/messages", json={"text": "改成4人，其他要求不变"}).status_code == 202
            changed = wait_for(client, run_id, lambda value: value["state"].get("turn_id", 1) > 1 or value["phase"] == "FAILED")
            assert changed["phase"] != "FAILED", changed
            assert changed["state"].get("execution_goal") is None
            assert client.post(f"/api/v1/runs/{run_id}/draft-decision", json=body).status_code == 409
            return
        assert client.post(f"/api/v1/runs/{run_id}/draft-decision", json=body).status_code == 202
        if decision != "save":
            reading = command("extract")
            assert reading.get("tab_id") is None, "A new preparation must observe the visible session before binding a tab."
            goal = client.get(f"/api/v1/runs/{run_id}").json()["state"]["execution_goal"]
            assert goal["plan_verification"] == "draft" and goal["pending_checks"] == review["unknowns"]
            _, observed = prepared_state()
            form = observed["fields"]["dom"]["forms"][0]
            form["controls"][1]["value"] = goal["requirements"]["visit_date"]
            minute = goal["stops"][0]["start_minute"]
            form["controls"][2]["value"] = f"{minute // 60:02d}:{minute % 60:02d}"
            if decision in {"blocked_click", "tab_closed"}:
                form["controls"][0]["value"] = "2"
                # Two differences deliberately leave this on the model/manual path;
                # a unique native-field difference now gets a deterministic type proposal.
                form["controls"][1]["value"] = "2000-01-01"
            respond(reading, snapshot_id="snapshot-fixture", page_version="page-fixture", url=observed["url"], text=form["context_text"], tables=[], elements=observed["elements"], fields=observed["fields"])
            if decision == "tab_closed":
                stale = command("snapshot")
                assert stale["tab_id"] == "fixture-tab"
                respond(stale, ok=False, outcome="blocked", error_kind="tab_closed", tab_id="fixture-tab")
                blocked = wait_for(client, run_id, lambda value: (value["state"].get("browser_wait") or {}).get("error_kind") == "tab_closed")
                assert "当前可见标签" in blocked["state"]["browser_wait"]["message"]
                assert blocked["state"]["execution_goal"] == goal
                retry = client.post(f"/api/v1/runs/{run_id}/resume", json={"decision": "resume", "interrupt_id": blocked["interrupt_id"]})
                assert retry.status_code == 202, retry.text
                rebound = command("snapshot")
                assert rebound["command_id"] != stale["command_id"] and rebound.get("tab_id") is None and rebound.get("expected_snapshot_id") is None
                form["controls"][0]["value"] = "3"
                form["controls"][1]["value"] = goal["requirements"]["visit_date"]
                form["form_id"] = "snapshot-restored:frame-0:form-0"
                respond(rebound, tab_id="visible-restored-tab", snapshot_id="snapshot-restored", page_version="page-restored", url=observed["url"], text=form["context_text"], tables=[], elements=observed["elements"], fields=observed["fields"])
        done = wait_for(client, run_id, lambda value: value["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == ("PARTIAL_FAILED" if decision == "blocked_click" else "SUCCEEDED"), done
        result = done["state"]["execution_outcome"]["data"]
        assert result["scope"] == ("draft_ready" if decision == "save" else "preparation_incomplete" if decision == "blocked_click" else "ready_to_review")
        assert result["business_completed"] is False
        assert done["state"]["action_results"] == []
        assert done["state"]["verifier"]["executable"] is False
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []
        if decision in {"prepare", "tab_closed"}:
            assert result["pending_checks"] == review["unknowns"] and result["plan_verification"] == "draft"
            assert done["state"]["execution_goal"] == goal
        if decision == "tab_closed":
            assert done["state"]["browser_observation"]["tab_id"] == "visible-restored-tab"
            events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
            assert len([event for event in events if event["event_type"] == "BROWSER_OBSERVATION" and event["payload"]["command_id"] == rebound["command_id"]]) == 1
        if decision == "save":
            assert client.get("/api/v1/memory/profile").json()["summaries"] == []


@pytest.mark.parametrize("fault", ["constraint", "address", "source", "date", "expired", "verifier_plan"])
def test_draft_preparation_requires_known_identity_and_no_hard_conflict(fault):
    state, _ = prepared_state()
    state["trip_spec"] = copy.deepcopy(state["execution_goal"]["requirements"])
    state["verifier"] = VerifierResult(plan_id=state["selected_plan"].plan_id, hard_constraints_pass=True, evidence_complete=False, unknown_evidence=[ConstraintCheck(name="queue", kind="unknown", passed=None)])
    stop = state["selected_plan"].stops[0]
    observed = state["browser_observation"]["observed_at"]
    stop.evidence_ids = ["identity"]
    state["evidence"] = [Evidence(evidence_id="identity", source="browser", source_ref="https://fixture.invalid", observed_at=observed, expires_at="2100-01-01T00:00:00Z", payload={"place_id": stop.place_id, "name": stop.name, "address": stop.address})]
    assert draft_review(state)["can_prepare"]
    if fault == "constraint":
        state["verifier"] = VerifierResult(plan_id=state["selected_plan"].plan_id, hard_constraints_pass=False, evidence_complete=False, hard_violations=[ConstraintCheck(name="budget", kind="hard", passed=False)])
    elif fault == "address":
        stop.address = "another branch"
    elif fault == "source":
        state["evidence"] = []
    elif fault == "date":
        state["trip_spec"]["visit_date"] = None
    elif fault == "verifier_plan":
        state["verifier"].plan_id = "another-plan"
        with pytest.raises(ValueError, match="draft_verifier_plan_mismatch"):
            draft_review(state)
        return
    else:
        state["evidence"][0].expires_at = state["evidence"][0].observed_at.replace(year=2000)
    review = draft_review(state)
    assert not review["can_prepare"] and review["preparation_blockers"]


async def test_resumed_prepare_submit_stops_before_ledger_or_browser_command(tmp_path, monkeypatch):
    import plango.graph as module

    actual_builder = module.build_graph
    nodes = {}

    def capture_builder(deps, *, extension, **kwargs):
        def capture(graph):
            extension(graph)
            nodes["operate"] = graph.nodes["browser_operate"].runnable
        return actual_builder(deps, extension=capture, **kwargs)

    monkeypatch.setattr(module, "build_graph", capture_builder)
    runtime = SimpleNamespace(world_service=SimpleNamespace(provider=SimpleNamespace(bind_run_state=lambda state: None)), bridge=SimpleNamespace(request=AsyncMock()))
    ledger = SimpleNamespace(reserve=AsyncMock())
    deps = GraphDeps(model=ModelAdapter(settings(tmp_path)), tools=SimpleNamespace(schemas=lambda: []), world=SimpleNamespace(), planner=None, memory=None, runs=None, action_provider=None, ledger=ledger)
    module.build_desktop_graph(runtime, deps, None)
    state, observed = prepared_state()
    state.update(browser_next=BrowserDecision(operation="click", idx=3).model_dump(), browser_action={"target": observed["elements"][3]}, approval_decision="approve", action_proposal={"proposal_id": "older-approved-submit"})
    result = await nodes["operate"].ainvoke(state)
    assert result["outcome"] == "PARTIAL_FAILED"
    ledger.reserve.assert_not_awaited()
    runtime.bridge.request.assert_not_awaited()
