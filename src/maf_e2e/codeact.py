from __future__ import annotations

import ast
import asyncio
import dataclasses
import importlib
import json
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_framework import (
    AgentSession,
    FunctionInvocationContext,
    FunctionMiddleware,
    FunctionTool,
    SessionContext,
)
from agent_framework_hyperlight import HyperlightCodeActProvider

from maf_e2e.middleware import OBSERVABILITY_CONTEXT

DISCOVERY_TOOL_NAMES = {
    "browser_close",
    "browser_console_messages",
    "browser_hover",
    "browser_navigate",
    "browser_navigate_back",
    "browser_network_requests",
    "browser_snapshot",
    "browser_tabs",
    "browser_take_screenshot",
    "browser_wait_for",
}

BROWSER_TOOL_NAMES = {
    "browser_click",
    "browser_close",
    "browser_console_messages",
    "browser_drag",
    "browser_drop",
    "browser_fill_form",
    "browser_handle_dialog",
    "browser_hover",
    "browser_navigate",
    "browser_navigate_back",
    "browser_network_request",
    "browser_network_requests",
    "browser_press_key",
    "browser_resize",
    "browser_select_option",
    "browser_snapshot",
    "browser_start_tracing",
    "browser_stop_tracing",
    "browser_tabs",
    "browser_take_screenshot",
    "browser_type",
    "browser_wait_for",
}

FORBIDDEN_CALLS = {"compile", "eval", "exec", "globals", "locals", "open", "__import__"}
SENSITIVE_ARGUMENT_KEYS = {"authorization", "password", "secret", "text", "token", "value"}
DESTRUCTIVE_ACTION_TERMS = {
    "cancel subscription",
    "close account",
    "delete",
    "destroy",
    "drop database",
    "erase",
    "factory reset",
    "remove account",
    "revoke access",
    "terminate",
}


class CodeActUnavailable(RuntimeError):
    pass


class CodeActPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ToolAuditEvent:
    timestamp: datetime
    run_id: str
    stage: str
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "run_id": self.run_id,
            "stage": self.stage,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "success": self.success,
            "error_type": self.error_type,
        }


@dataclass
class ToolAuditLog:
    events: list[ToolAuditEvent] = field(default_factory=list)
    active_run_ids: dict[str, str] = field(default_factory=dict)

    def bind_run(self, *, stage: str, run_id: str) -> None:
        self.active_run_ids[stage] = run_id

    def record(
        self,
        *,
        stage: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        success: bool,
        error: BaseException | None = None,
    ) -> None:
        metadata = OBSERVABILITY_CONTEXT.get() or {}
        self.events.append(
            ToolAuditEvent(
                timestamp=datetime.now(UTC),
                run_id=str(metadata.get("run_id") or self.active_run_ids.get(stage, "unknown")),
                stage=stage,
                tool_name=tool_name,
                arguments=_redact_arguments(dict(arguments)),
                success=success,
                error_type=type(error).__name__ if error is not None else None,
            )
        )

    def serializable(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self.events]


class CodeActPolicyMiddleware(FunctionMiddleware):
    def __init__(self, *, max_code_bytes: int, max_invocations: int) -> None:
        self.max_code_bytes = max_code_bytes
        self.max_invocations = max_invocations
        self._invocations: dict[tuple[str, str, int], int] = {}

    async def process(self, context: FunctionInvocationContext, call_next: Any) -> None:
        if context.function.name != "execute_code":
            await call_next()
            return

        arguments = context.arguments if isinstance(context.arguments, Mapping) else {}
        code = arguments.get("code")
        if not isinstance(code, str):
            raise CodeActPolicyError("execute_code requires a string code argument")
        validate_codeact_program(code, max_code_bytes=self.max_code_bytes)

        metadata = OBSERVABILITY_CONTEXT.get() or {}
        key = (
            str(metadata.get("run_id", "unknown")),
            str(metadata.get("stage", "unknown")),
            int(metadata.get("attempt", 1)),
        )
        count = self._invocations.get(key, 0) + 1
        if count > self.max_invocations:
            raise CodeActPolicyError(
                f"execute_code invocation limit exceeded ({count}>{self.max_invocations})"
            )
        self._invocations[key] = count
        await call_next()


class AuditedHyperlightCodeActProvider(HyperlightCodeActProvider):
    def __init__(
        self,
        *,
        stage: str,
        audit_log: ToolAuditLog,
        tools: Sequence[FunctionTool],
    ) -> None:
        super().__init__(tools=tools, approval_mode="never_require")
        self.stage = stage
        self.audit_log = audit_log

    def create_run_tool(self) -> FunctionTool:
        return self._execute_code_tool.create_run_tool()

    async def before_run(
        self,
        *,
        agent: Any,
        session: AgentSession | None,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        metadata = OBSERVABILITY_CONTEXT.get() or {}
        self.audit_log.bind_run(
            stage=self.stage,
            run_id=str(metadata.get("run_id", "unknown")),
        )
        await super().before_run(agent=agent, session=session, context=context, state=state)


def validate_codeact_program(code: str, *, max_code_bytes: int) -> None:
    if len(code.encode("utf-8")) > max_code_bytes:
        raise CodeActPolicyError(f"CodeAct program exceeds {max_code_bytes} bytes")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise CodeActPolicyError("CodeAct program is not valid Python") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise CodeActPolicyError("Imports are not allowed in CodeAct programs")
        if isinstance(node, ast.While):
            raise CodeActPolicyError("while loops are not allowed in CodeAct programs")
        if isinstance(node, (ast.AsyncFor, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            raise CodeActPolicyError("User-defined control structures are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise CodeActPolicyError("Dunder attribute access is not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise CodeActPolicyError("Dunder names are not allowed")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALLS
        ):
            raise CodeActPolicyError(f"{node.func.id}() is not allowed")


def preflight_hyperlight(*, require_kvm: bool = True) -> None:
    machine = platform.machine().lower()
    if sys.platform != "linux" or machine not in {"x86_64", "amd64"}:
        raise CodeActUnavailable("Hyperlight requires Linux x86_64")
    if sys.version_info >= (3, 14):
        raise CodeActUnavailable("Hyperlight backend wheels require Python 3.13 or earlier")
    libc_name, libc_version = platform.libc_ver()
    if libc_name != "glibc" or _version_tuple(libc_version) < (2, 34):
        raise CodeActUnavailable("Hyperlight requires glibc 2.34 or later")
    if require_kvm:
        kvm = Path("/dev/kvm")
        if not kvm.exists():
            raise CodeActUnavailable("Hyperlight requires /dev/kvm")
        if not os.access(kvm, os.R_OK | os.W_OK):
            raise CodeActUnavailable("Hyperlight requires read/write access to /dev/kvm")
    try:
        from hyperlight_sandbox import Sandbox

        importlib.import_module("hyperlight_sandbox_backend_wasm")
    except ImportError as exc:
        raise CodeActUnavailable("Hyperlight Wasm backend is not installed") from exc

    try:
        sandbox = Sandbox(backend="wasm", module="python_guest.path")
        result = sandbox.run("print(6 * 7)")
    except Exception as exc:
        raise CodeActUnavailable("Hyperlight sandbox smoke test failed") from exc
    if not result.success or result.stdout.strip() != "42":
        raise CodeActUnavailable("Hyperlight sandbox smoke test returned an invalid result")


def build_codeact_provider(
    *,
    stage: str,
    mcp_functions: Sequence[FunctionTool],
    allowed_origins: set[str],
    allow_file_upload: bool,
    allow_destructive_actions: bool,
    max_code_bytes: int,
    max_invocations: int,
    audit_log: ToolAuditLog,
    host_loop: asyncio.AbstractEventLoop | None = None,
) -> AuditedHyperlightCodeActProvider:
    allowed_names = DISCOVERY_TOOL_NAMES if stage == "discovery" else BROWSER_TOOL_NAMES
    if allow_file_upload and stage == "browser":
        allowed_names = {*allowed_names, "browser_file_upload"}
    tools = [
        _wrap_mcp_function(
            function,
            stage=stage,
            allowed_origins=allowed_origins,
            allow_file_upload=allow_file_upload,
            allow_destructive_actions=allow_destructive_actions,
            audit_log=audit_log,
            host_loop=host_loop,
        )
        for function in mcp_functions
        if function.name in allowed_names
    ]
    if not tools:
        raise CodeActUnavailable(f"No Playwright MCP tools were available for {stage}")
    provider = AuditedHyperlightCodeActProvider(stage=stage, audit_log=audit_log, tools=tools)
    provider.policy_middleware = CodeActPolicyMiddleware(  # type: ignore[attr-defined]
        max_code_bytes=max_code_bytes,
        max_invocations=max_invocations,
    )
    return provider


def _wrap_mcp_function(
    function: FunctionTool,
    *,
    stage: str,
    allowed_origins: set[str],
    allow_file_upload: bool,
    allow_destructive_actions: bool,
    audit_log: ToolAuditLog,
    host_loop: asyncio.AbstractEventLoop | None,
) -> FunctionTool:
    async def invoke_mcp(**arguments: Any) -> Any:
        error: BaseException | None = None
        try:
            _validate_mcp_call(
                function.name,
                arguments,
                allowed_origins=allowed_origins,
                allow_file_upload=allow_file_upload,
                allow_destructive_actions=allow_destructive_actions,
            )
            current_loop = asyncio.get_running_loop()
            if host_loop is not None and current_loop is not host_loop:
                future = asyncio.run_coroutine_threadsafe(
                    function.invoke(arguments=arguments, skip_parsing=True), host_loop
                )
                result = await asyncio.wrap_future(future)
            else:
                result = await function.invoke(arguments=arguments, skip_parsing=True)
            return _json_safe(result)
        except BaseException as exc:
            error = exc
            raise
        finally:
            audit_log.record(
                stage=stage,
                tool_name=function.name,
                arguments=arguments,
                success=error is None,
                error=error,
            )

    return FunctionTool(
        name=function.name,
        description=function.description,
        approval_mode="never_require",
        func=invoke_mcp,
        input_model=function.parameters(),
    )


def _validate_mcp_call(
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    allowed_origins: set[str],
    allow_file_upload: bool,
    allow_destructive_actions: bool,
) -> None:
    if tool_name == "browser_file_upload" and not allow_file_upload:
        raise CodeActPolicyError("Browser file upload is disabled")
    if (
        tool_name == "browser_click"
        and not allow_destructive_actions
        and _contains_destructive_intent(arguments)
    ):
        raise CodeActPolicyError("Destructive browser actions are disabled")
    for key, value in arguments.items():
        if key.lower() not in {"url", "uri", "origin"} or not isinstance(value, str):
            continue
        parsed = urlparse(value)
        if value.startswith("//"):
            raise CodeActPolicyError("Scheme-relative URLs are not allowed")
        if not parsed.scheme and not parsed.netloc:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise CodeActPolicyError(f"Unsupported navigation URL: {value}")
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        if origin not in allowed_origins:
            raise CodeActPolicyError(f"Origin is not allowed: {origin}")


def _contains_destructive_intent(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.casefold()
        return any(term in lowered for term in DESTRUCTIVE_ACTION_TERMS)
    if isinstance(value, Mapping):
        return any(_contains_destructive_intent(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_destructive_intent(item) for item in value)
    return False


def normalize_origin(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid HTTP(S) origin: {value}")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in value.split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    return tuple(parts)


def _redact_arguments(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.lower() in SENSITIVE_ARGUMENT_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_arguments(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_arguments(item) for item in value]
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _json_safe(dataclasses.asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _json_safe(to_dict())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value
