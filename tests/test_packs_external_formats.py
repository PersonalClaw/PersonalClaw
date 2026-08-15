"""AP-5 — outbound export of PersonalClaw entities into external harness formats.

Four rails carry this atom, and each has a test that fails when the rail is removed:

* **byte-identical rendering** — every format renders twice inside one test and is compared
  to a COMMITTED golden. A timestamp, an absolute path or a dict-order leak reds both the
  twice-render check and the golden diff.
* **§2.2 content redaction** — an AWS-key-shaped canary planted in an agent's prompt blocks
  the export, and the canary is then grepped for across every byte under the destination.
* **containment** — an entity named ``../evil`` is refused by the renderer AND by the path
  resolver, and the export writes nothing.
* **no clobber** — a file we did not write is never overwritten; one we did write is
  replaced only with ``overwrite=True``, and the replacement is byte-identical (a
  re-export is a no-op).

**No external binary is executed here.** "The external tool loads it" is asserted as a
FORMAT-CONFORMANCE claim: the frontmatter is parsed with a real YAML loader and the keys
that format documents as required are asserted present (and the invented-data key ``tools``
asserted absent).

Golden fixtures live in ``tests/fixtures/external_formats_golden/<format>/<relpath>`` and
are regenerated deliberately with ``python tests/test_packs_external_formats.py``. There is
no environment variable that rewrites them from inside the run under test: a golden a test
run rewrote blesses whatever that run did.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from personalclaw.agents.marketplace import AgentDefinition
from personalclaw.packs.external_formats import (
    CLAUDE_CODE_AGENTS,
    CURSOR_RULES,
    EXTERNAL_FORMATS,
    PROVENANCE_MARKER,
    SKILL_MD,
    DestNotConfirmed,
    ExportBlocked,
    ExportClobberRefused,
    ExportPathRefused,
    ExportRefused,
    ExportSkill,
    _resolve_target,
    default_dest_dir,
    export_entities,
    export_preview,
    format_names,
    get_format,
)

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "external_formats_golden"

#: Captured at IMPORT time, before any fixture repoints ``$HOME`` — so the real-home rail
#: below watches the operator's actual home, not a tmp one.
_REAL_HOME = Path(os.path.expanduser("~"))

#: AWS-key-shaped so BOTH detectors in the shared §2.2 scanner fire on it.
CANARY_AWS = "AKIAIOSFODNN7EXAMPLE"


# ── Fixed entities (no clocks, no machine paths — the golden inputs) ───────────


def _agent(name: str, **kw) -> AgentDefinition:
    base: dict[str, object] = {
        "name": name,
        "description": f"{name} agent",
        "created_at": 0.0,
        "updated_at": 0.0,
    }
    base.update(kw)
    return AgentDefinition(**base)  # type: ignore[arg-type]


GOLDEN_AGENTS = [
    _agent(
        "tax-analyst",
        description="Reads statements and drafts the quarterly filing: no advice",
        model="claude-sonnet-4",
        system_prompt="You reconcile ledgers.\nAlways cite the source row.",
        voice="Blunt. Numbers first, prose second.",
        skills=["ledger-read", "pdf-extract", "ledger-read"],
        mcp_servers={"fs": {"command": "x", "env": {"TOKEN": "should-never-render"}}},
    ),
    _agent(
        "budget-coach",
        description="Weekly spend review",
        system_prompt="Summarise spend against the plan.",
        skills=["ledger-read"],
    ),
]

GOLDEN_SKILL = ExportSkill(
    slug="ledger-read",
    text="---\nname: ledger-read\ndescription: Read a ledger CSV\n---\n\nOpen the file.\n",
)

#: ``(format, entities)`` — the per-format golden inputs.
GOLDEN_CASES = [
    (CLAUDE_CODE_AGENTS, GOLDEN_AGENTS),
    (CURSOR_RULES, GOLDEN_AGENTS),
    (SKILL_MD, [GOLDEN_SKILL, GOLDEN_AGENTS[1]]),
]


# ── Real-home rail ────────────────────────────────────────────────────────────


def _home_snapshot() -> dict[str, float]:
    """A cheap, precise fingerprint of the two real-home dirs this module could reach."""
    snap: dict[str, float] = {}
    for rel in (".claude", ".claude/agents", ".personalclaw"):
        d = _REAL_HOME / rel
        if not d.is_dir():
            continue
        for child in sorted(d.iterdir()):
            try:
                snap[str(child)] = child.stat().st_mtime
            except OSError:  # pragma: no cover — a racing external process
                snap[str(child)] = -1.0
    return snap


@pytest.fixture(autouse=True)
def _real_home_rail():
    """Report (not merely hope) that no test here writes into the operator's real home.

    The write path resolves its destination through ``default_dest_dir`` only; every test
    passes an explicit ``tmp_path``. If a future edit ever makes a renderer or the writer
    reach ``Path.home()`` directly, this rail names the file that appeared or changed.
    """
    before = _home_snapshot()
    yield
    after = _home_snapshot()
    assert after == before, (
        "a test in this module touched the REAL home: "
        f"{sorted(set(after) ^ set(before)) or 'mtimes changed'}"
    )


# ── The contract + registry ───────────────────────────────────────────────────


def test_registry_declares_the_three_v1_formats_and_is_consumed_by_name():
    assert format_names() == ["claude-code-agents", "cursor-rules", "skill-md"]
    assert [f.installKind for f in EXTERNAL_FORMATS.values()] == ["per-agent", "roster", "plugin"]
    assert get_format("claude-code-agents") is CLAUDE_CODE_AGENTS
    assert CLAUDE_CODE_AGENTS.dest == "~/.claude/agents/{slug}.md"
    with pytest.raises(ExportRefused, match="unknown external format"):
        get_format("vim-modelines")


def test_default_dest_dir_is_the_only_home_resolver(monkeypatch, tmp_path):
    """Home-anchored formats resolve through ``Path.home``; project-relative ones do not."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_dest_dir(CLAUDE_CODE_AGENTS) == tmp_path / ".claude" / "agents"
    assert default_dest_dir(CURSOR_RULES) is None
    assert default_dest_dir(SKILL_MD) is None


# ── done_when: byte-identical rendering, per format, against a committed golden ──


@pytest.mark.parametrize("fmt,entities", GOLDEN_CASES, ids=lambda v: getattr(v, "name", ""))
def test_rendering_is_byte_identical_across_runs(fmt, entities):
    first = {rf.relpath: rf.text.encode("utf-8") for rf in fmt.render(entities)}
    second = {rf.relpath: rf.text.encode("utf-8") for rf in fmt.render(list(entities))}
    assert first == second, f"{fmt.name} rendered differently on a second run"


@pytest.mark.parametrize("fmt,entities", GOLDEN_CASES, ids=lambda v: getattr(v, "name", ""))
def test_rendering_matches_the_committed_golden(fmt, entities):
    rendered = fmt.render(entities)
    assert rendered, f"{fmt.name} rendered nothing"
    for rf in rendered:
        golden = GOLDEN_DIR / fmt.name / rf.relpath
        assert (
            golden.exists()
        ), f"missing golden fixture {golden} — regenerate with `python {__file__}`"
        assert rf.text == golden.read_text(
            encoding="utf-8"
        ), f"{fmt.name}/{rf.relpath} diverged from its golden"


def test_no_rendered_output_carries_a_clock_or_a_machine_path():
    """The two nondeterminism sources that would make a re-export a spurious diff."""
    import re

    here = str(Path(__file__).resolve().parent)
    for fmt, entities in GOLDEN_CASES:
        for rf in fmt.render(entities):
            assert here not in rf.text, f"{fmt.name}/{rf.relpath} leaked an absolute path"
            assert str(_REAL_HOME) not in rf.text
            assert not re.search(
                r"\b20\d\d-\d\d-\d\dT", rf.text
            ), f"{fmt.name}/{rf.relpath} looks like it stamped a timestamp"


# ── done_when: the file an external tool actually loads (format conformance) ───


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), "frontmatter must open on line 1"
    _, front, _ = text.split("---\n", 2)
    return yaml.safe_load(front) or {}


def test_claude_code_agent_file_conforms_to_the_documented_frontmatter():
    """Format-conformance claim only — NO external binary is executed by this suite."""
    files = CLAUDE_CODE_AGENTS.render(GOLDEN_AGENTS)
    assert [rf.relpath for rf in files] == ["tax-analyst.md", "budget-coach.md"]
    for rf, defn in zip(files, GOLDEN_AGENTS, strict=True):
        front = _frontmatter(rf.text)
        assert front["name"] == defn.name, "the required `name` key must match the file slug"
        assert front["description"], "the required `description` key must be non-empty"
        assert "tools" not in front, "we must not invent a tool allowlist an agent never declared"
        assert rf.relpath == f"{front['name']}.md"
        assert rf.text.endswith(PROVENANCE_MARKER + "\n")
    body = files[0].text
    assert "## Skills" in body and "- ledger-read" in body
    assert body.count("- ledger-read") == 1, "duplicate declared skills must collapse"
    assert "should-never-render" not in body, "mcp_servers values must never be rendered"


def test_cursor_rules_is_one_roster_file_ordered_independently_of_input():
    files = CURSOR_RULES.render(GOLDEN_AGENTS)
    assert len(files) == 1 and files[0].relpath == "personalclaw-roster.mdc"
    front = _frontmatter(files[0].text)
    assert front["alwaysApply"] is False
    assert "2 agents" in front["description"]
    reversed_render = CURSOR_RULES.render(list(reversed(GOLDEN_AGENTS)))
    assert reversed_render[0].text == files[0].text, "roster order must not depend on input order"
    assert files[0].text.index("## budget-coach") < files[0].text.index("## tax-analyst")


def test_skill_md_ships_a_skill_near_verbatim_and_an_agent_as_a_skill():
    files = SKILL_MD.render([GOLDEN_SKILL, GOLDEN_AGENTS[1]])
    assert [rf.relpath for rf in files] == [
        "skills/ledger-read/SKILL.md",
        "skills/budget-coach/SKILL.md",
    ]
    assert files[0].text.startswith(GOLDEN_SKILL.text), "a skill must ship verbatim"
    assert PROVENANCE_MARKER in files[0].text
    assert _frontmatter(files[1].text)["name"] == "budget-coach"


def test_a_renderer_refuses_an_entity_kind_it_cannot_represent():
    with pytest.raises(ExportRefused, match="renders agents only"):
        CLAUDE_CODE_AGENTS.render([GOLDEN_SKILL])
    with pytest.raises(ExportRefused, match="nothing to export"):
        CURSOR_RULES.render([])


# ── done_when: explicit dest confirmation ─────────────────────────────────────


def test_export_refuses_without_explicit_dest_confirmation(tmp_path):
    dest = tmp_path / "claude-agents"
    with pytest.raises(DestNotConfirmed, match="explicit destination confirmation"):
        export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, dest)
    assert not dest.exists(), "a refused export must not even create the directory"


def test_confirmed_export_writes_exactly_the_rendered_files(tmp_path):
    result = export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)
    assert [p.name for p in result.written] == ["tax-analyst.md", "budget-coach.md"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["budget-coach.md", "tax-analyst.md"]
    assert (tmp_path / "tax-analyst.md").read_text() == CLAUDE_CODE_AGENTS.render(GOLDEN_AGENTS)[
        0
    ].text


def test_nested_relpaths_land_under_the_destination(tmp_path):
    export_entities(SKILL_MD, [GOLDEN_SKILL], tmp_path, confirm_dest=True)
    assert (tmp_path / "skills" / "ledger-read" / "SKILL.md").is_file()


# ── done_when: §2.2 content redaction runs on RENDERED output ─────────────────


def test_a_planted_credential_blocks_the_export_and_never_reaches_the_disk(tmp_path):
    leaky = _agent(
        "leaky-agent",
        system_prompt=f"Use this key when calling the API: {CANARY_AWS}",
    )
    with pytest.raises(ExportBlocked) as exc:
        export_entities(CLAUDE_CODE_AGENTS, [leaky], tmp_path, confirm_dest=True)
    assert exc.value.blocked[0].relpath == "leaky-agent.md"
    assert "credential" in exc.value.blocked[0].categories
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == [], f"a blocked export wrote files: {written}"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert CANARY_AWS.encode() not in path.read_bytes()


def test_a_credential_blocks_the_whole_batch_not_just_the_leaky_file(tmp_path):
    leaky = _agent("leaky-agent", system_prompt=f"key {CANARY_AWS}")
    with pytest.raises(ExportBlocked):
        export_entities(CLAUDE_CODE_AGENTS, [GOLDEN_AGENTS[1], leaky], tmp_path, confirm_dest=True)
    assert not (tmp_path / "budget-coach.md").exists(), "a partial roster must not be written"


def test_preview_reports_the_block_without_writing(tmp_path):
    leaky = _agent("leaky-agent", system_prompt=f"key {CANARY_AWS}")
    preview = export_preview(CLAUDE_CODE_AGENTS, [leaky])
    assert preview["format"] == "claude-code-agents"
    assert preview["installKind"] == "per-agent"
    assert preview["blocked"][0]["path"] == "leaky-agent.md"
    assert list(tmp_path.iterdir()) == []


def test_clean_output_is_not_blocked():
    assert export_preview(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS)["blocked"] == []


# ── Containment: a slug may never escape the destination ───────────────────────


def test_a_traversal_slug_is_refused_by_the_renderer(tmp_path):
    evil = _agent("../evil", description="escapes")
    with pytest.raises(ExportPathRefused, match=r"unsafe entity name"):
        CLAUDE_CODE_AGENTS.render([evil])
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(ExportPathRefused):
        export_entities(CLAUDE_CODE_AGENTS, [evil], dest, confirm_dest=True)
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "evil.md").exists(), "a write escaped the destination directory"


@pytest.mark.parametrize(
    "name", ["../evil", "/etc/passwd", "a/b", "a\\b", "..", ".", "", "Evil", "-lead"]
)
def test_unsafe_entity_names_are_refused_not_sanitised(name):
    with pytest.raises(ExportPathRefused):
        SKILL_MD.render([ExportSkill(slug=name, text="---\nname: x\n---\n")])


@pytest.mark.parametrize("relpath", ["../evil.md", "/etc/passwd", "a/../../b.md", "./x.md", ""])
def test_path_resolver_is_an_independent_second_containment_check(tmp_path, relpath):
    with pytest.raises(ExportPathRefused):
        _resolve_target(tmp_path, relpath)


def test_path_resolver_accepts_a_nested_relative_path(tmp_path):
    assert (
        _resolve_target(tmp_path, "skills/x/SKILL.md")
        == (tmp_path / "skills" / "x" / "SKILL.md").resolve()
    )


# ── No clobber: we replace only files we wrote ────────────────────────────────


def test_refuses_to_overwrite_a_file_we_did_not_write(tmp_path):
    foreign = tmp_path / "tax-analyst.md"
    foreign.write_text("---\nname: tax-analyst\n---\n\nThe user's OWN agent.\n")
    original = foreign.read_bytes()
    with pytest.raises(ExportClobberRefused, match="not written by personalclaw"):
        export_entities(
            CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True, overwrite=True
        )
    assert foreign.read_bytes() == original, "a foreign file was modified"
    assert not (tmp_path / "budget-coach.md").exists(), "the batch must abort before any write"


def test_an_existing_file_needs_overwrite_even_when_it_is_ours(tmp_path):
    export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)
    with pytest.raises(ExportClobberRefused, match="pass overwrite=True"):
        export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)


def test_re_exporting_our_own_file_is_a_byte_identical_no_op(tmp_path):
    export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)
    first = (tmp_path / "tax-analyst.md").read_bytes()
    export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True, overwrite=True)
    assert (tmp_path / "tax-analyst.md").read_bytes() == first


def test_destination_that_is_a_file_is_refused(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(ExportPathRefused, match="not a directory"):
        export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, blocker, confirm_dest=True)


# ── Deliberate golden regeneration (never from inside the run under test) ──────

if __name__ == "__main__":  # pragma: no cover
    for _fmt, _entities in GOLDEN_CASES:
        for _rf in _fmt.render(_entities):
            _out = GOLDEN_DIR / _fmt.name / _rf.relpath
            _out.parent.mkdir(parents=True, exist_ok=True)
            _out.write_text(_rf.text, encoding="utf-8")
            print(f"wrote {_out}")
