"""Outbound export of PersonalClaw entities into external harness formats.

Four rails carry this change, and each has a test that fails when the rail is removed:

* **byte-identical rendering** — every format renders twice inside one test and is compared
  to a COMMITTED golden. A timestamp, an absolute path or a dict-order leak reds both the
  twice-render check and the golden diff.
* **content redaction** — an AWS-key-shaped canary planted in an agent's prompt blocks
  the export, and the canary is then grepped for across every byte under the destination.
* **containment** — an entity named ``../evil`` is refused by the renderer AND by the path
  resolver, and the export writes nothing.
* **no clobber** — a file we did not write is never overwritten, and every one in the way is
  named in the one refusal; one we did write is replaced when it differs and left alone when
  it does not (a re-export is a no-op).

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
from personalclaw.config.loader import AgentProfile
from personalclaw.packs import external_formats
from personalclaw.packs.external_formats import (
    CLAUDE_CODE_AGENTS,
    CURSOR_RULES,
    EXTERNAL_FORMATS,
    FILE_NEW,
    FILE_REPLACE,
    FILE_SAME,
    FILE_THEIRS,
    PROVENANCE_MARKER,
    SKILL_MD,
    DestNotConfirmed,
    ExportBlocked,
    ExportClobberRefused,
    ExportPathRefused,
    ExportRefused,
    ExportSkill,
    ExportWriteFailed,
    _resolve_target,
    agent_from_profile,
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

#: AWS-key-shaped so BOTH detectors in the shared content scanner fire on it.
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


#: The exact basenames THIS module's golden cases render — its real-home blast radius.
#: A leak reaching ``Path.home()`` would create one of these; a concurrent xdist worker
#: writing an UNRELATED file in the same shared dir (or merely bumping a pre-existing
#: file's mtime) would not. Scoping the rail to these is what stops it blaming an innocent
#: test in this module for a neighbour's real-home leak (#1563) — the old rail watched
#: every child of the shared dirs by mtime, so any concurrent write tripped it and the
#: message degraded to the uninformative "mtimes changed".
_LEAK_BASENAMES: frozenset[str] = frozenset(
    [f"{a.name}.md" for a in GOLDEN_AGENTS]  # claude-code-agents → ~/.claude/agents/{slug}.md
    + [GOLDEN_SKILL.slug]  # skill-md → skills/{slug}/SKILL.md (the slug dir)
    + ["personalclaw-roster.mdc"]  # cursor-rules → the fixed roster file
)


def _home_snapshot(root: Path = _REAL_HOME) -> dict[str, int]:
    """Fingerprint ONLY the entries this module's own golden cases could create under
    ``root`` — keyed by existence + size, never bare mtime.

    ``root`` is a parameter (defaulting to the real home) so the scoping can be tested
    directly against a tmp tree. Two deliberate narrowings vs the original whole-home
    mtime scan, both to answer #1563:

    * only children whose NAME is one this module's writer would produce are recorded, so
      a neighbouring worker's unrelated file is invisible; and
    * the value is ``st_size``, so a foreign process bumping a pre-existing file's mtime
      without changing its contents cannot register as a change.

    A genuine leak from this module still trips it: the golden-named file appears (a new
    key) or its size changes.

    ``~/.personalclaw`` is deliberately NOT watched here: the suite-wide real-home guard
    (``tests/real_home_guard.py``) refuses every access under it, attributes it to the test
    that made it, and would refuse this listing of it too. What stays here is what the guard
    does not cover — the other tools' homes this module's writers render into.
    """
    snap: dict[str, int] = {}
    for rel in (".claude", ".claude/agents", ".cursor/rules"):
        d = root / rel
        if not d.is_dir():
            continue
        for child in sorted(d.iterdir()):
            if child.name not in _LEAK_BASENAMES:
                continue
            try:
                snap[str(child)] = child.stat().st_size
            except OSError:  # pragma: no cover — a racing external process
                snap[str(child)] = -1
    return snap


@pytest.fixture(autouse=True)
def _real_home_rail():
    """Report (not merely hope) that no test here writes into the operator's real home.

    The write path resolves its destination through ``default_dest_dir`` only; every test
    passes an explicit ``tmp_path``. If a future edit ever makes a renderer or the writer
    reach ``Path.home()`` directly, this rail names the golden-slug file that appeared or
    changed. It is scoped to THIS module's own output names (see ``_home_snapshot``) so it
    catches a leak here without being trippable by a concurrent xdist worker's write.
    """
    before = _home_snapshot()
    yield
    after = _home_snapshot()
    changed = sorted(k for k in set(after) | set(before) if before.get(k) != after.get(k))
    assert not changed, f"a test in this module touched the REAL home: {changed}"


# ── The rail's own scoping (regression guard for #1563) ───────────────────────


class TestRealHomeRailScoping:
    """The real-home rail must catch a leak from THIS module without being trippable by
    a concurrent xdist worker's unrelated write — the #1563 false positive."""

    def _agents_dir(self, root):
        d = root / ".claude" / "agents"
        d.mkdir(parents=True)
        return d

    def test_a_neighbours_unrelated_file_is_invisible(self, tmp_path):
        # A worker in another module writes an unrelated file into the shared dir.
        (self._agents_dir(tmp_path) / "some-other-agents-file.md").write_text("x")
        assert _home_snapshot(root=tmp_path) == {}, "only this module's golden names are watched"

    def test_a_bare_mtime_bump_on_a_watched_file_is_invisible(self, tmp_path):
        # Even a golden-named file that merely has its mtime moved (same contents) must
        # not read as a change — that was the reported 'mtimes changed' false positive.
        f = self._agents_dir(tmp_path) / f"{GOLDEN_AGENTS[0].name}.md"
        f.write_text("payload")
        before = _home_snapshot(root=tmp_path)
        os.utime(f, (f.stat().st_atime + 10_000, f.stat().st_mtime + 10_000))
        assert _home_snapshot(root=tmp_path) == before, "size-keyed, so an mtime move is a no-op"

    def test_this_modules_leak_is_caught(self, tmp_path):
        # A golden-named file APPEARING is a real leak from this module → visible.
        agents = self._agents_dir(tmp_path)
        before = _home_snapshot(root=tmp_path)
        (agents / f"{GOLDEN_AGENTS[0].name}.md").write_text("leaked")
        after = _home_snapshot(root=tmp_path)
        changed = sorted(k for k in set(after) | set(before) if before.get(k) != after.get(k))
        assert len(changed) == 1 and changed[0].endswith(f"{GOLDEN_AGENTS[0].name}.md")

    def test_a_size_change_on_a_watched_file_is_caught(self, tmp_path):
        # A leak that OVERWRITES an existing golden-named file changes its size → visible.
        f = self._agents_dir(tmp_path) / GOLDEN_SKILL.slug
        f.write_text("a")
        before = _home_snapshot(root=tmp_path)
        f.write_text("a much longer body")
        after = _home_snapshot(root=tmp_path)
        assert before != after, "size-keyed change is detected"


# ── The contract + registry ───────────────────────────────────────────────────


def test_registry_declares_the_three_v1_formats_and_is_consumed_by_name():
    assert format_names() == ["claude-code-agents", "cursor-rules", "skill-md"]
    assert [f.installKind for f in EXTERNAL_FORMATS.values()] == ["per-agent", "roster", "plugin"]
    assert get_format("claude-code-agents") is CLAUDE_CODE_AGENTS
    assert CLAUDE_CODE_AGENTS.dest == "~/.claude/agents/{slug}.md"
    with pytest.raises(ExportRefused, match="unknown external format"):
        get_format("vim-modelines")


def test_default_dest_dir_is_the_only_home_resolver(monkeypatch, tmp_path):
    """Home-anchored formats resolve through ``Path.home``; project-relative ones do not.
    (Claude Code's own directory follows ``$CLAUDE_CONFIG_DIR`` when that is set, so it is
    cleared here: this asserts the default.)"""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_dest_dir(CLAUDE_CODE_AGENTS) == tmp_path / ".claude" / "agents"
    assert default_dest_dir(CURSOR_RULES) is None
    assert default_dest_dir(SKILL_MD) is None


# ── Byte-identical rendering, per format, against a committed golden ──────────


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


# ── The file an external tool actually loads (format conformance) ─────────────


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
        # `tax-analyst` pins a model. Its name is one of THIS installation's models, which the
        # recipient cannot resolve, so the key would pin the exported agent to nothing it has.
        assert "model" not in front, "a model only PersonalClaw can resolve must not be written"
        assert rf.relpath == f"{front['name']}.md"
        assert rf.text.endswith(PROVENANCE_MARKER + "\n")
        assert rf.entities == (defn.name,), "each file names the agent rendered into it"
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
    assert files[0].entities == ("budget-coach", "tax-analyst"), "a roster names every agent in it"


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


# ── Explicit dest confirmation ────────────────────────────────────────────────


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


# ── Content redaction runs on RENDERED output ─────────────────────────────────


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
    preview = export_preview(CLAUDE_CODE_AGENTS, [leaky], tmp_path / "dest")
    assert preview["format"] == "claude-code-agents"
    assert preview["blocked"] == [{"path": "leaky-agent.md", "categories": ["credential"]}]
    # The sentence the write would refuse with, said BEFORE anything is confirmed.
    assert preview["refusal"] == (
        "Nothing is exported: leaky-agent.md holds what looks like a credential. Remove it, or "
        "leave it out to export the rest."
    )
    assert CANARY_AWS not in str(preview), "the preview must not carry the credential itself"
    assert list(tmp_path.iterdir()) == []


def test_clean_output_is_not_blocked(tmp_path):
    preview = export_preview(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path)
    assert preview["blocked"] == [] and preview["refusal"] is None


@pytest.mark.parametrize("refusal", ["a credential", "an unsafe name"])
def test_a_refused_export_leaves_no_folder_behind(tmp_path, refusal):
    """The destination is another tool's folder, often one that does not exist yet
    (``~/.claude/agents``). On ``main`` it was created before the checks ran, so an export the
    checks then refused left that folder behind, empty, with nothing of ours in it."""
    dest = tmp_path / "their-home" / ".claude" / "agents"
    entity = (
        _agent("leaky-agent", system_prompt=f"key {CANARY_AWS}")
        if refusal == "a credential"
        else _agent("../evil", description="escapes")
    )
    with pytest.raises((ExportBlocked, ExportPathRefused)):
        export_entities(CLAUDE_CODE_AGENTS, [entity], dest, confirm_dest=True)
    assert not (tmp_path / "their-home").exists()


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
    with pytest.raises(ExportClobberRefused) as exc:
        export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)
    assert str(exc.value) == (
        f"Refusing to overwrite {foreign.resolve()} (not written by PersonalClaw). Nothing is "
        "exported while it is there: leave it out to export the rest."
    )
    assert foreign.read_bytes() == original, "a foreign file was modified"
    assert not (tmp_path / "budget-coach.md").exists(), "the batch must abort before any write"


def test_every_file_in_the_way_is_named_in_the_one_refusal(tmp_path):
    """One refusal per clash would send the user back once for each file in the way."""
    for defn in GOLDEN_AGENTS:
        (tmp_path / f"{defn.name}.md").write_text(f"---\nname: {defn.name}\n---\n\nTheirs.\n")
    with pytest.raises(ExportClobberRefused) as exc:
        export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)
    assert [p.name for p in exc.value.targets] == ["tax-analyst.md", "budget-coach.md"]
    message = str(exc.value)
    assert message.startswith("Refusing to overwrite 2 files PersonalClaw did not write: ")
    assert "tax-analyst.md" in message and "budget-coach.md" in message
    assert message.endswith(
        "Nothing is exported while they are there: leave them out to export the rest."
    )


def test_a_file_that_cannot_be_read_is_not_taken_for_ours(tmp_path):
    """Fail closed: a folder where an agent's file would go cannot be shown to be ours."""
    (tmp_path / "budget-coach.md").mkdir()
    preview = export_preview(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS[1:], tmp_path)
    assert [f["state"] for f in preview["files"]] == [FILE_THEIRS]
    with pytest.raises(ExportClobberRefused):
        export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS[1:], tmp_path, confirm_dest=True)


def test_the_preview_and_the_write_decide_every_file_the_same_way(tmp_path):
    """The list a user confirms is the list the write acts on: new, same, replace, theirs."""
    fresh = _agent("fresh-agent", system_prompt="New here.")
    kept = _agent("kept-agent", system_prompt="Exported before, unchanged since.")
    edited = _agent("edited-agent", system_prompt="Exported before, edited since.")
    mine = _agent("mine-agent", system_prompt="The user's own file is in the way.")
    export_entities(CLAUDE_CODE_AGENTS, [kept, edited], tmp_path, confirm_dest=True)
    edited = _agent("edited-agent", system_prompt="The agent's instructions changed here.")
    (tmp_path / "mine-agent.md").write_text("---\nname: mine-agent\n---\n\nHand-written.\n")

    batch = [fresh, kept, edited, mine]
    preview = export_preview(CLAUDE_CODE_AGENTS, batch, tmp_path)
    assert [(f["path"], f["state"], f["entities"]) for f in preview["files"]] == [
        ("fresh-agent.md", FILE_NEW, ["fresh-agent"]),
        ("kept-agent.md", FILE_SAME, ["kept-agent"]),
        ("edited-agent.md", FILE_REPLACE, ["edited-agent"]),
        ("mine-agent.md", FILE_THEIRS, ["mine-agent"]),
    ]
    assert preview["refusal"] and "mine-agent.md" in preview["refusal"]
    with pytest.raises(ExportClobberRefused):
        export_entities(CLAUDE_CODE_AGENTS, batch, tmp_path, confirm_dest=True)
    assert not (tmp_path / "fresh-agent.md").exists(), "a refused batch writes nothing"

    result = export_entities(CLAUDE_CODE_AGENTS, batch[:3], tmp_path, confirm_dest=True)
    assert [p.name for p in result.written] == ["fresh-agent.md", "edited-agent.md"]
    assert [p.name for p in result.unchanged] == ["kept-agent.md"]
    assert "The agent's instructions changed here." in (tmp_path / "edited-agent.md").read_text()
    assert (tmp_path / "mine-agent.md").read_text().endswith("Hand-written.\n")


def test_re_exporting_our_own_file_is_a_byte_identical_no_op(tmp_path):
    export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)
    first = (tmp_path / "tax-analyst.md").read_bytes()
    again = export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)
    assert again.written == [], "nothing differs, so nothing is rewritten"
    assert [p.name for p in again.unchanged] == ["tax-analyst.md", "budget-coach.md"]
    assert (tmp_path / "tax-analyst.md").read_bytes() == first


def test_a_write_that_fails_part_way_names_what_it_wrote(tmp_path, monkeypatch):
    real_write = external_formats.atomic_write

    def full_disk_on_the_second(path, text, **kw):
        if Path(path).name == "budget-coach.md":
            raise OSError(28, "No space left on device")
        real_write(path, text, **kw)

    monkeypatch.setattr(external_formats, "atomic_write", full_disk_on_the_second)
    with pytest.raises(ExportWriteFailed) as exc:
        export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, tmp_path, confirm_dest=True)
    first = (tmp_path / "tax-analyst.md").resolve()
    assert exc.value.written == [first]
    assert str(exc.value) == (
        f"Could not write {(tmp_path / 'budget-coach.md').resolve()}: No space left on device. "
        f"1 file was written before it stopped: {first}."
    )


def test_destination_that_is_a_file_is_refused(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(ExportPathRefused, match="not a folder"):
        export_entities(CLAUDE_CODE_AGENTS, GOLDEN_AGENTS, blocker, confirm_dest=True)


# ── One of the user's agents, as the renderers take it ────────────────────────


def test_only_what_renders_travels_from_an_agent_profile():
    """The model, tools, triggers and approval mode name things only this installation has."""
    profile = AgentProfile(
        description="Reviews diffs",
        system_prompt="Read the diff twice.",
        voice="Terse.",
        model="reasoning",
        tools=["shell"],
        triggers=["t-1"],
        approval_mode="auto",
        skills=["diff-read", 7],  # a hand-edited config can hold anything
    )
    defn = agent_from_profile("diff-reviewer", profile)
    assert (defn.name, defn.description, defn.system_prompt, defn.voice, defn.skills) == (
        "diff-reviewer",
        "Reviews diffs",
        "Read the diff twice.",
        "Terse.",
        ["diff-read"],
    )
    assert defn.model == "" and defn.mcp_servers == {}
    text = CLAUDE_CODE_AGENTS.render([defn])[0].text
    assert "reasoning" not in text and "shell" not in text and "auto" not in text


# ── Deliberate golden regeneration (never from inside the run under test) ──────

if __name__ == "__main__":  # pragma: no cover
    for _fmt, _entities in GOLDEN_CASES:
        for _rf in _fmt.render(_entities):
            _out = GOLDEN_DIR / _fmt.name / _rf.relpath
            _out.parent.mkdir(parents=True, exist_ok=True)
            _out.write_text(_rf.text, encoding="utf-8")
            print(f"wrote {_out}")
