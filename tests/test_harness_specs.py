"""Unit tests for the self-development harness spec layer (harness/specs.py, profiles.py).

These lock the spec-schema contract and the profile resolution the CLI relies on. They are
pure/in-memory (no pytest subprocess) — the live reference-resolution round-trip
(``requiredTests`` collection) is exercised separately in test_harness_validate.py so this
module stays fast and hermetic.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from harness import profiles
from harness.specs import (
    KIND_RULE,
    KIND_TASK,
    Spec,
    SpecError,
    parse_spec,
    validate_all,
    validate_spec,
)


def _write(tmp_path: Path, subdir: str, name: str, text: str) -> Path:
    d = tmp_path / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


# ── parse_spec ────────────────────────────────────────────────────────────────


def test_parse_spec_reads_frontmatter_and_body(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "rules",
        "r.md",
        """\
        ---
        id: my-rule
        type: ai-coding-rule
        statement: a thing must hold
        appliesTo:
          - src/x.py
        source: because it broke once
        ---
        # Body
        why + how
        """,
    )
    spec = parse_spec(p)
    assert spec.kind == KIND_RULE
    assert spec.id == "my-rule"
    assert spec.get_list("appliesTo") == ["src/x.py"]
    assert "why + how" in spec.body


def test_parse_spec_missing_frontmatter_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "rules", "r.md", "# just a body, no frontmatter\n")
    with pytest.raises(SpecError, match="missing YAML frontmatter"):
        parse_spec(p)


def test_parse_spec_unknown_type_raises(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "rules",
        "r.md",
        """\
        ---
        id: r
        type: not-a-real-kind
        ---
        body
        """,
    )
    with pytest.raises(SpecError, match="unknown or missing type"):
        parse_spec(p)


def test_get_list_tolerates_scalar(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "rules",
        "r.md",
        """\
        ---
        id: r
        type: ai-coding-rule
        statement: s
        appliesTo: src/single.py
        source: x
        ---
        body
        """,
    )
    spec = parse_spec(p)
    # A scalar where a list is expected is coerced to a one-item list.
    assert spec.get_list("appliesTo") == ["src/single.py"]


# ── validate_spec ─────────────────────────────────────────────────────────────


def _rule(**meta: object) -> Spec:
    base = {
        "id": "r",
        "type": KIND_RULE,
        "statement": "s",
        "appliesTo": ["src/x.py"],
        "source": "src",
    }
    base.update(meta)
    return Spec(path=Path("harness/specs/rules/r.md"), kind=KIND_RULE, meta=base, body="why")


def test_valid_rule_has_no_issues() -> None:
    assert validate_spec(_rule()) == []


def test_rule_missing_required_field_flagged() -> None:
    spec = _rule()
    del spec.meta["statement"]
    issues = validate_spec(spec)
    assert any("statement" in i.message and i.level == "error" for i in issues)


def test_rule_without_body_flagged() -> None:
    spec = _rule()
    spec.body = "   \n"
    issues = validate_spec(spec)
    assert any("body" in i.message for i in issues)


def test_malformed_id_flagged() -> None:
    spec = _rule(id="has spaces")
    issues = validate_spec(spec)
    assert any("malformed id" in i.message for i in issues)


def test_type_directory_mismatch_flagged() -> None:
    # A task-typed spec physically filed under rules/ is a filing mistake.
    spec = Spec(
        path=Path("harness/specs/rules/mis.md"),
        kind=KIND_TASK,
        meta={
            "id": "t",
            "type": KIND_TASK,
            "title": "t",
            "intent": "i",
            "touchedAreas": ["src/x.py"],
            "acceptance": {"negative": ["x"]},
        },
        body="",
    )
    issues = validate_spec(spec)
    assert any("filed under" in i.message for i in issues)


def test_task_requires_negative_acceptance() -> None:
    spec = Spec(
        path=Path("harness/specs/tasks/t.md"),
        kind=KIND_TASK,
        meta={
            "id": "T9.1",
            "type": KIND_TASK,
            "title": "t",
            "intent": "i",
            "touchedAreas": ["src/x.py"],
            "acceptance": {"positive": ["did the thing"]},  # no negative clause
        },
        body="",
    )
    issues = validate_spec(spec)
    assert any("negative" in i.message for i in issues)


def test_unknown_requiredrules_reference_flagged() -> None:
    spec = _rule(requiredRules=["no-such-rule"])
    issues = validate_spec(spec, known_ids={"r"})
    assert any("unknown spec id" in i.message for i in issues)


# ── validate_all ──────────────────────────────────────────────────────────────


def test_duplicate_ids_flagged() -> None:
    a = _rule(id="dup")
    b = _rule(id="dup")
    b.path = Path("harness/specs/rules/b.md")
    issues = validate_all([a, b])
    assert any("duplicate id" in i.message for i in issues)


# ── profiles ──────────────────────────────────────────────────────────────────


def test_profile_registry_has_core_profiles() -> None:
    names = profiles.profile_names()
    assert {"fast", "web", "replay", "full", "scan"} <= names


def test_resolve_commands_substitutes_tests() -> None:
    cmds = profiles.resolve_commands(["fast"], tests=["tests/test_a.py::t1"])
    assert len(cmds) == 1
    assert cmds[0].command.endswith("tests/test_a.py::t1")
    assert cmds[0].profile == "fast"


def test_resolve_commands_skips_needs_tests_when_none() -> None:
    # `fast` needs node-ids; with none, it contributes no command (caller decides error).
    assert profiles.resolve_commands(["fast"], tests=[]) == []


def test_resolve_commands_dedups_and_ignores_unknown() -> None:
    cmds = profiles.resolve_commands(["web", "web", "does-not-exist"])
    commands = [c.command for c in cmds]
    # web contributes 2 commands, requested twice → still 2 (deduped); unknown ignored.
    assert commands == ["npm run typecheck:web", "npm run test:web"]
