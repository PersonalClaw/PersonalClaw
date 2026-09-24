"""`GET /api/skills` publishes HOW each skill came to exist (#576).

Two producers write a `source:` frontmatter marker — the auto-extractor writes
`source: auto` and session-draft promotion writes `source: taught` — and before this
change nothing read either one back. `source` on the wire is derived purely from the
directory the skill sits in, so a skill the user explicitly taught the agent was
indistinguishable from one dropped into the same directory by hand, on every surface.

What these pin:

  - a taught skill reports `provenance: "taught"` while `source` stays the TIER
    (`local`) — the ruling's "do NOT override the tier-derived source", and the thing
    that keeps the editable/deletable decisions reading the tier;
  - an auto-extracted skill reports `provenance: "auto"`;
  - a hand-authored skill reports `""` (the vacuity control — the field is not just
    always populated);
  - an unrecognized frontmatter `source:` is NOT published, because this value reaches
    a UI badge and frontmatter is free-form text;
  - the agent-local tier — the SECOND listing site, and the tier a "this agent only"
    promotion actually lands in — carries it too;
  - the writer and the reader agree by construction: the exact bytes
    `ephemeral._skill_markdown` produces parse back as `taught`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import skills as skills_h
from personalclaw.skills import ephemeral
from personalclaw.skills.loader import AUTO_SKILL_SOURCE_VALUE


def _make_skill(root: Path, name: str, frontmatter: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    return skill_dir


@pytest.fixture
def skill_root(tmp_path, monkeypatch):
    """A single global skill discovery root, wired into `_all_skill_paths`."""
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr("personalclaw.agent._all_skill_paths", lambda: [str(root)])
    return root


def _list() -> list[dict]:
    req = make_mocked_request("GET", "/api/skills")
    resp = asyncio.run(skills_h.api_skills_list(req))
    assert resp.status == 200
    return json.loads(resp.body.decode())


def _entry(name: str) -> dict:
    return next(s for s in _list() if s["name"] == name)


def test_a_taught_skill_reports_taught_without_changing_its_tier(skill_root):
    """The whole point: both facts survive to the API, and they stay separate."""
    _make_skill(
        skill_root,
        "termbase-check",
        f"---\nname: termbase-check\ndescription: d\n"
        f"source: {ephemeral.TAUGHT_SKILL_SOURCE_VALUE}\n---\n\nbody\n",
    )
    entry = _entry("termbase-check")
    assert entry["provenance"] == "taught"
    # NOT overridden — every surface that decides "can I edit/delete this?" reads `source`.
    assert entry["source"] == "local"
    assert entry["type"] == "installed"


def test_an_auto_extracted_skill_reports_auto(skill_root):
    _make_skill(
        skill_root,
        "auto-thing",
        f"---\nname: auto-thing\ndescription: d\nsource: {AUTO_SKILL_SOURCE_VALUE}\n---\n\nb\n",
    )
    entry = _entry("auto-thing")
    assert entry["provenance"] == "auto"
    assert entry["source"] == "local"


def test_a_hand_authored_skill_reports_no_provenance(skill_root):
    """Vacuity control: absence of the marker has always meant hand-authored, and the
    field must be able to be empty or `provenance` says nothing."""
    _make_skill(skill_root, "by-hand", "---\nname: by-hand\ndescription: d\n---\n\nb\n")
    assert _entry("by-hand")["provenance"] == ""


def test_an_unrecognized_frontmatter_source_is_not_published(skill_root):
    """Frontmatter is free-form text a hand-authored skill can put anything in, and this
    value reaches a UI badge — so the emitted vocabulary is closed to what the two
    producers actually write."""
    _make_skill(
        skill_root, "odd", "---\nname: odd\ndescription: d\nsource: whatever-i-like\n---\n\nb\n"
    )
    assert _entry("odd")["provenance"] == ""


def test_the_marker_is_read_case_and_whitespace_tolerantly(skill_root):
    _make_skill(
        skill_root, "shouty", "---\nname: shouty\ndescription: d\nsource:  TAUGHT \n---\n\nb\n"
    )
    assert _entry("shouty")["provenance"] == "taught"


def test_a_bom_does_not_hide_the_marker(skill_root, tmp_path):
    """`_parse_always` carries this same tolerance for the same reason: a BOM'd SKILL.md
    otherwise reads as having no frontmatter at all."""
    skill_dir = skill_root / "bommed"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bommed\ndescription: d\nsource: taught\n---\n\nb\n", encoding="utf-8-sig"
    )
    assert _entry("bommed")["provenance"] == "taught"


def test_the_agent_local_tier_reports_provenance_too(skill_root, tmp_path, monkeypatch):
    """The SECOND listing site. A "this agent only" promotion lands here, so a fix that
    only covered the global tier would miss the tier the promote flow defaults toward."""
    agent_dir = tmp_path / "agent-skills"
    agent_dir.mkdir()
    _make_skill(
        agent_dir,
        "agent-taught",
        "---\nname: agent-taught\ndescription: d\nsource: taught\n---\n\nb\n",
    )
    monkeypatch.setattr("personalclaw.skills.loader.agent_skills_dir", lambda _name: agent_dir)

    entries = [s for s in _list() if s["name"] == "agent-taught"]
    assert entries, "agent-local tier produced no listing entry"
    for entry in entries:
        assert entry["provenance"] == "taught"
        assert entry["source"] == "agent-local"


def test_promotion_writes_exactly_the_marker_the_listing_reads(tmp_path):
    """Writer and reader share one literal, so a rename cannot silently split them —
    which is the state this issue reported: a marker written and read by nothing."""
    md = ephemeral._skill_markdown("Check the termbase", "step one\n", "check-the-termbase")
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(md, encoding="utf-8")
    assert skills_h._parse_provenance(skill_md) == ephemeral.TAUGHT_SKILL_SOURCE_VALUE


def test_an_unreadable_skill_file_does_not_break_the_listing(skill_root):
    """The read is best-effort the way `_parse_always`'s is: a directory in place of the
    file is the cheap stand-in for any OSError, and it must not 500 the whole list."""
    (skill_root / "weird").mkdir()
    (skill_root / "weird" / "SKILL.md").mkdir()
    assert skills_h._parse_provenance(skill_root / "weird" / "SKILL.md") == ""
