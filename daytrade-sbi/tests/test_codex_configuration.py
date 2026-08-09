from __future__ import annotations

import tomllib
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_custom_subagents_are_project_scoped_and_read_only():
    config = tomllib.loads(
        (REPOSITORY_ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")
    )

    assert config["agents"]["max_concurrent_threads_per_session"] == 2

    agent_dir = REPOSITORY_ROOT / ".codex" / "agents"
    agents = [
        tomllib.loads(path.read_text(encoding="utf-8"))
        for path in sorted(agent_dir.glob("*.toml"))
    ]
    assert {agent["name"] for agent in agents} == {
        "market_researcher",
        "source_auditor",
    }
    assert all(agent["sandbox_mode"] == "read-only" for agent in agents)


def test_daytrade_skills_require_explicit_invocation():
    skill_root = REPOSITORY_ROOT / ".agents" / "skills"

    for skill_name in ("prepare-daytrade-plan", "record-daytrade-result"):
        metadata = yaml.safe_load(
            (skill_root / skill_name / "agents" / "openai.yaml").read_text(
                encoding="utf-8"
            )
        )
        assert metadata["policy"]["allow_implicit_invocation"] is False
        assert f"${skill_name}" in metadata["interface"]["default_prompt"]
