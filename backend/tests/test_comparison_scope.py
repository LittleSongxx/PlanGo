"""Additional independent comparison probes, separate from the fixed 12-case baseline."""

import unittest

from plango.outcomes import TaskIntent, price_comparison, update_task_context
from plango_harness.agent.decisions import RequirementOutput


def offline_state(text, prices, **fields):
    """Controlled structured model proposal and observed prices; no language-quality claim."""
    state = {"run_id": "quality-comparison-scope", "turn_id": 1, "input_text": text}
    state["browser_task_context"] = update_task_context(state, TaskIntent(kind="reasoning", requirements=RequirementOutput(
        **fields, field_evidence={field: text for field in fields})))
    state["browser_artifacts"] = [
        {
            "artifact_id": "offline-price-list",
            "type": "browser_page",
            "source": "browser",
            "url": "https://fixture.invalid/quality/scope",
            "data": {
                "places": [
                    {
                        "name": name,
                        "average_price": price,
                        "price_unit": "人均",
                        "quote": f"{name} 人均{price}元",
                    }
                    for name, price in prices
                ]
            },
        }
    ]
    return state


class ComparisonScopeQuality(unittest.TestCase):
    def test_other_merchants_cannot_complete_named_comparison(self):
        state = offline_state(
            "比较雾岚餐厅和杉木餐厅，3人，总预算240元，推荐更便宜的一家",
            [("远山餐厅", 68), ("海湾餐厅", 82)],
            party_size=3, budget=240,
        )
        result = price_comparison(state)
        self.assertTrue(
            result is None or not result["complete"], "目标两家均无证据，不能算完成比较"
        )
        if result:
            self.assertIsNone(
                result["data"]["recommendation"], "不得将未请求的两家冒充目标比较结果"
            )

    def test_accepted_total_price_cap_cannot_disappear(self):
        state = offline_state(
            "比较雾岚餐厅和杉木餐厅，3人，总价不超过240元，推荐更便宜的一家",
            [("雾岚餐厅", 90), ("杉木餐厅", 100)],
            party_size=3, budget=240,
        )
        self.assertEqual(state["browser_task_context"]["total_budget"], 240)
        result = price_comparison(state)
        self.assertTrue(
            result is None or not result["complete"], "270和300元都超过240元，不能成功推荐"
        )
        if result:
            self.assertIsNone(result["data"]["recommendation"])


if __name__ == "__main__":
    unittest.main()
