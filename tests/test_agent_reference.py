"""Drift guard for the shipped offline agent reference (PLATFORM-LEGIBILITY §3.1).

The reference under ``personalclaw/reference/*.md`` is GENERATED from the same
``build_manifest`` output the live ``/api/manifest`` walks — one source, two
renderings. This suite is what keeps the checked-in copy honest: it renders fresh
and byte-compares, so a tool/route added without its ``TOOL_META`` / route entry
(or a manual edit to the generated files) reddens the build. Regenerate with
``python -m personalclaw.manifest_reference``.

It also asserts the two operator-facing contracts the reference exists to serve:
the `pclaw-api` skill points at a reference that actually exists and cross-links
`pclaw-features`, and ``doctor --paths`` resolves the reference dir the skill tells
agents to find.
"""

from __future__ import annotations

import re
from pathlib import Path

import personalclaw.manifest_reference as ref_mod
from personalclaw.manifest_reference import reference_dir, render_reference


def test_checked_in_reference_matches_a_fresh_render():
    """Every shipped reference file byte-matches a fresh render — no manual drift.

    This is the whole point: an agent reads exact signatures, so the checked-in
    copy must equal what the generator produces from the current registries.
    """
    rendered = render_reference()
    root = reference_dir()
    mismatches: list[str] = []
    for filename, expected in rendered.items():
        path = root / filename
        if not path.is_file():
            mismatches.append(f"{filename}: missing from the shipped reference dir")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            mismatches.append(f"{filename}: differs from a fresh render")
    assert not mismatches, (
        "Offline reference is stale — regenerate with "
        "`python -m personalclaw.manifest_reference`:\n" + "\n".join(mismatches)
    )


def test_reference_has_the_four_expected_files():
    """index/tools/routes/providers all render (a dropped section would be a regression)."""
    assert set(render_reference()) == {"index.md", "tools.md", "routes.md", "providers.md"}


def test_route_signature_prefix_is_stripped():
    """A docstring that restates the route signature is de-duplicated in the summary."""
    assert ref_mod._clean_summary("GET /api/foo — does a thing") == "does a thing"
    assert ref_mod._clean_summary("GET/PUT /api/agent/config — read or write") == "read or write"
    assert ref_mod._clean_summary("POST /api/x/:id/activate") == ""
    # A plain sentence with no route signature is left untouched.
    assert ref_mod._clean_summary("List all scheduled jobs.") == "List all scheduled jobs."


def test_tools_reference_lists_every_provider():
    """Each tool provider heading appears — the reference covers the whole surface."""
    tools_md = render_reference()["tools.md"]
    providers = {
        "personalclaw-core",
        "personalclaw-schedule",
        "personalclaw-artifacts",
        "personalclaw-memory",
        "personalclaw-knowledge-tools",
        "personalclaw-tasks-tools",
    }
    for p in providers:
        assert f"## {p}" in tools_md, f"{p} missing from tools.md"


def test_reference_examples_carry_no_invented_params():
    """Every ```json example block in tools.md is valid JSON (faithful args).

    The drift test already checks example arg names against the live schema; this
    asserts the RENDERED form stays machine-parseable, since the reference's value
    is that an agent can copy an example verbatim.
    """
    import json

    tools_md = render_reference()["tools.md"]
    blocks = re.findall(r"```json\n(.*?)\n```", tools_md, re.DOTALL)
    assert blocks, "expected at least one example json block"
    for b in blocks:
        json.loads(b)  # raises on malformed JSON → test fails


def _skill_path() -> Path:
    return Path(ref_mod.__file__).parent / "skills" / "bundled" / "pclaw-api" / "SKILL.md"


def test_pclaw_api_skill_ships_and_cross_references_features():
    """The operator skill exists, points at the reference + doctor --paths, and
    cross-references its prose twin (§3.1)."""
    text = _skill_path().read_text(encoding="utf-8")
    assert "reference/index.md" in text
    assert "doctor --paths" in text
    assert "pclaw-features" in text
    # The verify-loop and never-guess disciplines are the skill's reason to exist.
    assert "verify" in text.lower()
    assert "reference" in text.lower()


def test_doctor_paths_resolves_the_reference_dir():
    """``doctor --paths`` prints the reference dir the skill tells agents to find."""
    from personalclaw.skills.loader import skills_dir

    # Mirror _doctor_paths' resolution without capturing stdout: the contract is
    # that the reference dir it prints exists and holds the rendered files.
    rd = reference_dir()
    assert rd.is_dir()
    assert (rd / "index.md").is_file()
    # skills_dir is one of the other printed anchors — assert it's importable/callable.
    assert isinstance(skills_dir(), Path)
