"""The real-tree checks split from ``tests/test_inert_surface_baseline.py``.

``scripts/gate_report.py`` in the rails-checks job holds the inert-surface ratchet's shrink-only
half; these hold what it does not: the committed baseline byte-matches a fresh render and is not
stale on the shrink side, and two census kinds still find a population. They run once per CI run, in
the rails job. The module named above says what they hold
to, and its own tests prove each check works on trees they plant.
"""

from __future__ import annotations

import json

import pytest

from scripts.generate_inert_surface_baseline import (
    KIND_CONFIG_READER,
    _enum_members,
    _parse,
    _src_py_files,
    baseline_path,
    build_baseline,
    build_inventory,
)


def _committed_inventory() -> dict:
    path = baseline_path()
    assert path.is_file(), (
        "inert-surface-baseline.json is missing — generate it with "
        "`python scripts/generate_inert_surface_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.timeout(300)
def test_committed_baseline_is_not_stale_on_the_shrink_side():
    """Every committed per-file count must be >= the current count.

    The complement of the rise check: if a file's committed count is BELOW its current
    count that is the rise case (covered above); if it is ABOVE, a cleanup shrank the real
    population but the baseline was not regenerated, so the ratchet's floor is looser than
    reality. Neither is allowed to pass silently — a stale-high baseline should be
    regenerated (see this module's ``# how to update``)."""
    committed = _committed_inventory()
    current = build_inventory()
    stale_high = sorted(
        f"{rel}: committed {committed['per_file'][rel]['inert']} > current "
        f"{current['per_file'].get(rel, {}).get('inert', 0)}"
        for rel in committed["per_file"]
        if committed["per_file"][rel]["inert"] > current["per_file"].get(rel, {}).get("inert", 0)
    )
    assert not stale_high, (
        "the committed baseline is stale-HIGH — a cleanup shrank the inert population but "
        "inert-surface-baseline.json was not regenerated:\n  "
        + "\n  ".join(stale_high)
        + "\n\nRun `python scripts/generate_inert_surface_baseline.py` in the cleanup commit."
    )


@pytest.mark.timeout(300)
def test_committed_baseline_byte_matches_a_fresh_render():
    """Belt-and-suspenders on both directions at once: while the population is unchanged the
    committed file is byte-identical to a fresh render. This is what makes a legitimate
    shrink require a regeneration in the same commit (the two prior tests already forbid a
    silent rise or a stale-high floor)."""
    fresh = build_baseline()
    committed = baseline_path().read_text(encoding="utf-8")
    assert committed == fresh, (
        "inert-surface-baseline.json does not match a fresh render. If a cleanup "
        "legitimately shrank a counter, regenerate it with "
        "`python scripts/generate_inert_surface_baseline.py` in the same commit. If a "
        "counter ROSE, do NOT regenerate — add the missing writer or reader instead."
    )


@pytest.mark.timeout(300)
def test_the_real_config_reader_census_fires_and_clears_an_accessor():
    """Vacuity + real-tree calibration in both directions."""
    inv = build_inventory()
    assert inv["totals"]["by_kind"][KIND_CONFIG_READER] > 0
    flagged = {surface for bucket in inv["per_file"].values() for surface in bucket["surfaces"]}
    assert f"{KIND_CONFIG_READER}:routing.energy_sampling" in flagged
    for wired in [
        "agent.self_qa.enabled",
        "external_access.a2a.enabled",
        "external_access.openai.enabled",
        "inbox.enabled",
        "knowledge.embed_batch_size",
        "knowledge.embed_retry_budget",
        "knowledge.max_mentions_per_claim",
        "knowledge.require_citations",
        "knowledge.similarity_degree_cap",
        "knowledge.synthesis_window",
        "learning.self_model_enabled",
        "resilience.remediation.enabled",
        "sources.enabled",
        "workflows.max_concurrent_llm_nodes",
        "workflows.retention_per_def",
    ]:
        assert f"{KIND_CONFIG_READER}:{wired}" not in flagged


@pytest.mark.timeout(300)
def test_the_enum_census_still_finds_a_nontrivial_population():
    """Vacuity guard. Clearing a whole class per iteration site is a broad clear, so a bug
    that over-cleared (or a detector that silently matched everything) would leave the enum
    census reporting nothing while every other kind still looked healthy. The census must
    still SEE many enum classes and still REPORT some inert members.

    🔴 The timeout is RAISED, not the work reduced, and the number is measured — the same
    ruling `test_render_is_deterministic` records above, for the same reason and at the same
    number. This is the file's second-heaviest test: it walks and parses every `src` file for
    the `>50` claim, then `build_inventory()` walks the tree AGAIN. Measured through pytest at
    load average 40: **19.2s** of call time alone, and **28.5s** running with the rest of this
    file — second only to the 42.8s of `test_render_is_deterministic`, which is already at 300
    for having crossed 120s in CI twice (#1205, #1222). CI starved this one past 120s the same
    way. Both assertions are untouched; only the wall-clock budget moves.

    Neither half of the work can be dropped without gutting the guard. The `>50` sweep is
    what proves the walk still SEES the population, and the `>= 5` half has to read the real
    census output — a stubbed or cached inventory would assert on the stub, which is exactly
    the suspiciously-clean census this test exists to catch. `_parse()` is deliberately
    uncached tree-wide (see `test_render_is_deterministic`: both builds have to be real), so
    there is no memoization to lean on here either.
    """
    files = _src_py_files()
    classes = {
        cls for f in files if (tree := _parse(f)) is not None for cls, _ in _enum_members(tree)
    }
    assert len(classes) > 50, f"only {len(classes)} enum classes seen — the walk is broken"
    assert build_inventory()["totals"]["by_kind"]["enum"] >= 5, (
        "the enum census reports (almost) nothing — whole-class clearing has over-reached; "
        "shrink the detected shapes rather than trusting a suspiciously clean census"
    )
