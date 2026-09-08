"""The pilot evaluator must reject wrong totals and failed negative runs; no live calls."""

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from check_live_task_quality import FIXTURE, evaluate_turn, source_manifest  # noqa:E402


class PilotContractCheck(unittest.TestCase):
    def test_positive_arithmetic_checked_independently_and_negative_not_vacuous(self):
        cases = json.loads(FIXTURE.read_text())["cases"]
        turn = cases[0]["turns"][0]
        data = {
            "party_size": 3,
            "total_budget": 240,
            "recommendation": "雾岚餐厅",
            "savings": 42,
            "entries": [
                {
                    "name": "雾岚餐厅",
                    "unit_price": 68,
                    "total": 204,
                    "within_budget": True,
                    "source_url": turn["dom"]["url"],
                    "evidence_id": "a",
                    "quote": "雾岚餐厅 人均68元",
                },
                {
                    "name": "杉木餐厅",
                    "unit_price": 82,
                    "total": 246,
                    "within_budget": False,
                    "source_url": turn["dom"]["url"],
                    "evidence_id": "b",
                    "quote": "杉木餐厅 人均82元",
                },
            ],
        }
        snapshot = {
            "phase": "SUCCEEDED",
            "state": {
                "browser_artifacts": [
                    {
                        "type": "browser_page",
                        "url": turn["dom"]["url"],
                        "data": {"text": turn["dom"]["text"]},
                    },
                    {"type": "price_comparison", "data": data},
                ]
            },
        }
        self.assertTrue(all(evaluate_turn(snapshot, turn, ["command1"], [], "terminal").values()))
        broken = copy.deepcopy(snapshot)
        broken["state"]["browser_artifacts"][1]["data"]["entries"][0]["total"] = 68
        self.assertFalse(
            evaluate_turn(broken, turn, ["command1"], [], "terminal")[
                "prices_totals_and_budget_flags_correct"
            ]
        )
        negative = cases[1]["turns"][0]
        failed = {"phase": "FAILED", "state": {"browser_artifacts": []}}
        self.assertFalse(all(evaluate_turn(failed, negative, [], [], "terminal").values()))
        self.assertNotIn(".env", source_manifest())


if __name__ == "__main__":
    unittest.main()
