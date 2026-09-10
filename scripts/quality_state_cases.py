"""Declared imported-state workflows; no model replacement or scoring.

The imported draft is explicitly synthetic. Only subsequent messages and draft
decisions traverse the normal API and real workflow. This does not measure how
the model creates the initial task, and it is not Electron end-to-end coverage.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plango.browser import bindings
from plango.outcomes import draft_review
from plango.requirements import OfferSourceRef
from plango_harness.agent.contracts import (
    ConstraintCheck,
    Evidence,
    Location,
    OfferReference,
    PlaceCandidate,
    PlanCandidate,
    PlanStop,
    RunPhase,
    TripSpec,
    VerifierResult,
)
from plango_harness.agent.state import initial_state
from plango_harness.providers.world import Supply, WorldProviderError
from sqlalchemy.engine import make_url

FIXTURE = Path(__file__).resolve().parents[1] / "eval/quality-v1/runtime-fixtures.dev.json"
ROOT = FIXTURE.parents[2]
LEGACY_DRIVERS = {"DEV-05": "edit", "DEV-06": "edit", "DEV-09": "save_restart", "DEV-10": "message_recovery"}


def case_driver(case):
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", case_id):
        raise ValueError("invalid_quality_case_id")
    driver = (case.get("environment") or {}).get("driver") or LEGACY_DRIVERS.get(case_id)
    if driver not in {"edit", "save_restart", "message_recovery", "message_uncertain"}:
        raise ValueError("unsupported_imported_case_driver")
    return driver


def load_runtime_fixture(fixture=None, fixture_provenance=None):
    """Resolve the declared value, without inferring any desired post-task state."""
    inline = fixture is not None
    if inline and (not isinstance(fixture, dict) or not isinstance(fixture_provenance, dict)):
        raise ValueError("inline_fixture_requires_source_path_and_pointer")
    provenance = fixture_provenance or {"path": str(FIXTURE.relative_to(ROOT)), "json_pointer": ""}
    if not isinstance(provenance.get("path"), str) or not isinstance(provenance.get("json_pointer"), str):
        raise ValueError("fixture_source_path_and_pointer_required")
    path = (ROOT / provenance["path"]).resolve()
    if not path.is_relative_to(ROOT) or path.suffix != ".json":
        raise ValueError("fixture_source_must_be_repository_json")
    raw = path.read_bytes()  # A missing file is an error, never fabricated provenance.
    value = json.loads(raw)
    pointer = provenance["json_pointer"]
    if pointer and not pointer.startswith("/"):
        raise ValueError("invalid_fixture_json_pointer")
    for token in pointer.split("/")[1:] if pointer else []:
        if re.search(r"~(?![01])", token) or isinstance(value, list) and not re.fullmatch(r"0|[1-9]\d*", token):
            raise ValueError("invalid_fixture_json_pointer")
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if not isinstance(value, dict) or inline and encoded != json.dumps(fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode():
        raise ValueError("inline_fixture_does_not_match_declared_source")
    if value.get("route", {}).get("mode") != "walking" or type(value["route"].get("cost_per_person")) not in {int, float} or value["route"]["cost_per_person"] != 0:
        raise ValueError("imported_fixture_only_supports_declared_zero_fare_walking_route")
    if value.get("initial_draft", {}).get("save_state") != "not_saved" or value["initial_draft"].get("business_completed") is not False:
        raise ValueError("imported_fixture_requires_unsaved_nonbusiness_initial_state")
    return value, {"kind": "inline_verified" if inline else "file", "path": str(path.relative_to(ROOT)), "json_pointer": pointer,
                   "whole_file_sha256": hashlib.sha256(raw).hexdigest(), "value_sha256": hashlib.sha256(encoded).hexdigest()}


def install_world(runtime, case_dir, *, place, evidence, origin, fixture):
    """Replace only this owned runtime's world reads with the declared fixture."""
    world = runtime.world_service.provider
    log_path = Path(case_dir) / "controlled-world.jsonl"

    def record(method, **arguments):
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"method": method, "arguments": arguments,
                                     "scope": "explicit_synthetic_world", "at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")

    async def blocked(*args, **kwargs):
        record("undeclared_provider_network_blocked")
        raise WorldProviderError("quality_fixture_network_forbidden")

    async def geocode(address, **kwargs):
        record("geocode", address=address)
        if address == origin.name:
            return origin
        if address in {place.name, place.address}:
            return Location(name=address, latitude=place.latitude, longitude=place.longitude)
        return None

    async def search(query, location, *, limit=8, **kwargs):
        record("search_places", query=query, latitude=location.latitude, longitude=location.longitude)
        return [place][:limit], [evidence]

    async def get_place(place_id, **kwargs):
        record("get_place", place_id=place_id)
        return place if place_id == place.place_id else None

    async def refresh(previous):
        record("refresh_place", place_id=previous.place_id)
        if previous.place_id != place.place_id:
            raise WorldProviderError("quality_fixture_place_missing")
        return place, evidence

    async def supply(place_id, at_minute):
        record("get_supply", place_id=place_id, at_minute=at_minute)
        if place_id != place.place_id:
            raise WorldProviderError("quality_fixture_place_missing")
        return Supply(place_id=place_id, **fixture["supply"], observed_at=evidence.observed_at, expires_at=evidence.expires_at)

    async def route(start, destination, *, mode=None, visit_date=None, timezone_name="Asia/Shanghai", at_minute=None):
        record("estimate_route", place_id=destination.place_id, mode=mode,
               origin={"latitude": start.latitude, "longitude": start.longitude},
               visit_date=visit_date.isoformat() if visit_date else None, at_minute=at_minute)
        if (destination.place_id != place.place_id or (start.latitude, start.longitude) != (origin.latitude, origin.longitude)
                or mode not in {None, "walking"}):
            raise WorldProviderError("quality_fixture_route_missing")
        value = {**fixture["route"], "source": "simulated", "recommended": "walking", "destination_place_id": place.place_id}
        proof = Evidence(evidence_id="quality-route:" + place.place_id, source="simulated",
                         source_ref="fixture://quality-v1/imported-state-route" if evidence.source_ref == "fixture://quality-v1/imported-state" else evidence.source_ref + "/route",
                         claim="显式受控单程步行路线，非实时高德",
                         payload=value, observed_at=evidence.observed_at, expires_at=evidence.expires_at)
        return value, proof

    async def weather(location, *args, **kwargs):
        record("get_weather", latitude=location.latitude, longitude=location.longitude)
        raise WorldProviderError("quality_fixture_weather_unknown")

    # Keep BrowserWorld's origin/binding and model/browser authorization logic.
    for name, function in {"geocode": geocode, "search_places": search, "get_place": get_place,
                           "refresh_place": refresh, "get_supply": supply,
                           "estimate_route": route, "get_weather": weather}.items():
        setattr(world, name, function)
    for name in ("_get", "_request", "geocode", "search_places", "search_pois", "get_place", "refresh_place", "estimate_route", "get_supply", "get_weather"):
        setattr(world.amap, name, blocked)
    return log_path


async def seed(runtime, case, case_dir, *, user_id="desktop", browser_session_id=None, fixture=None, fixture_provenance=None):
    """Import a declared unsaved initial draft, then produce a real review pause."""
    case_dir = Path(case_dir).resolve()
    database_path = make_url(runtime.settings.database_url).database
    if (not runtime.settings.is_sqlite or not database_path
            or not all(Path(path).resolve().is_relative_to(case_dir) for path in (
                database_path, runtime.settings.data_dir, runtime.settings.checkpoint_path))):
        raise ValueError("quality_seed_requires_case_owned_sqlite_data_dir")
    if not runtime._started or runtime.graph is None:
        raise ValueError("quality_seed_requires_started_runtime")
    driver = case_driver(case)
    manifest_path = case_dir / "imported-state.json"
    if manifest_path.exists():
        raise ValueError("quality_seed_refuses_overwriting_existing_attempt")
    fixture, provenance = load_runtime_fixture(fixture, fixture_provenance)
    original = case["environment"]["initial_state"]
    raw_spec = dict(original["spec"])
    raw_place = raw_spec.pop("selected_poi")
    raw_offer = raw_spec.pop("selected_offer")
    run_id, place_id = uuid.uuid4().hex, "quality:" + uuid.uuid4().hex
    source_id = "imported-source-" + uuid.uuid4().hex
    source = OfferSourceRef(command_id=source_id, artifact_id="page:" + source_id)
    offer_hash = hashlib.sha256(json.dumps(raw_offer, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    reference = OfferReference(**source.model_dump(),
                               offer_index=0, offer_hash=offer_hash, place_id=place_id)
    now = datetime.fromisoformat(case["as_of"])
    origin = Location.model_validate(fixture["origin"])
    place = PlaceCandidate(place_id=place_id, name=raw_place["name"], address=raw_place["address"],
                           **fixture["place"], evidence_ids=[source_id])
    legacy_source = provenance["path"] == str(FIXTURE.relative_to(ROOT)) and not provenance["json_pointer"]
    source_ref = "fixture://quality-v1/imported-state" if legacy_source else "fixture://" + provenance["path"] + "#" + provenance["json_pointer"]
    evidence = Evidence(evidence_id=source_id, source="simulated", source_ref=source_ref,
                        claim="明确合成初态：门店身份已选择；完整餐费、供给及优惠完整规则未知。",
                        payload={**place.model_dump(mode="json"), "imported_offer": raw_offer, "fixture_kind": "explicit_synthetic_initial_state"},
                        observed_at=now, expires_at=now + timedelta(days=3), confidence=1)
    raw_spec["max_distance_km"] = raw_spec.pop("route_distance_km")
    seed_text = "导入受控既有草案，保留已选门店与优惠；供给和完整使用规则未知，尚未保存。"
    spec = TripSpec(**raw_spec, goal=seed_text, location=origin, required_activities=["餐厅"],
                    must_visit_place_ids=[place_id], selected_offer=reference)
    version = 1  # independent actual initial version; task's 4 is an alias
    start = int(spec.time_window_start[:2]) * 60 + int(spec.time_window_start[3:5])
    draft = fixture["initial_draft"]
    arrival = start + draft["arrival_offset_minutes"]
    unknown = [ConstraintCheck(name="imported:" + str(i), kind="unknown", passed=None, detail=detail)
               for i, detail in enumerate(draft["unknowns"])]
    plan = PlanCandidate(plan_id="imported:" + uuid.uuid4().hex, version=version, label="受控导入的未保存草案",
                         party_size=spec.party_size, total_cost=0, evidence_ids=[source_id],
                         stops=[PlanStop(place_id=place_id, name=place.name, address=place.address, category=place.category,
                                         start_minute=arrival, end_minute=arrival + draft["dwell_minutes"],
                                         requested_dwell_min=draft["dwell_minutes"], unit_price=None,
                                         travel_min=fixture["route"]["walking_min"], distance_km=fixture["route"]["distance_km"],
                                         distance_kind="route", transport_cost=0, estimated_wait_min=None,
                                         tags=["price_unknown", "supply_unknown"], locked=True, evidence_ids=[source_id])],
                         checks=unknown, rationale="仅导入既有受控初态；0为已知费用小计，完整餐费未知，不是免费或已完成。")
    state = initial_state(run_id=run_id, user_id=user_id, input_text=seed_text)
    state.update(trip_spec=spec, selected_poi={**place.model_dump(mode="json"), "evidence": evidence.model_dump(mode="json")}, place_candidates=[place], evidence=[evidence],
                 selected_plan=plan, candidate_plans=[plan], plan_version=version, turn_count=1,
                 verifier=VerifierResult(plan_id=plan.plan_id, evidence_complete=False, unknown_evidence=unknown),
                 phase=RunPhase.PLAN_DRAFTED, browser_task_context={"mode": "planning", "kind": "planning", "request": seed_text, "turn_id": 1},
                 reason="显式导入初态，未执行本题修改/保存，未发生业务交易。")
    state["clarification"] = draft_review(state)
    state["interrupt_id"] = state["clarification"]["interrupt_id"]
    manifest_path.write_text(json.dumps({"case_id": case["case_id"], "run_id": run_id,
                                        "driver": driver, "fixture_provenance": provenance,
                                        "status": "import_started", "environment_level": "imported_state_workflow"}, ensure_ascii=False) + "\n")
    install_world(runtime, case_dir, place=place, evidence=evidence, origin=origin, fixture=fixture)
    await runtime.runs.create_with_event(run_id, user_id, seed_text)
    async with runtime.database.session() as session:
        async with session.begin():
            await session.execute(bindings.insert().values(run_id=run_id, browser_session_id=browser_session_id or "quality-" + run_id,
                                  enabled_skills=[], location_context={"source": "manual", "city": "重庆", "longitude": origin.longitude, "latitude": origin.latitude}))
    await runtime.runs.save_state(state)
    await runtime.graph.aupdate_state({"configurable": {"thread_id": run_id}}, state, as_node="browser_variants")
    await runtime._run_graph(run_id)
    initial_projection = await runtime.get_run(run_id)
    if not initial_projection.get("draft_review") or initial_projection.get("outcome"):
        raise ValueError("quality_initial_review_pause_not_established")
    aliases = {original["run_id"]: run_id, raw_place["id"]: place_id,
               raw_offer["offer_id"]: reference.model_dump(mode="json"), "initial_plan_version": version}
    manifest = {"case_id": case["case_id"], "driver": driver, "status": "imported_unsaved_review", "environment_level": "imported_state_workflow",
                "fixture_path": provenance["path"], "fixture_sha256": provenance["whole_file_sha256"],
                "fixture_value_sha256": provenance["value_sha256"], "fixture_json_pointer": provenance["json_pointer"], "fixture_provenance": provenance,
                "run_id": run_id, "aliases": aliases, "imported_at": datetime.now(timezone.utc).isoformat(),
                "clock": {"fixture_observed_at": case["as_of"], "fixture_expires_at": evidence.expires_at.isoformat(),
                          "fixture_scope": "synthetic observation uses the registered case business clock", "runtime_time": "database leases, timeouts, call logs remain real wall clock"},
                "scope": "Imported historical fixture is not model output; initial review is unsaved; no new input acceptance records were preloaded.",
                "limitations": ["无真实网页、无真实高德", "导入优惠引用保持身份但无真实浏览器命令回执；不能把它算作优惠网页提取成功", "后端API工作流不覆盖Electron发送与展示"]}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {**manifest, "initial_projection": initial_projection}


def wait_for_stop(client, run_id, timeout=320):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}")
        response.raise_for_status()
        value = response.json()
        if not value.get("command_pending") and (value.get("draft_review") or value.get("state", {}).get("clarification")
                or value.get("state", {}).get("browser_wait") or value["phase"] in {"SUCCEEDED", "FAILED", "PARTIAL_FAILED", "INFEASIBLE", "CANCELLED"}):
            return value
        time.sleep(0.05)
    raise TimeoutError("quality_case_wait_timeout")


def run_edits(client, case, run_id, collect_checkpoint, *, timeout=320):
    """Send only the preregistered natural-language turns; never inject target fields."""
    if case_driver(case) != "edit":
        raise ValueError("run_edits_requires_edit_driver")
    if not case["agent_input"]["user_turns"]:
        raise ValueError("edit_driver_requires_preregistered_input")
    turns = []
    for index, turn in enumerate(case["agent_input"]["user_turns"], 1):
        response = client.post(f"/api/v1/runs/{run_id}/messages", json={"text": turn["message"]})
        if response.status_code != 202:
            return {"stop_reason": "input_rejected", "http_status": response.status_code, "turn_results": turns}
        snapshot = wait_for_stop(client, run_id, timeout)
        collect_checkpoint(f"t{index}", snapshot)
        turns.append({"turn": index, "phase": snapshot["phase"], "input_receipt": response.json()})
        if not snapshot.get("draft_review") and index < len(case["agent_input"]["user_turns"]):
            collect_checkpoint("final", snapshot)
            return {"stop_reason": "unexpected_stop_before_scripted_next_turn", "turn_results": turns}
    collect_checkpoint("final", snapshot)
    clarification = (snapshot.get("state") or {}).get("clarification") or {}
    return {"stop_reason": "unscripted_clarification" if clarification and clarification.get("kind") != "draft_review" else "script_finished", "turn_results": turns}


def save_draft(client, run_id, collect_checkpoint, *, timeout=320):
    """Backend part only; an Electron runner must separately click the real UI."""
    snapshot = client.get(f"/api/v1/runs/{run_id}").json()
    review = snapshot.get("draft_review")
    if not review:
        raise ValueError("quality_save_requires_existing_unsaved_review")
    response = client.post(f"/api/v1/runs/{run_id}/draft-decision", json={"decision": "save", **{k: review[k] for k in ("interrupt_id", "plan_id", "plan_version")}})
    response.raise_for_status()
    saved = wait_for_stop(client, run_id, timeout)
    collect_checkpoint("before_restart", saved)
    return {"receipt": response.json(), "snapshot": saved, "scope": "backend_only_save_no_Electron_click"}


def check_wiring():
    """Offline fallback is only a wiring test, never a quality attempt."""
    import sqlite3
    import tempfile

    from fastapi.testclient import TestClient
    from plango.app import create_app
    from plango.settings import DesktopSettings

    tasks = json.loads((FIXTURE.parent / "tasks.dev.json").read_text())
    parent = FIXTURE.parents[2] / "output/quality-state-wiring"
    parent.mkdir(parents=True, exist_ok=True)
    results = []
    for index in (4, 5):
        work = Path(tempfile.mkdtemp(prefix="offline-", dir=parent)).resolve()
        config = DesktopSettings(_env_file=None, database_url=f"sqlite+aiosqlite:///{work}/runs.sqlite", data_dir=work,
                                 checkpoint_path=work / "checkpoint.sqlite", openai_api_key="", embedding_api_key="", amap_webservice_key="")
        app = create_app(config, token="offline-wiring-only")
        fallback_calls = []

        async def offline(schema, *, fallback, **kwargs):
            fallback_calls.append(schema.__name__)
            return fallback

        app.state.runtime.model.structured = offline
        snapshots = {}

        def collect(stage, snapshot):
            snapshots[stage] = snapshot

        with TestClient(app, headers={"Authorization": "Bearer offline-wiring-only"}) as client:
            imported = client.portal.call(seed, app.state.runtime, tasks[index], work)
            run_id = imported["run_id"]
            result = run_edits(client, tasks[index], run_id, collect, timeout=20)
            (work / "offline-wiring.json").write_text(json.dumps({"scope": "offline fallback wiring; not quality evaluation", "result": result, "snapshots": snapshots}, ensure_ascii=False, indent=2))
            assert result["stop_reason"] in {"script_finished", "unscripted_clarification"}, result
            assert {"RequirementOutput", "TaskIntent", "TaskDecision"} & set(fallback_calls), "Actual requirement workflow must run after the natural input"
            with sqlite3.connect(work / "runs.sqlite") as db:
                state = json.loads(db.execute("SELECT state_json FROM agent_run WHERE run_id = ?", (run_id,)).fetchone()[0])
            spec = state.get("trip_spec")
            if index == 4:
                # Disabled-model fallback does not promise natural-language
                # party extraction; this check only proves sparse API writes.
                assert spec["budget"] is None and spec["per_person_budget"] is None
            else:
                first_spec = snapshots["t1"]["state"]["trip_spec"]
                assert first_spec["search_radius_km"] == 0.5 and first_spec["max_distance_km"] == 2
            if snapshots["final"].get("draft_review"):
                saved = save_draft(client, run_id, collect, timeout=20)
                assert saved["snapshot"]["state"]["execution_outcome"]["data"]["business_completed"] is False
        # A new app/lifespan uses the same owned SQLite checkpoint, not copied memory.
        with TestClient(create_app(config, token="offline-wiring-only"), headers={"Authorization": "Bearer offline-wiring-only"}) as client:
            restored = client.get(f"/api/v1/runs/{run_id}").json()
            assert restored["state"].get("trip_spec") == spec
        results.append({"case_id": tasks[index]["case_id"], "work": str(work), "fallback_calls": fallback_calls})
    print(json.dumps({"scope": "offline fallback API/checkpoint wiring; no quality score", "checks": results}, ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-wiring", action="store_true", required=True)
    parser.parse_args()
    check_wiring()
