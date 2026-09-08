from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from langchain_core.exceptions import OutputParserException
from openai import APIConnectionError, APITimeoutError
from plango_harness.observability import agent_span
from plango_harness.settings import Settings
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ModelProviderUnavailable(RuntimeError):
    """A failed provider request must not become a successful local fallback."""

    def __init__(self, category: str) -> None:
        self.category = category
        message = {
            "quota": "模型服务额度不足，请检查运行服务的账户余额或配额。",
            "authentication": "模型鉴权失败，请检查运行服务中的模型密钥。",
            "permission": "模型服务拒绝访问，请检查所选模型与账户权限。",
            "rate_limit": "模型服务请求过于频繁，请稍后重试。",
            "timeout": "模型请求超时，本轮未取得可用回复。",
            "connection": "无法连接模型服务，请检查运行服务的网络与接口地址。",
            "provider": "模型服务暂时异常，请稍后重试。",
            "request": "模型服务不接受当前请求，请核对模型及接口能力配置。",
            "internal": "模型调用遇到内部错误，请根据任务记录检查运行服务。",
        }.get(category, "模型服务不可用，请检查运行服务中的配置。")
        super().__init__(message)


class StructuredOutputError(ValueError):
    """The provider replied, but did not produce the requested structure."""


ACCOUNT_ERRORS = {"Arrearage", "InsufficientBalance", "insufficient_quota"}


def _error_category(error: Exception, code: Any, status: Any) -> str:
    if code in ACCOUNT_ERRORS:
        return "quota"
    if status == 401:
        return "authentication"
    if status == 403:
        return "permission"
    if status == 429:
        return "rate_limit"
    if isinstance(error, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
        return "timeout"
    if isinstance(error, (httpx.TransportError, APIConnectionError)):
        return "connection"
    if isinstance(status, int):
        return "provider" if status >= 500 else "request"
    if isinstance(error, (ValidationError, OutputParserException, json.JSONDecodeError, StructuredOutputError)):
        return "structured_output"
    return "internal"


def _validation_hint(error: Exception) -> dict[str, Any]:
    """Repair receives field locations, never raw model output or exception messages."""
    hint: dict[str, Any] = {"error_type": type(error).__name__}
    cause: BaseException | None = error
    for _ in range(3):
        if isinstance(cause, ValidationError):
            hint["locations"] = [
                [str(part)[:64] for part in item["loc"][:8]]
                for item in cause.errors(include_input=False, include_context=False, include_url=False)[:8]
            ]
            break
        cause = getattr(cause, "__cause__", None)
        if cause is None:
            break
    return hint


@dataclass(frozen=True)
class ModelMetadata:
    provider: str
    model: str
    prompt_version: str
    thinking_mode: str


class ModelAdapter:
    """Provider-neutral chat, tool-call and structured-output adapter."""

    def __init__(self, settings: Settings, model: Any | None = None) -> None:
        self.settings = settings
        self.system_prefix = ""
        self._model = model
        self._http_client: httpx.AsyncClient | None = None
        # ponytail: serialize one run's model budget; add reservations only if
        # parallel model latency is worth the accounting complexity.
        self._call_lock = asyncio.Lock()
        self.metadata = ModelMetadata(
            provider="dashscope"
            if "dashscope" in settings.openai_base_url
            else "openai-compatible",
            model=settings.openai_model,
            prompt_version="plango-structured-v2-schema-repair",
            thinking_mode="disabled" if "dashscope" in settings.openai_base_url else "provider_default",
        )
        self.call_count = 0
        self.fallback_count = 0
        self.last_usage: dict[str, int] = {}
        self.total_tokens = 0
        self.token_baseline = 0
        self.total_latency_ms = 0.0
        self.last_latency_ms = 0.0
        self.last_error: str | None = None
        self.last_call_kind: str | None = None
        self.call_records: list[dict[str, Any]] = []
        self.deadline_at: float | None = None
        self.cleanup_reserve_seconds = 5.0
        self.token_reserve = min(2000, max(256, settings.max_model_tokens // 4))
        self.per_call_output_cap = min(1024, max(256, settings.max_model_tokens // 12))

    def set_run_budget(
        self,
        deadline_at: float | None,
        *,
        cleanup_reserve_seconds: float = 5.0,
        token_reserve: int | None = None,
        token_baseline: int = 0,
    ) -> None:
        """Set the shared run deadline without storing prompt/response text."""
        self.deadline_at = deadline_at
        self.token_baseline = max(0, int(token_baseline))
        self.cleanup_reserve_seconds = max(0.0, float(cleanup_reserve_seconds))
        if token_reserve is not None:
            self.token_reserve = max(0, int(token_reserve))

    def _remaining_seconds(self) -> float | None:
        if self.deadline_at is None:
            return None
        return self.deadline_at - time.time() - self.cleanup_reserve_seconds

    def _remaining_tokens(self) -> int:
        return max(0, self.token_limit - self.total_tokens - self.token_reserve)

    @property
    def token_limit(self) -> int:
        return self.token_baseline + self.settings.max_model_tokens

    @staticmethod
    def _input_token_estimate(system: str, user: str, definitions: str = "") -> int:
        # Admission heuristic, not an exact provider tokenizer. Count schemas too; reported usage is authoritative.
        return len(system) + len(user) + len(definitions) + 256

    def _completion_cap(self, remaining_tokens: int, input_tokens: int) -> int:
        return max(
            256,
            min(
                self.per_call_output_cap,
                remaining_tokens // 4,
                remaining_tokens - input_tokens,
            ),
        )

    async def _invoke(self, awaitable, *, timeout: float | None):
        if timeout is None:
            return await awaitable
        if timeout <= 0:
            raise TimeoutError("run_deadline_exhausted")
        return await asyncio.wait_for(awaitable, timeout=timeout)

    def reset_run(
        self,
        total_tokens: int = 0,
        *,
        call_count: int = 0,
        fallback_count: int = 0,
        total_latency_ms: float = 0.0,
        last_error: str | None = None,
        last_usage: dict[str, int] | None = None,
        call_records: list[dict[str, Any]] | None = None,
    ) -> None:
        """Restore the run counters before replaying a graph checkpoint."""
        self.token_baseline = 0
        self.call_count = max(0, int(call_count))
        self.fallback_count = max(0, int(fallback_count))
        self.last_usage = dict(last_usage or {})
        self.total_tokens = max(0, int(total_tokens))
        self.total_latency_ms = max(0.0, float(total_latency_ms))
        self.last_latency_ms = float((call_records or [{}])[-1].get("latency_ms", 0.0) or 0.0)
        self.last_error = last_error
        self.last_call_kind = (call_records or [{}])[-1].get("kind")
        self.call_records = list(call_records or [])[-100:]

    def _record(
        self,
        kind: str,
        status: str,
        started: float,
        error: str | None = None,
        provider_error: Exception | None = None,
    ) -> None:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        self.last_call_kind = kind
        self.last_latency_ms = latency_ms
        self.total_latency_ms += latency_ms
        self.last_error = error
        record = {
            "kind": kind,
            "status": status,
            "latency_ms": latency_ms,
            "error": error,
            "model": self.metadata.model,
            "prompt_version": self.metadata.prompt_version,
            "thinking_mode": self.metadata.thinking_mode,
        }
        if provider_error is not None:
            http_status = getattr(provider_error, "status_code", None)
            code = getattr(provider_error, "code", None)
            body = getattr(provider_error, "body", None)
            if isinstance(body, dict):
                inner = body.get("error", body)
                if isinstance(inner, dict):
                    code = inner.get("code", code)
            if isinstance(http_status, int):
                record["http_status"] = http_status
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code):
                record["provider_error_code"] = code
            record["error_category"] = _error_category(provider_error, record.get("provider_error_code"), http_status)
        # Keep only numeric usage metadata; never persist request/response text.
        usage = self.last_usage
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        if isinstance(input_tokens, int):
            record["input_tokens"] = input_tokens
        if isinstance(output_tokens, int):
            record["output_tokens"] = output_tokens
        if isinstance(usage.get("total_tokens"), int):
            record["total_tokens"] = usage["total_tokens"]
        self.call_records.append(record)
        # Keep the in-memory audit bounded; only metadata is stored.
        if len(self.call_records) > 100:
            del self.call_records[:-100]

    def snapshot(self) -> dict[str, Any]:
        return {
            "provider": self.metadata.provider,
            "model": self.metadata.model,
            "prompt_version": self.metadata.prompt_version,
            "thinking_mode": self.metadata.thinking_mode,
            "call_count": self.call_count,
            "fallback_count": self.fallback_count,
            "total_tokens": self.total_tokens,
            "token_baseline": self.token_baseline,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "last_latency_ms": self.last_latency_ms,
            "last_error": self.last_error,
            "last_call_kind": self.last_call_kind,
            "last_usage": dict(self.last_usage),
            "call_records": list(self.call_records),
        }

    @property
    def available(self) -> bool:
        return self._model is not None or self.settings.model_enabled

    def _get_model(self):
        if (
            self._model is None
            and self.settings.model_enabled
        ):
            from langchain_openai import ChatOpenAI

            # Keep connections within this adapter's runtime/event loop instead of the SDK cache.
            if self._http_client is None:
                self._http_client = httpx.AsyncClient(
                    timeout=self.settings.openai_timeout_seconds, follow_redirects=True
                )
            model_kwargs: dict[str, Any] = {
                "http_async_client": self._http_client,
                "model": self.settings.openai_model,
                "api_key": self.settings.openai_api_key,  # type: ignore[arg-type]
                "base_url": self.settings.openai_base_url,
                "temperature": 0.1,
                "timeout": self.settings.openai_timeout_seconds,
                "max_retries": self.settings.openai_max_retries,
                "max_completion_tokens": self.settings.max_model_tokens,
            }
            if "dashscope" in self.settings.openai_base_url:
                # Qwen hybrid models enable thinking by default; disabling it
                # makes the per-call completion cap auditable and predictable.
                model_kwargs["extra_body"] = {"enable_thinking": False}
            self._model = ChatOpenAI(
                **model_kwargs
            )
        return self._model

    async def structured(
        self, schema: type[T], *, system: str, user: str, fallback: T, image: str | None = None,
    ) -> T:
        async with self._call_lock:
            return await self._structured(schema, system=system, user=user, fallback=fallback, image=image)

    async def _structured(
        self,
        schema: type[T],
        *,
        system: str,
        user: str,
        fallback: T,
        image: str | None = None,
    ) -> T:
        system = self.system_prefix + system
        started = time.perf_counter()
        self.last_usage = {}
        remaining_tokens = self._remaining_tokens()
        remaining_seconds = self._remaining_seconds()
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        input_tokens = self._input_token_estimate(system, user, schema_text) + (2048 if image else 0)
        if remaining_tokens <= input_tokens + 256:
            self.fallback_count += 1
            self._record("structured", "fallback", started, "model_token_budget")
            return fallback
        if remaining_seconds is not None and remaining_seconds <= 0:
            self.fallback_count += 1
            self._record("structured", "fallback", started, "run_deadline_exhausted")
            return fallback
        try:
            model = self._get_model()
        except Exception as exc:
            self.fallback_count += 1
            self._record("structured", "fallback", started, type(exc).__name__)
            return fallback
        if model is None:
            self.fallback_count += 1
            self._record("structured", "fallback", started, "model_unavailable")
            return fallback
        self.call_count += 1
        try:
            async with agent_span("invoke_agent", agent="structured", model=self.metadata.model):
                try:
                    runnable = model.with_structured_output(schema, include_raw=True)
                except TypeError:
                    # Small test doubles and older compatible clients may not
                    # expose include_raw; keep the schema-only path working.
                    runnable = model.with_structured_output(schema)
                invoke = runnable
                if hasattr(invoke, "bind"):
                    completion_cap = self._completion_cap(remaining_tokens, input_tokens)
                    invoke = invoke.bind(
                        max_completion_tokens=completion_cap
                    )
                result = await self._invoke(
                    invoke.ainvoke(
                        [{"role": "system", "content": system}, {"role": "user", "content": ([{"type":"text","text":user},{"type":"image_url","image_url":{"url":image}}] if image else user)}]
                    ),
                    timeout=(
                        min(float(self.settings.openai_timeout_seconds), remaining_seconds)
                        if remaining_seconds is not None
                        else float(self.settings.openai_timeout_seconds)
                    ),
                )
            usage_result = result.get("raw") if isinstance(result, dict) and result.get("raw") else result
            self._capture_usage(usage_result)
            parsing_error = result.get("parsing_error") if isinstance(result, dict) else None
            if isinstance(result, dict) and "parsed" in result:
                result = result.get("parsed")
            if self.total_tokens > self.token_limit:
                self.fallback_count += 1
                self._record("structured", "fallback", started, "model_token_budget")
                return fallback
            if isinstance(parsing_error, Exception):
                raise parsing_error
            if parsing_error is not None:
                raise StructuredOutputError("structured_parsing_error")
            if isinstance(result, schema):
                self._record("structured", "success", started)
                return result
            if isinstance(result, dict):
                parsed = schema.model_validate(result)
                self._record("structured", "success", started)
                return parsed
            raise StructuredOutputError("structured_output_invalid")
        except Exception as exc:
            self._record("structured", "retry", started, type(exc).__name__, provider_error=exc)
            if self.call_records[-1].get("error_category") != "structured_output":
                self.call_records[-1]["status"] = "error"
                # The SDK owns bounded transport retries. JSON repair is only
                # meaningful after a response failed schema/parsing validation.
                raise ModelProviderUnavailable(str(self.call_records[-1]["error_category"])) from None
            retry_started = time.perf_counter()
            self.last_usage = {}
            remaining_tokens = self._remaining_tokens()
            remaining_seconds = self._remaining_seconds()
            repair_system = (system + "\n前次结构化输出未通过验证。请依据以下 JSON Schema 返回合法 JSON，不要输出 Markdown。\nJSON Schema: "
                + schema_text + "\nValidation: " + json.dumps(_validation_hint(exc), ensure_ascii=False, separators=(",", ":")))
            input_tokens = self._input_token_estimate(repair_system, user) + (2048 if image else 0)
            if remaining_tokens <= input_tokens + 256:
                self.fallback_count += 1
                self._record("structured_retry", "fallback", retry_started, "model_token_budget")
                return fallback
            if remaining_seconds is not None and remaining_seconds <= 0:
                self.fallback_count += 1
                self._record("structured_retry", "fallback", retry_started, "run_deadline_exhausted")
                return fallback
            self.call_count += 1
            retry_error = "structured_retry_invalid"
            retry_provider_error: Exception | None = None
            try:
                invoke = model
                if hasattr(invoke, "bind"):
                    completion_cap = self._completion_cap(remaining_tokens, input_tokens)
                    invoke = invoke.bind(
                        max_completion_tokens=completion_cap
                    )
                message = await self._invoke(
                    invoke.ainvoke(
                        [
                            {"role": "system", "content": repair_system},
                            {"role": "user", "content": ([{"type":"text","text":user},{"type":"image_url","image_url":{"url":image}}] if image else user)},
                        ]
                    ),
                    timeout=(
                        min(float(self.settings.openai_timeout_seconds), remaining_seconds)
                        if remaining_seconds is not None
                        else float(self.settings.openai_timeout_seconds)
                    ),
                )
                self._capture_usage(message)
                if self.total_tokens > self.token_limit:
                    self.fallback_count += 1
                    self._record("structured_retry", "fallback", retry_started, "model_token_budget")
                    return fallback
                content = getattr(message, "content", message)
                if isinstance(content, list):
                    content = "".join(str(item) for item in content)
                raw = str(content)
                start, end = raw.find("{"), raw.rfind("}")
                if start >= 0 and end > start:
                    parsed = schema.model_validate(json.loads(raw[start : end + 1]))
                    self._record("structured_retry", "success", retry_started)
                    return parsed
            except Exception as retry_exc:
                retry_error = type(retry_exc).__name__
                retry_provider_error = retry_exc
            self.fallback_count += 1
            self._record("structured_retry", "fallback", retry_started, retry_error, provider_error=retry_provider_error)
            if retry_provider_error is not None and self.call_records[-1].get("error_category") != "structured_output":
                self.fallback_count -= 1
                self.call_records[-1]["status"] = "error"
                raise ModelProviderUnavailable(str(self.call_records[-1]["error_category"])) from None
        return fallback

    async def tool_calls(
        self, *, system: str, user: str, tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        async with self._call_lock:
            return await self._tool_calls(system=system, user=user, tools=tools)

    async def _tool_calls(
        self,
        *,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize OpenAI-compatible tool calls before registry validation."""
        system = self.system_prefix + system
        started = time.perf_counter()
        self.last_usage = {}
        remaining_tokens = self._remaining_tokens()
        remaining_seconds = self._remaining_seconds()
        definitions = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
        input_tokens = self._input_token_estimate(system, user, definitions)
        if remaining_tokens <= input_tokens + 256:
            self.fallback_count += 1
            self._record("tool_call", "fallback", started, "model_token_budget")
            return []
        if remaining_seconds is not None and remaining_seconds <= 0:
            self.fallback_count += 1
            self._record("tool_call", "fallback", started, "run_deadline_exhausted")
            return []
        try:
            model = self._get_model()
        except Exception as exc:
            self.fallback_count += 1
            self._record("tool_call", "fallback", started, type(exc).__name__, provider_error=exc)
            return []
        if model is None:
            self.fallback_count += 1
            self._record("tool_call", "fallback", started, "model_unavailable")
            return []
        self.call_count += 1
        try:
            bound = model.bind_tools(tools)
            async with agent_span("invoke_agent", agent="tool_call", model=self.metadata.model):
                invoke = bound
                if hasattr(invoke, "bind"):
                    completion_cap = self._completion_cap(remaining_tokens, input_tokens)
                    invoke = invoke.bind(
                        max_completion_tokens=completion_cap
                    )
                response = await self._invoke(
                    invoke.ainvoke(
                        [{"role": "system", "content": system}, {"role": "user", "content": user}]
                    ),
                    timeout=(
                        min(float(self.settings.openai_timeout_seconds), remaining_seconds)
                        if remaining_seconds is not None
                        else float(self.settings.openai_timeout_seconds)
                    ),
                )
            self._capture_usage(response)
            if self.total_tokens > self.token_limit:
                self.fallback_count += 1
                self._record("tool_call", "fallback", started, "model_token_budget")
                return []
            calls = getattr(response, "tool_calls", None) or []
            normalized: list[dict[str, Any]] = []
            for call in calls:
                if isinstance(call, dict) and isinstance(call.get("function"), dict):
                    function = call["function"]
                    name = function.get("name")
                    args = function.get("arguments", {})
                else:
                    name = (
                        call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
                    )
                    args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        continue
                if name and isinstance(args, dict):
                    normalized.append({"name": str(name), "arguments": args})
            self._record("tool_call", "success", started)
            return normalized
        except Exception as exc:
            self.fallback_count += 1
            self._record("tool_call", "fallback", started, type(exc).__name__, provider_error=exc)
            if self.call_records[-1].get("error_category") != "structured_output":
                self.fallback_count -= 1
                self.call_records[-1]["status"] = "error"
                raise ModelProviderUnavailable(str(self.call_records[-1]["error_category"])) from None
            return []

    def _capture_usage(self, response: Any) -> None:
        response_metadata = getattr(response, "response_metadata", {}) or {}
        usage = getattr(response, "usage_metadata", None) or response_metadata.get("token_usage")
        if isinstance(usage, dict):
            self.last_usage = {
                str(key): int(value)
                for key, value in usage.items()
                if isinstance(value, (int, float))
            }
            total = self.last_usage.get("total_tokens")
            if total is None:
                total = self.last_usage.get("input_tokens", self.last_usage.get("prompt_tokens", 0))
                total += self.last_usage.get("output_tokens", self.last_usage.get("completion_tokens", 0))
            self.total_tokens += max(0, total)

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
            self._model = None
