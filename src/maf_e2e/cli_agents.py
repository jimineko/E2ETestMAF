from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

from agent_framework import (
    AgentMiddlewareLayer,
    AgentResponse,
    AgentSession,
    BaseAgent,
    FunctionTool,
    Message,
    SessionContext,
    normalize_messages,
    normalize_tools,
)
from pydantic import BaseModel

from maf_e2e.middleware import AgentRetryMiddleware, invoke_tool_with_telemetry


class SubscriptionCLIError(RuntimeError):
    pass


def import_github_copilot_agent() -> type[Any]:
    try:
        module = importlib.import_module("agent_framework.github")
    except ImportError as exc:
        raise SubscriptionCLIError(
            "GitHub Copilot support requires `uv sync --extra cli-providers`"
        ) from exc
    return cast(type[Any], module.GitHubCopilotAgent)


def create_github_copilot_agent(
    *,
    definition: Mapping[str, Any],
    cli_path: str,
    model: str | None,
    timeout_seconds: int,
    context_providers: Sequence[Any],
    model_retries: int,
    trace_content: bool,
) -> Any:
    agent_type = import_github_copilot_agent()
    options: dict[str, Any] = {
        "cli_path": cli_path,
        "timeout": float(timeout_seconds),
    }
    if model:
        options["model"] = model
    return agent_type(
        instructions=str(definition["instructions"]),
        name=str(definition["name"]),
        description=str(definition["description"]),
        context_providers=context_providers,
        middleware=[
            AgentRetryMiddleware(
                stage=_role_from_name(str(definition["name"])),
                max_retries=model_retries,
                trace_content=trace_content,
            )
        ],
        default_options=options,
    )


class CodexRuntime:
    def __init__(self, *, cli_path: str, cwd: str) -> None:
        self.cli_path = cli_path
        self.cwd = cwd
        self._client: Any | None = None

    def client(self) -> Any:
        if self._client is None:
            try:
                module = importlib.import_module("openai_codex")
            except ImportError as exc:
                raise SubscriptionCLIError(
                    "Codex CLI support requires `uv sync --extra cli-providers`"
                ) from exc
            config = module.CodexConfig(codex_bin=self.cli_path, cwd=self.cwd)
            self._client = module.AsyncCodex(config)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


class RawCodexCLIAgent(BaseAgent):
    def __init__(
        self,
        *,
        runtime: CodexRuntime,
        instructions: str,
        name: str,
        description: str,
        model: str | None,
        timeout_seconds: float,
        max_tool_rounds: int,
        context_providers: Sequence[Any] | None = None,
        middleware: Sequence[Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            context_providers=context_providers,
            middleware=middleware,
        )
        self.runtime = runtime
        self.instructions = instructions
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_tool_rounds = max_tool_rounds

    async def run(
        self,
        messages: Any = None,
        *,
        stream: bool = False,
        session: AgentSession | None = None,
        tools: Any = None,
        options: Mapping[str, Any] | None = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
        **_: Any,
    ) -> AgentResponse[Any]:
        del function_invocation_kwargs, client_kwargs
        if stream:
            raise SubscriptionCLIError("Codex CLI streaming is not supported")
        active_session = session or self.create_session()
        input_messages = normalize_messages(messages)
        session_context, provider_options = await self._prepare_context(
            active_session, input_messages, dict(options or {})
        )
        provider_tools = provider_options.get("tools", [])
        available_tools = _function_tools([tools, provider_tools])
        response_type = (options or {}).get("response_format")
        if not isinstance(response_type, type) or not issubclass(response_type, BaseModel):
            response_type = None
        prompt = _message_text(input_messages)
        provider_instructions = str(provider_options.get("instructions", "")).strip()
        if provider_instructions:
            prompt = f"{provider_instructions}\n\n{prompt}"

        thread, replay = await self._thread(active_session)
        if replay:
            prompt = f"Recovered session transcript:\n{replay}\n\nNew request:\n{prompt}"
        transcript = active_session.state.setdefault("codex_cli_transcript", [])
        transcript.append({"role": "user", "text": prompt})
        response = await self._run_loop(
            thread=thread,
            prompt=prompt,
            tools=available_tools,
            response_type=response_type,
            transcript=transcript,
        )
        session_context._response = response
        await self._run_after_providers(session=active_session, context=session_context)
        return response

    async def _prepare_context(
        self,
        session: AgentSession,
        input_messages: list[Message],
        options: dict[str, Any],
    ) -> tuple[SessionContext, dict[str, Any]]:
        context = SessionContext(
            session_id=session.session_id,
            service_session_id=session.service_session_id,
            input_messages=input_messages,
            options=options,
        )
        for provider in self.context_providers:
            await provider.before_run(
                agent=self,  # type: ignore[arg-type]
                session=session,
                context=context,
                state=session.state.setdefault(provider.source_id, {}),
            )
        provider_options: dict[str, Any] = {}
        if context.tools:
            provider_options["tools"] = list(context.tools)
        if context.instructions:
            provider_options["instructions"] = "\n".join(context.instructions)
        return context, provider_options

    async def _thread(self, session: AgentSession) -> tuple[Any, str]:
        client = self.runtime.client()
        module = importlib.import_module("openai_codex")
        kwargs = {
            "developer_instructions": _codex_instructions(self.instructions),
            "model": self.model,
            "sandbox": module.Sandbox.read_only,
            "approval_mode": module.ApprovalMode.deny_all,
        }
        if session.service_session_id:
            try:
                return await client.thread_resume(session.service_session_id, **kwargs), ""
            except Exception:
                replay = _render_transcript(session.state.get("codex_cli_transcript"))
        else:
            replay = ""
        thread = await client.thread_start(**kwargs)
        session.service_session_id = thread.id
        return thread, replay

    async def _run_loop(
        self,
        *,
        thread: Any,
        prompt: str,
        tools: list[FunctionTool],
        response_type: type[BaseModel] | None,
        transcript: list[Any],
    ) -> AgentResponse[Any]:
        tool_map = {tool.name: tool for tool in tools}
        next_prompt = _bridge_prompt(prompt, tools, response_type)
        for round_number in range(self.max_tool_rounds + 1):
            schema = _bridge_schema(response_type, tools) if tools else _final_schema(response_type)
            async with asyncio.timeout(self.timeout_seconds):
                result = await thread.run(
                    next_prompt,
                    model=self.model,
                    output_schema=schema,
                    sandbox=importlib.import_module("openai_codex").Sandbox.read_only,
                    approval_mode=importlib.import_module("openai_codex").ApprovalMode.deny_all,
                )
            if not result.final_response:
                raise SubscriptionCLIError("Codex CLI returned no final response")
            payload = json.loads(result.final_response)
            transcript.append({"role": "assistant", "text": result.final_response})
            if not tools:
                return _agent_response(payload, response_type, result.id)
            if payload.get("action") == "final":
                return _agent_response(payload.get("final"), response_type, result.id)
            if round_number >= self.max_tool_rounds:
                raise SubscriptionCLIError(
                    f"Codex CLI tool round limit exceeded ({self.max_tool_rounds})"
                )
            tool_name = payload.get("tool_name")
            arguments = payload.get("arguments")
            if not isinstance(tool_name, str) or tool_name not in tool_map:
                raise SubscriptionCLIError(f"Codex CLI requested an unknown tool: {tool_name!r}")
            if not isinstance(arguments, Mapping):
                raise SubscriptionCLIError("Codex CLI tool arguments must be an object")
            tool_result = await invoke_tool_with_telemetry(
                tool_map[tool_name],
                arguments=dict(arguments),
                stage=_role_from_name(self.name or ""),
            )
            rendered = _render_tool_result(tool_result)
            transcript.append(
                {"role": "tool", "name": tool_name, "arguments": dict(arguments), "text": rendered}
            )
            next_prompt = (
                f"MAF tool {tool_name} returned:\n{rendered}\n\n"
                "Continue using the required response envelope."
            )
        raise AssertionError("unreachable")

class CodexCLIAgent(AgentMiddlewareLayer, RawCodexCLIAgent):  # type: ignore[misc]
    pass


def create_codex_agent(
    *,
    definition: Mapping[str, Any],
    runtime: CodexRuntime,
    model: str | None,
    timeout_seconds: float,
    max_tool_rounds: int,
    context_providers: Sequence[Any],
    model_retries: int,
    trace_content: bool,
) -> CodexCLIAgent:
    role = _role_from_name(str(definition["name"]))
    return CodexCLIAgent(
        runtime=runtime,
        instructions=str(definition["instructions"]),
        name=str(definition["name"]),
        description=str(definition["description"]),
        model=model,
        timeout_seconds=timeout_seconds,
        max_tool_rounds=max_tool_rounds,
        context_providers=context_providers,
        middleware=[
            AgentRetryMiddleware(
                stage=role,
                max_retries=model_retries,
                trace_content=trace_content,
            )
        ],
    )


def _function_tools(sources: Sequence[Any]) -> list[FunctionTool]:
    result: list[FunctionTool] = []
    seen: set[str] = set()
    for source in sources:
        for item in normalize_tools(source):
            candidates = [item]
            functions = getattr(item, "functions", None)
            if functions is not None:
                candidates = list(functions)
            for candidate in candidates:
                if isinstance(candidate, FunctionTool) and candidate.name not in seen:
                    result.append(candidate)
                    seen.add(candidate.name)
    return result


def _message_text(messages: Sequence[Message]) -> str:
    return "\n".join(message.text for message in messages if message.text).strip()


def _bridge_prompt(
    prompt: str, tools: Sequence[FunctionTool], response_type: type[BaseModel] | None
) -> str:
    declarations = [
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters()}
        for tool in tools
    ]
    final_name = response_type.__name__ if response_type is not None else "JSON value"
    return (
        f"{prompt}\n\n"
        "You may only interact with the application by returning one MAF tool request. "
        "Do not use Codex shell, file, network, or browser capabilities. "
        f"When finished, return the final {final_name}.\n"
        f"Available MAF tools: {json.dumps(declarations, ensure_ascii=False)}"
    )


def _bridge_schema(
    response_type: type[BaseModel] | None, tools: Sequence[FunctionTool]
) -> dict[str, Any]:
    final = _final_schema(response_type)
    definitions = final.pop("$defs", None)
    argument_schemas = [tool.parameters() for tool in tools]
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["tool", "final"]},
            "tool_name": {
                "anyOf": [
                    {"type": "string", "enum": [tool.name for tool in tools]},
                    {"type": "null"},
                ]
            },
            "arguments": {"anyOf": [*argument_schemas, {"type": "null"}]},
            "final": {"anyOf": [final, {"type": "null"}]},
        },
        "required": ["action", "tool_name", "arguments", "final"],
        "additionalProperties": False,
    }
    if definitions:
        schema["$defs"] = definitions
    return schema


def _final_schema(response_type: type[BaseModel] | None) -> dict[str, Any]:
    if response_type is None:
        return {"type": "object", "additionalProperties": True}
    return response_type.model_json_schema()


def _agent_response(
    payload: Any, response_type: type[BaseModel] | None, response_id: str
) -> AgentResponse[Any]:
    value = response_type.model_validate(payload) if response_type is not None else payload
    text = json.dumps(payload, ensure_ascii=False)
    return AgentResponse(
        messages=Message("assistant", [text]),
        response_id=response_id,
        value=value,
    )


def _render_tool_result(value: Any) -> str:
    if isinstance(value, list):
        texts = [getattr(item, "text", None) for item in value]
        if any(texts):
            return "\n".join(str(text) for text in texts if text)
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except TypeError:
        return str(value)


def _render_transcript(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "\n".join(json.dumps(item, default=str, ensure_ascii=False) for item in value[-30:])


def _codex_instructions(instructions: str) -> str:
    return (
        f"{instructions}\n\n"
        "Security boundary: never use built-in shell, filesystem mutation, browser, or network "
        "capabilities. Use only the MAF tool envelope supplied in each request."
    )


def _role_from_name(name: str) -> str:
    return {
        "DiscoveryAgent": "discovery",
        "TestGenerator": "generator",
        "PlaywrightExecutor": "browser",
        "AssertJudge": "judge",
        "SafetyReviewer": "safety",
    }.get(name, name or "unknown")
