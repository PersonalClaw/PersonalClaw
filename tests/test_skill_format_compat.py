"""SKILL.md format compatibility — the contract `docs/reference/skill-format.md` states.

Why this file exists: PersonalClaw claims a vanilla ecosystem `SKILL.md` imports
cleanly, and that `triggers:` is a PersonalClaw extension rather than a
requirement. That claim is only worth making if it is pinned, so each case below
corresponds to a documented paragraph in the reference doc.

The bug class these guard against is specific and nasty: the frontmatter parser
returning `{}` for a file that plainly HAS metadata. Such a skill installs, lists
without a name or description, never trigger-matches, and reports no error
anywhere — indistinguishable from "the author wrote no frontmatter".
"""

from pathlib import Path

from personalclaw.skills.loader import SkillsLoader

# ── The compatibility promise ────────────────────────────────────────────────


def test_vanilla_skill_without_triggers_parses(tmp_path: Path) -> None:
    """The headline claim: a foreign skill needs only name + description."""
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: pdf-tools\ndescription: Work with PDFs\n---\n\n# Body\n")
    meta = SkillsLoader._parse_frontmatter(f)
    assert meta == {"name": "pdf-tools", "description": "Work with PDFs"}
    assert "triggers" not in meta, "triggers must not be synthesized when absent"


def test_absent_triggers_never_matches_but_still_lists(tmp_path: Path) -> None:
    """A skill with no `triggers:` is inert for matching, not broken.

    Note a scoped loader still syncs the BUNDLED skills into its directory
    (`_ensure_builtin_skills`), so assert on our row rather than the whole list.
    """
    d = tmp_path / "vanilla-pdf-tools"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: pdf-tools\ndescription: PDFs\n---\n\nB\n")
    loader = SkillsLoader(skills_path=tmp_path)
    row = next(r for r in loader.list_skills() if r["key"] == "vanilla-pdf-tools")
    assert row["triggers"] == ""
    assert row["description"] == "PDFs"
    # Empty triggers must not degrade into "matches everything".
    assert "vanilla-pdf-tools" not in loader.get_triggered_skills("anything at all")


# ── Tolerant reads: each of these silently returned {} before ────────────────


def test_utf8_bom_does_not_erase_metadata(tmp_path: Path) -> None:
    """A Windows-editor BOM sat before `---` and cost every field."""
    f = tmp_path / "SKILL.md"
    f.write_bytes(b"\xef\xbb\xbf---\nname: a\ndescription: d\n---\n\nB\n")
    assert SkillsLoader._parse_frontmatter(f) == {"name": "a", "description": "d"}


def test_leading_blank_line_does_not_erase_metadata(tmp_path: Path) -> None:
    f = tmp_path / "SKILL.md"
    f.write_text("\n\n---\nname: a\ndescription: d\n---\n\nB\n")
    assert SkillsLoader._parse_frontmatter(f) == {"name": "a", "description": "d"}


def test_crlf_frontmatter_parses(tmp_path: Path) -> None:
    """Via a file, `read_text` already translates newlines — this pins the promise."""
    f = tmp_path / "SKILL.md"
    f.write_bytes(b"---\r\nname: a\r\ndescription: d\r\n---\r\n\r\nB\r\n")
    assert SkillsLoader._parse_frontmatter(f) == {"name": "a", "description": "d"}


def test_crlf_parses_when_text_is_supplied_directly() -> None:
    """The string entry point gets no universal-newline translation.

    `read_text` normalizes CRLF, so the file-based test above cannot distinguish
    a parser that handles CRLF from one that doesn't. Callers that pass content
    read elsewhere (e.g. `mcp_core.py`) hand over raw text, so the normalization
    has to live in the parser — and needs a test that would notice its removal.
    """
    meta = SkillsLoader._parse_frontmatter_text(
        "---\r\nname: a\r\ndescription: d\r\n---\r\n\r\nB\r\n"
    )
    assert meta == {"name": "a", "description": "d"}


def test_no_frontmatter_still_yields_empty(tmp_path: Path) -> None:
    """The tolerance must not invent metadata where there is none."""
    f = tmp_path / "SKILL.md"
    f.write_text("# Just a heading\n\nProse.\n")
    assert SkillsLoader._parse_frontmatter(f) == {}


# ── YAML-list triggers: the ecosystem's natural spelling ─────────────────────


def test_triggers_as_yaml_block_list_is_accepted(tmp_path: Path) -> None:
    """Vanilla authors write a list; we store comma-separated. Fold, don't drop."""
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: a\ndescription: d\ntriggers:\n  - pdf\n  - fill form\n---\n\nB\n")
    assert SkillsLoader._parse_frontmatter(f)["triggers"] == "pdf, fill form"


def test_yaml_list_triggers_actually_match(tmp_path: Path) -> None:
    """The fold is only worth anything if matching consumes it."""
    d = tmp_path / "vanilla-yaml-list"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: a\ndescription: d\ntriggers:\n  - zzqpdfsplit\n---\n\nB\n"
    )
    loader = SkillsLoader(skills_path=tmp_path)
    assert "vanilla-yaml-list" in loader.get_triggered_skills("please zzqpdfsplit this")


def test_comma_separated_triggers_still_work(tmp_path: Path) -> None:
    """The PersonalClaw-native spelling is unchanged by the list support."""
    d = tmp_path / "native-commas"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: a\ndescription: d\ntriggers: zzqpdfsplit, forms\n---\n\nB\n"
    )
    loader = SkillsLoader(skills_path=tmp_path)
    assert "native-commas" in loader.get_triggered_skills("a zzqpdfsplit")


def test_empty_triggers_value_stays_empty(tmp_path: Path) -> None:
    """`triggers:` with nothing under it is empty, not a one-item list."""
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: a\ntriggers:\n---\n\nB\n")
    assert SkillsLoader._parse_frontmatter(f)["triggers"] == ""


def test_block_scalar_descriptions_are_folded(tmp_path: Path) -> None:
    """Multi-line descriptions are the ecosystem's other common spelling.

    This capability existed only in `marketplace._parse_description`, so the
    Store showed a folded description while the loader (and thus the installed
    skill) showed the bare `|`. Promoted into the one parser; pinned here.
    """
    P = SkillsLoader._parse_frontmatter_text
    for indicator in ("|", ">", "|-", ">+"):
        meta = P(f"---\nname: a\ndescription: {indicator}\n  line one\n  line two\n---\n\nB\n")
        assert meta["description"] == "line one line two", f"failed for {indicator!r}"


def test_marketplace_description_matches_the_loader(tmp_path: Path) -> None:
    """The Store preview and the installed skill must agree — including on a BOM."""
    from personalclaw.skills.marketplace import _parse_description

    f = tmp_path / "SKILL.md"
    f.write_bytes("﻿---\nname: a\ndescription: |\n  one\n  two\n---\n\nB\n".encode())
    assert _parse_description(f) == "one two"
    assert SkillsLoader._parse_frontmatter(f)["description"] == "one two"


def test_dashboard_always_flag_survives_a_bom(tmp_path: Path) -> None:
    """`always: true` under a BOM used to read as false on the live skills route."""
    from personalclaw.dashboard.handlers.skills import _parse_always

    f = tmp_path / "SKILL.md"
    f.write_bytes("﻿---\nname: a\nalways: true\n---\n\nB\n".encode())
    assert _parse_always(f) is True


# ── Documented limits (a line parser, not YAML) ──────────────────────────────


def test_nested_mapping_keys_are_not_hoisted(tmp_path: Path) -> None:
    """A nested key must not masquerade as a top-level field."""
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: a\nmeta:\n  sub: v\n---\n\nB\n")
    meta = SkillsLoader._parse_frontmatter(f)
    assert meta["name"] == "a"
    assert "sub" not in meta, "nested keys must not reach the top level"


def test_value_containing_colon_is_preserved(tmp_path: Path) -> None:
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: a\ndescription: Use x: then y\n---\n\nB\n")
    assert SkillsLoader._parse_frontmatter(f)["description"] == "Use x: then y"


def test_quotes_are_stripped(tmp_path: Path) -> None:
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: \"quoted\"\ndescription: 'single'\n---\n\nB\n")
    meta = SkillsLoader._parse_frontmatter(f)
    assert meta == {"name": "quoted", "description": "single"}


def test_documented_yaml_limits_hold() -> None:
    """Pin the "Not supported" table in docs/reference/skill-format.md.

    These are limits, not bugs — but they are limits we PUBLISH, so the doc and
    the parser must not drift apart. Each expectation here was measured.
    """
    P = SkillsLoader._parse_frontmatter_text
    # Flow mappings stay raw text.
    assert P("---\nname: a\nmeta: {k: v}\n---\n\nB\n")["meta"] == "{k: v}"
    # Anchors/aliases are not interpreted.
    assert P("---\nname: &x a\nother: *x\n---\n\nB\n") == {"name": "&x a", "other": "*x"}
    # Last duplicate key wins.
    assert P("---\nname: first\nname: second\n---\n\nB\n")["name"] == "second"
    # Comment lines are skipped rather than becoming a key.
    assert P("---\n# a comment\nname: a\n---\n\nB\n") == {"name": "a"}


# ── strip_frontmatter: same tolerance, higher stakes (prompt content) ────────


def test_strip_frontmatter_tolerates_bom_and_blank_lines() -> None:
    """A missed delimiter leaks raw YAML into the model prompt."""
    for text in (
        "﻿---\nname: a\n---\n\nBody\n",
        "\n---\nname: a\n---\n\nBody\n",
        "---\r\nname: a\r\n---\r\n\r\nBody\r\n",
        "---\nname: a\n---\n\nBody\n",
    ):
        assert SkillsLoader.strip_frontmatter(text) == "Body", f"leaked for {text!r}"


def test_strip_frontmatter_passes_through_bodyless_markdown() -> None:
    assert SkillsLoader.strip_frontmatter("# Heading\n\nProse.") == "# Heading\n\nProse."
