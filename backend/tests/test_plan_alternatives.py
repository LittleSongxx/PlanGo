"""Adversarial alternatives against the real compiler/verifier, with an explicit offline world."""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from planora.agent.contracts import (
    Evidence,
    Location,
    PlaceCandidate,
    PlanCandidate,
    PlanDraft,
    PlanDraftStop,
    PlanStop,
    TripSpec,
)
from planora.domain.planning import compile_plan_draft
from planora.providers.world import Supply
from planora.tools.registry import ToolContext
from yoyu.planning import BrowserPlanEngine, preserve_locks, variants


class AlternativesCheck(unittest.IsolatedAsyncioTestCase):
    async def test_skip_invalid_cheapest_keep_goal_order_locks_and_evidence_quality(self):
        now = datetime.now(timezone.utc)

        def place(pid, category, price, distance, tags):
            return PlaceCandidate(
                place_id=pid,
                name=pid,
                category=category,
                latitude=31.2,
                longitude=121.4,
                average_price=price,
                price_known=True,
                distance_km=distance,
                tags=tags,
                open_minute=480,
                close_minute=1320,
                source="browser",
                evidence_ids=[pid + ":proof"],
            )

        places = [
            place("locked-art", "展览", 30, 1, ["室内"]),
            place("original-food", "餐厅", 60, 4, ["室内"]),
            place("cheapest-outdoor", "餐厅", 5, 0.1, ["户外"]),
            place("cheap-unknown-queue", "餐厅", 10, 0.3, ["室内"]),
            place("next-feasible-food", "餐厅", 20, 1, ["室内"]),
        ]
        catalog = {p.place_id: p for p in places}
        evidence = [
            Evidence(
                evidence_id=p.place_id + ":proof",
                source="browser",
                source_ref="https://fixture.invalid/" + p.place_id,
                payload={"place_id": p.place_id, "open_minute": 480, "close_minute": 1320},
                observed_at=now,
                expires_at=now + timedelta(minutes=10),
            )
            for p in places
        ]

        class ObservedWorld:
            async def get_place(self, pid):
                return catalog[pid]

            async def get_supply(self, pid, at_minute):
                return Supply(
                    place_id=pid,
                    open_now=True,
                    reservable=True,
                    seats_left=None,
                    estimated_wait_min=None if pid == "cheap-unknown-queue" else 0,
                    source="browser",
                    observed_at=now,
                    expires_at=now + timedelta(minutes=10),
                )

            async def estimate_route(self, origin, destination):
                route = {
                    "driving_min": 5,
                    "distance_km": destination.distance_km,
                    "source": "browser",
                }
                return route, Evidence(
                    evidence_id="route:" + destination.place_id,
                    source="browser",
                    source_ref="https://fixture.invalid/route",
                    payload={"place_id": destination.place_id, **route},
                    observed_at=now,
                    expires_at=now + timedelta(minutes=10),
                )

        world = ObservedWorld()
        planner = BrowserPlanEngine(world)
        spec = TripSpec(
            goal="两人，先看展再吃饭，保留14:30展览",
            party_size=2,
            budget=300,
            indoor_required=True,
            required_activities=["展览", "餐厅"],
            activity_order=["展览", "餐厅"],
            location=Location(name="上海", latitude=31.2, longitude=121.4),
        )
        selected = PlanCandidate(
            plan_id="selected",
            label="当前方案",
            version=3,
            party_size=2,
            total_cost=180,
            stops=[
                PlanStop(
                    place_id="locked-art",
                    name="locked-art",
                    category="展览",
                    start_minute=870,
                    end_minute=910,
                    locked=True,
                    estimated_cost=60,
                    evidence_ids=["locked-art:proof"],
                ),
                PlanStop(
                    place_id="original-food",
                    name="original-food",
                    category="餐厅",
                    start_minute=915,
                    end_minute=975,
                    estimated_cost=120,
                    evidence_ids=["original-food:proof"],
                ),
            ],
        )
        checked = await planner.evaluate(spec, selected, evidence=evidence)
        self.assertTrue(checked.verifier.executable, checked.verifier)
        evidence += planner.last_evidence
        context = ToolContext(
            world=world,
            planner=planner,
            memory=None,
            runs=None,
            action_provider=None,
            run_id="fixture",
            user_id="fixture",
            tool_call_count=6,
            max_tool_calls=48,
        )
        state = {
            "selected_plan": checked.plan,
            "trip_spec": spec,
            "verifier": checked.verifier,
            "evidence": evidence,
            "place_candidates": places,
        }
        result = await variants(
            state,
            SimpleNamespace(planner=planner, max_tool_calls=48, tool_context=lambda _: context),
        )
        self.assertEqual(len(result["candidate_plans"]), 2)
        alternative = result["candidate_plans"][1]
        self.assertEqual(
            [stop.place_id for stop in alternative.stops], ["locked-art", "next-feasible-food"]
        )
        self.assertEqual([stop.category for stop in alternative.stops], ["展览", "餐厅"])
        self.assertEqual(alternative.total_cost, 100)
        self.assertEqual(alternative.version, 3)
        lock = alternative.stops[0]
        self.assertTrue(lock.locked)
        self.assertEqual((lock.start_minute, lock.end_minute), (870, 910))
        self.assertFalse(any(c.kind == "unknown" for c in alternative.checks))
        self.assertLessEqual(result["tool_call_count"], 42)
        # Recompiling an exact user choice must restore its original lock rather than silently turning it flexible.
        draft = PlanDraft(
            stops=[
                PlanDraftStop(place_id=s.place_id, duration_minutes=s.end_minute - s.start_minute)
                for s in alternative.stops
            ]
        )
        compiled = compile_plan_draft(spec, draft, places, evidence=result["evidence"], version=4)
        restored = preserve_locks(compiled, alternative)
        self.assertTrue(restored.stops[0].locked)
        self.assertEqual((restored.stops[0].start_minute, restored.stops[0].end_minute), (870, 910))
        self.assertIsNone(
            preserve_locks(compiled.model_copy(update={"stops": compiled.stops[1:]}), alternative)
        )


if __name__ == "__main__":
    unittest.main()
