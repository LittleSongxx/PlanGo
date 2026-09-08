"""High-severity review findings; separate from the fixed 12 + 1 + 2 acceptance pools."""

import unittest

from test_comparison_scope import offline_state
from yoyu.outcomes import price_comparison, update_task_context

PRICES = [("雾岚餐厅", 68), ("杉木餐厅", 82)]


class UnresolvedConstraintQuality(unittest.TestCase):
    def test_per_person_cap_cannot_be_ignored(self):
        state = offline_state("比较雾岚餐厅和杉木餐厅，3人，每人最多60元，推荐便宜的一家", PRICES)
        result = price_comparison(state)
        self.assertTrue(
            result is None or not result["complete"],
            "68和82元均高于每人60元，不能把明确上限遗漏后标为完成",
        )

    def test_price_evidence_cannot_verify_food_avoidance(self):
        state = offline_state(
            "比较雾岚餐厅和杉木餐厅，3人，总预算240元，不吃花生，推荐便宜的一家", PRICES
        )
        result = price_comparison(state)
        self.assertTrue(
            result is None or not result["complete"],
            "两个人均价格没有食材信息，不能宣称已完成含忌口要求的推荐",
        )
        if result:
            self.assertIsNone(
                result["data"]["recommendation"],
                "忌口未核验时不得给出最终推荐，即使状态标为未完成",
            )

    def test_explicitly_unknown_party_cannot_reuse_previous_count(self):
        state = offline_state("比较雾岚餐厅和杉木餐厅，3人，总预算240元，推荐便宜的一家", PRICES)
        state.update(input_text="人数还没确定，先别按3人算", turn_id=2)
        state["browser_task_context"] = update_task_context(state)
        result = price_comparison(state)
        self.assertTrue(
            result is None or not result["complete"],
            "用户撤回了人数确认，不能继续用旧三人总价宣称预算已满足",
        )


if __name__ == "__main__":
    unittest.main()
