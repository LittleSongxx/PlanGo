"""Controlled saved-page/POI fixtures; no merchant, model, or external map execution."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plango.browser import commands
from plango.graph import artifact
from plango.offers import offer_hash
from plango_harness.agent.contracts import Evidence, Location, TripSpec
from plango_harness.agent.decisions import RequirementOutput
from sqlalchemy import update
from test_browser_harness import TOKEN
from test_dianping_preview import observation as preview_observation
from test_input_acceptance import app_without_worker
from test_selected_poi_refresh import canonical_place


def seed_page(client, *, command_id="offers-original", age=0, grounded=True, merchant=None):
    runtime = client.app.state.runtime
    created = client.post("/api/v1/runs", json={"input_text": "读取当前网页门店与优惠", "browser_session_id": "fixture",
        "location_context": {"city": "重庆", "source": "manual", "longitude": 106.57, "latitude": 29.56}}).json()
    rid = created["run_id"]
    place = merchant or canonical_place()
    identity = place.name + "\n地址：" + place.address
    items = [{"name": "精选双人餐", "price": 98.0, "people": 2, "quote": "精选双人餐\n售价98元\n周一至周日\n随时退", "conditions": ["周一至周日", "随时退"]},
             {"name": "50元代金券", "price": 47.0, "quote": "50元代金券\n售价47元\n周一至周日", "conditions": ["周一至周日"]}]
    observed = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    observation = {"command_id": command_id, "browser_session_id": "fixture", "ok": True, "outcome": "observed", "url": "https://fixture.invalid/shop",
                   "snapshot_id": "saved-snapshot", "observed_at": observed, "title": place.name, "text": identity + "\n团购套餐\n" + "\n".join(item["quote"] for item in items), "tables": []}
    processed = {"places": [{"name": place.name, "address": place.address, "quote": identity if grounded else "伪造门店身份"}], "offers": items}
    source = {"command_id": command_id, "artifact_id": "page:" + command_id}
    async def seed():
        async with runtime.database.session() as session:
            async with session.begin():
                await session.execute(commands.insert().values(command_id=command_id, run_id=rid, browser_session_id="fixture",
                    payload={"operation": "extract", "_processed": processed, "_processed_version": 4, "_turn_id": 1}, result=observation, created_at=1))
        row = await runtime.runs.get(rid)
        state = {**row["state_json"], "phase": "PARTIAL_FAILED", "outcome": "PARTIAL_FAILED", "model_token_count": 1234, "tool_call_count": 5,
                 "browser_artifacts": [artifact(observation, processed)], "browser_task_context": {"mode": "browser", "kind": "extract", "turn_id": 1, "request": "读取当前网页门店与优惠"}}
        await runtime.runs.save_state_and_events(state, expected_version=row["version"], events=[])
    client.portal.call(seed)
    return rid, source, items, observed


def selection_body(snapshot, source, items, index=0, poi_id="fixture"):
    return {"expected_version": snapshot["version"], "source_ref": source, "offer_index": index, "offer_hash": offer_hash(items[index]), "poi_id": poi_id}


def test_browser_comparison_edits_pin_saved_source_without_default_trip_or_new_budget(tmp_path):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid, source, _, observed = seed_page(client)
        before = client.get(f"/api/v1/runs/{rid}").json()
        assert before["offer_comparison"]["constraints"]["party_size"] is None
        assert before["offer_comparison"]["constraints"]["budget"] is None
        fields = {"party_size": 2, "visit_date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(), "budget": 150.0}
        response = client.post(f"/api/v1/runs/{rid}/requirements", json={"expected_version": before["version"], "fields": fields, "offer_source": source})
        assert response.status_code == 202, response.text
        edited = response.json()
        assert edited["version"] > before["version"] and edited["offer_comparison"]["constraints"]["party_size"] == 2
        assert not edited["state"].get("trip_spec") and not edited["state"].get("previous_spec")
        assert edited["state"]["turn_id"] == before["state"]["turn_id"]
        assert edited["state"].get("turn_budget") == before["state"].get("turn_budget")
        assert (edited["state"]["model_token_count"], edited["state"]["tool_call_count"]) == (1234, 5)
        assert client.post(f"/api/v1/runs/{rid}/requirements", json={"expected_version": edited["version"], "fields": {"party_size": 3}, "offer_source": source}).status_code == 202
        result = client.get(f"/api/v1/runs/{rid}").json()
        assert result["offer_comparison"]["entries"][0]["status"] == "ineligible"
        assert result["offer_comparison"]["source"]["observed_at"] == observed
        assert result["offer_comparison"]["source_ref"] == source
        saved = client.portal.call(runtime.bridge.get, source["command_id"])
    with TestClient(app_without_worker(tmp_path), headers={"Authorization": "Bearer " + TOKEN}) as client:
        restored = client.get(f"/api/v1/runs/{rid}").json()
        assert restored["offer_comparison"] == result["offer_comparison"]
        assert client.portal.call(client.app.state.runtime.bridge.get, source["command_id"]) == saved


def test_legacy_preview_without_processed_payload_is_derived_readonly_with_original_time(tmp_path):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    runtime.model.structured = AsyncMock(side_effect=AssertionError("No model when projecting retained receipts"))
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid, source, _, _ = seed_page(client)
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        observation = {**preview_observation(command_id=source["command_id"]), "observed_at": old, "ok": True, "outcome": "observed", "snapshot_id": "retained-preview", "browser_session_id": "fixture"}
        async def legacy():
            async with runtime.database.session() as session:
                async with session.begin():
                    await session.execute(update(commands).where(commands.c.command_id == source["command_id"]).values(payload={"operation": "extract", "_turn_id": 1}, result=observation))
        client.portal.call(legacy)
        retained = client.portal.call(runtime.bridge.get, source["command_id"])
        result = client.get(f"/api/v1/runs/{rid}").json()["offer_comparison"]
        assert result["merchant"]["name"] == "青竹餐厅(江北店)"
        assert [entry["price"] for entry in result["entries"]] == [47, 98, 19.9]
        assert result["source"]["observed_at"] == old and result["source"]["valid"] is False
        assert all(entry["status"] == "unknown" for entry in result["entries"])
        assert client.portal.call(runtime.bridge.get, source["command_id"]) == retained
        assert "_processed" not in retained["payload"]
        runtime.model.structured.assert_not_awaited()


def test_explicit_new_read_updates_comparison_without_silently_replacing_selected_offer(tmp_path):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid, old_source, items, _ = seed_page(client)
        chosen = {**old_source, "offer_index": 0, "offer_hash": offer_hash(items[0]), "place_id": "amap:fixture"}
        async def fresh_read():
            old = await runtime.bridge.get(old_source["command_id"])
            payload = {**old["payload"], "_turn_id": 2}
            observation = {**old["result"], "command_id": "fresh-offers", "snapshot_id": "fresh-snapshot", "observed_at": datetime.now(timezone.utc).isoformat()}
            row = await runtime.runs.get(rid)
            state = {**row["state_json"], "turn_id": 2, "input_text": "重新读取当前页面门店与优惠", "previous_spec": TripSpec(goal="原选用仍保留", selected_offer=chosen).model_dump(mode="json")}
            state["browser_task_context"]["offer_source"] = old_source
            state["browser_task_context"] = {
                **state["browser_task_context"],
                "mode": "browser",
                "kind": "extract",
                "operation": "read",
                "request": state["input_text"],
                "latest": state["input_text"],
                "turn_id": 2,
            }
            state["browser_task_context"].pop("offer_source", None)
            assert "offer_source" not in state["browser_task_context"]
            async with runtime.database.session() as session:
                async with session.begin():
                    await session.execute(commands.insert().values(command_id="fresh-offers", run_id=rid, browser_session_id="fixture", payload=payload, result=observation, created_at=2))
            state["browser_artifacts"] = [artifact(observation, payload["_processed"])]
            await runtime.runs.save_state_and_events(state, expected_version=row["version"], events=[])
        client.portal.call(fresh_read)
        result = client.get(f"/api/v1/runs/{rid}").json()
        assert result["offer_comparison"]["source_ref"]["command_id"] == "fresh-offers"
        assert result["selected_offer"]["command_id"] == old_source["command_id"]
        assert result["selected_offer"]["offer_hash"] == chosen["offer_hash"]


def test_first_offer_plan_requires_an_origin_instead_of_geocoding_the_default_city(tmp_path):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    runtime.world_service.provider.amap.get_place = AsyncMock(return_value=canonical_place())
    runtime.world_service.provider.amap.geocode = AsyncMock(side_effect=AssertionError("Search city is not the user's origin"))
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid, source, items, _ = seed_page(client)
        client.portal.call(runtime.bridge.update_location, rid, {"city": "重庆", "source": "config"})
        before = client.get(f"/api/v1/runs/{rid}").json()
        response = client.post(f"/api/v1/runs/{rid}/offer-selection", json=selection_body(before, source, items))
        assert response.status_code == 202, response.text
        client.portal.call(runtime._run_graph, rid)
        paused = client.get(f"/api/v1/runs/{rid}").json()
        assert paused["phase"] == "REQUIREMENTS_READY" and ":location:" in paused["interrupt_id"]
        assert not paused["state"].get("trip_spec")
        assert paused["selected_offer"]["place_id"] == "amap:fixture"
        runtime.world_service.provider.amap.geocode.assert_not_awaited()


@pytest.mark.parametrize("reply,fields,expected", [
    ("从重庆观音桥地铁站出发，2026年9月10日18:30，安排2小时，3人，总预算250元，只去已选的这家店，保留选定优惠并注明缺失规则。",
     {"party_size": 2, "budget": None}, (3, "2026-09-10", "18:30", 120, 250)),
    ("从重庆观音桥地铁站出发", {"party_size": 3, "visit_date": "2026-09-10", "budget": None},
     (3, "2026-09-10", None, 360, None)),
    ("从重庆观音桥地铁站出发，总预算不限，人均预算不限，日期待定，时间待定",
     {"party_size": 3, "visit_date": "2026-09-10", "budget": 250, "per_person_budget": 100}, (3, None, None, 360, None)),
], ids=["all-explicit-slots", "origin-only", "clear-and-unknown"])
def test_first_offer_origin_answer_merges_all_explicit_slots_and_survives_restart(tmp_path, reply, fields, expected):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    place = canonical_place()
    runtime.world_service.provider.amap.get_place = AsyncMock(return_value=place)
    origin = Location(name="重庆观音桥地铁站", latitude=place.latitude, longitude=place.longitude)
    runtime.world_service.provider.geocode = AsyncMock(return_value=origin)
    proposal = RequirementOutput(location_name=origin.name)
    if expected[2]:
        proposal = RequirementOutput(location_name=origin.name, party_size=3, budget=250,
            visit_date=datetime(2026, 9, 10).date(), time_window_start='18:30', duration_minutes=120)
    elif expected[1] is None:
        proposal = RequirementOutput(location_name=origin.name, clear_budget=True, clear_per_person_budget=True,
            visit_date_unknown=True, time_window_start_unknown=True)
    runtime.model.structured = AsyncMock(side_effect=lambda schema, *, fallback, **kwargs: proposal if schema is RequirementOutput else fallback)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid, source, items, _ = seed_page(client)
        client.portal.call(runtime.bridge.update_location, rid, {"city": "重庆", "source": "config"})
        before = client.get(f"/api/v1/runs/{rid}").json()
        edited = client.post(f"/api/v1/runs/{rid}/requirements", json={"expected_version": before["version"], "fields": fields, "offer_source": source}).json()
        selected = client.post(f"/api/v1/runs/{rid}/offer-selection", json=selection_body(edited, source, items)).json()
        client.portal.call(runtime._run_graph, rid)
        paused = client.get(f"/api/v1/runs/{rid}").json()
        assert ":location:" in paused["interrupt_id"] and not paused["state"].get("trip_spec")
        runtime.world_service.provider.geocode.assert_not_awaited()
        body = {"text": reply, "request_id": "origin-answer"}
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).status_code == 202
        accepted = client.portal.call(runtime.runs.get, rid)
        command = accepted["pending_command"]
        client.portal.call(lambda: runtime._run_graph(rid, resume=command["payload"], command_id=command["id"]))
        restored = client.get(f"/api/v1/runs/{rid}").json()
        state = restored["state"]
        spec = state["trip_spec"]
        assert tuple(spec[key] for key in ("party_size", "visit_date", "time_window_start", "duration_minutes", "budget")) == expected
        assert spec["per_person_budget"] is None and spec["travel_mode"] == "driving"
        assert spec["selected_offer"] == selected["selected_offer"] and spec["must_visit_place_ids"] == [place.place_id]
        assert spec["location"]["name"] == origin.name and spec["search_location"]["name"] == place.name
        runtime.world_service.provider.geocode.assert_awaited_once_with(origin.name)
        assert state["input_text"].endswith(reply) and state["messages"][-1]["content"] == reply
        assert state["messages"][:-1] == paused["state"]["messages"]
        assert state["turn_id"] == paused["state"]["turn_id"]
        assert state["consumed_command_id"] == command["id"] and state["turn_budget"]["id"] == accepted["state_json"]["turn_budget"]["id"]
        assert state["model_token_count"] >= 1234 and state["tool_call_count"] >= paused["state"]["tool_call_count"]
        assert not state["action_results"]
        if expected[2]:
            assert restored.get("draft_review") and not (state.get("clarification") or {}).get("fields"), restored
        else:
            # Explicitly undecided date and time stay unset and are reported with the
            # draft; they are not re-asked every turn.
            assert not (state.get("clarification") or {}).get("fields")
            assert state["trip_spec"]["visit_date"] == expected[1] and state["trip_spec"]["time_window_start"] == expected[2]
        row = client.portal.call(runtime.runs.get, rid)
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).status_code == 202
        assert client.portal.call(runtime.runs.get, rid) == row
        events = client.get(f"/api/v1/runs/{rid}/events").json()["events"]
        assert sum(event["event_type"] == "USER_MESSAGE" and event["payload"]["text"] == reply for event in events) == 1
    with TestClient(app_without_worker(tmp_path), headers={"Authorization": "Bearer " + TOKEN}) as client:
        restarted = client.get(f"/api/v1/runs/{rid}").json()["state"]
        for key in ("trip_spec", "messages", "turn_id", "turn_budget", "model_token_count", "tool_call_count", "consumed_command_id"):
            assert restarted[key] == state[key]


@pytest.mark.parametrize("case", ["stale", "ungrounded_merchant", "ungrounded_offer", "wrong_hash", "wrong_address", "wrong_branch", "cross_run", "unknown"])
def test_offer_selection_rejects_unverified_source_identity_and_unresolved_actions(tmp_path, case):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    canonical = canonical_place()
    if case == "wrong_address":
        canonical = canonical.model_copy(update={"address": "重庆市渝中区邹容路999号"})
    if case == "wrong_branch":
        canonical = canonical.model_copy(update={"name": "雾岚餐厅(其他分店)"})
    runtime.world_service.provider.amap.get_place = AsyncMock(return_value=canonical)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid, source, items, _ = seed_page(client, age=601 if case == "stale" else 0, grounded=case != "ungrounded_merchant")
        if case == "cross_run":
            _, source, items, _ = seed_page(client, command_id="foreign-source")
        if case == "ungrounded_offer":
            items[0] = {**items[0], "quote": "伪造优惠原文"}
            async def replace_offer():
                command = await runtime.bridge.get(source["command_id"])
                payload = {**command["payload"], "_processed": {**command["payload"]["_processed"], "offers": items}}
                async with runtime.database.session() as session:
                    async with session.begin():
                        await session.execute(update(commands).where(commands.c.command_id == source["command_id"]).values(payload=payload))
            client.portal.call(replace_offer)
        if case == "unknown":
            client.portal.call(lambda: runtime.runs.record_action(action_id="unknown-offer-action", run_id=rid, plan_id="fixture", plan_version=1,
                tool_name="click", idempotency_key="old-key", request_hash="old-hash", arguments={}, status="UNKNOWN"))
        before = client.portal.call(runtime.runs.get, rid)
        body = selection_body(before, source, items)
        if case == "wrong_hash":
            body["offer_hash"] = "0" * 64
        response = client.post(f"/api/v1/runs/{rid}/offer-selection", json=body)
        assert response.status_code == 409, response.text
        assert client.portal.call(runtime.runs.get, rid) == before
        assert not before["state_json"].get("trip_spec")
        if case not in {"wrong_address", "wrong_branch"}:
            runtime.world_service.provider.amap.get_place.assert_not_awaited()


@pytest.mark.parametrize("case", ["wording", "different_brand", "different_number", "different_branch", "different_floor", "ungrounded"])
def test_identity_confirmation_only_records_a_bounded_user_mapping(tmp_path, case):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    page_place = canonical_place().model_copy(update={"name": "青竹餐厅(观音桥大融城店)", "address": "观音桥步行街8号附5号大融城6楼6-010"})
    canonical = canonical_place().model_copy(update={"name": "青竹餐厅(大融城店)", "address": "重庆市江北区观音桥步行街8号大融城6层"})
    changes = {"different_brand": {"name": "白鹭餐厅(大融城店)"}, "different_number": {"address": "重庆市江北区观音桥步行街99号大融城6层"},
               "different_branch": {"name": "青竹餐厅(解放碑店)"}, "different_floor": {"address": "重庆市江北区观音桥步行街8号大融城9层"}}
    canonical = canonical.model_copy(update=changes.get(case, {}))
    runtime.world_service.provider.amap.get_place = AsyncMock(return_value=canonical)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid, source, items, observed = seed_page(client, merchant=page_place, grounded=case != "ungrounded")
        before = client.portal.call(runtime.runs.get, rid)
        body = selection_body(before, source, items)
        assert client.post(f"/api/v1/runs/{rid}/offer-selection", json=body).status_code == 409
        assert client.portal.call(runtime.runs.get, rid) == before
        response = client.post(f"/api/v1/runs/{rid}/offer-selection", json={**body, "identity_confirmed": True})
        if case != "wording":
            assert response.status_code == 409, response.text
            assert client.portal.call(runtime.runs.get, rid) == before
            return
        assert response.status_code == 202, response.text
        selected = response.json()
        evidence = next(item for item in selected["state"]["evidence"] if item["evidence_id"] == selected["selected_offer"]["identity_evidence_id"])
        assert evidence["source"] == "user"
        assert evidence["payload"]["browser"]["name"] == page_place.name
        assert evidence["payload"]["browser"]["observed_at"] == observed
        assert evidence["payload"]["canonical"]["name"] == canonical.name
        assert evidence["payload"]["canonical"]["address"] == canonical.address
        assert evidence["payload"]["business_verified"] is False and evidence["payload"]["transaction_authorized"] is False
        client.portal.call(runtime._run_graph, rid)
        paused = client.get(f"/api/v1/runs/{rid}").json()
        assert not paused["state"]["action_results"]
        assert evidence in paused["state"]["evidence"]
        assert paused["state"]["trip_spec"]["party_size"] is None and paused["state"]["trip_spec"]["budget"] is None


def test_selected_offer_same_run_uses_canonical_poi_and_preserves_source_through_planning_and_restart(tmp_path):
    app = app_without_worker(tmp_path)
    runtime = app.state.runtime
    place = canonical_place()
    runtime.world_service.provider.amap.get_place = AsyncMock(return_value=place)
    proof = Evidence(evidence_id="fixture-canonical", source="amap", source_ref="https://fixture.invalid/poi", observed_at=datetime.now(timezone.utc),
                     expires_at=datetime.now(timezone.utc) + timedelta(minutes=10), payload={"place_id": place.place_id, "name": place.name, "address": place.address, "latitude": place.latitude, "longitude": place.longitude})
    runtime.world_service.provider.amap.search_places = AsyncMock(return_value=([place], [proof]))
    runtime.world_service.provider.amap.search_pois = AsyncMock(return_value={"pois": [{"id": "fixture", "name": place.name, "address": place.address, "location": "106.57,29.56"}], "observed_at": datetime.now(timezone.utc).isoformat()})
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        rid, source, items, observed = seed_page(client)
        candidates = client.post(f"/api/v1/runs/{rid}/merchant-candidates", json={"source_ref": source})
        assert candidates.status_code == 200 and candidates.json()["source"] == "amap"
        assert candidates.json()["candidates"][0]["poi_id"] == "fixture"
        before = client.get(f"/api/v1/runs/{rid}").json()
        fields = {"party_size": 2, "visit_date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(), "budget": None}
        edited = client.post(f"/api/v1/runs/{rid}/requirements", json={"expected_version": before["version"], "fields": fields, "offer_source": source}).json()
        body = selection_body(edited, source, items)
        selected = client.post(f"/api/v1/runs/{rid}/offer-selection", json=body)
        assert selected.status_code == 202, selected.text
        accepted = selected.json()
        assert accepted["run_id"] == rid and accepted["selected_offer"]["place_id"] == place.place_id
        assert accepted["state"]["selected_poi"]["address"] == place.address
        assert accepted["state"]["action_proposal"] is None and accepted["state"]["approval_decision"] is None
        assert client.post(f"/api/v1/runs/{rid}/offer-selection", json=body).status_code == 409
        # The compiled graph first requests the still-unconfirmed start time, without defaults for budget.
        client.portal.call(runtime._run_graph, rid)
        paused = client.get(f"/api/v1/runs/{rid}").json()
        assert paused["phase"] == "REQUIREMENTS_READY", paused
        spec = paused["state"]["trip_spec"]
        assert spec["party_size"] == 2 and spec["budget"] is None
        assert spec["selected_offer"] == accepted["selected_offer"]
        assert spec["must_visit_place_ids"] == [place.place_id]
        assert spec["search_location"]["latitude"] == place.latitude
        assert spec["location"]["latitude"] == 29.56
        assert paused["offer_comparison"]["source"]["observed_at"] == observed
        assert not paused["state"]["action_results"]
        completed_fields = client.post(f"/api/v1/runs/{rid}/requirements", json={"expected_version": paused["version"], "fields": {"time_window_start": "18:30"}, "offer_source": source})
        assert completed_fields.status_code == 202, completed_fields.text
        client.portal.call(runtime._run_graph, rid)
        draft = client.get(f"/api/v1/runs/{rid}").json()
        assert draft.get("draft_review"), draft
        assert draft["state"]["selected_plan"]["stops"][0]["place_id"] == place.place_id
        review = draft["draft_review"]
        saved = client.post(f"/api/v1/runs/{rid}/draft-decision", json={"decision": "save", **{key: review[key] for key in ("interrupt_id", "plan_id", "plan_version")}})
        assert saved.status_code == 202, saved.text
        row = client.portal.call(runtime.runs.get, rid)
        client.portal.call(lambda: runtime._run_graph(rid, resume=row["pending_command"]["payload"], command_id=row["pending_command"]["id"]))
        finished = client.get(f"/api/v1/runs/{rid}").json()
        assert finished["phase"] == "SUCCEEDED", finished
        assert finished["state"]["execution_outcome"]["data"]["scope"] == "draft_ready"
        spec = finished["state"]["trip_spec"]
        kept = client.portal.call(runtime.bridge.get, source["command_id"])
    with TestClient(app_without_worker(tmp_path), headers={"Authorization": "Bearer " + TOKEN}) as client:
        restored = client.get(f"/api/v1/runs/{rid}").json()
        assert restored["selected_offer"] == accepted["selected_offer"]
        assert restored["state"]["trip_spec"] == spec
        assert restored["offer_comparison"]["source"]["observed_at"] == observed
        assert client.portal.call(client.app.state.runtime.bridge.get, source["command_id"]) == kept
