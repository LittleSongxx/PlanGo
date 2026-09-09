"""Synthetic native-form facts exercise the same deterministic user-facing Outcome path."""

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from plango.app import create_app
from plango.graph import BrowserDecision, artifact
from plango.outcomes import ExecutionGoal, preparation_outcome
from plango_harness.agent.contracts import PlaceCandidate, PlanCandidate, PlanStop, TripSpec
from test_browser_harness import TOKEN, settings, wait_for
from test_browser_navigation import browser_driver


def prepared_state():
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=1)
    spec = TripSpec(goal="明天三人晚餐", party_size=3, visit_date=day, timezone="Asia/Shanghai", time_window_start="18:30")
    stop = PlanStop(place_id="amap:fixture", name="雾岚餐厅", address="重庆市渝中区邹容路1号", category="餐厅", start_minute=1110, end_minute=1170)
    plan = PlanCandidate(plan_id="plan-fixture", version=2, party_size=3, stops=[stop])
    goal = ExecutionGoal(run_id="run-fixture", plan_id=plan.plan_id, plan_version=2, approval_id="approval-fixture", request=spec.goal, requirements=spec, stops=plan.stops)
    elements = [
        {"idx": 0, "tag": "input", "input_type": "number", "name": "人数", "text": ""},
        {"idx": 1, "tag": "input", "input_type": "date", "name": "预约日期", "text": ""},
        {"idx": 2, "tag": "input", "input_type": "time", "name": "预约时间", "text": ""},
        {"idx": 3, "tag": "button", "input_type": "submit", "name": "", "text": "确认预约", "disabled": False},
    ]
    form = {"form_id": "snapshot-fixture:frame-0:form-0", "action_url": "https://fixture.invalid/reserve", "context_text": "商家：雾岚餐厅 地址：重庆市渝中区邹容路1号 人数 预约日期 预约时间 确认预约", "controls": [
        {"idx": 0, "input_type": "number", "name": "人数", "label": "人数", "value": "3"},
        {"idx": 1, "input_type": "date", "name": "预约日期", "label": "预约日期", "value": day.isoformat()},
        {"idx": 2, "input_type": "time", "name": "预约时间", "label": "预约时间", "value": "18:30"},
    ], "submit_indices": [3], "truncated": False}
    observation = {"command_id": "read-fixture", "ok": True, "outcome": "observed", "snapshot_id": "snapshot-fixture", "page_version": "page-fixture", "tab_id": "tab-fixture", "url": "https://fixture.invalid/book", "observed_at": datetime.now(timezone.utc).isoformat(), "text": form["context_text"], "elements": elements, "fields": {"dom": {"forms": [form]}}}
    state = {"run_id": "run-fixture", "selected_plan": plan, "execution_goal": goal.model_dump(mode="json"), "browser_observation": observation, "browser_artifacts": [artifact(observation)]}
    return state, observation


def test_matching_native_form_is_ready_to_review_without_business_completion():
    state, _ = prepared_state()
    outcome = preparation_outcome(state)
    assert outcome.status == "satisfied"
    assert outcome.data["scope"] == "ready_to_review"
    assert outcome.data["business_completed"] is False
    assert outcome.data["entries"][0]["timezone"] == "Asia/Shanghai"
    assert outcome.data["entries"][0]["time_source"] == "approved_plan"
    assert outcome.evidence_ids == ["page:read-fixture"]
    assert "尚未提交" in outcome.summary


@pytest.mark.parametrize("case", ["body_identity", "negated_identity", "wrong_branch", "party", "date", "time", "split_forms", "truncated", "stale", "wrong_snapshot", "fake_submit", "foreign_action", "unknown_date", "old_plan", "unknown_action"])
def test_preparation_cannot_be_claimed_from_wrong_missing_stale_or_mixed_facts(case):
    state, observation = prepared_state()
    form = observation["fields"]["dom"]["forms"][0]
    if case == "body_identity":
        form["context_text"] = "人数 预约日期 预约时间 确认预约"
    elif case == "negated_identity":
        form["context_text"] = "并非本次商家 " + form["context_text"]
    elif case == "wrong_branch":
        form["context_text"] = form["context_text"].replace("邹容路1号", "邹容路99号")
    elif case in {"party", "date", "time"}:
        form["controls"][{"party": 0, "date": 1, "time": 2}[case]]["value"] = {"party": "2", "date": "2000-01-01", "time": "19:00"}[case]
    elif case == "split_forms":
        other = copy.deepcopy(form)
        other["form_id"] = "snapshot-fixture:frame-0:form-1"
        other["controls"] = other["controls"][1:]
        form["controls"] = form["controls"][:1]
        observation["fields"]["dom"]["forms"].append(other)
    elif case == "truncated":
        form["truncated"] = True
    elif case == "stale":
        observation["observed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    elif case == "wrong_snapshot":
        form["form_id"] = "old-snapshot:frame-0:form-0"
    elif case == "fake_submit":
        observation["elements"][3]["input_type"] = "button"
    elif case == "foreign_action":
        form["action_url"] = "https://other.invalid/reserve"
    elif case == "unknown_date":
        state["execution_goal"]["requirements"]["visit_date"] = None
    elif case == "old_plan":
        state["execution_goal"]["plan_version"] = 1
    else:
        state["action_results"] = [{"status": "UNKNOWN"}]
    state["browser_artifacts"] = [artifact(observation)]
    outcome = preparation_outcome(state)
    assert outcome.status != "satisfied", case
    if case in {"party", "date", "time"}:
        assert outcome.status == "mismatch"
        assert outcome.data["issues"][0]["differences"]


@pytest.mark.parametrize("user_text,text,tables,expected", [
    ("读取当前网页菜单", "帮助中心：如何登录", [], "PARTIAL_FAILED"),
    ("读取当前网页内容", "帮助中心：账号设置说明", [], "SUCCEEDED"),
    ("读取当前网页菜单", "时价菜，价格待确认", [{"headers": ["菜品", "价格"], "rows": [["时价菜", "时价"]]}], "SUCCEEDED"),
])
def test_read_goal_has_real_content_or_menu_postcondition(tmp_path, user_text, text, tables, expected):
    app = create_app(settings(tmp_path), token=TOKEN)

    async def finish(schema, *, fallback, **kwargs):
        return BrowserDecision(operation="finish") if schema is BrowserDecision else fallback

    app.state.runtime.model.structured = finish
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={"input_text": user_text, "browser_session_id": "fixture-desktop"}).json()["run_id"]
        command, respond, _ = browser_driver(client, run_id)
        respond(command("extract"), text=text, tables=tables)
        done = wait_for(client, run_id, lambda v: v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert done["phase"] == expected, done
        assert done["state"]["execution_outcome"]["status"] == ("satisfied" if expected == "SUCCEEDED" else "needs_evidence")
        assert done["state"]["execution_outcome"]["data"]["business_completed"] is False


def test_selected_poi_is_refreshed_by_id_and_saved_with_one_fixed_evidence(tmp_path):
    app = create_app(settings(tmp_path), token=TOKEN)
    canonical = PlaceCandidate(place_id="amap:TRUSTED", name="规范门店", address="重庆市渝中区邹容路1号", category="餐厅", latitude=29.56, longitude=106.57, source="amap")
    lookup = AsyncMock(return_value=canonical)
    app.state.runtime.world_service.provider.amap.get_place = lookup
    app.state.runtime._enqueue_run = AsyncMock(return_value=True)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        response = client.post("/api/v1/runs", json={"input_text": "以选中门店为中心规划", "browser_session_id": "fixture-desktop", "location_context": {"city": "重庆", "longitude": 106.5, "latitude": 29.5, "source": "manual"}, "selected_poi": {"poi_id": "TRUSTED", "name": "客户端伪造名称", "address": "客户端伪造地址", "longitude": 0.0, "latitude": 0.0, "source": "amap"}})
        assert response.status_code == 202, response.text
        lookup.assert_awaited_once_with("amap:TRUSTED", refresh=True)
        run_id = response.json()["run_id"]
        current = client.get("/api/v1/runs/" + run_id).json()
        target = current["state"]["selected_poi"]
        assert (target["name"], target["longitude"], target["latitude"]) == (canonical.name, canonical.longitude, canonical.latitude)
        assert current["location_context"]["longitude"] == 106.5
        assert target["evidence"]["evidence_id"] in target["evidence_ids"]
        assert target["evidence"]["payload"]["address"] == canonical.address
        event = client.get("/api/v1/runs/" + run_id + "/events").json()["events"][0]
        assert event["payload"]["selected_poi"]["evidence"] == target["evidence"]


def test_plan_approval_reaches_readonly_preparation_then_reenters_planning_on_edit(tmp_path):
    """Full API/graph path, using explicitly synthetic provider and DOM observations."""
    app = create_app(settings(tmp_path), token=TOKEN)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        run_id = client.post("/api/v1/runs", json={
            "input_text": "今天18:30，3人吃饭，帮我规划一个餐厅行程，总预算300元",
            "browser_session_id": "fixture-desktop",
            "location_context": {"city": "重庆", "longitude": 106.57, "latitude": 29.56, "source": "manual"},
        }).json()["run_id"]
        command, respond, approve = browser_driver(client, run_id)
        provider_fields = {
            "places": [{"place_id": "browser:fixture", "name": "雾岚餐厅", "address": "重庆市渝中区邹容路1号", "category": "餐厅", "latitude": 29.56, "longitude": 106.57, "average_price": 50, "open_minute": 0, "close_minute": 1440,
                        "supply": {"open_now": True, "reservable": True, "seats_left": 10, "estimated_wait_min": 0}}],
            "routes": {"browser:fixture": {"driving_min": 0, "walking_min": 0, "transit_min": 0, "distance_km": 0,
                "origin": {"latitude": 29.56, "longitude": 106.57}, "cost_per_person": 0}},  # Explicit controlled route/fee for this form-approval check.
        }
        respond(command("extract"), fields=provider_fields)
        paused = wait_for(client, run_id, lambda v: v["phase"] in {"WAITING_APPROVAL", "FAILED", "INFEASIBLE"})
        assert paused["phase"] == "WAITING_APPROVAL", paused
        approved_plan = paused["state"]["selected_plan"]
        approve()
        prepared_read = command("extract")
        preparing = client.get("/api/v1/runs/" + run_id).json()
        goal = preparing["state"]["execution_goal"]
        assert goal["requirements"]["visit_date"]
        assert goal["stops"][0]["address"] == provider_fields["places"][0]["address"]
        _, form_observation = prepared_state()
        form = form_observation["fields"]["dom"]["forms"][0]
        form["controls"][0]["value"] = str(goal["requirements"]["party_size"])
        form["controls"][1]["value"] = goal["requirements"]["visit_date"]
        minute = goal["stops"][0]["start_minute"]
        form["controls"][2]["value"] = f"{minute // 60:02d}:{minute % 60:02d}"
        respond(prepared_read, snapshot_id="snapshot-fixture", page_version="page-fixture", url=form_observation["url"], text=form["context_text"], tables=[], elements=form_observation["elements"], fields=form_observation["fields"])
        completed = wait_for(client, run_id, lambda v: v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"})
        assert completed["phase"] == "SUCCEEDED", completed
        assert completed["state"]["execution_outcome"]["data"]["scope"] == "ready_to_review"
        assert completed["state"]["action_results"] == []
        assert any(item["type"] == "browser_preparation" for item in completed["state"]["browser_artifacts"])
        changed = client.post("/api/v1/runs/" + run_id + "/messages", json={"text": "改成4人，其他要求不变"})
        assert changed.status_code == 202, changed.text
        replanning = wait_for(client, run_id, lambda v: v["state"].get("turn_id", 0) > 1 and bool(v["state"].get("browser_wait")))
        assert replanning["state"]["browser_task_context"]["mode"] == "planning"
        assert replanning["state"].get("execution_goal") is None
        assert replanning["state"].get("execution_outcome") is None
        respond(command("extract"), fields=provider_fields)
        new_plan = wait_for(client, run_id, lambda v: v["phase"] in {"WAITING_APPROVAL", "FAILED", "INFEASIBLE"})
        assert new_plan["phase"] == "WAITING_APPROVAL", new_plan
        assert new_plan["state"]["trip_spec"]["party_size"] == 4
        assert new_plan["state"]["selected_plan"]["version"] > approved_plan["version"]
        assert new_plan["interrupt_id"] != paused["interrupt_id"]
        assert client.post("/api/v1/runs/" + run_id + "/resume", json={"decision": "approve", "interrupt_id": paused["interrupt_id"]}).status_code == 409
