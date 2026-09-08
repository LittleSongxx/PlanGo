"""Offline malformed extraction regressions: quantities and package totals are not unit prices."""

import unittest
from types import SimpleNamespace

from plango.browser import run_context
from plango.settings import DesktopSettings
from plango.world import BrowserWorld, Item, ObservedPlace, PageData, table_data


class PriceGroundingCheck(unittest.IsolatedAsyncioTestCase):
    async def test_model_omissions_cannot_erase_explicit_dom_table_rows(self):
        async def get(_):
            return None

        async def structured(*args, **kwargs):
            return PageData(menu=[Item(name="时价菜", price=99, quote="时价菜 99元")])

        world = BrowserWorld(
            DesktopSettings(), SimpleNamespace(get=get), SimpleNamespace(structured=structured)
        )
        token = run_context.set({})
        try:
            result = await world.extract(
                {
                    "command_id": "table-omission",
                    "title": "菜单",
                    "text": "菜单",
                    "tables": [
                        {
                            "headers": ["菜品", "价格"],
                            "rows": [["清蒸鱼", "128元"], ["时价菜", "询价"]],
                        }
                    ],
                }
            )
            self.assertEqual(
                [(item.name, item.price) for item in result.menu],
                [("清蒸鱼", 128), ("时价菜", None)],
            )
            self.assertEqual(result.menu[1].quote, "时价菜 | 询价")
        finally:
            run_context.reset(token)
            await world.close()

    async def test_currency_original_price_and_per_person_units_require_literal_evidence(self):
        text = "双人套餐 2人份 套餐128元\n星河餐厅 双人套餐128元\n午餐套餐 原价228元 现价128元\n月亮餐厅 人均64元"
        data = PageData(
            menu=[Item(name="双人套餐", price=2, people=2, quote="双人套餐 2人份 套餐128元")],
            offers=[
                Item(
                    name="午餐套餐",
                    price=128,
                    original_price=228,
                    quote="午餐套餐 原价228元 现价128元",
                )
            ],
            places=[
                ObservedPlace(
                    name="星河餐厅",
                    average_price=128,
                    price_unit="人均",
                    quote="星河餐厅 双人套餐128元",
                ),
                ObservedPlace(
                    name="月亮餐厅", average_price=64, price_unit="人均", quote="月亮餐厅 人均64元"
                ),
            ],
        )

        async def get(_):
            return None

        async def structured(*args, **kwargs):
            return data

        world = BrowserWorld(
            DesktopSettings(), SimpleNamespace(get=get), SimpleNamespace(structured=structured)
        )
        token = run_context.set({"extracted": {}})
        try:
            result = await world.extract(
                {"command_id": "offline-price-fixture", "text": text, "tables": []}
            )
            self.assertIsNone(result.menu[0].price)
            self.assertIsNone(result.places[0].average_price)
            self.assertIsNone(result.places[0].price_unit)
            self.assertEqual(result.places[1].average_price, 64)
            self.assertEqual(result.offers[0].price, 128)
            self.assertEqual(result.offers[0].original_price, 228)
        finally:
            run_context.reset(token)
            await world.close()
        columns = table_data(
            {
                "tables": [
                    {"headers": ["套餐", "原价", "现价"], "rows": [["双人套餐", "228", "128"]]}
                ]
            }
        )
        self.assertEqual(columns.offers[0].price, 128)
        self.assertEqual(columns.offers[0].original_price, 228)


if __name__ == "__main__":
    unittest.main()
