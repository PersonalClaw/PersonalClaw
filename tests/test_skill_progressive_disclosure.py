"""Two-phase progressive skill disclosure (skill-progressive-disclosure, #29).

Phase 1 = the agent's context carries a compact INDEX; Phase 2 = the agent pulls a
full body on demand via the skill_invoke tool (which also records the use, #25)."""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.mcp_core import _call_tool_inner, _list_tools
from personalclaw.skills.loader import SkillsLoader
from personalclaw.validation import MCP_CORE_SCHEMAS


def _create_skill(base: Path, name: str, body: str) -> None:
    (base / name).mkdir(parents=True, exist_ok=True)
    (base / name / "SKILL.md").write_text(body, encoding="utf-8")


# ── the skill_invoke tool (Phase 2) ──


def test_skill_invoke_registered():
    assert "skill_invoke" in {t["name"] for t in _list_tools()}
    assert "skill_invoke" in MCP_CORE_SCHEMAS


def test_skill_invoke_returns_full_body(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _create_skill(
        skills, "tiny-url",
        "---\nname: tiny-url\ndescription: shorten urls\n---\n# Tiny URL\nStep 1. do it.",
    )
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    # usage store writes under skills_dir → temp; safe.
    out = _call_tool_inner("skill_invoke", {"name": "tiny-url"})
    assert "Step 1. do it." in out
    assert "[Skill: tiny-url]" in out
    # frontmatter stripped
    assert "description: shorten urls" not in out


def test_skill_invoke_unknown(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    out = _call_tool_inner("skill_invoke", {"name": "nope"})
    assert out.startswith("Error")


def test_skill_invoke_requires_name(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: tmp_path)
    assert _call_tool_inner("skill_invoke", {"name": ""}).startswith("Error")


def test_skill_invoke_records_use(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _create_skill(skills, "foo", "---\nname: foo\ndescription: d\n---\n# Foo\nbody")
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    # usage.py binds skills_dir at import → patch it in that namespace too.
    monkeypatch.setattr("personalclaw.skills.usage.skills_dir", lambda: skills)
    _call_tool_inner("skill_invoke", {"name": "foo"})
    from personalclaw.skills.usage import SkillUsageStore

    assert SkillUsageStore(path=skills / ".usage.json").get("foo").count == 1


# ── Phase-1 index (get_context) ──


def test_index_uses_skill_invoke_not_cat(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _create_skill(skills, "tiny-url", "---\nname: tiny-url\ndescription: shorten urls\n---\n# T\nx")
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    loader = SkillsLoader(skills_path=skills, install_builtins=False)
    ctx = loader.get_context()
    assert "skill_invoke" in ctx
    assert "cat <path>" not in ctx
    assert "tiny-url" in ctx  # indexed


def test_index_excludes_archived(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    _create_skill(skills, "auto/old", "---\nname: auto/old\ndescription: stale\nstatus: archived\n---\n# x\ny")
    _create_skill(skills, "live", "---\nname: live\ndescription: fresh\n---\n# x\ny")
    monkeypatch.setattr("personalclaw.skills.loader.skills_dir", lambda: skills)
    loader = SkillsLoader(skills_path=skills, install_builtins=False)
    ctx = loader.get_context()
    assert "live" in ctx
    assert "auto/old" not in ctx  # archived skill kept off the index


# ── turn-time threshold: index-only above N, inline at/below ──


def _builder_with_skills(tmp_path, n_matching: int):
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore

    skills = tmp_path / "skills"
    for i in range(n_matching):
        # all trigger on "deploy widget" so the surfacer returns them all
        _create_skill(
            skills, f"s{i}",
            f"---\nname: s{i}\ndescription: deploy widget {i}\ntriggers: deploy widget\n---\n# S{i}\nBODY-{i}",
        )
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=skills, install_builtins=False),
    )


def _patch_cfg(monkeypatch, *, max_triggered: int, threshold: int):
    """Load a real AppConfig, override the two skills knobs, and pin AppConfig.load."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    cfg.skills.max_triggered = max_triggered
    cfg.skills.progressive_disclosure_threshold = threshold
    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: cfg))
    return cfg


def test_above_threshold_injects_index_only(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, max_triggered=10, threshold=3)
    builder = _builder_with_skills(tmp_path, 5)
    msg, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert "INDEX only" in msg
    assert "skill_invoke" in msg
    assert "BODY-0" not in msg  # bodies NOT inlined above threshold


def test_at_threshold_inlines_bodies(tmp_path, monkeypatch):
    _patch_cfg(monkeypatch, max_triggered=10, threshold=8)
    builder = _builder_with_skills(tmp_path, 3)
    msg, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert "BODY-0" in msg  # inlined at/below threshold
    assert "INDEX only" not in msg
