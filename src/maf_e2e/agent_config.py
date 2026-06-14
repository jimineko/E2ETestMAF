from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from agent_framework import Agent, FunctionMiddleware, SkillsProvider, SupportsChatGetResponse
from agent_framework.declarative import AgentFactory

from maf_e2e.middleware import ChatRetryMiddleware, ToolTelemetryMiddleware
from maf_e2e.workflow import AgentSet

AGENT_FILES = {
    "discovery": "discovery.yaml",
    "generator": "generator.yaml",
    "browser": "browser.yaml",
    "judge": "judge.yaml",
    "safety": "safety.yaml",
}
ALLOWED_TOP_LEVEL = {"kind", "name", "description", "instructions", "model"}
ALLOWED_MODEL_KEYS = {"options"}
ALLOWED_MODEL_OPTIONS = {
    "frequencyPenalty",
    "maxOutputTokens",
    "presencePenalty",
    "seed",
    "temperature",
    "topK",
    "topP",
    "stopSequences",
    "allowMultipleToolCalls",
}
EXPECTED_NAMES = {
    "DiscoveryAgent",
    "TestGenerator",
    "PlaywrightExecutor",
    "AssertJudge",
    "SafetyReviewer",
}


def load_agent_set(
    config_dir: Path,
    client: SupportsChatGetResponse[Any],
    *,
    skill_paths: list[Path],
    model_retries: int,
    trace_content: bool = False,
    tool_middleware: FunctionMiddleware | None = None,
) -> AgentSet:
    definitions = load_agent_definitions(config_dir)
    return build_chat_agent_set(
        definitions,
        client,
        skill_paths=skill_paths,
        model_retries=model_retries,
        trace_content=trace_content,
        tool_middleware=tool_middleware,
    )


def build_chat_agent_set(
    definitions: dict[str, dict[str, Any]],
    client: SupportsChatGetResponse[Any],
    *,
    skill_paths: list[Path],
    model_retries: int,
    trace_content: bool = False,
    tool_middleware: FunctionMiddleware | None = None,
) -> AgentSet:

    factory = AgentFactory(client=client, safe_mode=True)
    skills = _load_skills(skill_paths)
    agents: dict[str, Agent] = {}
    for role, definition in definitions.items():
        agent = factory.create_agent_from_dict(definition)
        middleware: list[Any] = [
            ChatRetryMiddleware(
                stage=role,
                max_retries=model_retries,
                trace_content=trace_content,
            )
        ]
        if tool_middleware is not None:
            middleware.append(tool_middleware)
        elif role in {"discovery", "browser"}:
            middleware.append(ToolTelemetryMiddleware(stage=role))
        agent.middleware = middleware
        if skills is not None and role in {"discovery", "generator"}:
            agent.context_providers.append(skills)
        agents[role] = agent
    return AgentSet(**agents)


def load_agent_definitions(config_dir: Path) -> dict[str, dict[str, Any]]:
    definitions = {
        role: _load_definition(config_dir / filename) for role, filename in AGENT_FILES.items()
    }
    names = [str(definition.get("name", "")) for definition in definitions.values()]
    if len(set(names)) != len(names):
        raise ValueError("Agent YAML names must be unique")
    if set(names) != EXPECTED_NAMES:
        raise ValueError(f"Agent YAML names must be exactly {sorted(EXPECTED_NAMES)}")
    return definitions


def load_skills(paths: list[Path]) -> SkillsProvider | None:
    return _load_skills(paths)


def _load_definition(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Missing agent definition: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Agent definition must be a mapping: {path}")
    unexpected = set(raw) - ALLOWED_TOP_LEVEL
    if unexpected:
        raise ValueError(f"Forbidden agent YAML fields in {path.name}: {sorted(unexpected)}")
    if raw.get("kind") != "Prompt":
        raise ValueError(f"Agent kind must be Prompt: {path.name}")
    for required in ("name", "description", "instructions"):
        if not isinstance(raw.get(required), str) or not str(raw[required]).strip():
            raise ValueError(f"Agent field {required!r} is required: {path.name}")
    model = raw.get("model")
    if model is not None:
        if not isinstance(model, dict) or set(model) - ALLOWED_MODEL_KEYS:
            raise ValueError(f"Only model.options is allowed in {path.name}")
        options = model.get("options")
        if options is not None and not isinstance(options, dict):
            raise ValueError(f"model.options must be a mapping in {path.name}")
        if isinstance(options, dict) and set(options) - ALLOWED_MODEL_OPTIONS:
            raise ValueError(
                f"Unsupported model.options in {path.name}: "
                f"{sorted(set(options) - ALLOWED_MODEL_OPTIONS)}"
            )
    if _contains_powerfx(raw):
        raise ValueError(f"PowerFx expressions are forbidden in {path.name}")
    return raw


def _contains_powerfx(value: object) -> bool:
    if isinstance(value, str):
        return value.lstrip().startswith("=")
    if isinstance(value, dict):
        return any(_contains_powerfx(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_powerfx(item) for item in value)
    return False


def _load_skills(paths: list[Path]) -> SkillsProvider | None:
    if not paths:
        return None
    resolved: list[Path] = []
    for path in paths:
        if not path.is_dir() or not (path / "SKILL.md").is_file():
            raise ValueError(f"Skill path must contain SKILL.md: {path}")
        scripts_dir = path / "scripts"
        if scripts_dir.exists():
            raise ValueError(f"Read-only skills cannot contain scripts/: {path}")
        unexpected_dirs = [
            child.name for child in path.iterdir() if child.is_dir() and child.name != "references"
        ]
        if unexpected_dirs:
            raise ValueError(
                f"Read-only skills only allow references/: {path} ({sorted(unexpected_dirs)})"
            )
        resolved.append(path.resolve())
    return SkillsProvider.from_paths(
        resolved,
        resource_directories=["references"],
        script_directories=[],
        script_extensions=(),
    )
