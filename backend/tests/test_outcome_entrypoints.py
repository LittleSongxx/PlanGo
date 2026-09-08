"""Two independently reproduced completion bypasses; offline TEST fixtures only."""

import tempfile
import unittest

from fastapi.testclient import TestClient
from test_browser_harness import TOKEN, fixture, settings, wait_for
from test_task_quality import TERMINAL
from yoyu.app import create_app
from yoyu.graph import BrowserDecision, ImageReading
from yoyu.world import ObservedPlace, PageData

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j/a0AAAAASUVORK5CYII="
QUOTES = "雾岚餐厅 人均68元\n杉木餐厅 人均82元"


async def literal_facts_only(schema, *, fallback, **kwargs):
    if schema is ImageReading:
        return ImageReading(text=QUOTES)
    if schema is PageData:
        return PageData(
            places=[
                ObservedPlace(
                    name=name, average_price=price, price_unit="人均", quote=f"{name} 人均{price}元"
                )
                for name, price in [("雾岚餐厅", 68), ("杉木餐厅", 82)]
            ]
        )
    if schema is BrowserDecision:
        return BrowserDecision(operation="finish")
    return fallback


class OutcomeEntrypointQuality(unittest.TestCase):
    def check_requested_arithmetic(self, goal, *, image=False):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory).model_copy(
                update={"openai_api_key": "offline-fixture-replaced"}
            )
            app = create_app(config, token=TOKEN)
            app.state.runtime.model.structured = literal_facts_only
            app.state.runtime.model._model = object()  # TEST only; every call is replaced above.
            with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
                payload = {"input_text": goal, "browser_session_id": "fixture-desktop"}
                if image:
                    payload["image"] = PNG
                response = client.post("/api/v1/runs", json=payload)
                self.assertEqual(response.status_code, 202, response.text)
                run_id = response.json()["run_id"]
                result = wait_for(
                    client,
                    run_id,
                    lambda value: value["phase"] in TERMINAL or value["state"].get("browser_wait"),
                )
                if result["state"].get("browser_wait"):
                    command = client.get(
                        "/api/v1/browser/commands?browser_session_id=fixture-desktop"
                    ).json()["commands"][0]
                    client.post(
                        "/api/v1/browser/commands/" + command["command_id"] + "/result",
                        json={**fixture(command), "text": QUOTES, "tables": []},
                    )
                    result = wait_for(client, run_id, lambda value: value["phase"] in TERMINAL)
                self.assertEqual(
                    result["phase"],
                    "PARTIAL_FAILED" if image else "SUCCEEDED",
                    "截图只完成OCR应保持未完成；网页算价必须实际交付结果",
                )
                if not image:
                    for amount in ("204", "246", "42"):
                        self.assertIn(
                            amount,
                            result["state"]["reason"],
                            "读取/OCR成功不能冒充已回答总价和差额",
                        )
                    self.assertTrue(
                        any(
                            a["type"] not in {"image", "browser_page"}
                            for a in result["state"]["browser_artifacts"]
                        ),
                        "完成计算必须交付实际答案",
                    )
                else:
                    self.assertTrue(
                        any(
                            a["type"] == "image" and a["source"] == "user"
                            for a in result["state"]["browser_artifacts"]
                        ),
                        "未完成比较仍应保留来源为用户的截图观测",
                    )

    def test_image_comparison_requires_more_than_ocr(self):
        self.check_requested_arithmetic(
            "比较截图里的雾岚餐厅和杉木餐厅，3人，总预算240元，推荐便宜的一家并说明差价",
            image=True,
        )

    def test_calculation_request_requires_more_than_page_read(self):
        self.check_requested_arithmetic("算一下网页上雾岚餐厅和杉木餐厅3人各自的总价，给出差额")


if __name__ == "__main__":
    unittest.main()
