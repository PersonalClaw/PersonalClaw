"""Shrink-only ratchet for the committed docs-lint baseline (PLATFORM-HARDENING-FLOORS §6.2).

``docs-lint-baseline.json`` is a GENERATED census (by
``scripts/generate_docs_lint_baseline.py``) of docs drift across three finding kinds — dead
relative links, stale ``file.py:NNN`` citations whose file is missing, and plan
``**Status:**`` headers that contradict a DONE'd ``## Execution log`` (the "plan headers lie"
defect). CLAUDE.md and EXECUTION-PROTOCOL §3 demand docs move with the change but nothing
mechanical enforced it; this census does.

This suite is the ratchet that keeps the census honest. It regenerates in-memory and asserts
every per-file finding counter **may only shrink** versus the committed baseline:

  * A per-file counter that ROSE — a NEW dead link, stale citation, or stale header — reds
    CI, naming the file and the new finding. This is ``done_when``: "a dead link or a stale
    ``file:line`` citation reds CI".
  * A DECREASE is welcome (a doc fix landed). The ratchet does NOT demand exact equality and
    does NOT require the count to go down — only that it never goes up. This is why we
    shipped at the MEASURED population, not zero: a never-run gate given teeth at zero would
    red every pre-existing dead link/citation/header at once (an outage). See the generator's
    module docstring.

⚠️  FORBIDDEN-TO-RAISE RULE — "fix the doc, not the baseline" (the ``done_when`` doc line; do
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

from scripts.generate_docs_lint_baseline import (
    baseline_path,
    build_baseline,
    build_inventory,
    find_stale_header,
    regressions,
)

# The forbidden-to-raise phrase, asserted present in both the generator and this test so the
# ``done_when`` "forbidden-to-raise doc line is present" cannot silently be dropped.
_FORBIDDEN_TO_RAISE = "fix the doc, not the baseline"


def _committed_inventory() -> dict:
    path = baseline_path()
    assert path.is_file(), (
        "docs-lint-baseline.json is missing — generate it with "
        "`python scripts/generate_docs_lint_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_no_per_file_counter_rose_vs_committed_baseline():
    """The ratchet: no file's docs-lint finding count may exceed its committed count.

    A rise means a NEW dead link, stale citation, or stale header was introduced. FIX THE DOC
    — do not raise the committed number to go green.
    """
    committed = _committed_inventory()
    current = build_inventory()
    rose = regressions(committed["per_file"], current["per_file"])
    assert not rose, (
        "docs-lint finding count ROSE for one or more files — new docs drift was "
        "introduced:\n  "
        + "\n  ".join(rose)
        + "\n\nFORBIDDEN: do NOT regenerate docs-lint-baseline.json to bless the higher "
        "number. Fix the doc named above (repair the dead link, update/remove the stale "
        "citation, or reconcile the plan header with its execution log). Regenerate the "
        "baseline ONLY when a count legitimately shrank, in that same commit."
    )


def test_committed_baseline_is_not_stale_on_the_shrink_side():
    """Every committed per-file count must be >= the current count.

    The complement of the rise check: if a file's committed count is BELOW its current count
    that is the rise case (covered above); if it is ABOVE, a doc fix shrank the real
    population but the baseline was not regenerated, so the ratchet's floor is looser than
    reality. Neither is allowed to pass silently — a stale-high baseline should be
    regenerated (see this module's ``# how to update``)."""
    committed = _committed_inventory()
    current = build_inventory()
    stale_high = sorted(
        f"{rel}: committed {committed['per_file'][rel]['total']} > current "
        f"{current['per_file'].get(rel, {}).get('total', 0)}"
        for rel in committed["per_file"]
        if committed["per_file"][rel]["total"] > current["per_file"].get(rel, {}).get("total", 0)
    )
    assert not stale_high, (
        "the committed baseline is stale-HIGH — a doc fix shrank the finding population but "
        "docs-lint-baseline.json was not regenerated:\n  "
        + "\n  ".join(stale_high)
        + "\n\nRun `python scripts/generate_docs_lint_baseline.py` in the fix commit."
    )


def test_committed_baseline_byte_matches_a_fresh_render():
    """Belt-and-suspenders on both directions at once: while the population is unchanged the
    committed file is byte-identical to a fresh render. This is what makes a legitimate shrink
    require a regeneration in the same commit (the two prior tests already forbid a silent
    rise or a stale-high floor)."""
    fresh = build_baseline()
    committed = baseline_path().read_text(encoding="utf-8")
    assert committed == fresh, (
        "docs-lint-baseline.json does not match a fresh render. If a doc fix legitimately "
        "shrank a counter, regenerate it with `python scripts/generate_docs_lint_baseline.py`"
        " in the same commit. If a counter ROSE, do NOT regenerate — fix the doc instead."
    )


def test_render_is_deterministic():
    """Generating twice yields byte-identical output — no set-ordering, no timestamps, no
    absolute paths. Determinism is the whole contract; without it the ratchet is noise."""
    assert build_baseline() == build_baseline()


def test_baseline_is_well_shaped_and_sorted():
    """The committed inventory has the declared shape and every finding list is sorted."""
    inv = _committed_inventory()
    assert set(inv) == {"generated_from", "per_file", "totals"}, inv.keys()
    assert inv["generated_from"] == "scripts/generate_docs_lint_baseline.py"
    total = 0
    for rel, bucket in inv["per_file"].items():
        assert set(bucket) == {"findings", "total"}, bucket
        assert isinstance(rel, str) and rel and not rel.startswith("/"), rel
        assert rel.startswith("docs/") and rel.endswith(".md"), rel
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


def test_baseline_ships_at_a_nonzero_measured_population():
    """The census must ship at the MEASURED population, not zero.

    A zeroed baseline would mean the ratchet was given teeth before the existing docs drift
    was fixed — the outage this atom explicitly avoids. A nonzero total is the evidence we
    measured first and committed the real number (a separate cleanup owns driving it down)."""
    inv = _committed_inventory()
    assert inv["totals"]["total"] > 0, (
        "docs-lint-baseline.json reports zero findings — ship at the MEASURED population, "
        "not zero (a never-run gate given teeth at zero is an outage)."
    )


def test_a_new_finding_reds_the_ratchet():
    """done_when: "a dead link or a stale ``file:line`` citation reds CI".

    We do NOT add real drift to the tree — we exercise the SHARED comparison the ratchet
    relies on against a synthetic ``current`` that carries one extra finding for a real file,
    and assert the comparison flags it (naming file + finding). This proves the gate would red
    on a genuine new dead link or stale citation without perturbing the actual census."""
    committed = _committed_inventory()
    per_file = committed["per_file"]
    assert per_file, "baseline is empty; cannot exercise the ratchet"

    victim = sorted(per_file)[0]
    synthetic = {
        rel: {"total": bucket["total"], "findings": list(bucket["findings"])}
        for rel, bucket in per_file.items()
    }
    synthetic[victim]["findings"].append("dead_link:docs/does-not-exist.md")
    synthetic[victim]["total"] += 1

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
    committed = _committed_inventory()
    per_file = committed["per_file"]
    victim = next((rel for rel, b in per_file.items() if b["total"] > 0), None)
    assert victim is not None, "expected at least one file with findings"
    shrunk = {
        rel: {"total": bucket["total"], "findings": list(bucket["findings"])}
        for rel, bucket in per_file.items()
    }
    shrunk[victim]["findings"].pop()
    shrunk[victim]["total"] -= 1
    assert regressions(per_file, shrunk) == []


# ── plan-hygiene reproduction (done_when's testable clause) ──────────────────────


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
    """done_when: "the plan-hygiene checker reproduces the known stale-header audit findings
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
    """done_when: "the forbidden-to-raise doc line is present" — in BOTH the generator and
    this test, so neither can drop it unnoticed."""
    from scripts import generate_docs_lint_baseline as gen

    assert _FORBIDDEN_TO_RAISE in (gen.__doc__ or "").lower()
    assert _FORBIDDEN_TO_RAISE in (__doc__ or "").lower()
