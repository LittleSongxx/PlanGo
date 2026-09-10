"""Controlled observations exercise the preview contract, never real availability."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from plango.booking_preview import booking_preview_outcome

URL = "https://www.szuo.com/en/niccolo-chongqing-tealounge/reserve/landing?pax=2&start_date=2026-09-11&start_time=15%3A00"


def controlled_state():
    return {"selected_poi": {"name": "The Tea Lounge"},
            "execution_goal": {"kind": "page_read", "request": f"只读取并核对网页参数：{URL}；不查询空位或提交预约。"},
            "browser_observation": {"command_id": "controlled-read", "ok": True, "outcome": "observed",
                                    "snapshot_id": "controlled-snapshot", "page_version": "controlled-page", "tab_id": "controlled-tab",
                                    "url": URL, "observed_at": datetime.now(timezone.utc).isoformat(),
                                    "fields": {"booking_preview": {"adapter": "szuo_tealounge_v1", "protected": True,
                                                                   "merchant_label": "The Tea Lounge", "party_label": "2 Guests",
                                                                   "date_label": "Fri Sep 11", "time_label": "3:00 pm"}}}}


def test_controlled_preview_satisfied_is_never_availability_or_business_completion():
    state = controlled_state()
    before = deepcopy(state)
    result = booking_preview_outcome(state)
    assert result and result.status == "satisfied"
    assert result.data["scope"] == "booking_parameters"
    assert result.data["business_completed"] is False and result.data["availability_checked"] is False
    assert result.data["field_sources"]["date_year"] == "request_url_only"
    assert result.data["requested"] == {"party_size": 2, "date": "2026-09-11", "time": "15:00"}
    assert result.evidence_ids == ["page:controlled-read"]
    assert state == before


def test_unrelated_or_ambiguous_requests_do_not_activate_preview():
    for url in [URL + "&pax=2", URL + "#other", URL.replace("pax=2", "pax=0"),
                URL.replace("2026-09-11", "2026-02-30"), URL.replace("15%3A00", "25%3A00"),
                URL + " " + URL.replace("pax=2", "pax=3")]:
        state = controlled_state()
        state["execution_goal"]["request"] = url
        assert booking_preview_outcome(state) is None, url
    state = controlled_state()
    state["execution_goal"]["kind"] = "itinerary_preparation"
    assert booking_preview_outcome(state) is None


def test_another_site_is_compared_the_same_way_rather_than_ignored():
    """The check is a parameter comparison, so a second venue needs no new adapter."""
    other = URL.replace("www.szuo.com", "book.example.invalid").replace("niccolo-chongqing-tealounge", "another-venue")
    state = controlled_state()
    state["selected_poi"] = {"name": "Another Venue"}
    state["execution_goal"]["request"] = f"只读取并核对网页参数：{other}；不查询空位或提交预约。"
    state["browser_observation"]["url"] = other
    preview = state["browser_observation"]["fields"]["booking_preview"]
    preview.update(adapter="another_venue_v1", merchant_label="Another Venue")
    result = booking_preview_outcome(state)
    assert result and result.status == "satisfied", result
    assert result.data["requested"] == {"party_size": 2, "date": "2026-09-11", "time": "15:00"}
    assert result.data["business_completed"] is False and result.data["availability_checked"] is False


def test_a_different_requested_url_than_the_one_observed_needs_evidence():
    state = controlled_state()
    state["execution_goal"]["request"] = URL.replace("niccolo-chongqing", "niccolo-suzhou")
    result = booking_preview_outcome(state)
    assert result and result.status == "needs_evidence" and "request_url" in result.data["missing_evidence"]


def test_controlled_missing_or_stale_snapshot_needs_evidence():
    for key, value in [("snapshot_id", ""), ("page_version", ""), ("tab_id", ""), ("ok", False),
                       ("outcome", "failed"), ("observed_at", "not-a-date"),
                       ("observed_at", (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat())]:
        state = controlled_state()
        state["browser_observation"][key] = value
        result = booking_preview_outcome(state)
        assert result and result.status == "needs_evidence" and "current_snapshot" in result.data["missing_evidence"]


def test_controlled_capture_requires_protection_and_all_visible_labels():
    for key, value in [("protected", False), ("protected", "true"), ("adapter", ""),
                       ("merchant_label", "Another venue"), ("party_label", "3 Guests"),
                       ("date_label", "Fri Sep 12"), ("time_label", "3:00 am"), ("time_label", "")]:
        state = controlled_state()
        state["browser_observation"]["fields"]["booking_preview"][key] = value
        result = booking_preview_outcome(state)
        assert result and result.status == "needs_evidence", (key, value)


def test_an_uncomparable_merchant_label_is_reported_not_treated_as_a_mismatch():
    """Page identity is anchored by the URL, so a label with nothing to compare against
    is reported as unverified rather than withholding the parameter check."""
    state = controlled_state()
    state.pop("selected_poi")
    result = booking_preview_outcome(state)
    assert result and result.status == "satisfied"
    assert result.data["merchant_verified"] is False


def test_controlled_old_parameters_or_wrong_merchant_cannot_satisfy_current_request():
    for url in [URL.replace("pax=2", "pax=3"), URL.replace("2026-09-11", "2026-09-12"),
                URL.replace("15%3A00", "14%3A00"), URL.replace("niccolo-chongqing", "niccolo-suzhou")]:
        state = controlled_state()
        state["browser_observation"]["url"] = url
        result = booking_preview_outcome(state)
        assert result and result.status == "needs_evidence" and "request_url" in result.data["missing_evidence"]


def test_controlled_historical_preview_cannot_replace_missing_current_observation():
    state = controlled_state()
    state["browser_artifacts"] = [deepcopy(state["browser_observation"])]
    state["browser_observation"] = {}
    result = booking_preview_outcome(state)
    assert result and result.status == "needs_evidence" and not result.evidence_ids


def test_controlled_pending_action_and_manual_gate_cannot_be_hidden_by_preview():
    state = controlled_state()
    state["action_results"] = [{"status": "UNKNOWN"}]
    assert booking_preview_outcome(state).status == "needs_evidence"

    state = controlled_state()
    state["browser_observation"]["fields"]["dom"] = {"manual_gate": "captcha"}
    assert booking_preview_outcome(state).status == "needs_evidence"


def test_controlled_notice_restart_resumes_original_request_without_cart_or_new_budget(tmp_path):
    from fastapi.testclient import TestClient
    from plango.app import create_app
    from plango.task import BrowserDecision, TaskDecision
    from test_browser_harness import TOKEN, settings, wait_for
    config = settings(tmp_path)
    def application():
        app = create_app(config, token=TOKEN)
        async def actor(schema, **kwargs):
            assert schema is TaskDecision
            return TaskDecision(operation="read", browser=BrowserDecision(operation="navigate", url=URL))
        app.state.runtime.model.structured = actor
        return app
    headers = {"Authorization": "Bearer " + TOKEN}
    notice_url = URL.replace("/landing?", "/message?")

    def pending(client):
        return client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]

    def acknowledge(client, command, snapshot):
        result = {"command_id": command["command_id"], "browser_session_id": "fixture-desktop", "ok": True,
                  "outcome": "observed", "tab_id": "fixture-tab", "url": notice_url, "title": "Controlled booking preview"}
        if snapshot:
            result.update(snapshot_id=command["command_id"], page_version="controlled-page", text="Confirm and continue",
                          observed_at=datetime.now(timezone.utc).isoformat(), fields={"dom": {"forms": []}})
        assert client.post(f"/api/v1/browser/commands/{command['command_id']}/result", json=result).status_code == 200

    with TestClient(application(), headers=headers) as client:
        rid = client.post("/api/v1/runs", json={"input_text": f"只读取并核对网页预填参数：{URL}；不查询空位或提交预约。",
                                               "browser_session_id": "fixture-desktop"}).json()["run_id"]
        wait_for(client, rid, lambda value: bool(value["state"].get("browser_wait")))
        nav = pending(client)
        assert nav["operation"] == "navigate"
        acknowledge(client, nav, False)
        wait_for(client, rid, lambda value: (value["state"].get("browser_wait") or {}).get("command_id") not in (None, nav["command_id"]))
        read = pending(client)
        assert read["operation"] == "extract"
        acknowledge(client, read, True)
        paused = wait_for(client, rid, lambda value: (value["state"].get("browser_wait") or {}).get("error_kind") == "booking_notice")
        original_budget = paused["state"]["turn_budget"]

    with TestClient(application(), headers=headers) as client:
        assert not client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"]
        result = client.post(f"/api/v1/runs/{rid}/interrupts/{paused['interrupt_id']}/resume", json={"decision": "resume"})
        assert result.status_code == 202, result.text
        wait_for(client, rid, lambda value: (value["state"].get("browser_wait") or {}).get("command_id") not in (None, read["command_id"]))
        fresh = pending(client)
        assert fresh["operation"] == "extract"
        assert fresh.get("tab_id") is None, "A reopened notice resumes against the current visible tab; the result still needs exact source verification"
        observed = {**controlled_state()["browser_observation"], "command_id": fresh["command_id"],
                    "snapshot_id": fresh["command_id"], "tab_id": "reopened-tab", "browser_session_id": "fixture-desktop"}
        assert client.post(f"/api/v1/browser/commands/{fresh['command_id']}/result", json=observed).status_code == 200
        done = wait_for(client, rid, lambda value: bool(value.get("outcome")))
        assert done["phase"] == "SUCCEEDED", {"reason": done["state"].get("reason"), "outcome": done["state"].get("execution_outcome"), "observation": done["state"].get("browser_observation")}
        assert done["state"]["execution_outcome"]["data"]["scope"] == "booking_parameters"
        assert done["state"]["execution_outcome"]["data"]["availability_checked"] is False
        assert done["state"]["turn_budget"]["id"] == original_budget["id"]
        assert done["state"]["turn_id"] == paused["state"]["turn_id"]
        assert not done["state"].get("action_results")
