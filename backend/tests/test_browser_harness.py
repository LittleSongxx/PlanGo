"""Offline protocol fixtures only. No vendor credentials, external pages or paid model calls."""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from plango.app import create_app
from plango.settings import DesktopSettings

TOKEN = "isolated-test-token"


def settings(directory):
    return DesktopSettings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{directory}/runs.sqlite",
        data_dir=Path(directory),
        checkpoint_path=Path(directory) / "checkpoints.sqlite",
        openai_api_key="",
        embedding_api_key="",
        amap_webservice_key="",
    )


def wait_for(client, run_id, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = client.get("/api/v1/runs/" + run_id).json()
        if predicate(value):
            return value
        time.sleep(0.02)
    raise AssertionError(value)


def fixture(command, **extra):
    return dict(
        command_id=command["command_id"],
        browser_session_id="fixture-desktop",
        ok=True,
        outcome="observed",
        snapshot_id="fixture-snapshot",
        tab_id="fixture-tab",
        url="https://fixture.invalid/menu",
        title="Offline fixture",
        text="清炒时蔬 28元",
        tables=[
            {"headers": ["菜品", "价格"], "rows": [["清炒时蔬", "28元"], ["未知价格", "时价"]]}
        ],
        **extra,
    )


class BrowserHarnessCheck(unittest.TestCase):
    def test_read_restart_auth_provenance_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory)
            with TestClient(
                create_app(config, token=TOKEN), headers={"Authorization": "Bearer " + TOKEN}
            ) as client:
                self.assertEqual(
                    client.get(
                        "/api/v1/runs", headers={"Authorization": "Bearer wrong"}
                    ).status_code,
                    401,
                )
                run_id = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "读取当前网页菜单",
                        "browser_session_id": "fixture-desktop",
                    },
                ).json()["run_id"]
                paused = wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
                command = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                self.assertEqual(paused["phase"], "REQUIREMENTS_READY")
                self.assertEqual(
                    client.get("/api/v1/browser/commands?browser_session_id=other").json()[
                        "commands"
                    ],
                    [],
                )
                bad = {**fixture(command), "browser_session_id": "other"}
                self.assertEqual(
                    client.post(
                        "/api/v1/browser/commands/" + command["command_id"] + "/result", json=bad
                    ).status_code,
                    409,
                )
            # The interrupted checkpoint and browser request survive a complete backend restart.
            with TestClient(
                create_app(config, token=TOKEN), headers={"Authorization": "Bearer " + TOKEN}
            ) as client:
                replay = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop&after=999"
                ).json()["commands"][0]
                self.assertEqual(replay["command_id"], command["command_id"])
                response = client.post(
                    "/api/v1/browser/commands/" + command["command_id"] + "/result",
                    json=fixture(command),
                )
                self.assertEqual(response.status_code, 200, response.text)
                done = wait_for(client, run_id, lambda v: v["phase"] in {"FAILED", "SUCCEEDED"})
                self.assertEqual(done["phase"], "SUCCEEDED", done)
                page = done["state"]["browser_artifacts"][0]
                self.assertEqual(page["source"], "browser")
                self.assertEqual(page["data"]["menu"][0]["price"], 28)
                self.assertIsNone(page["data"]["menu"][1]["price"])
                self.assertEqual(done["state"].get("action_results", []), [])
                self.assertTrue(
                    client.post(
                        "/api/v1/browser/commands/" + command["command_id"] + "/result",
                        json=fixture(command),
                    ).json()["replayed"]
                )
                changed = {**fixture(command), "text": "tampered"}
                self.assertEqual(
                    client.post(
                        "/api/v1/browser/commands/" + command["command_id"] + "/result",
                        json=changed,
                    ).status_code,
                    409,
                )
                events = client.get("/api/v1/runs/" + run_id + "/events").json()["events"]
                self.assertEqual([e["seq"] for e in events], sorted({e["seq"] for e in events}))
                self.assertTrue(any(e["event_type"] == "BROWSER_OBSERVATION" for e in events))

    def test_disabled_model_comparison_reports_partial_after_real_read(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(
                create_app(settings(directory), token=TOKEN),
                headers={"Authorization": "Bearer " + TOKEN},
            ) as client:
                rid = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "比较网页菜单，推荐最便宜的一家",
                        "browser_session_id": "fixture-desktop",
                    },
                ).json()["run_id"]
                wait_for(client, rid, lambda v: bool(v["state"].get("browser_wait")))
                command = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                client.post(
                    "/api/v1/browser/commands/" + command["command_id"] + "/result",
                    json=fixture(command),
                )
                result = wait_for(
                    client, rid, lambda v: v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"}
                )
                self.assertEqual(result["phase"], "PARTIAL_FAILED", result)
                self.assertTrue(result["state"]["browser_artifacts"])

    def test_login_pause_retry_and_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(
                create_app(settings(directory), token=TOKEN),
                headers={"Authorization": "Bearer " + TOKEN},
            ) as client:
                run_id = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "读取当前网页菜单",
                        "browser_session_id": "fixture-desktop",
                    },
                ).json()["run_id"]
                wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
                command = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                response = client.post(
                    "/api/v1/browser/commands/" + command["command_id"] + "/result",
                    json={
                        "command_id": command["command_id"],
                        "browser_session_id": "fixture-desktop",
                        "ok": False,
                        "outcome": "blocked",
                        "error_kind": "authentication_required",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                paused = client.get("/api/v1/runs/" + run_id).json()
                self.assertEqual(paused["phase"], "REQUIREMENTS_READY")
                response = client.post(
                    "/api/v1/runs/" + run_id + "/resume",
                    json={"decision": "resume", "text": "已登录"},
                )
                self.assertEqual(response.status_code, 202, response.text)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    pending = client.get(
                        "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                    ).json()["commands"]
                    if pending:
                        break
                    time.sleep(0.02)
                self.assertNotEqual(pending[0]["command_id"], command["command_id"])
                client.post(
                    "/api/v1/browser/commands/" + pending[0]["command_id"] + "/result",
                    json=fixture(pending[0]),
                )
                self.assertEqual(
                    wait_for(client, run_id, lambda v: v["phase"] == "SUCCEEDED")["phase"],
                    "SUCCEEDED",
                )
                self.assertEqual(client.get("/api/v1/memory/profile").json()["preferences"], [])
                added = client.post(
                    "/api/v1/memory/preferences", json={"text": "不吃香菜", "polarity": "dislike"}
                )
                self.assertEqual(added.status_code, 200, added.text)
                self.assertEqual(added.json()["preferences"][0]["text"], "不吃香菜")
                removed = client.delete("/api/v1/memory/preferences", params={"text": "不吃香菜"})
                self.assertEqual(removed.json()["preferences"], [])


class ActionAndPlanningCheck(unittest.TestCase):
    def test_browser_write_requires_exact_approval_and_receipt(self):
        from plango.graph import BrowserDecision

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(settings(directory), token=TOKEN)

            async def fixture_model(schema, *, fallback, **kwargs):
                if schema is BrowserDecision:
                    return BrowserDecision(
                        operation="click", idx=3, rationale="Offline fixture approval test"
                    )
                return fallback

            app.state.runtime.model.structured = fixture_model
            with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
                run_id = client.post(
                    "/api/v1/runs",
                    json={"input_text": "在网页点击预约", "browser_session_id": "fixture-desktop"},
                ).json()["run_id"]
                wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
                cmd = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                obs = fixture(cmd, elements=[{"idx": 3, "tag": "button", "text": "预约"}])
                self.assertEqual(
                    client.post(
                        "/api/v1/browser/commands/" + cmd["command_id"] + "/result", json=obs
                    ).status_code,
                    200,
                )
                paused = wait_for(
                    client, run_id, lambda v: v["phase"] in {"WAITING_APPROVAL", "FAILED"}
                )
                self.assertEqual(paused["phase"], "WAITING_APPROVAL", paused)
                self.assertEqual(
                    client.get(
                        "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                    ).json()["commands"],
                    [],
                )
                self.assertEqual(
                    client.post(
                        "/api/v1/runs/" + run_id + "/resume",
                        json={"decision": "approve", "interrupt_id": "approval:stale"},
                    ).status_code,
                    409,
                )
                self.assertEqual(
                    client.post(
                        "/api/v1/runs/" + run_id + "/resume",
                        json={"decision": "approve", "interrupt_id": paused["interrupt_id"]},
                    ).status_code,
                    202,
                )
                waiting = wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
                pending = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"]
                self.assertEqual(len(pending), 1, waiting)
                write = pending[0]
                self.assertEqual(write["operation"], "click")
                self.assertEqual(write["expected_snapshot_id"], "fixture-snapshot")
                self.assertTrue(write["approved_action_id"])
                result = {
                    "command_id": write["command_id"],
                    "browser_session_id": "fixture-desktop",
                    "ok": True,
                    "outcome": "executed",
                    "url": obs["url"],
                    "tab_id": "fixture-tab",
                }
                self.assertEqual(
                    client.post(
                        "/api/v1/browser/commands/" + write["command_id"] + "/result", json=result
                    ).status_code,
                    200,
                )
                verification = wait_for(
                    client,
                    run_id,
                    lambda v: (
                        bool(v["state"].get("browser_wait"))
                        and v["state"]["browser_wait"]["command_id"] != write["command_id"]
                    ),
                )
                verify_command = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                self.assertEqual(verify_command["operation"], "snapshot", verification)
                client.post(
                    "/api/v1/browser/commands/" + verify_command["command_id"] + "/result",
                    json={
                        **fixture(verify_command),
                        "snapshot_id": "fixture-after",
                        "text": "结果仍在处理中",
                    },
                )
                done = wait_for(
                    client, run_id, lambda v: v["phase"] in {"PARTIAL_FAILED", "FAILED"}
                )
                self.assertEqual(done["state"]["action_results"][0]["status"], "UNKNOWN", done)
                self.assertEqual(
                    client.get(
                        "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                    ).json()["commands"],
                    [],
                )
                self.assertTrue(
                    client.post(
                        "/api/v1/browser/commands/" + write["command_id"] + "/result", json=result
                    ).json()["replayed"]
                )

                action_id = done["state"]["action_results"][0]["action_id"]
                resolve_url = f"/api/v1/runs/{run_id}/actions/{action_id}/resolve"
                confirmation = {
                    "status": "SUCCEEDED",
                    "note": "我已在网站核对商家、人数和预约日期，预约已完成",
                    "reference": "USER-CHECKED-42",
                }
                self.assertEqual(
                    client.post(
                        f"/api/v1/runs/{run_id}/actions/unknown-action/resolve", json=confirmation
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    client.post(resolve_url, json={**confirmation, "note": "   "}).status_code, 422
                )
                self.assertEqual(
                    client.post(
                        resolve_url, json={**confirmation, "source": "browser"}
                    ).status_code,
                    422,
                )
                original_save = app.state.runtime.runs.save_state_and_events

                async def interrupted_projection(state, **kwargs):
                    if any(
                        e.get("event_type") == "ACTION_RESOLVED" for e in kwargs.get("events", [])
                    ):
                        raise RuntimeError("offline fixture: crash after ledger commit")
                    return await original_save(state, **kwargs)

                app.state.runtime.runs.save_state_and_events = interrupted_projection
                with self.assertRaisesRegex(RuntimeError, "crash after ledger commit"):
                    client.post(resolve_url, json=confirmation)
                app.state.runtime.runs.save_state_and_events = original_save
                resolved = client.post(resolve_url, json=confirmation)
                self.assertEqual(resolved.status_code, 200, resolved.text)
                snapshot = resolved.json()
                self.assertEqual(snapshot["phase"], "SUCCEEDED")
                user_result = snapshot["state"]["action_results"][0]
                self.assertEqual(user_result["result"]["source"], "user")
                self.assertEqual(user_result["result"]["resolution_source"], "user_confirmation")
                self.assertFalse(user_result["result"]["automatically_verified"])
                self.assertFalse(user_result["resolution_required"])
                self.assertEqual(
                    user_result["result"]["observation"],
                    done["state"]["action_results"][0]["result"]["observation"],
                )
                self.assertEqual(user_result["result"]["browser_verification"]["source"], "browser")
                repeated = client.post(resolve_url, json=confirmation)
                self.assertEqual(repeated.status_code, 200, repeated.text)
                self.assertTrue(repeated.json()["replayed"])
                self.assertEqual(repeated.json()["event_seq"], snapshot["event_seq"])
                self.assertEqual(
                    client.post(resolve_url, json={**confirmation, "status": "FAILED"}).status_code,
                    409,
                )
                event = client.get(f"/api/v1/runs/{run_id}/events").json()["events"][-1]
                self.assertEqual(event["event_type"], "ACTION_RESOLVED")
                self.assertEqual(event["payload"]["resolution_source"], "user_confirmation")
                self.assertFalse(event["payload"]["automatically_verified"])
                client.post(f"/api/v1/runs/{run_id}/messages", json={"text": "重新检查菜单"})
                wait_for(
                    client,
                    run_id,
                    lambda v: (
                        v["state"].get("turn_id", 0) > 1 and bool(v["state"].get("browser_wait"))
                    ),
                )
                self.assertEqual(client.post(resolve_url, json=confirmation).status_code, 409)

    def test_real_provider_contract_compiles_and_exact_selection_preserves_spec(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(
                create_app(settings(directory), token=TOKEN),
                headers={"Authorization": "Bearer " + TOKEN},
            ) as client:
                run_id = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "我们2人，在望京先看展再吃饭，总预算400元，下午2点开始，行程4小时",
                        "browser_session_id": "fixture-desktop",
                    },
                ).json()["run_id"]
                wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
                cmd = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]

                def place(pid, name, category, price):
                    return {
                        "place_id": pid,
                        "name": name,
                        "category": category,
                        "latitude": 39.997,
                        "longitude": 116.482,
                        "average_price": price,
                        "open_minute": 0,
                        "close_minute": 1440,
                        "tags": ["室内"],
                        "supply": {
                            "open_now": True,
                            "reservable": True,
                            "seats_left": 10,
                            "estimated_wait_min": 0,
                        },
                    }

                ids = ["browser:fixture-art", "browser:fixture-food"]
                fields = {
                    "location": {"name": "望京", "latitude": 39.997, "longitude": 116.482},
                    "places": [
                        place(ids[0], "Offline art", "展览", 30),
                        place(ids[1], "Offline food", "餐厅", 50),
                    ],
                    "routes": {
                        pid: {
                            "driving_min": 5,
                            "walking_min": 5,
                            "transit_min": 5,
                            "distance_km": 0,
                        }
                        for pid in ids
                    },
                }
                response = client.post(
                    "/api/v1/browser/commands/" + cmd["command_id"] + "/result",
                    json=fixture(cmd, fields=fields),
                )
                self.assertEqual(response.status_code, 200, response.text)
                paused = wait_for(
                    client,
                    run_id,
                    lambda v: v["phase"] in {"WAITING_APPROVAL", "FAILED", "INFEASIBLE"},
                )
                self.assertEqual(paused["phase"], "WAITING_APPROVAL", paused)
                state = paused["state"]
                self.assertTrue(state["verifier"]["executable"])
                self.assertEqual(state["selected_plan"]["total_cost"], 160)
                self.assertTrue(
                    all(s["supply_source"] == "browser" for s in state["selected_plan"]["stops"])
                )
                self.assertFalse(any(e["source"] == "simulated" for e in state["evidence"]))
                self.assertIsNone(state["selected_plan"]["robustness"])
                candidate = state["candidate_plans"][0]
                stale = client.post(
                    "/api/v1/runs/" + run_id + "/plans/select",
                    json={"plan_id": candidate["plan_id"], "plan_version": 999},
                )
                self.assertEqual(stale.status_code, 409)
                chosen = client.post(
                    "/api/v1/runs/" + run_id + "/plans/select",
                    json={"plan_id": candidate["plan_id"], "plan_version": candidate["version"]},
                )
                self.assertEqual(chosen.status_code, 202, chosen.text)
                refreshed = wait_for(
                    client,
                    run_id,
                    lambda v: (
                        v["state"].get("plan_version", 0) > 1
                        and bool(v["state"].get("browser_wait"))
                    ),
                )
                fresh_cmd = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                self.assertNotEqual(fresh_cmd["command_id"], cmd["command_id"], refreshed)
                client.post(
                    "/api/v1/browser/commands/" + fresh_cmd["command_id"] + "/result",
                    json=fixture(fresh_cmd, fields=fields),
                )
                updated = wait_for(
                    client,
                    run_id,
                    lambda v: (
                        v["state"].get("plan_version", 0) > 1
                        and v["phase"] in {"WAITING_APPROVAL", "FAILED", "INFEASIBLE"}
                    ),
                )
                self.assertEqual(updated["state"]["trip_spec"]["budget"], 400)
                self.assertEqual(updated["state"]["trip_spec"]["party_size"], 2)
                self.assertEqual(updated["state"]["selected_plan"]["version"], 2)
                self.assertNotEqual(updated["interrupt_id"], paused["interrupt_id"])
                self.assertEqual(
                    client.post(
                        "/api/v1/runs/" + run_id + "/resume",
                        json={"decision": "approve", "interrupt_id": paused["interrupt_id"]},
                    ).status_code,
                    409,
                )


                accepted = client.post(
                    "/api/v1/runs/" + run_id + "/resume",
                    json={"decision": "approve", "interrupt_id": updated["interrupt_id"]},
                )
                self.assertEqual(accepted.status_code, 202, accepted.text)
                preparing = wait_for(client, run_id, lambda v: bool(v["state"].get("execution_goal")) and bool(v["state"].get("browser_wait")))
                execution_goal = preparing["state"]["execution_goal"]
                self.assertEqual(execution_goal["plan_id"], updated["state"]["selected_plan"]["plan_id"])
                self.assertEqual(execution_goal["plan_version"], 2)
                self.assertEqual(execution_goal["requirements"]["party_size"], 2)
                self.assertEqual({s["place_id"] for s in execution_goal["stops"]}, set(ids))
                self.assertEqual(preparing["state"]["browser_task_context"]["kind"], "prepare")
                handoff_command = client.get("/api/v1/browser/commands?browser_session_id=fixture-desktop").json()["commands"][0]
                client.post(
                    "/api/v1/browser/commands/" + handoff_command["command_id"] + "/result",
                    json={**fixture(handoff_command), "text": "帮助中心：账号设置", "tables": []},
                )
                not_finished = wait_for(client, run_id, lambda v: v["phase"] in {"PARTIAL_FAILED", "FAILED", "SUCCEEDED"})
                self.assertEqual(not_finished["phase"], "PARTIAL_FAILED", not_finished)
                self.assertEqual(not_finished["state"]["execution_goal"], execution_goal)
                self.assertEqual(not_finished["state"].get("action_results", []), [])


class BoundaryCheck(unittest.TestCase):
    def test_settings_ignore_foreign_environment_and_image_uses_budgeted_adapter(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch

        from plango.graph import ImageReading
        from plango.settings import settings_from_env

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {
                    "PLANORA_RUNTIME_PROFILE": "sandbox",
                    "DATABASE_URL": "sqlite+aiosqlite:////tmp/unrelated.sqlite",
                    "PLANORA_CHECKPOINT_PATH": "/tmp/unrelated-cp",
                    "PLANORA_WORLD_PROVIDER": "sandbox",
                    "PLANGO_DATA_DIR": directory,
                },
            ):
                config = settings_from_env()
                self.assertEqual(config.runtime_profile, "desktop")
                self.assertEqual(config.world_provider, "browser")
                self.assertEqual(config.checkpoint_path, Path(directory) / "checkpoints.sqlite")
                self.assertIn(directory, config.database_url)
            config = settings(directory).model_copy(
                update={"openai_api_key": "test-provider-is-replaced"}
            )
            app = create_app(config, token=TOKEN)
            calls = []

            class FixtureImageModel:
                def with_structured_output(self, schema, include_raw=False):
                    self.schema = schema
                    return self

                def bind(self, **kwargs):
                    return self

                async def ainvoke(self, messages):
                    calls.append(messages)
                    assert self.schema is ImageReading
                    return {
                        "parsed": ImageReading(text="用户截图：餐厅套餐价格128元"),
                        "raw": SimpleNamespace(
                            usage_metadata={
                                "input_tokens": 30,
                                "output_tokens": 10,
                                "total_tokens": 40,
                            }
                        ),
                    }

            app.state.runtime.model._model = FixtureImageModel()
            # A tiny actual PNG, not an external image URL or credential.
            image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j/a0AAAAASUVORK5CYII="
            with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
                response = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "提取截图菜单",
                        "image": image,
                        "browser_session_id": "fixture-desktop",
                        "enabled_skills": [],
                    },
                )
                self.assertEqual(response.status_code, 202, response.text)
                run_id = response.json()["run_id"]
                done = wait_for(client, run_id, lambda v: v["phase"] in {"SUCCEEDED", "FAILED"})
                self.assertEqual(done["phase"], "SUCCEEDED", done)
                self.assertEqual(done["state"]["model_token_count"], 40)
                self.assertEqual(calls[0][1]["content"][1]["image_url"]["url"], image)
                self.assertEqual(done["state"]["browser_artifacts"][0]["source"], "user")
                followup = client.post(
                    "/api/v1/runs/" + run_id + "/messages", json={"text": "帮我预约餐厅"}
                )
                self.assertEqual(followup.status_code, 202, followup.text)
                booking = wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
                self.assertEqual(booking["phase"], "REQUIREMENTS_READY")
                self.assertEqual(booking["state"].get("browser_image_context"), "")
                self.assertIsNone(
                    client.portal.call(app.state.runtime.bridge.binding, run_id)["input_image"]
                )
                self.assertEqual(
                    len(
                        client.get(
                            "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                        ).json()["commands"]
                    ),
                    1,
                )
                client.post("/api/v1/runs/" + run_id + "/cancel")
                self.assertEqual(
                    client.get(
                        "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                    ).json()["commands"],
                    [],
                )

    def test_repeated_identical_read_gets_new_command_and_cancelled_rows_do_not_starve(self):
        from plango.browser import commands
        from sqlalchemy import insert

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(settings(directory), token=TOKEN)
            with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
                rid = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "读取当前网页菜单",
                        "browser_session_id": "fixture-desktop",
                    },
                ).json()["run_id"]
                seen = []
                for turn in range(3):
                    if turn:
                        client.post(
                            "/api/v1/runs/" + rid + "/messages", json={"text": "再检查菜单"}
                        )
                    paused = wait_for(
                        client,
                        rid,
                        lambda v: (
                            bool(v["state"].get("browser_wait"))
                            and v["state"]["browser_wait"]["command_id"] not in seen
                        ),
                    )
                    command = client.get(
                        "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                    ).json()["commands"][0]
                    self.assertNotIn(command["command_id"], seen, paused)
                    seen.append(command["command_id"])
                    self.assertEqual(
                        client.post(
                            "/api/v1/browser/commands/" + command["command_id"] + "/result",
                            json={
                                **fixture(command),
                                "tables": [
                                    {
                                        "headers": ["菜品", "价格"],
                                        "rows": [["清炒时蔬", str(28 + turn * 100) + "元"]],
                                    }
                                ],
                            },
                        ).status_code,
                        200,
                    )
                    self.assertEqual(
                        wait_for(client, rid, lambda v: v["phase"] == "SUCCEEDED")["phase"],
                        "SUCCEEDED",
                    )

                current = client.get("/api/v1/runs/" + rid).json()
                menus = [
                    v["price"]
                    for a in current["state"]["browser_artifacts"]
                    for v in a.get("data", {}).get("menu", [])
                ]
                self.assertEqual(menus, [228.0])

                async def stale_rows():
                    async with app.state.runtime.database.session() as session:
                        async with session.begin():
                            for index in range(35):
                                await session.execute(
                                    insert(commands).values(
                                        command_id="stale-" + str(index),
                                        run_id=rid,
                                        browser_session_id="fixture-desktop",
                                        payload={"command_id": "stale-" + str(index)},
                                        created_at=0,
                                    )
                                )

                client.portal.call(stale_rows)
                new = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "读取当前网页菜单",
                        "browser_session_id": "fixture-desktop",
                    },
                ).json()["run_id"]
                wait_for(client, new, lambda v: bool(v["state"].get("browser_wait")))
                available = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"]
                self.assertEqual(len(available), 1)
                self.assertEqual(available[0]["run_id"], new)


class ReceiptIntegrationCheck(unittest.TestCase):
    def test_page_confirmation_is_observed_but_business_identity_remains_unknown(self):
        from plango.graph import BrowserDecision

        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory)
            app = create_app(config, token=TOKEN)

            async def fixture_model(schema, *, fallback, **kwargs):
                return (
                    BrowserDecision(operation="click", idx=3, rationale="Test only")
                    if schema is BrowserDecision
                    else fallback
                )

            app.state.runtime.model.structured = fixture_model
            with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
                run_id = client.post(
                    "/api/v1/runs",
                    json={"input_text": "在网页预约餐厅", "browser_session_id": "fixture-desktop"},
                ).json()["run_id"]
                wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
                initial = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                before = {
                    **fixture(initial, elements=[{"idx": 3, "tag": "button", "text": "确认预约"}]),
                    "url": "https://www.meituan.com/reservation/new",
                    "text": "星河餐厅 预约确认",
                }
                client.post(
                    "/api/v1/browser/commands/" + initial["command_id"] + "/result", json=before
                )
                approval = wait_for(client, run_id, lambda v: v["phase"] == "WAITING_APPROVAL")
                client.post(
                    "/api/v1/runs/" + run_id + "/resume",
                    json={"decision": "approve", "interrupt_id": approval["interrupt_id"]},
                )
                wait_for(client, run_id, lambda v: bool(v["state"].get("browser_wait")))
                click = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                client.post(
                    "/api/v1/browser/commands/" + click["command_id"] + "/result",
                    json={
                        "command_id": click["command_id"],
                        "browser_session_id": "fixture-desktop",
                        "ok": True,
                        "outcome": "executed",
                        "tab_id": "fixture-tab",
                        "url": before["url"],
                    },
                )
                verifying = wait_for(
                    client,
                    run_id,
                    lambda v: (
                        bool(v["state"].get("browser_wait"))
                        and v["state"]["browser_wait"]["command_id"] != click["command_id"]
                    ),
                )
                self.assertEqual(verifying["state"]["action_results"][0]["status"], "UNKNOWN")
                snapshot = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                self.assertEqual(snapshot["operation"], "snapshot")
                after = {
                    **fixture(snapshot),
                    "snapshot_id": "fresh-receipt-snapshot",
                    "url": "https://www.meituan.com/reservation/result",
                    "text": "星河餐厅 预约成功\n预约编号：R2026090888",
                }
                client.post(
                    "/api/v1/browser/commands/" + snapshot["command_id"] + "/result", json=after
                )
                done = wait_for(
                    client,
                    run_id,
                    lambda v: v["phase"] in {"SUCCEEDED", "PARTIAL_FAILED", "FAILED"},
                )
                self.assertEqual(done["phase"], "PARTIAL_FAILED", done)
                self.assertEqual(done["state"]["action_results"][0]["status"], "UNKNOWN")
                self.assertEqual(
                    done["state"]["action_results"][0]["result"]["scope"], "page_confirmation"
                )
                receipt = done["state"]["action_results"][0]["result"]["receipt"]
                self.assertEqual(receipt["reference"], "R2026090888")
                self.assertEqual(receipt["source_url"], after["url"])
                self.assertEqual(
                    client.get(
                        "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                    ).json()["commands"],
                    [],
                )


class BrowserTurnRegressionCheck(unittest.TestCase):
    def test_approval_edit_replans_and_cancelled_run_accepts_followup(self):
        from plango.graph import BrowserDecision

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(settings(directory), token=TOKEN)

            async def fixture_model(schema, *, fallback, **kwargs):
                return (
                    BrowserDecision(operation="click", idx=3)
                    if schema is BrowserDecision
                    else fallback
                )

            app.state.runtime.model.structured = fixture_model
            with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
                rid = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "帮我在网页预约餐厅",
                        "browser_session_id": "fixture-desktop",
                    },
                ).json()["run_id"]
                wait_for(client, rid, lambda v: bool(v["state"].get("browser_wait")))
                first = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                client.post(
                    "/api/v1/browser/commands/" + first["command_id"] + "/result",
                    json=fixture(first, elements=[{"idx": 3, "text": "预约", "tag": "button"}]),
                )
                approval = wait_for(client, rid, lambda v: v["phase"] == "WAITING_APPROVAL")
                changed = client.post(
                    "/api/v1/runs/" + rid + "/messages", json={"text": "改成明天预约"}
                )
                self.assertEqual(changed.status_code, 202, changed.text)
                replanning = wait_for(
                    client,
                    rid,
                    lambda v: (
                        v["state"].get("turn_id", 0) > 1 and bool(v["state"].get("browser_wait"))
                    ),
                )
                self.assertEqual(replanning["phase"], "REQUIREMENTS_READY")
                self.assertIn("明天", replanning["state"]["input_text"])
                self.assertEqual(
                    client.post(
                        "/api/v1/runs/" + rid + "/resume",
                        json={"decision": "approve", "interrupt_id": approval["interrupt_id"]},
                    ).status_code,
                    409,
                )
                second = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                self.assertNotEqual(second["command_id"], first["command_id"])
                # Even the same DOM snapshot cannot reuse the previous turn's proposal ID.
                client.post(
                    "/api/v1/browser/commands/" + second["command_id"] + "/result",
                    json=fixture(second, elements=[{"idx": 3, "text": "预约", "tag": "button"}]),
                )
                updated = wait_for(client, rid, lambda v: v["phase"] == "WAITING_APPROVAL")
                self.assertNotEqual(updated["interrupt_id"], approval["interrupt_id"])
                client.post(
                    "/api/v1/runs/" + rid + "/resume",
                    json={"decision": "reject", "interrupt_id": updated["interrupt_id"]},
                )
                wait_for(client, rid, lambda v: v["phase"] == "CANCELLED")
                resumed = client.post(
                    "/api/v1/runs/" + rid + "/messages", json={"text": "再检查菜单"}
                )
                self.assertEqual(resumed.status_code, 202, resumed.text)
                wait_for(
                    client,
                    rid,
                    lambda v: (
                        v["state"].get("turn_id", 0) > 2 and bool(v["state"].get("browser_wait"))
                    ),
                )

    def test_unknown_type_ack_cannot_become_success_from_unchanged_page(self):
        from plango.graph import BrowserDecision

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(settings(directory), token=TOKEN)

            async def fixture_model(schema, *, fallback, **kwargs):
                return (
                    BrowserDecision(operation="type", idx=0, text="明天")
                    if schema is BrowserDecision
                    else fallback
                )

            app.state.runtime.model.structured = fixture_model
            with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
                rid = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "在网页填写预约日期",
                        "browser_session_id": "fixture-desktop",
                    },
                ).json()["run_id"]
                wait_for(client, rid, lambda v: bool(v["state"].get("browser_wait")))
                first = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                client.post(
                    "/api/v1/browser/commands/" + first["command_id"] + "/result",
                    json=fixture(
                        first,
                        elements=[{"idx": 0, "text": "预约日期", "tag": "input", "value": ""}],
                    ),
                )
                approval = wait_for(client, rid, lambda v: v["phase"] == "WAITING_APPROVAL")
                client.post(
                    "/api/v1/runs/" + rid + "/resume",
                    json={"decision": "approve", "interrupt_id": approval["interrupt_id"]},
                )
                wait_for(client, rid, lambda v: bool(v["state"].get("browser_wait")))
                typing = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                self.assertEqual(typing["operation"], "type")
                client.post(
                    "/api/v1/browser/commands/" + typing["command_id"] + "/result",
                    json={
                        "command_id": typing["command_id"],
                        "browser_session_id": "fixture-desktop",
                        "ok": False,
                        "outcome": "unknown",
                        "error_kind": "desktop_restarted_during_command",
                    },
                )
                wait_for(
                    client,
                    rid,
                    lambda v: (
                        bool(v["state"].get("browser_wait"))
                        and v["state"]["browser_wait"]["command_id"] != typing["command_id"]
                    ),
                )
                check = client.get(
                    "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                ).json()["commands"][0]
                client.post(
                    "/api/v1/browser/commands/" + check["command_id"] + "/result",
                    json={
                        **fixture(
                            check,
                            elements=[{"idx": 0, "text": "预约日期", "tag": "input", "value": ""}],
                        ),
                        "snapshot_id": "fresh-unchanged-input",
                    },
                )
                final = wait_for(
                    client, rid, lambda v: v["phase"] in {"PARTIAL_FAILED", "FAILED", "SUCCEEDED"}
                )
                self.assertEqual(final["phase"], "PARTIAL_FAILED", final)
                self.assertEqual(final["state"]["action_results"][0]["status"], "UNKNOWN")
                self.assertTrue(
                    final["state"]["action_results"][0]["result"]["resolution_required"]
                )
                action_id = final["state"]["action_results"][0]["action_id"]
                resolved = client.post(
                    f"/api/v1/runs/{rid}/actions/{action_id}/resolve",
                    json={"status": "FAILED", "note": "我已核对网站，输入没有完成"},
                )
                self.assertEqual(resolved.status_code, 200, resolved.text)
                self.assertEqual(resolved.json()["state"]["action_results"][0]["status"], "FAILED")
                self.assertEqual(
                    resolved.json()["state"]["action_results"][0]["result"]["source"], "user"
                )
                self.assertFalse(
                    resolved.json()["state"]["action_results"][0]["resolution_required"]
                )


if __name__ == "__main__":
    unittest.main()
