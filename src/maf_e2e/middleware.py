from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from time import monotonic
from typing import Any

from agent_framework import (
    AgentContext,
    AgentMiddleware,
    ChatContext,
    ChatMiddleware,
    FunctionInvocationContext,
    FunctionMiddleware,
)
from opentelemetry import trace

from maf_e2e.models import FailureKind

LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer("maf_e2e")
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}
OBSERVABILITY_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "maf_e2e_observability_context", default=None
)


def exception_status_code(exc: BaseException) -> int | None:
    for candidate in (exc, getattr(exc, "response", None), getattr(exc, "details", None)):
        value = getattr(candidate, "status_code", None)
        if isinstance(value, int):
            return value
        value = getattr(candidate, "code", None)
        if isinstance(value, int):
            return value
        value = getattr(candidate, "status", None)
        if isinstance(value, int):
            return value
        if isinstance(candidate, dict):
            value = candidate.get("code")
            if isinstance(value, int):
                return value
            error = candidate.get("error")
            if isinstance(error, dict) and isinstance(error.get("code"), int):
                return int(error["code"])
    for chained in (getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
        if chained is not None:
            status = exception_status_code(chained)
            if status is not None:
                return status
    return None


def is_transient_error(exc: BaseException) -> bool:
    if is_quota_error(exc):
        return False
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    return exception_status_code(exc) in TRANSIENT_STATUS_CODES


def is_quota_error(exc: BaseException) -> bool:
    status = exception_status_code(exc)
    text = _safe_exception_text(exc)
    return status == 429 and ("quota" in text or "resource_exhausted" in text)


def classify_failure(exc: BaseException, *, stage: str) -> FailureKind:
    if exc.__class__.__name__ == "StructuredOutputError":
        return FailureKind.STRUCTURED_OUTPUT
    if is_quota_error(exc):
        return FailureKind.MODEL_QUOTA
    if stage == "browser" and not is_transient_error(exc):
        return FailureKind.PLAYWRIGHT
    if is_transient_error(exc):
        return FailureKind.MODEL_TRANSIENT
    status = exception_status_code(exc)
    text = str(exc).lower()
    if status in {400, 401, 403, 404, 422} or "content_filter" in text:
        return FailureKind.MODEL_PERMANENT
    return FailureKind.UNKNOWN


def _safe_exception_text(exc: BaseException) -> str:
    parts = [str(exc).lower()]
    for attr in ("message", "status", "details"):
        value = getattr(exc, attr, None)
        if value is not None:
            parts.append(str(value).lower())
    for chained in (getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
        if chained is not None:
            parts.append(_safe_exception_text(chained))
    return " ".join(parts)


class ChatRetryMiddleware(ChatMiddleware):
    def __init__(self, *, stage: str, max_retries: int, trace_content: bool = False) -> None:
        self.stage = stage
        self.max_retries = max_retries
        self.trace_content = trace_content

    async def process(
        self,
        context: ChatContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        metadata = OBSERVABILITY_CONTEXT.get() or {}
        for retry_attempt in range(1, self.max_retries + 2):
            started = monotonic()
            try:
                with TRACER.start_as_current_span(
                    "maf_e2e.model_call",
                    record_exception=False,
                    set_status_on_exception=False,
                ) as span:
                    _set_common_attributes(span, metadata, self.stage)
                    span.set_attribute("maf.retry_attempt", retry_attempt)
                    try:
                        await call_next()
                    except Exception as exc:
                        span.set_attribute("maf.success", False)
                        span.set_attribute("exception.type", type(exc).__name__)
                        span.set_attribute("maf.duration_ms", int((monotonic() - started) * 1000))
                        raise
                    span.set_attribute("maf.success", True)
                    span.set_attribute("maf.duration_ms", int((monotonic() - started) * 1000))
                    _set_usage_attributes(span, context.result)
                    if self.trace_content:
                        span.set_attribute(
                            "gen_ai.input",
                            "\n".join(message.text or "" for message in context.messages)[:4000],
                        )
                        span.set_attribute(
                            "gen_ai.output", str(getattr(context.result, "text", ""))[:4000]
                        )
                LOGGER.info(
                    "model_call run_id=%s stage=%s attempt=%s retry_attempt=%d "
                    "success=true duration_ms=%d",
                    metadata.get("run_id", "unknown"),
                    self.stage,
                    metadata.get("attempt", 1),
                    retry_attempt,
                    int((monotonic() - started) * 1000),
                )
                return
            except Exception as exc:
                transient = is_transient_error(exc)
                LOGGER.warning(
                    "model_call run_id=%s stage=%s attempt=%s retry_attempt=%d "
                    "success=false exception=%s transient=%s",
                    metadata.get("run_id", "unknown"),
                    self.stage,
                    metadata.get("attempt", 1),
                    retry_attempt,
                    type(exc).__name__,
                    transient,
                )
                if not transient or retry_attempt > self.max_retries:
                    raise
                await asyncio.sleep(float(retry_attempt))


class AgentRetryMiddleware(AgentMiddleware):
    def __init__(self, *, stage: str, max_retries: int, trace_content: bool = False) -> None:
        self.stage = stage
        self.max_retries = max_retries
        self.trace_content = trace_content

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        metadata = OBSERVABILITY_CONTEXT.get() or {}
        for retry_attempt in range(1, self.max_retries + 2):
            started = monotonic()
            try:
                with TRACER.start_as_current_span(
                    "maf_e2e.cli_agent_call",
                    record_exception=False,
                    set_status_on_exception=False,
                ) as span:
                    _set_common_attributes(span, metadata, self.stage)
                    span.set_attribute("maf.retry_attempt", retry_attempt)
                    await call_next()
                    span.set_attribute("maf.success", True)
                    span.set_attribute("maf.duration_ms", int((monotonic() - started) * 1000))
                    if self.trace_content:
                        span.set_attribute(
                            "gen_ai.input",
                            "\n".join(message.text or "" for message in context.messages)[:4000],
                        )
                return
            except Exception as exc:
                transient = is_transient_error(exc)
                if not transient or retry_attempt > self.max_retries:
                    raise
                await asyncio.sleep(float(retry_attempt))


async def invoke_tool_with_telemetry(
    function: Any, *, arguments: dict[str, Any], stage: str
) -> Any:
    started = monotonic()
    success = False
    try:
        result = await function.invoke(arguments=arguments)
        success = True
        return result
    finally:
        metadata = OBSERVABILITY_CONTEXT.get() or {}
        duration_ms = int((monotonic() - started) * 1000)
        with TRACER.start_as_current_span("maf_e2e.tool_call") as span:
            _set_common_attributes(span, metadata, stage)
            span.set_attribute("tool.name", str(function.name))
            span.set_attribute("tool.success", success)
            span.set_attribute("tool.duration_ms", duration_ms)


class ToolTelemetryMiddleware(FunctionMiddleware):
    def __init__(self, *, stage: str) -> None:
        self.stage = stage

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        started = monotonic()
        name = context.function.name
        success = False
        try:
            await call_next()
            success = True
        finally:
            metadata = OBSERVABILITY_CONTEXT.get() or {}
            duration_ms = int((monotonic() - started) * 1000)
            with TRACER.start_as_current_span("maf_e2e.tool_call") as span:
                _set_common_attributes(span, metadata, self.stage)
                span.set_attribute("tool.name", name)
                span.set_attribute("tool.success", success)
                span.set_attribute("tool.duration_ms", duration_ms)
            LOGGER.info(
                "tool_call run_id=%s stage=%s attempt=%s tool=%s success=%s duration_ms=%d",
                metadata.get("run_id", "unknown"),
                self.stage,
                metadata.get("attempt", 1),
                name,
                success,
                duration_ms,
            )


def _set_common_attributes(span: Any, metadata: dict[str, Any], stage: str) -> None:
    span.set_attribute("maf.run_id", str(metadata.get("run_id", "unknown")))
    span.set_attribute("maf.stage", str(metadata.get("stage", stage)))
    span.set_attribute("maf.attempt", int(metadata.get("attempt", 1)))


def _set_usage_attributes(span: Any, result: object) -> None:
    usage = getattr(result, "usage", None)
    if usage is None:
        return
    for source, target in (
        ("input_tokens", "gen_ai.usage.input_tokens"),
        ("output_tokens", "gen_ai.usage.output_tokens"),
        ("total_tokens", "gen_ai.usage.total_tokens"),
    ):
        value = getattr(usage, source, None)
        if isinstance(value, int):
            span.set_attribute(target, value)
