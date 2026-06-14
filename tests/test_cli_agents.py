from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from agent_framework import FunctionTool
from pydantic import BaseModel

from maf_e2e.cli_agents import CodexCLIAgent, SubscriptionCLIError


class Result(BaseModel):
    passed: bool


class FakeThread:
    def __init__(self, *responses: dict[str, Any], thread_id: str = "thread-1") -> None:
        self.id = thread_id
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def run(self, prompt: str, **kwargs: Any) -> Any:
        self.calls.append({"prompt": prompt, **kwargs})
        payload = self.responses.pop(0)
        return SimpleNamespace(
            id=f"turn-{len(self.calls)}",
            final_response=json.dumps(payload),
        )


class FakeClient:
    def __init__(self, thread: FakeThread, *, resume_error: Exception | None = None) -> None:
        self.thread = thread
        self.resume_error = resume_error
        self.started = 0
        self.resumed: list[str] = []

    async def thread_start(self, **kwargs: Any) -> FakeThread:
        del kwargs
        self.started += 1
        return self.thread

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> FakeThread:
        del kwargs
        self.resumed.append(thread_id)
        if self.resume_error is not None:
            raise self.resume_error
        return self.thread


class FakeRuntime:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    def client(self) -> FakeClient:
        return self._client


def make_agent(
    client: FakeClient, *, max_tool_rounds: int = 2, timeout_seconds: float = 10
) -> CodexCLIAgent:
    return CodexCLIAgent(
        runtime=FakeRuntime(client),  # type: ignore[arg-type]
        instructions="Run the E2E stage.",
        name="PlaywrightExecutor",
        description="Browser executor",
        model=None,
        timeout_seconds=timeout_seconds,
        max_tool_rounds=max_tool_rounds,
        context_providers=[],
        middleware=[],
    )


async def test_codex_returns_native_structured_value() -> None:
    thread = FakeThread({"passed": True})
    agent = make_agent(FakeClient(thread))
    session = agent.create_session(session_id="test")

    response = await agent.run(
        "Judge this run",
        session=session,
        options={"response_format": Result},
    )

    assert response.value == Result(passed=True)
    assert session.service_session_id == "thread-1"
    assert session.state["codex_cli_transcript"]


async def test_codex_invokes_maf_tool_then_returns_final() -> None:
    called: list[str] = []

    async def snapshot() -> dict[str, bool]:
        called.append("snapshot")
        return {"visible": True}

    tool = FunctionTool(
        name="browser_snapshot",
        description="Take a snapshot",
        func=snapshot,
        input_model={"type": "object", "properties": {}, "additionalProperties": False},
    )
    thread = FakeThread(
        {
            "action": "tool",
            "tool_name": "browser_snapshot",
            "arguments": {},
            "final": None,
        },
        {"action": "final", "tool_name": None, "arguments": None, "final": {"passed": True}},
    )
    agent = make_agent(FakeClient(thread))

    response = await agent.run(
        "Inspect the page",
        session=agent.create_session(),
        tools=[tool],
        options={"response_format": Result},
    )

    assert called == ["snapshot"]
    assert response.value == Result(passed=True)
    assert len(thread.calls) == 2


async def test_codex_rejects_unknown_tool() -> None:
    thread = FakeThread(
        {"action": "tool", "tool_name": "shell", "arguments": {}, "final": None}
    )
    agent = make_agent(FakeClient(thread))

    with pytest.raises(SubscriptionCLIError, match="unknown tool"):
        await agent.run(
            "Inspect",
            session=agent.create_session(),
            tools=[
                FunctionTool(
                    name="browser_snapshot",
                    func=lambda: "ok",
                    input_model={"type": "object", "properties": {}},
                )
            ],
            options={"response_format": Result},
        )


async def test_codex_rejects_invalid_tool_arguments() -> None:
    tool = FunctionTool(
        name="browser_navigate",
        func=lambda url: url,
        input_model={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    )
    thread = FakeThread(
        {"action": "tool", "tool_name": "browser_navigate", "arguments": {}, "final": None}
    )

    with pytest.raises(TypeError, match="browser_navigate"):
        await make_agent(FakeClient(thread)).run(
            "Navigate", tools=[tool], options={"response_format": Result}
        )


async def test_codex_enforces_tool_round_limit() -> None:
    tool = FunctionTool(
        name="browser_snapshot",
        func=lambda: "ok",
        input_model={"type": "object", "properties": {}},
    )
    tool_call = {
        "action": "tool",
        "tool_name": "browser_snapshot",
        "arguments": {},
        "final": None,
    }
    thread = FakeThread(tool_call, tool_call)

    with pytest.raises(SubscriptionCLIError, match="tool round limit"):
        await make_agent(FakeClient(thread), max_tool_rounds=1).run(
            "Inspect", tools=[tool], options={"response_format": Result}
        )


async def test_codex_turn_timeout_is_enforced() -> None:
    class SlowThread(FakeThread):
        async def run(self, prompt: str, **kwargs: Any) -> Any:
            del prompt, kwargs
            await asyncio.sleep(0.1)
            raise AssertionError("timeout was not enforced")

    with pytest.raises(TimeoutError):
        await make_agent(
            FakeClient(SlowThread({"passed": True})), timeout_seconds=0.01
        ).run("Judge", options={"response_format": Result})


async def test_codex_rebuilds_missing_thread_from_transcript() -> None:
    thread = FakeThread({"passed": True}, thread_id="thread-new")
    client = FakeClient(thread, resume_error=RuntimeError("missing"))
    agent = make_agent(client)
    session = agent.get_session("thread-old")
    session.state["codex_cli_transcript"] = [{"role": "assistant", "text": "old result"}]

    await agent.run("Continue", session=session, options={"response_format": Result})

    assert client.resumed == ["thread-old"]
    assert client.started == 1
    assert session.service_session_id == "thread-new"
    assert "Recovered session transcript" in thread.calls[0]["prompt"]
