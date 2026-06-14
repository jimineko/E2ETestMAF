from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from agent_framework import FunctionInvocationContext, FunctionTool

from maf_qa.codeact import (
    CodeActPolicyError,
    CodeActPolicyMiddleware,
    ToolAuditLog,
    build_codeact_provider,
    validate_codeact_program,
)
from maf_qa.middleware import OBSERVABILITY_CONTEXT


def _function(name: str, calls: list[dict[str, Any]]) -> FunctionTool:
    async def invoke(**arguments: Any) -> dict[str, Any]:
        calls.append(arguments)
        return {"ok": True, "arguments": arguments}

    property_name = "element" if name == "browser_click" else "url"
    return FunctionTool(
        name=name,
        description=f"Fake {name}",
        func=invoke,
        input_model={
            "type": "object",
            "properties": {property_name: {"type": "string"}},
            "required": [property_name],
        },
    )


@pytest.mark.parametrize(
    "program",
    [
        "import os",
        "while True:\n    pass",
        "eval('1 + 1')",
        "value.__class__",
        "def forever():\n    return forever()",
    ],
)
def test_codeact_policy_rejects_unsafe_python(program: str) -> None:
    with pytest.raises(CodeActPolicyError):
        validate_codeact_program(program, max_code_bytes=32_768)


def test_codeact_policy_accepts_bounded_tool_program() -> None:
    validate_codeact_program(
        "result = call_tool('browser_snapshot')\nprint(result)",
        max_code_bytes=32_768,
    )


async def test_wrapped_mcp_tool_enforces_origin_and_records_audit() -> None:
    calls: list[dict[str, Any]] = []
    audit = ToolAuditLog()
    provider = build_codeact_provider(
        stage="discovery",
        mcp_functions=[_function("browser_navigate", calls)],
        allowed_origins={"https://example.com"},
        allow_file_upload=False,
        allow_destructive_actions=False,
        max_code_bytes=32_768,
        max_invocations=3,
        audit_log=audit,
    )
    wrapped = provider.get_tools()[0]
    token = OBSERVABILITY_CONTEXT.set(
        {"run_id": "run-1", "stage": "discovery", "attempt": 1}
    )
    try:
        result = await wrapped.invoke(
            arguments={"url": "https://example.com/home"}, skip_parsing=True
        )
        assert result["ok"] is True
        with pytest.raises(CodeActPolicyError):
            await wrapped.invoke(
                arguments={"url": "https://attacker.invalid/"}, skip_parsing=True
            )
        with pytest.raises(CodeActPolicyError):
            await wrapped.invoke(
                arguments={"url": "//attacker.invalid/collect"}, skip_parsing=True
            )
    finally:
        OBSERVABILITY_CONTEXT.reset(token)

    assert calls == [{"url": "https://example.com/home"}]
    assert [event.success for event in audit.events] == [True, False, False]
    assert all(event.run_id == "run-1" for event in audit.events)


def test_audit_log_redacts_sensitive_arguments() -> None:
    audit = ToolAuditLog()
    audit.record(
        stage="browser",
        tool_name="browser_fill_form",
        arguments={"fields": [{"name": "password", "value": "super-secret"}]},
        success=True,
    )

    serialized = audit.serializable()
    assert "super-secret" not in str(serialized)
    assert serialized[0]["arguments"]["fields"][0]["value"] == "[REDACTED]"


async def test_execute_code_invocation_limit_is_per_stage_attempt() -> None:
    middleware = CodeActPolicyMiddleware(max_code_bytes=1024, max_invocations=1)
    function = FunctionTool(
        name="execute_code",
        description="fake",
        func=lambda code: code,
        input_model={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    )
    context = FunctionInvocationContext(function=function, arguments={"code": "print(1)"})
    called = 0

    async def call_next() -> None:
        nonlocal called
        called += 1

    token = OBSERVABILITY_CONTEXT.set(
        {"run_id": "run-1", "stage": "browser", "attempt": 1}
    )
    try:
        await middleware.process(context, call_next)
        with pytest.raises(CodeActPolicyError):
            await middleware.process(context, call_next)
    finally:
        OBSERVABILITY_CONTEXT.reset(token)
    assert called == 1


async def test_mcp_callback_is_bridged_back_to_host_event_loop() -> None:
    host_thread = threading.get_ident()
    callback_threads: list[int] = []

    async def invoke(**arguments: Any) -> dict[str, Any]:
        callback_threads.append(threading.get_ident())
        return arguments

    function = FunctionTool(
        name="browser_navigate",
        description="fake",
        func=invoke,
        input_model={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    )
    provider = build_codeact_provider(
        stage="discovery",
        mcp_functions=[function],
        allowed_origins={"https://example.com"},
        allow_file_upload=False,
        allow_destructive_actions=False,
        max_code_bytes=32_768,
        max_invocations=3,
        audit_log=ToolAuditLog(),
        host_loop=asyncio.get_running_loop(),
    )
    wrapped = provider.get_tools()[0]

    result = await asyncio.to_thread(
        lambda: asyncio.run(
            wrapped.invoke(
                arguments={"url": "https://example.com"}, skip_parsing=True
            )
        )
    )

    assert result == {"url": "https://example.com"}
    assert callback_threads == [host_thread]


async def test_wrapped_mcp_tool_rejects_destructive_clicks() -> None:
    calls: list[dict[str, Any]] = []
    provider = build_codeact_provider(
        stage="browser",
        mcp_functions=[_function("browser_click", calls)],
        allowed_origins={"https://example.com"},
        allow_file_upload=False,
        allow_destructive_actions=False,
        max_code_bytes=32_768,
        max_invocations=3,
        audit_log=ToolAuditLog(),
    )

    with pytest.raises(CodeActPolicyError, match="Destructive"):
        await provider.get_tools()[0].invoke(
            arguments={"element": "Delete account confirmation"},
            skip_parsing=True,
        )

    assert calls == []
