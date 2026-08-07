"""Shrink-only ratchet for the committed inert-surface inventory (PLATFORM-HARDENING-FLOORS SH3.2).

``inert-surface-baseline.json`` is a GENERATED census (by
``scripts/generate_inert_surface_baseline.py``) of *declared-but-inert surfaces* across
five seam kinds — config keys, enum members, trigger kinds, ``_EDITABLE_CONFIG`` entries,
and SDK exports — each being a place where something is declared and nothing on the other
side of the seam consumes or produces it. That defect passes ordinary tests because they
hand-build the state the missing writer should have created; only a census of both ends
catches it.

This suite is the ratchet that keeps the census honest. It regenerates in-memory and
asserts every per-file inert counter **may only shrink** versus the committed baseline:

  * A per-file counter that ROSE — a NEW declared-but-inert surface — reds CI, naming the
    file and the new surface. This is ``done_when``: "adding a declared-but-unread surface
    reds CI".
  * A DECREASE is welcome (a cleanup added the missing writer/reader). The ratchet does
    NOT demand exact equality and does NOT require the count to go down — only that it
    never goes up. This is why we shipped at the MEASURED population, not zero: a never-run
    gate given teeth at zero would red every pre-existing inert surface at once (an
    outage). See the generator's module docstring.

⚠️  FORBIDDEN-TO-RAISE RULE (the ``done_when`` doc line — do not weaken it): when this test
    reds because a counter ROSE, the fix is to ADD THE MISSING WRITER OR READER for the new
    surface — NEVER to regenerate ``inert-surface-baseline.json`` to bless the higher
    number. Raising a committed count to make CI green re-hides exactly the defect this
    census exists to surface.

# how to update
    Regenerate the committed baseline ONLY when a counter LEGITIMATELY SHRANK (a real
    cleanup landed that wired the missing writer/reader), and do it in that SAME commit::

        python scripts/generate_inert_surface_baseline.py

    Each such cleanup commit should be able to point at the writer/reader it added.
"""

from __future__ import annotations

import json

from scripts.generate_inert_surface_baseline import (
    baseline_path,
    build_baseline,
    build_inventory,
    regressions,
)

# The forbidden-to-raise sentence, asserted present in both the generator and this test so
# the ``done_when`` "forbidden-to-raise doc line is present" cannot silently be dropped.
_FORBIDDEN_TO_RAISE = "add the missing writer or reader"


def _committed_inventory() -> dict:
    path = baseline_path()
    assert path.is_file(), (
        "inert-surface-baseline.json is missing — generate it with "
        "`python scripts/generate_inert_surface_baseline.py`"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_no_per_file_counter_rose_vs_committed_baseline():
    """The ratchet: no file's inert-surface count may exceed its committed count.

    A rise means a NEW declared-but-inert surface was added — a config key nothing reads,
    an enum member nobody references, a trigger kind nothing dispatches, an editable-config
    entry with no backing field, or an SDK export nothing imports. FIX IT BY ADDING THE
    MISSING WRITER OR READER — do not raise the committed number to go green.
    """
    committed = _committed_inventory()
    current = build_inventory()
    rose = regressions(committed["per_file"], current["per_file"])
    assert not rose, (
        "inert-surface count ROSE for one or more files — a new declared-but-inert "
        "surface was introduced:\n  "
        + "\n  ".join(rose)
        + "\n\nFORBIDDEN: do NOT regenerate inert-surface-baseline.json to bless the higher "
        "number. Add the missing writer or reader for the surface named above (that is the "
        "whole point of this ratchet). Regenerate the baseline ONLY when a count "
        "legitimately shrank, in that same commit."
    )


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


def test_render_is_deterministic():
    """Generating twice yields byte-identical output — no set-ordering, no timestamps, no
    absolute paths. Determinism is the whole contract; without it the ratchet is noise."""
    assert build_baseline() == build_baseline()


def test_baseline_is_well_shaped_and_sorted():
    """The committed inventory has the declared shape and every list is sorted."""
    inv = _committed_inventory()
    assert set(inv) == {"generated_from", "per_file", "totals"}, inv.keys()
    assert inv["generated_from"] == "scripts/generate_inert_surface_baseline.py"
    total = 0
    for rel, bucket in inv["per_file"].items():
        assert set(bucket) == {"inert", "surfaces"}, bucket
        assert isinstance(rel, str) and rel and not rel.startswith("/"), rel
        surfaces = bucket["surfaces"]
        assert surfaces == sorted(surfaces), f"{rel} surfaces not sorted"
        assert len(surfaces) == len(set(surfaces)), f"{rel} has duplicate surfaces"
        assert bucket["inert"] == len(surfaces), f"{rel} inert count != len(surfaces)"
        for s in surfaces:
            kind = s.split(":", 1)[0]
            assert kind in {
                "config",
                "enum",
                "trigger_kind",
                "editable_config",
                "sdk_export",
            }, s
        total += bucket["inert"]
    assert inv["totals"]["inert"] == total
    assert inv["totals"]["inert"] == sum(inv["totals"]["by_kind"].values())


def test_baseline_ships_at_a_nonzero_measured_population():
    """The census must ship at the MEASURED population, not zero.

    A zeroed baseline would mean the ratchet was given teeth before the existing inert
    surfaces were driven down — the outage this atom explicitly avoids. A nonzero total is
    the evidence we measured first and committed the real number (SH3.3 owns driving it
    down)."""
    inv = _committed_inventory()
    assert inv["totals"]["inert"] > 0, (
        "inert-surface-baseline.json reports zero inert surfaces — ship at the MEASURED "
        "population, not zero (a never-run gate given teeth at zero is an outage)."
    )


def test_a_new_inert_surface_reds_the_ratchet():
    """done_when: "adding a declared-but-unread surface reds CI".

    We do NOT add dead code to the tree — we exercise the SHARED comparison the ratchet
    relies on against a synthetic ``current`` that carries one extra surface for a real
    file, and assert the comparison flags it (naming file + surface). This proves the gate
    would red on a genuine new inert surface without perturbing the actual census."""
    committed = _committed_inventory()
    per_file = committed["per_file"]
    assert per_file, "baseline is empty; cannot exercise the ratchet"

    victim = sorted(per_file)[0]
    synthetic = {
        rel: {"inert": bucket["inert"], "surfaces": list(bucket["surfaces"])}
        for rel, bucket in per_file.items()
    }
    synthetic[victim]["surfaces"].append("enum:SyntheticProbe.NEW_MEMBER")
    synthetic[victim]["inert"] += 1

    rose = regressions(per_file, synthetic)
    assert any(victim in line and "NEW_MEMBER" in line for line in rose), rose


def test_a_new_file_with_inert_surfaces_reds_the_ratchet():
    """A file absent from the baseline that acquires an inert surface counts as a rise from
    an implicit zero — covering the "brand new file introduces an inert surface" case."""
    committed = _committed_inventory()
    synthetic = {
        rel: {"inert": bucket["inert"], "surfaces": list(bucket["surfaces"])}
        for rel, bucket in committed["per_file"].items()
    }
    synthetic["src/personalclaw/brand_new_module.py"] = {
        "inert": 1,
        "surfaces": ["sdk_export:NeverImported"],
    }
    rose = regressions(committed["per_file"], synthetic)
    assert any("brand_new_module.py" in line for line in rose), rose


def test_a_cleanup_that_shrinks_a_counter_does_not_red_the_ratchet():
    """The other side of the contract: driving a counter DOWN (a real cleanup) is welcome —
    the rise-only comparison must return no regression for a shrink."""
    committed = _committed_inventory()
    per_file = committed["per_file"]
    victim = next((rel for rel, b in per_file.items() if b["inert"] > 0), None)
    assert victim is not None, "expected at least one file with inert surfaces"
    shrunk = {
        rel: {"inert": bucket["inert"], "surfaces": list(bucket["surfaces"])}
        for rel, bucket in per_file.items()
    }
    shrunk[victim]["surfaces"].pop()
    shrunk[victim]["inert"] -= 1
    assert regressions(per_file, shrunk) == []


def test_forbidden_to_raise_doc_line_is_present():
    """done_when: "the forbidden-to-raise doc line is present" — in BOTH the generator and
    this test, so neither can drop it unnoticed."""
    from scripts import generate_inert_surface_baseline as gen

    assert _FORBIDDEN_TO_RAISE in (gen.__doc__ or "").lower()
    assert _FORBIDDEN_TO_RAISE in (__doc__ or "").lower()
