"""The real-tree checks split from ``tests/test_docs_lint_baseline.py``.

``scripts/gate_report.py`` in the rails-checks job holds the docs-lint ratchet's shrink-only half;
these hold what it does not: the committed baseline byte-matches a fresh render and is not stale on
the shrink side, and the render really scanned a corpus. They run once per CI run, in the rails job.
The module named above says what they hold
to, and its own tests prove each check works on trees they plant.
"""

from __future__ import annotations

import json

from scripts.generate_docs_lint_baseline import (
    baseline_path,
    build_baseline,
    build_inventory,
)


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


def test_baseline_ships_at_the_measured_population_whatever_it_is():
    """The census must ship at the MEASURED population — which may legitimately be zero.

    The original form of this test asserted ``total > 0``, because at introduction a zeroed
    baseline would have meant the ratchet was given teeth before the existing drift was
    fixed. That premise was about the gate's FIRST day and it is now spent: the drift has
    been driven down, and a rule that forbids reaching zero forbids ever finishing the
    cleanup it exists to drive.

    What must never happen is a baseline that is zero because it stopped LOOKING. So the
    invariant asserted here is the honest one: the committed number equals what a fresh
    render measures right now, and the renderer really did scan a corpus. Zero findings over
    a real corpus is success; zero findings over an empty corpus is the vacuous shape, and
    the corpus assertion is what tells them apart.
    """
    inv = _committed_inventory()
    fresh = _fresh_inventory()
    assert inv["totals"] == fresh["totals"], (
        "docs-lint-baseline.json disagrees with a fresh render — regenerate it in the same "
        "commit as the fix that changed the count."
    )
    assert _scanned_doc_count() > 0, (
        "the docs-lint renderer scanned ZERO documents — a baseline of zero findings then "
        "proves nothing about the docs, only that the scanner found nothing to read."
    )
