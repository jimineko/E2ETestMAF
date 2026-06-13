from __future__ import annotations

import json
from typing import Any, Protocol

from agent_framework import AgentResponse, AgentSession
from pydantic import BaseModel


class AgentRunner(Protocol):
    def create_session(self, *, session_id: str | None = None) -> AgentSession: ...


async def run_structured[OutputT: BaseModel](
    agent: AgentRunner,
    prompt: str,
    output_type: type[OutputT],
    session: AgentSession,
) -> OutputT:
    response: AgentResponse[Any] = await agent.run(  # type: ignore[attr-defined]
        prompt,
        session=session,
        options={"response_format": output_type},
    )
    value = response.value
    if isinstance(value, output_type):
        return value
    if value is not None:
        return output_type.model_validate(value)
    return output_type.model_validate_json(_extract_json(response.text))


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1])
    try:
        json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError("Agent did not return valid structured JSON") from exc
    return stripped
