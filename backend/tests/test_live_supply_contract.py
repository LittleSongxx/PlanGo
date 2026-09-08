"""DOM text fixtures exercise the production parser, without synthetic fields.places."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from planora.agent.contracts import (
    Evidence,
    Location,
    PlaceCandidate,
    PlanCandidate,
    PlanStop,
    TripSpec,
)
from yoyu.browser import run_context
from yoyu.planning import BrowserPlanEngine
from yoyu.settings import DesktopSettings
from yoyu.supply import literal_supply
from yoyu.world import BrowserWorld


class LiveSupplyContractCheck(unittest.IsolatedAsyncioTestCase):
    async def test_visible_hours_and_wait_without_seats_and_unknown_facts(self):
        now = datetime.now(timezone.utc)
        observation = {
            "command_id": "dom-only",
            "url": "https://fixture.invalid/shop",
            "snapshot_id": "dom-only-snapshot",
            "observed_at": now.isoformat(),
            "title": "星河餐厅 - 门店详情",
            "text": "星河餐厅\n营业时间：10:00-22:00\n营业中\n预计排队7分钟\n支持预约",
        }
        place = PlaceCandidate(
            place_id="amap:shop",
            name="星河餐厅",
            category="餐厅",
            latitude=31.2304,
            longitude=121.4737,
            average_price=50,
            price_known=True,
            source="amap",
            evidence_ids=["place-proof"],
        )

        async def no_cache(_):
            return None

        async def model(schema, *, fallback, **kwargs):
            return fallback

        world = BrowserWorld(
            DesktopSettings(amap_webservice_key="offline-amap-stub"),
            SimpleNamespace(get=no_cache),
            SimpleNamespace(structured=model),
        )

        async def page():
            return observation

        async def search(query, location, *, limit):
            return [place], [
                Evidence(
                    evidence_id="place-proof",
                    source="amap",
                    source_ref="offline-amap-fixture",
                    payload={"place_id": place.place_id},
                    observed_at=now,
                    expires_at=now + timedelta(minutes=10),
                )
            ]

        async def route(origin, destination):
            result = {"distance_km": 1, "driving_min": 5, "source": "browser"}
            return result, Evidence(
                evidence_id="route-proof",
                source="browser",
                source_ref="https://fixture.invalid/shop",
                payload={"place_id": place.place_id, **result},
                observed_at=now,
                expires_at=now + timedelta(minutes=10),
            )

        world.page = page
        world.amap.search_places = search
        world.amap.estimate_route = route
        token = run_context.set({"places": {}})
        try:
            found, source_evidence = await world.search_places(
                "餐厅", Location(name="上海", latitude=31.2304, longitude=121.4737)
            )
            place = found[0]
            self.assertEqual(place.open_minute, 600)
            self.assertEqual(place.close_minute, 1320)
            self.assertEqual(place.source, "browser")
            self.assertTrue(
                any(e.source == "browser" and "营业时间" in e.claim for e in source_evidence)
            )
            supply = await world.get_supply(place.place_id, 14 * 60)
            self.assertEqual(supply.source, "browser")
            self.assertTrue(supply.open_now)
            self.assertTrue(supply.reservable)
            self.assertEqual(supply.estimated_wait_min, 7)
            self.assertIsNone(supply.seats_left)
            plan = PlanCandidate(
                plan_id="readable-preview",
                stops=[
                    PlanStop(
                        place_id=place.place_id,
                        name=place.name,
                        category="餐厅",
                        start_minute=840,
                        end_minute=900,
                        estimated_cost=50,
                        evidence_ids=["place-proof"],
                    )
                ],
                total_cost=50,
                party_size=1,
            )
            spec = TripSpec(
                goal="吃饭",
                location=Location(name="上海", latitude=31.2304, longitude=121.4737),
                party_size=1,
                budget=200,
            )
            evidence = source_evidence
            evaluation = await BrowserPlanEngine(world).evaluate(spec, plan, evidence=evidence)
            self.assertTrue(evaluation.verifier.executable, evaluation.verifier)
            observation["text"] = "星河餐厅\n菜单介绍，暂未显示营业或排队信息"
            unknown = await world.get_supply(place.place_id, 14 * 60)
            self.assertIsNone(unknown.estimated_wait_min)
            self.assertIsNone(unknown.reservable)
            self.assertIsNone(unknown.seats_left)
            # Place hours remain observed; absence of queue data makes execution incomplete, not venue closed.
            evaluation = await BrowserPlanEngine(world).evaluate(spec, plan, evidence=evidence)
            self.assertNotIn("closed", evaluation.plan.stops[0].tags)
            self.assertIsNone(evaluation.plan.stops[0].estimated_wait_min)
            self.assertFalse(evaluation.verifier.executable)
            self.assertTrue(evaluation.verifier.hard_constraints_pass)
            self.assertTrue(
                any(c.name.startswith("supply:") for c in evaluation.verifier.unknown_evidence)
            )
            absent = literal_supply("暂无信息", "星河餐厅", "门店列表")
            self.assertTrue(
                all(
                    absent[k] is None
                    for k in ("open_now", "reservable", "seats_left", "estimated_wait_min")
                )
            )
            other = literal_supply("月亮餐厅 营业中 排队5分钟", "星河餐厅", "搜索结果")
            self.assertIsNone(other["open_now"])
        finally:
            run_context.reset(token)
            await world.close()


if __name__ == "__main__":
    unittest.main()
