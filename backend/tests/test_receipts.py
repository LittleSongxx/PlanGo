"""Run: PYTHONPATH=backend python -m unittest discover -s backend/tests -p test_receipts.py"""

import unittest

from yoyu.receipts import verify_receipt


def page(text, *, snapshot="before", timestamp="2026-09-08T10:00:00+00:00", **changes):
    return {
        "ok": True,
        "outcome": "observed",
        "url": "https://www.meituan.com/booking",
        "tab_id": "tab-1",
        "snapshot_id": snapshot,
        "observed_at": timestamp,
        "text": text,
        **changes,
    }


def after(text, **changes):
    return page(text, snapshot="after", timestamp="2026-09-08T10:01:00+00:00", **changes)


class ReceiptVerificationTest(unittest.TestCase):
    def test_observed_confirmation_requires_bound_fresh_identity(self):
        before = page("请确认预约信息")
        success = after("预约成功\n预约编号：R20260908")
        proof = verify_receipt(before, success, "帮我预约")
        self.assertEqual(
            proof,
            {
                "scope": "page_confirmation",
                "kind": "reservation",
                "reference": "R20260908",
                "quote": success["text"],
                "source_url": success["url"],
                "observed_at": success["observed_at"],
            },
        )
        cases = [
            (page("上次预约成功 预约编号：R20260908"), success, "帮我预约"),
            (before, {**success, "snapshot_id": "before"}, "帮我预约"),
            (before, {**success, "observed_at": before["observed_at"]}, "帮我预约"),
            (before, {**success, "tab_id": "tab-2"}, "帮我预约"),
            (before, {**success, "url": "https://www.dianping.com/booking"}, "帮我预约"),
            (before, {**success, "url": "https://meituan.com.evil.test/booking"}, "帮我预约"),
            (before, {**success, "url": "https://user:secret@www.meituan.com/booking"}, "帮我预约"),
            (before, {**success, "outcome": "executed"}, "帮我预约"),
            (before, {**success, "observed_at": None}, "帮我预约"),
            (before, success, "帮我取号"),
            (before, success, "不要预约"),
            (before, success, "帮我预约并支付"),
            (before, after("预约未成功 预约编号：R20260908"), "帮我预约"),
            (before, after("未预约成功 预约编号：R20260908"), "帮我预约"),
            (before, after("不是预约成功 预约编号：R20260908"), "帮我预约"),
            (before, after("如果预约成功 预约编号：R20260908"), "帮我预约"),
            (before, after("预约成功了吗？ 预约编号：R20260908"), "帮我预约"),
            (before, after("预约成功 预约编号：R20260908，等待商家确认"), "帮我预约"),
            (before, after("示例：预约成功 预约编号：R20260908"), "帮我预约"),
            (before, after("预约成功", receipt={"reference": "FAKE123"}), "帮我预约"),
            (before, after("预约成功 预约编号：UNKNOWN"), "帮我预约"),
            (before, after("预约成功 预约编号：123***"), "帮我预约"),
            (before, after("预约成功 预约编号：R111 预约编号：R222"), "帮我预约"),
            (before, after("预约成功" + "正文" * 100 + "预约编号：R20260908"), "帮我预约"),
        ]
        for old, new, goal in cases:
            with self.subTest(text=new["text"], goal=goal):
                self.assertIsNone(verify_receipt(old, new, goal))
        self.assertEqual(
            verify_receipt(page("确认人数"), after("取号成功 排队号：A12，等待叫号"), "帮我取号")[
                "kind"
            ],
            "queue",
        )
        self.assertEqual(
            verify_receipt(
                page("确认商品"), after("下单成功 订单号：ORD20260908，待支付"), "帮我下单"
            )["kind"],
            "order",
        )
        existing = page("订单号：ORD20260908，已创建")
        cancelled = after("取消成功 订单号：ORD20260908")
        self.assertEqual(verify_receipt(existing, cancelled, "取消订单")["kind"], "cancel")
        self.assertIsNone(verify_receipt(before, cancelled, "取消订单"))
        self.assertIsNone(
            verify_receipt(page("取消成功 订单号：ORD20260908"), cancelled, "取消订单")
        )
        self.assertIsNone(
            verify_receipt(existing, after("取消失败 订单号：ORD20260908"), "取消订单")
        )
        self.assertIsNone(verify_receipt(existing, cancelled, "取消预约"))

    def test_requested_identity_and_documentation_are_not_business_success(self):
        existing = page("订单号：ORD-A12 待使用\n订单号：ORD-B34 待使用")
        self.assertIsNone(
            verify_receipt(existing, after("取消成功 订单号：ORD-B34"), "取消订单 ORD-A12")
        )
        self.assertEqual(
            verify_receipt(existing, after("取消成功 订单号：ORD-A12"), "取消订单 ORD-A12")[
                "reference"
            ],
            "ORD-A12",
        )
        self.assertIsNone(
            verify_receipt(
                page("查看预约结果"),
                after("预约成功订单查询指南\n预约编号：R20260908\n请到商家后台核对实际状态"),
                "预约星河餐厅",
            )
        )
        proof = verify_receipt(
            page("星河餐厅 2人 预约确认"),
            after("月亮餐厅 6人 预约成功 预约编号：R20260908"),
            "预约星河餐厅，2人",
        )
        # Visible confirmation text cannot establish that the requested shop/party matched.
        self.assertEqual(proof["scope"], "page_confirmation")
        self.assertNotIn("business_success", proof)


if __name__ == "__main__":
    unittest.main()
