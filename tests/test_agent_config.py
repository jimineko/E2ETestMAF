from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from maf_qa.agent_config import AGENT_FILES, load_agent_set

ROOT = Path(__file__).parents[1]


class FakeClient:
    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not called")

    async def get_streaming_response(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not called")


def test_loads_five_declarative_agents_with_shared_client() -> None:
    client = FakeClient()
    agents = load_agent_set(
        ROOT / "agents",
        client,  # type: ignore[arg-type]
        skill_paths=[ROOT / "skills" / "sample-app"],
        model_retries=2,
    )

    assert agents.discovery.client is client  # type: ignore[attr-defined]
    assert agents.browser.client is client  # type: ignore[attr-defined]
    assert len(agents.discovery.context_providers) == 1  # type: ignore[attr-defined]
    assert len(agents.generator.context_providers) == 1  # type: ignore[attr-defined]
    assert agents.browser.context_providers == []  # type: ignore[attr-defined]
    assert agents.judge.context_providers == []  # type: ignore[attr-defined]
    assert agents.safety.context_providers == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("tools", [], "Forbidden"),
        ("outputSchema", {"type": "object"}, "Forbidden"),
        ("model", {"provider": "OpenAI"}, "model.options"),
        ("description", "=Env.SECRET", "PowerFx"),
    ],
)
def test_rejects_forbidden_declarative_fields(
    tmp_path: Path, field: str, value: object, match: str
) -> None:
    config_dir = _copy_agents(tmp_path)
    path = config_dir / AGENT_FILES["discovery"]
    definition = yaml.safe_load(path.read_text(encoding="utf-8"))
    definition[field] = value
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        load_agent_set(
            config_dir,
            FakeClient(),  # type: ignore[arg-type]
            skill_paths=[],
            model_retries=0,
        )


def test_rejects_duplicate_names_and_skill_scripts(tmp_path: Path) -> None:
    config_dir = _copy_agents(tmp_path)
    judge_path = config_dir / AGENT_FILES["judge"]
    judge = yaml.safe_load(judge_path.read_text(encoding="utf-8"))
    judge["name"] = "DiscoveryAgent"
    judge_path.write_text(yaml.safe_dump(judge), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_agent_set(
            config_dir,
            FakeClient(),  # type: ignore[arg-type]
            skill_paths=[],
            model_retries=0,
        )

    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Test", encoding="utf-8")
    with pytest.raises(ValueError, match="scripts"):
        load_agent_set(
            ROOT / "agents",
            FakeClient(),  # type: ignore[arg-type]
            skill_paths=[skill],
            model_retries=0,
        )


def _copy_agents(tmp_path: Path) -> Path:
    config_dir = tmp_path / "agents"
    shutil.copytree(ROOT / "agents", config_dir)
    return config_dir
