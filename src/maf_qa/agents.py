from __future__ import annotations

import json
from typing import Any, Protocol

from agent_framework import AgentResponse, AgentSession
from pydantic import BaseModel, ValidationError

from maf_qa.middleware import OBSERVABILITY_CONTEXT


class StructuredOutputError(ValueError):
    pass


class AgentRunner(Protocol):
    def create_session(self, *, session_id: str | None = None) -> AgentSession: ...


async def run_structured[OutputT: BaseModel](
    agent: AgentRunner,
    prompt: str,
    output_type: type[OutputT],
    session: AgentSession,
    *,
    retries: int = 1,
    tools: list[Any] | None = None,
    run_id: str = "unknown",
    stage: str = "unknown",
    attempt: int = 1,
) -> OutputT:
    current_prompt = prompt
    current_tools = tools
    for repair_attempt in range(retries + 1):
        run_kwargs: dict[str, Any] = {
            "session": session,
            "options": {"response_format": output_type},
        }
        if current_tools is not None:
            run_kwargs["tools"] = current_tools
        token = OBSERVABILITY_CONTEXT.set({"run_id": run_id, "stage": stage, "attempt": attempt})
        try:
            response: AgentResponse[Any] = await agent.run(  # type: ignore[attr-defined]
                current_prompt,
                **run_kwargs,
            )
        finally:
            OBSERVABILITY_CONTEXT.reset(token)
        try:
            value = response.value
            if isinstance(value, output_type):
                return value
            if value is not None:
                return output_type.model_validate(value)
            return output_type.model_validate_json(_extract_json(response.text))
        except (ValidationError, ValueError, TypeError) as exc:
            if repair_attempt >= retries:
                raise StructuredOutputError(
                    f"Agent output could not be validated as {output_type.__name__}"
                ) from exc
            current_prompt = (
                "Return the previous result again as valid JSON matching this schema. "
                "Do not repeat any browser action or call tools.\n"
                f"Schema: {output_type.model_json_schema()}"
            )
            current_tools = []
    raise AssertionError("unreachable")


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
