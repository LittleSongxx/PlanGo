"""Synthetic DOM/provider facts exercise exact Goal continuation and separately approved input correction."""

import copy
import json

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import BrowserDecision, artifact
from plango.outcomes import browser_context, preparation_correction
from plango.runtime import preparation_resume_contract
from test_browser_harness import TOKEN, settings, wait_for
from test_browser_navigation import browser_driver
from test_preparation_outcome import prepared_state


def observed_form(goal, snapshot, **changes):
    _, observation = prepared_state()
    minute = goal["stops"][0]["start_minute"]
    values = {"party": str(goal["requirements"]["party_size"]), "date": goal["requirements"]["visit_date"], "time": f"{minute // 60:02d}:{minute % 60:02d}", **changes}
    form = observation["fields"]["dom"]["forms"][0]
    form["form_id"] = snapshot + ":frame-0:form-0"
    for control, key in zip(form["controls"], ("party", "date", "time")):
        control["value"] = values[key]
    return {"snapshot_id": snapshot, "page_version": "page:" + snapshot, "tab_id": "fixture-tab", "url": observation["url"], "text": form["context_text"], "tables": [], "elements": observation["elements"], "fields": observation["fields"]}


def test_same_goal_continues_after_partial_and_unique_time_change_requires_exact_new_approval(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    decisions = []

    async def model(schema, *, fallback, **kwargs):
        if schema is BrowserDecision:
            decisions.append(kwargs["user"])
            return BrowserDecision(operation="finish")
        return fallback

    app.state.runtime.model.structured = model
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": "今天18:30，3人吃饭，帮我规划一个餐厅行程，总预算300元", "browser_session_id": "fixture-desktop",
                                                "location_context": {"city": "重庆", "longitude": 106.57, "latitude": 29.56, "source": "manual"}}).json()["run_id"]
        command, respond, approve = browser_driver(client, run_id)
        respond(command("extract"), fields={"places": [{"place_id": "browser:fixture", "name": "雾岚餐厅", "address": "重庆市渝中区邹容路1号", "category": "餐厅", "latitude": 29.56, "longitude": 106.57, "average_price": 50}],
                                           "routes": {"browser:fixture": {"driving_min": 0, "walking_min": 0, "transit_min": 0, "distance_km": 0}}})
        draft = wait_for(client, run_id, lambda value: bool(value.get("draft_review")))["draft_review"]
        assert client.post(f"/api/v1/runs/{run_id}/draft-decision", json={"decision": "prepare", **{key: draft[key] for key in ("plan_id", "plan_version", "interrupt_id")}}).status_code == 202
        initial_read = command("extract")
        goal = client.get(f"/api/v1/runs/{run_id}").json()["state"]["execution_goal"]
        respond(initial_read, **observed_form(goal, "incomplete", party="2", date="2000-01-01"))
        partial = wait_for(client, run_id, lambda value: value["phase"] == "PARTIAL_FAILED")
        assert partial["preparation_resume"]["can_resume"]
        body = {key: partial["preparation_resume"][key] for key in ("plan_id", "plan_version", "approval_id")}
        assert client.post(f"/api/v1/runs/{run_id}/preparation/resume", json={**body, "plan_version": body["plan_version"] + 1}).status_code == 409
        before_decisions = len(decisions)
        resumed = client.post(f"/api/v1/runs/{run_id}/preparation/resume", json=body)
        assert resumed.status_code == 202, resumed.text
        fresh = command("extract")
        assert fresh.get("tab_id") is None
        current = client.get(f"/api/v1/runs/{run_id}").json()
        assert current["state"]["execution_goal"] == goal
        assert current["state"]["turn_id"] == partial["state"]["turn_id"] + 1
        assert current["state"].get("action_proposal") is None
        minute = goal["stops"][0]["start_minute"]
        wrong = f"{(minute - 1) // 60:02d}:{(minute - 1) % 60:02d}"
        respond(fresh, **observed_form(goal, "fresh-form", time=wrong))
        pending = wait_for(client, run_id, lambda value: value["phase"] == "WAITING_APPROVAL")
        action = pending["state"]["action_proposal"]["actions"][0]
        expected = f"{minute // 60:02d}:{minute % 60:02d}"
        assert action["tool_name"] == "type" and action["arguments"]["arguments"] == {"idx": 2, "text": expected}
        assert len(decisions) == before_decisions, "Known DOM correction must not ask a model for Vision or the next action."
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []
        assert client.post(f"/api/v1/runs/{run_id}/resume", json={"decision": "approve", "interrupt_id": body["approval_id"]}).status_code == 409
        approve()
        typing = command("type")
        assert typing["expected_snapshot_id"] == "fresh-form" and typing["arguments"] == {"idx": 2, "text": expected}
        respond(typing, outcome="executed", tab_id="fixture-tab", url="https://fixture.invalid/book")
        respond(command("snapshot"), **observed_form(goal, "after-approved-type"))
        done = wait_for(client, run_id, lambda value: value["phase"] in {"SUCCEEDED", "FAILED", "PARTIAL_FAILED"})
        assert done["phase"] == "SUCCEEDED", done
        assert done["state"]["execution_goal"] == goal
        assert done["state"]["execution_outcome"]["data"]["scope"] == "ready_to_review"
        assert done["state"]["action_results"][0]["result"]["scope"] == "browser_interaction"
        assert not done["state"]["execution_outcome"]["data"]["business_completed"]
        assert client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"] == []


@pytest.mark.parametrize("case", ["positive", "other_merchant", "foreign_action", "two_fields", "two_forms", "old_snapshot", "unknown", "disabled"])
def test_only_a_unique_current_native_form_difference_can_propose_type(case):
    state, observation = prepared_state()
    form = observation["fields"]["dom"]["forms"][0]
    form["controls"][2]["value"] = "18:29"
    if case == "other_merchant":
        form["context_text"] = "别的门店 地址：别的地址"
    elif case == "foreign_action":
        form["action_url"] = "https://foreign.invalid/submit"
    elif case == "two_fields":
        form["controls"][0]["value"] = "2"
    elif case == "two_forms":
        duplicate = copy.deepcopy(form)
        duplicate["form_id"] = "snapshot-fixture:frame-0:form-1"
        observation["fields"]["dom"]["forms"].append(duplicate)
    elif case == "unknown":
        state["action_results"] = [{"status": "UNKNOWN"}]
    elif case == "disabled":
        observation["elements"][2]["disabled"] = True
    state["browser_artifacts"] = [artifact(observation)]
    if case == "old_snapshot":
        state["browser_observation"] = {**observation, "snapshot_id": "different"}
    correction = preparation_correction(state)
    if case == "positive":
        assert correction["idx"] == 2 and correction["text"] == "18:30"
        encoded = json.loads(browser_context(state, budget=20000))
        assert encoded["observation"]["fields"]["dom"]["forms"][0]["controls"][2]["value"] == "18:29"
    else:
        assert correction is None


@pytest.mark.parametrize("case", ["UNKNOWN", "RUNNING", "rejected", "cancelled", "different_plan"])
def test_preparation_continuation_does_not_grant_new_business_authority(case):
    state, _ = prepared_state()
    row = {"run_id": state["run_id"], "phase": "PARTIAL_FAILED", "state_json": state}
    actions = [{"status": case}] if case in {"UNKNOWN", "RUNNING"} else []
    if case == "rejected":
        state["approval_decision"] = "reject"
    elif case == "cancelled":
        row["cancel_requested"] = True
    elif case == "different_plan":
        state["execution_goal"]["plan_version"] += 1
    assert not preparation_resume_contract(row, actions)["can_resume"]
