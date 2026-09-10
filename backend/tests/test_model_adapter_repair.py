"""Offline provider doubles exercise structured repair quality and actual usage boundaries."""

import json
import unittest
from types import SimpleNamespace

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError
from plango.settings import DesktopSettings
from plango_harness.agent.model_adapter import ModelAdapter, ModelProviderUnavailable
from pydantic import BaseModel, Field, ValidationError


class Choice(BaseModel):
    merchant_id: str
    total: float = Field(ge=0)


class RepairProvider:
    def __init__(self, usage=25, account_failure=False):
        self.raw_calls = []
        self.primary_calls = 0
        self.usage = usage
        self.account_failure = account_failure

    def with_structured_output(self, schema, include_raw=True):
        outer = self

        class Native:
            def bind(self, **kwargs):
                return self

            async def ainvoke(self, messages):
                outer.primary_calls += 1
                if outer.account_failure:
                    error = RuntimeError("private-account-detail")
                    error.body = {
                        "error": {"code": "insufficient_quota", "message": "private-account-detail"}
                    }
                    error.status_code = 429
                    raise error
                try:
                    schema.model_validate(
                        {
                            "merchant_id": "observed-merchant",
                            "total": "private-invalid-model-payload",
                        }
                    )
                except ValidationError as error:
                    return {
                        "parsed": None,
                        "raw": SimpleNamespace(
                            content="private-invalid-model-payload",
                            usage_metadata={"total_tokens": 25},
                        ),
                        "parsing_error": error,
                    }

        return Native()

    def bind(self, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.raw_calls.append(messages)
        prompt = messages[0]["content"]
        # This double can correct the shape only when the actual repair contract is supplied.
        schema = json.loads(prompt.split("JSON Schema: ", 1)[1].split("\nValidation: ", 1)[0])
        assert set(schema["required"]) == {"merchant_id", "total"}
        assert "total" in json.loads(prompt.split("\nValidation: ", 1)[1])["locations"][0]
        return SimpleNamespace(
            content=json.dumps({"merchant_id": "observed-merchant", "total": 128}),
            usage_metadata={"total_tokens": self.usage},
        )


class ModelRepairCheck(unittest.IsolatedAsyncioTestCase):
    async def test_new_turn_budget_keeps_cumulative_usage_and_its_own_cap(self):
        provider = RepairProvider(usage=2100)
        adapter = ModelAdapter(DesktopSettings(max_model_tokens=2000), model=provider)
        adapter.reset_run(1900, call_count=4)
        adapter.set_run_budget(None, token_baseline=1900)
        fallback = Choice(merchant_id="none", total=0)
        self.assertIs(await adapter.structured(Choice, system="报价", user="128元", fallback=fallback), fallback)
        self.assertEqual(adapter.total_tokens, 4025)
        self.assertEqual(adapter.call_count, 6)
        self.assertEqual(adapter.token_limit, 3900)
        self.assertEqual(adapter.last_error, "model_token_budget")
        adapter.reset_run()
        self.assertEqual(adapter.token_baseline, 0)

    async def test_transport_auth_and_internal_errors_never_use_json_repair(self):
        request = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")
        failures = [
            (APITimeoutError(request=request), "timeout"),
            (APIConnectionError(request=request), "connection"),
            (TypeError("private-programming-detail"), "internal"),
            *[(APIStatusError("private-provider-detail", response=httpx.Response(status, request=request), body=None), category)
              for status, category in [(401, "authentication"), (403, "permission"), (429, "rate_limit"), (503, "provider"), (400, "request")]],
        ]
        for error, category in failures:
            with self.subTest(category=category):
                class FailedProvider:
                    calls = 0

                    def with_structured_output(self, *args, **kwargs):
                        return self

                    def bind(self, **kwargs):
                        return self

                    async def ainvoke(self, messages):
                        self.calls += 1
                        raise error

                provider = FailedProvider()
                adapter = ModelAdapter(DesktopSettings(), model=provider)
                with self.assertRaises(ModelProviderUnavailable) as caught:
                    await adapter.structured(Choice, system="读取", user="报价128元", fallback=Choice(merchant_id="none", total=0))
                self.assertEqual(caught.exception.category, category)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(adapter.fallback_count, 0)
                self.assertEqual(adapter.call_records[-1]["error_category"], category)
                self.assertEqual(adapter.call_records[-1]["status"], "error")
                self.assertNotIn("private-", json.dumps(adapter.call_records))

    async def test_repair_receives_schema_and_safe_field_feedback(self):
        provider = RepairProvider()
        adapter = ModelAdapter(DesktopSettings(), model=provider)
        result = await adapter.structured(
            Choice,
            system="选择真实报价",
            user="observed-merchant 的报价128元",
            fallback=Choice(merchant_id="none", total=0),
        )
        self.assertEqual(result, Choice(merchant_id="observed-merchant", total=128))
        self.assertEqual(adapter.call_count, 2)
        self.assertEqual(adapter.total_tokens, 50)
        self.assertEqual(adapter.call_records[-1]["kind"], "structured_retry")
        self.assertEqual(adapter.call_records[-1]["status"], "success")
        self.assertNotIn("private-invalid-model-payload", json.dumps(provider.raw_calls))
        self.assertNotIn("private-invalid-model-payload", json.dumps(adapter.call_records))

    async def test_retry_over_budget_cannot_return_success(self):
        provider = RepairProvider(usage=2100)
        adapter = ModelAdapter(DesktopSettings(max_model_tokens=2000), model=provider)
        fallback = Choice(merchant_id="no-result", total=0)
        result = await adapter.structured(
            Choice, system="选择报价", user="报价128元", fallback=fallback
        )
        self.assertIs(result, fallback)
        self.assertEqual(adapter.total_tokens, 2125)
        self.assertEqual(adapter.call_count, 2)
        self.assertEqual(adapter.call_records[-1]["status"], "fallback")
        self.assertEqual(adapter.call_records[-1]["error"], "model_token_budget")
        self.assertEqual(adapter.call_records[-1]["total_tokens"], 2100)
        self.assertFalse(any(r["status"] == "success" for r in adapter.call_records))

    async def test_account_failure_does_not_retry_or_hide_as_fallback(self):
        provider = RepairProvider(account_failure=True)
        adapter = ModelAdapter(DesktopSettings(), model=provider)
        with self.assertRaises(ModelProviderUnavailable):
            await adapter.structured(
                Choice,
                system="选择报价",
                user="报价128元",
                fallback=Choice(merchant_id="none", total=0),
            )
        self.assertEqual(provider.primary_calls, 1)
        self.assertEqual(provider.raw_calls, [])
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(adapter.call_records[-1]["status"], "error")
        self.assertNotIn("private-account-detail", json.dumps(adapter.call_records))

    async def test_repair_prompt_overhead_is_charged_before_second_call(self):
        provider = RepairProvider()
        # Enough for the first schema, but not the extra validation/repair prompt.
        adapter = ModelAdapter(DesktopSettings(max_model_tokens=900), model=provider)
        fallback = Choice(merchant_id="none", total=0)
        result = await adapter.structured(
            Choice, system="选择报价", user="报价128元", fallback=fallback
        )
        self.assertIs(result, fallback)
        self.assertEqual(provider.primary_calls, 1)
        self.assertEqual(provider.raw_calls, [])
        self.assertEqual(adapter.call_records[-1]["kind"], "structured_retry")
        self.assertEqual(adapter.call_records[-1]["error"], "model_token_budget")

    async def test_schema_and_tool_definitions_count_before_admission(self):
        class LargeShape(BaseModel):
            value: str = Field(description="x" * 1200)

        provider = RepairProvider()
        adapter = ModelAdapter(DesktopSettings(max_model_tokens=1000), model=provider)
        fallback = LargeShape(value="none")
        self.assertIs(
            await adapter.structured(LargeShape, system="s", user="u", fallback=fallback), fallback
        )
        self.assertEqual(provider.primary_calls, 0)
        self.assertEqual(adapter.last_error, "model_token_budget")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "x" * 1200,
                    "parameters": {"type": "object"},
                },
            }
        ]
        self.assertEqual(await adapter.tool_calls(system="s", user="u", tools=tools), [])
        self.assertEqual(adapter.call_count, 0)


if __name__ == "__main__":
    unittest.main()
