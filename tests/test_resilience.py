from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from agent_framework import AgentResponse, AgentSession, ChatContext
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel

from maf_qa.agents import StructuredOutputError, run_structured
from maf_qa.middleware import (
    OBSERVABILITY_CONTEXT,
    ChatRetryMiddleware,
    ToolTelemetryMiddleware,
)


class Output(BaseModel):
    answer: str


class RepairAgent:
    def __init__(self) -> None:
        self.calls = 0
        self.tools: list[list[Any] | None] = []

    async def run(self, messages: str, **kwargs: Any) -> AgentResponse[Any]:
        del messages
        self.tools.append(kwargs.get("tools"))
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(value={"wrong": "shape"})
        return AgentResponse(value=Output(answer="repaired"))


class InvalidAgent:
    async def run(self, messages: str, **kwargs: Any) -> AgentResponse[Any]:
        del messages, kwargs
        return AgentResponse(value={"wrong": "shape"})


async def test_structured_output_repairs_without_reusing_tools() -> None:
    agent = RepairAgent()
    result = await run_structured(
        agent,  # type: ignore[arg-type]
        "prompt",
        Output,
        AgentSession(),
        retries=1,
        tools=[object()],
    )

    assert result.answer == "repaired"
    assert agent.tools[0] is not None
    assert agent.tools[1] == []


async def test_structured_output_exhaustion() -> None:
    with pytest.raises(StructuredOutputError):
        await run_structured(
            InvalidAgent(),  # type: ignore[arg-type]
            "prompt",
            Output,
            AgentSession(),
            retries=0,
        )


class HttpError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


async def test_chat_middleware_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def call_next() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise HttpError(429)

    async def no_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr("maf_qa.middleware.asyncio.sleep", no_sleep)
    middleware = ChatRetryMiddleware(stage="judge", max_retries=2)
    await middleware.process(ChatContext(object(), [], {}), call_next)  # type: ignore[arg-type]
    assert attempts == 3


async def test_chat_middleware_does_not_retry_permanent_errors() -> None:
    attempts = 0

    async def call_next() -> None:
        nonlocal attempts
        attempts += 1
        raise HttpError(401)

    middleware = ChatRetryMiddleware(stage="judge", max_retries=2)
    with pytest.raises(HttpError):
        await middleware.process(ChatContext(object(), [], {}), call_next)  # type: ignore[arg-type]
    assert attempts == 1


async def test_tool_middleware_observes_without_retrying() -> None:
    attempts = 0

    async def call_next() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("tool failed")

    context = SimpleNamespace(function=SimpleNamespace(name="browser_click"))
    middleware = ToolTelemetryMiddleware(stage="browser")
    with pytest.raises(RuntimeError, match="tool failed"):
        await middleware.process(context, call_next)  # type: ignore[arg-type]
    assert attempts == 1


async def test_spans_do_not_capture_message_or_tool_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("maf_qa.middleware.TRACER", provider.get_tracer("test"))

    async def call_next() -> None:
        return None

    token = OBSERVABILITY_CONTEXT.set({"run_id": "run-1", "stage": "judge", "attempt": 2})
    try:
        context = ChatContext(object(), [], {})  # type: ignore[arg-type]
        context.metadata["prompt"] = "API_KEY=super-secret"
        await ChatRetryMiddleware(stage="judge", max_retries=0).process(context, call_next)
    finally:
        OBSERVABILITY_CONTEXT.reset(token)

    attributes = dict(exporter.get_finished_spans()[0].attributes)
    assert attributes["maf.run_id"] == "run-1"
    assert attributes["maf.stage"] == "judge"
    assert attributes["maf.attempt"] == 2
    assert "super-secret" not in repr(attributes)
