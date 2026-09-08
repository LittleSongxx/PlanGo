"""Offline context isolation tests; only a replaced geocoder supplies fixture coordinates."""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from planora.agent.contracts import Location
from yoyu.app import create_app
from yoyu.settings import DesktopSettings


def settings(directory):
    return DesktopSettings(
        database_url=f"sqlite+aiosqlite:///{directory}/runs.sqlite",
        data_dir=Path(directory),
        checkpoint_path=Path(directory) / "checkpoints.sqlite",
    )


def paused(client, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = client.get("/api/v1/runs/" + run_id).json()
        if value["phase"] == "REQUIREMENTS_READY" and value.get("interrupt_id"):
            return value
        time.sleep(0.02)
    raise AssertionError(value)


class LocationContextCheck(unittest.TestCase):
    def test_scoped_cities_no_coordinate_fabrication_and_browser_read_independence(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(
                create_app(settings(directory), token="location-test"),
                headers={"Authorization": "Bearer location-test"},
            ) as client:
                cases = [("上海", 121.4737, 31.2304), ("深圳", 114.0579, 22.5431)]
                for city, lon, lat in cases:
                    response = client.post(
                        "/api/v1/runs",
                        json={
                            "input_text": "我们2人，预算400元，安排半天看展再吃饭",
                            "browser_session_id": "fixture",
                            "location_context": {
                                "city": city,
                                "longitude": lon,
                                "latitude": lat,
                                "source": "device",
                            },
                        },
                    )
                    self.assertEqual(response.status_code, 202, response.text)
                    state = paused(client, response.json()["run_id"])["state"]
                    self.assertEqual(state["trip_spec"]["location"]["name"], city)
                    self.assertEqual(state["trip_spec"]["location"]["longitude"], lon)
                    self.assertEqual(state["trip_spec"]["location"]["latitude"], lat)
                    self.assertEqual(state["location_origin"]["source"], "device")
                response = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "我们2人，安排半天看展再吃饭",
                        "browser_session_id": "fixture",
                        "location_context": {"city": "杭州", "source": "config"},
                    },
                )
                missing = paused(client, response.json()["run_id"])
                self.assertIn("杭州", missing["state"]["clarification"]["question"])
                self.assertIsNone(missing["state"].get("trip_spec"))
                self.assertNotIn("39.997", str(missing["state"]))
                invalid = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "规划行程",
                        "browser_session_id": "fixture",
                        "location_context": {"city": "上海", "latitude": 31.2, "source": "manual"},
                    },
                )
                self.assertEqual(invalid.status_code, 422)
                read = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "读取菜单",
                        "browser_session_id": "fixture",
                        "location_context": {"city": "上海", "source": "config"},
                    },
                )
                read_state = paused(client, read.json()["run_id"])
                self.assertTrue(read_state["state"].get("browser_wait"))
                self.assertIsNone(read_state["state"].get("trip_spec"))

    def test_explicit_city_overrides_desktop_default_and_persists_after_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory).model_copy(
                update={"amap_webservice_key": "replaced-offline-geocoder"}
            )
            app = create_app(config, token="location-test")
            calls = []

            async def geocode(address):
                calls.append(address)
                return (
                    Location(name=address, latitude=39.9042, longitude=116.4074)
                    if address == "北京"
                    else None
                )

            app.state.runtime.world_service.provider.amap.geocode = geocode
            with TestClient(app, headers={"Authorization": "Bearer location-test"}) as client:
                response = client.post(
                    "/api/v1/runs",
                    json={
                        "input_text": "我们2人，在北京先看展再吃饭，预算400元，行程4小时",
                        "browser_session_id": "fixture",
                        "location_context": {
                            "city": "上海",
                            "longitude": 121.4737,
                            "latitude": 31.2304,
                            "source": "config",
                        },
                    },
                )
                rid = response.json()["run_id"]
                first = paused(client, rid)
                self.assertEqual(calls, ["北京"])
                self.assertEqual(first["state"]["trip_spec"]["location"]["name"], "北京")
                self.assertEqual(first["state"]["trip_spec"]["location"]["longitude"], 116.4074)
                self.assertEqual(first["state"]["location_origin"]["source"], "user")
                update = client.post(
                    "/api/v1/runs/" + rid + "/messages",
                    json={
                        "text": "预算改为500元",
                        "location_context": {"city": "广州", "source": "manual"},
                    },
                )
                self.assertEqual(update.status_code, 202, update.text)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    second = paused(client, rid)
                    if second["state"].get("turn_id", 0) > 1:
                        break
                    time.sleep(0.02)
                self.assertEqual(second["state"]["trip_spec"]["location"]["name"], "北京")
                self.assertEqual(second["location_context"]["city"], "广州")
                self.assertEqual(second["state"]["trip_spec"]["budget"], 500)
                self.assertEqual(calls, ["北京"])


if __name__ == "__main__":
    unittest.main()
