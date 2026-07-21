"""Tests for personalclaw.orchestrator_skill — orchestrator SKILL.md generation."""

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@dataclass
class _FakeAgent:
    name: str
    description: str = ""
    filename: str = ""
    model: str = ""
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    source: str = "builtin"
    package: str = ""


@pytest.fixture()
def skills_loader(tmp_path):
    loader = MagicMock()
    loader._dir = tmp_path
    return loader


def _read_skill(tmp_path: Path) -> str:
    return (tmp_path / "orchestrator" / "SKILL.md").read_text(encoding="utf-8")


def _config_with(agents):
    """Build a fake AppConfig whose .agents maps name -> agent record.

    generate_orchestrator_skill reads agents from AppConfig.load().agents
    (a dict keyed by name, each value exposing a .description attribute).
    """
    cfg = MagicMock()
    cfg.agents = {a.name: a for a in agents}
    return cfg


@patch("personalclaw.config.loader.AppConfig.load")
@patch("personalclaw.orchestrator_skill.load_all")
@patch("personalclaw.orchestrator_skill.load")
@patch("personalclaw.orchestrator_skill.save")
def test_includes_agents_from_metadata(
    mock_save, mock_load, mock_load_all, mock_config_load, skills_loader
):
    from personalclaw.orchestrator_skill import generate_orchestrator_skill

    mock_config_load.return_value = _config_with(
        [_FakeAgent(name="code-reviewer", description="Reviews code")]
    )
    mock_load.return_value = "Use for CR reviews and security audits."
    mock_load_all.return_value = {"code-reviewer": "Use for CR reviews and security audits."}
    generate_orchestrator_skill(skills_loader)
    content = _read_skill(skills_loader._dir)
    assert "code-reviewer" in content
    assert "Use for CR reviews and security audits." in content


@patch("personalclaw.config.loader.AppConfig.load")
@patch("personalclaw.orchestrator_skill.load_all")
@patch("personalclaw.orchestrator_skill.load")
@patch("personalclaw.orchestrator_skill.save")
def test_auto_seeds_metadata_from_description(
    mock_save, mock_load, mock_load_all, mock_config_load, skills_loader
):
    from personalclaw.orchestrator_skill import generate_orchestrator_skill

    mock_config_load.return_value = _config_with(
        [_FakeAgent(name="code-reviewer", description="Reviews code quality")]
    )
    mock_load.return_value = ""  # no metadata file
    mock_load_all.return_value = {}
    generate_orchestrator_skill(skills_loader)
    mock_save.assert_called_once_with("code-reviewer", "Reviews code quality")


@patch("personalclaw.config.loader.AppConfig.load")
@patch("personalclaw.orchestrator_skill.load_all")
@patch("personalclaw.orchestrator_skill.load")
@patch("personalclaw.orchestrator_skill.save")
def test_excludes_personalclaw_and_orchestrator(
    mock_save, mock_load, mock_load_all, mock_config_load, skills_loader
):
    from personalclaw.orchestrator_skill import generate_orchestrator_skill

    mock_config_load.return_value = _config_with(
        [
            _FakeAgent(name="personalclaw", description="General"),
            _FakeAgent(name="personalclaw-orchestrator", description="Orchestrator"),
            _FakeAgent(name="code-reviewer", description="Reviews code"),
        ]
    )
    mock_load.return_value = ""
    mock_load_all.return_value = {}
    generate_orchestrator_skill(skills_loader)
    content = _read_skill(skills_loader._dir)
    assert "personalclaw-orchestrator" not in content
    # personalclaw should not appear as a heading in roster
    assert "### personalclaw" not in content
    assert "### code-reviewer" in content


@patch("personalclaw.config.loader.AppConfig.load")
@patch("personalclaw.orchestrator_skill.load_all")
@patch("personalclaw.orchestrator_skill.load")
@patch("personalclaw.orchestrator_skill.save")
def test_skill_has_always_true_and_delegation_guidelines(
    mock_save, mock_load, mock_load_all, mock_config_load, skills_loader
):
    from personalclaw.orchestrator_skill import generate_orchestrator_skill

    mock_config_load.return_value = _config_with(
        [_FakeAgent(name="code-reviewer", description="Reviews code")]
    )
    mock_load.return_value = ""
    mock_load_all.return_value = {}
    generate_orchestrator_skill(skills_loader)
    content = _read_skill(skills_loader._dir)
    assert "always: true" in content
    assert "When to delegate" in content
    assert "When NOT to delegate" in content
    assert "Effort scaling" in content
    assert "subagent_run" in content


@patch("personalclaw.config.loader.AppConfig.load")
@patch("personalclaw.orchestrator_skill.load_all")
@patch("personalclaw.orchestrator_skill.load")
@patch("personalclaw.orchestrator_skill.save")
def test_removes_legacy_conductor_skill_dir(
    mock_save, mock_load, mock_load_all, mock_config_load, skills_loader
):
    """The feature was renamed conductor → orchestrator. Generating the
    orchestrator skill must delete any pre-rename ``conductor/`` dir so the stale
    always-loaded SKILL.md doesn't double-inject the routing table."""
    from personalclaw.orchestrator_skill import generate_orchestrator_skill

    # Seed a legacy conductor/ skill dir on disk.
    legacy = skills_loader._dir / "conductor"
    legacy.mkdir()
    (legacy / "SKILL.md").write_text("stale", encoding="utf-8")

    mock_config_load.return_value = _config_with(
        [_FakeAgent(name="code-reviewer", description="Reviews code")]
    )
    mock_load.return_value = ""
    mock_load_all.return_value = {}
    generate_orchestrator_skill(skills_loader)

    assert not legacy.exists(), "legacy conductor/ dir should be removed"
    assert (skills_loader._dir / "orchestrator" / "SKILL.md").is_file()
