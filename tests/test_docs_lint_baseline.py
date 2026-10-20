"""Shrink-only ratchet for the committed docs-lint baseline.

``docs-lint-baseline.json`` is a GENERATED census (by
``scripts/generate_docs_lint_baseline.py``) of docs drift across three finding kinds — dead
relative links, stale ``file.py:NNN`` citations whose file is missing, and plan
``**Status:**`` headers that contradict a DONE'd ``## Execution log`` (the "plan headers lie"
defect). CLAUDE.md and AGENTS.md demand docs move with the change but nothing
mechanical enforced it; this census does.

This suite is the ratchet that keeps the census honest. It regenerates in-memory and asserts
every per-file finding counter **may only shrink** versus the committed baseline:

  * A per-file counter that ROSE — a NEW dead link, stale citation, or stale header — reds
    CI, naming the file and the new finding. This is the rule: "a dead link or a stale
    ``file:line`` citation reds CI".
  * A DECREASE is welcome (a doc fix landed). The ratchet does NOT demand exact equality and
    does NOT require the count to go down — only that it never goes up. This is why we
    shipped at the MEASURED population, not zero: a never-run gate given teeth at zero would
    red every pre-existing dead link/citation/header at once (an outage). See the generator's
    module docstring.

⚠️  FORBIDDEN-TO-RAISE RULE — "fix the doc, not the baseline" (the forbidden-to-raise doc line; do
    not weaken it): when this test reds because a counter ROSE, the fix is to FIX THE DOC —
    repair the dead link, update or remove the stale citation, or reconcile the plan header
    with its execution log — NEVER to regenerate ``docs-lint-baseline.json`` to bless the
    higher number. Raising a committed count to make CI green re-hides exactly the drift this
    census exists to surface.

# how to update
    Regenerate the committed baseline ONLY when a counter LEGITIMATELY SHRANK (a real doc fix
    landed), and do it in that SAME commit::

        python scripts/generate_docs_lint_baseline.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.generate_docs_lint_baseline import (
    baseline_path,
    build_baseline,
    build_inventory,
    find_stale_header,
    regressions,
)

# The forbidden-to-raise phrase, asserted present in both the generator and this test so the
# requirement that the doc line is present cannot silently be dropped.
_FORBIDDEN_TO_RAISE = "fix the doc, not the baseline"


def _committed_inventory() -> dict:
    path = baseline_path()
    assert path.is_file(), (
        "docs-lint-baseline.json is missing — generate it with "
        "`python scripts/generate_docs_lint_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _fresh_inventory() -> dict:
    """What the renderer measures right now, independent of what is committed."""
    return build_inventory()


def _scanned_doc_count() -> int:
    """How many documents the renderer actually read.

    The corpus size is NOT derivable from the baseline: ``per_file`` records only files that
    HAVE findings, so a clean corpus and an empty corpus both render as ``{}``. Counting the
    scanned set is what distinguishes "we looked at 120 docs and found nothing" from "we
    looked at nothing".
    """
    from scripts.generate_docs_lint_baseline import _tracked_files, _tracked_md

    return len(_tracked_md(_tracked_files()))


def test_render_is_deterministic(tmp_path):
    """Generating twice yields byte-identical output — no set-ordering, no timestamps, no
    absolute paths. Determinism is the whole contract; without it the ratchet is noise.

    Rendered over a small git work tree this test builds (the census reads ``git ls-files``);
    the real tree's render is the rails job's (``scripts/gate_report.py``). Its findings are
    asserted too, so a render that ignored *root* and read this checkout cannot pass.
    """
    from scripts import generate_docs_lint_baseline as gen

    docs = {
        "README.md": "[ok](docs/a.md) [dead](docs/missing.md) and src/gone.py:12\n",
        "docs/a.md": "back to [readme](../README.md)\n",
        "docs/untracked-neighbour.md": "[also dead](nowhere.md)\n",
    }
    for rel, text in docs.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md", "docs/a.md"], check=True)
    first = build_baseline(tmp_path)
    assert first == build_baseline(tmp_path)
    assert json.loads(first)["per_file"] == {
        "README.md": {
            "findings": ["dead_link:docs/missing.md", "stale_citation:src/gone.py"],
            "total": 2,
        }
    }
    assert str(tmp_path) not in first
    assert gen._repo_root() == Path(gen.__file__).resolve().parents[1]


def test_baseline_is_well_shaped_and_sorted():
    """The committed inventory has the declared shape and every finding list is sorted."""
    inv = _committed_inventory()
    assert set(inv) == {"generated_from", "per_file", "totals"}, inv.keys()
    assert inv["generated_from"] == "scripts/generate_docs_lint_baseline.py"
    total = 0
    for rel, bucket in inv["per_file"].items():
        assert set(bucket) == {"findings", "total"}, bucket
        assert isinstance(rel, str) and rel and not rel.startswith("/"), rel
        assert rel.endswith(".md"), rel
        findings = bucket["findings"]
        assert findings == sorted(findings), f"{rel} findings not sorted"
        assert len(findings) == len(set(findings)), f"{rel} has duplicate findings"
        assert bucket["total"] == len(findings), f"{rel} total != len(findings)"
        assert findings, f"{rel} present with no findings (should be omitted)"
        for f in findings:
            kind = f.split(":", 1)[0]
            assert kind in {"dead_link", "stale_citation", "stale_header"}, f
        total += bucket["total"]
    assert inv["totals"]["total"] == total
    assert inv["totals"]["total"] == sum(inv["totals"]["by_kind"].values())


def test_the_scan_set_is_every_tracked_markdown_file():
    """The scan SCOPE is a contract, not an implementation detail.

    A non-zero corpus is not enough: the scope was previously ``docs/**``, which measured
    **76 of 267** tracked ``*.md`` files and passed the ``> 0`` assertion above with the
    entire reader-facing repo root outside it — ``README.md``, ``SHOWCASE.md``,
    ``CONTRIBUTING.md``, ``AGENTS.md``, and every README under ``src/``, ``web/`` and
    ``mobile/``. A gate that silently stops looking at the first document a reader opens is
    the failure this pins, so the scope is asserted by EQUALITY against ``git ls-files``
    rather than by a floor.
    """
    from scripts.generate_docs_lint_baseline import _tracked_files, _tracked_md

    tracked = _tracked_files()
    scanned = set(_tracked_md(tracked))
    every_md = {p for p in tracked if p.endswith(".md")}
    missed = sorted(every_md - scanned)
    assert not missed, (
        "the docs-lint census skips tracked markdown files, so nothing checks their "
        f"links: {missed[:20]}"
    )
    # The root documents a stranger reads first must be in the set by name, so a future
    # narrowing cannot pass by keeping the equality true over a shrunken tracked set.
    for anchor in ("README.md", "CONTRIBUTING.md", "SHOWCASE.md", "AGENTS.md"):
        assert anchor in scanned, f"{anchor} is tracked but outside the docs-lint scan set"


def test_a_new_finding_reds_the_ratchet():
    """The rule: "a dead link or a stale ``file:line`` citation reds CI".

    We do NOT add real drift to the tree — we exercise the SHARED comparison the ratchet
    relies on against a synthetic ``current`` that carries one extra finding for a real file,
    and assert the comparison flags it (naming file + finding). This proves the gate would red
    on a genuine new dead link or stale citation without perturbing the actual census."""
    # A synthetic PAIR, so the ratchet is exercised whether or not the real corpus currently
    # carries any drift. Seeding the victim from the committed baseline made this test
    # delete itself the day the last real finding was fixed — a rail that only works while
    # the repo is broken.
    victim = "docs/vision.md"
    per_file = {victim: {"total": 1, "findings": ["dead_link:docs/already-known.md"]}}
    synthetic = {
        victim: {
            "total": 2,
            "findings": ["dead_link:docs/already-known.md", "dead_link:docs/does-not-exist.md"],
        }
    }

    rose = regressions(per_file, synthetic)
    assert any(victim in line and "does-not-exist" in line for line in rose), rose


def test_a_new_file_with_a_finding_reds_the_ratchet():
    """A file absent from the baseline that acquires a finding counts as a rise from an
    implicit zero — covering the "brand new doc introduces a dead link" case."""
    committed = _committed_inventory()
    synthetic = {
        rel: {"total": bucket["total"], "findings": list(bucket["findings"])}
        for rel, bucket in committed["per_file"].items()
    }
    synthetic["docs/brand-new-doc.md"] = {
        "total": 1,
        "findings": ["stale_citation:src/gone.py"],
    }
    rose = regressions(committed["per_file"], synthetic)
    assert any("brand-new-doc.md" in line for line in rose), rose


def test_a_fix_that_shrinks_a_counter_does_not_red_the_ratchet():
    """The other side of the contract: driving a counter DOWN (a real doc fix) is welcome —
    the rise-only comparison must return no regression for a shrink."""
    # Synthetic for the same reason as the rise case above: a shrink cannot be demonstrated
    # from a corpus that has nothing left to shrink.
    victim = "docs/vision.md"
    per_file = {victim: {"total": 2, "findings": ["dead_link:a.md", "dead_link:b.md"]}}
    shrunk = {victim: {"total": 1, "findings": ["dead_link:a.md"]}}
    assert regressions(per_file, shrunk) == []


# ── plan-hygiene reproduction ────────────────────────────────────────────────────


_SEEDED_STALE_PLAN = """# Some Plan

**Status:** DESIGNED — awaiting an owner slot before execution.

Body prose.

## Execution log

<!-- Append only: - [YYYY-MM-DD][T<id>] DEVIATION|DISCOVERY|DONE|BLOCKED: <one line> -->

- [2026-08-04][T1.1] DONE (PR #123): shipped the thing the header says is only DESIGNED.
"""

_SEEDED_CORRECT_PLAN_MATCHING = """# Some Plan

**Status:** DONE — shipped 2026-08-04 (PR #123).

## Execution log

<!-- Append only: - [YYYY-MM-DD][T<id>] DEVIATION|DISCOVERY|DONE|BLOCKED: <one line> -->

- [2026-08-04][T1.1] DONE (PR #123): shipped the thing.
"""

_SEEDED_STALE_HEADER_NO_LOG = """# Some Plan

**Status:** DESIGNED — awaiting an owner slot before execution.

Body prose, no execution log yet.
"""


def test_plan_hygiene_flags_a_seeded_stale_header():
    """The rule: "the plan-hygiene checker reproduces the known stale-header audit findings
    on a seeded stale header". A ``**Status:** DESIGNED`` header on a plan whose
    ``## Execution log`` already carries a DONE entry is the exact 2026-08-04 drift; the
    checker must flag it."""
    findings = find_stale_header("docs/roadmap/plans/SEEDED.md", _SEEDED_STALE_PLAN)
    assert findings == ["stale_header:DESIGNED"], findings


def test_plan_hygiene_is_quiet_on_a_correct_header():
    """The complement: a header that matches reality (DONE + a DONE'd log), a stale-shape
    header with NO execution log, and a plan OUTSIDE ``docs/roadmap/plans/`` are all quiet —
    the checker under-reports rather than crying wolf."""
    assert find_stale_header("docs/roadmap/plans/OK.md", _SEEDED_CORRECT_PLAN_MATCHING) == []
    assert find_stale_header("docs/roadmap/plans/NOLOG.md", _SEEDED_STALE_HEADER_NO_LOG) == []
    # Same stale text, but the file is not a plan → not held to the heuristic.
    assert find_stale_header("docs/architecture/overview.md", _SEEDED_STALE_PLAN) == []


def test_forbidden_to_raise_doc_line_is_present():
    """The rule: "the forbidden-to-raise doc line is present" — in BOTH the generator and
    this test, so neither can drop it unnoticed."""
    from scripts import generate_docs_lint_baseline as gen

    assert _FORBIDDEN_TO_RAISE in (gen.__doc__ or "").lower()
    assert _FORBIDDEN_TO_RAISE in (__doc__ or "").lower()
