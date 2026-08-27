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

    ONE further case, added 2026-08-19: a counter may also rise because the CENSUS started
    seeing a population it was previously blind to — not because anything became inert.
    `sdk/channel.py` was the only `sdk/` module with no `__all__`, and the ``sdk_export``
    detector keys on `__all__`, so its 104 published re-exports were invisible to this
    ratchet for the whole life of the facade (it counted 0). Declaring the surface moved it
    to 104 without changing one line of what the module publishes.

    That case is NOT an escape hatch, and it does not soften the rule above. It is
    admissible only with the arithmetic attached: render the census against the PRE-change
    file with an ``__all__`` mechanically derived from its own imports, and show the count is
    identical. If the two numbers differ, the difference is a real new inert surface and the
    forbidden-to-raise rule applies to it in full. (Measured for the case above: 104 before,
    104 after. Of those 104, 97 are imported by a bundled channel app in the apps repo — the
    census cannot see across the repo boundary by construction, which the generator's own
    docstring says of every ``sdk_export``.)
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from scripts.generate_inert_surface_baseline import (
    _attribute_names_in_src,
    _enum_members,
    _inert_enum_members,
    _iterated_enum_classes,
    _parse,
    _src_py_files,
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


@pytest.mark.timeout(300)
def test_render_is_deterministic():
    """Generating twice yields byte-identical output — no set-ordering, no timestamps, no
    absolute paths. Determinism is the whole contract; without it the ratchet is noise.

    🔴 The timeout is RAISED, not the work reduced, and the number is measured. Two builds
    take **13.1s** on an idle machine (6.7s + 6.4s) — nowhere near the suite's 120s default.
    But `build_baseline()` is a CPU-bound AST walk of the whole tree, run twice, competing
    with 17 other xdist workers, and CI starved it past 120s twice (#1205, #1222). That is
    ≥9x, so 300s is headroom rather than a new cliff. Measured again through pytest
    rather than raw calls: this ONE test took **51.5s** under light local load, so the
    margin under 120s was thin, not comfortable — the 13.1s figure is the floor.

    Serializing this test instead is NOT available: the suite runs `--dist worksteal`
    (`pyproject.toml`), which ignores `xdist_group` — measured in PHF-9, where switching to
    `loadgroup` cost +58% wall time and was rejected.

    Building once and comparing a re-render would be cheaper and WRONG: the contract is that
    *collection* is order-stable too, so both builds have to be real.
    """
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


# ── enum census: whole-enum iteration clears a class (PHF-12) ────────────────
#
# The census once reported an enum member inert whenever its NAME was never accessed as an
# attribute, which called every iteration-only enum dead: `workflows/publish.py:136`
# validates author-supplied lineage edges against `{e.value for e in Lineage}`, so
# `Lineage.INFORMED_BY` and `Lineage.RELATED` are reachable and were reported anyway. These
# tests pin both directions of the corrected rule against a FIXTURE tree — never by adding
# dead code to `src/` — plus a vacuity guard that the real census still finds a population.


def _fixture_tree(tmp_path: Path, modules: dict[str, str]) -> list[Path]:
    """Write ``{filename: source}`` into ``tmp_path`` and return the sorted file list."""
    for name, source in modules.items():
        (tmp_path / name).write_text(textwrap.dedent(source), encoding="utf-8")
    return sorted(tmp_path.glob("*.py"))


def _inert_in(tmp_path: Path, modules: dict[str, str]) -> set[str]:
    """``{"Class.MEMBER"}`` the enum detector reports for a fixture tree."""
    files = _fixture_tree(tmp_path, modules)
    return {surface for _, surface in _inert_enum_members(files, _attribute_names_in_src(files))}


def test_an_enum_consumed_only_by_iteration_is_not_flagged(tmp_path):
    """Every shape the detector claims to support clears its class, with no member of any of
    them touched as an attribute anywhere in the fixture tree."""
    inert = _inert_in(
        tmp_path,
        {
            "looped.py": """
                from enum import Enum

                class Looped(Enum):
                    ONE = "one"
                    TWO = "two"

                def kinds():
                    out = []
                    for member in Looped:
                        out.append(member.value)
                    return out
                """,
            "comprehended.py": """
                from enum import Enum

                class Comprehended(str, Enum):
                    ALPHA = "alpha"
                    BETA = "beta"

                ALLOWED = {e.value for e in Comprehended}
                """,
            "called.py": """
                from enum import Enum

                class Called(Enum):
                    RED = "red"
                    BLUE = "blue"

                ORDERED = tuple(sorted(Called, key=str))
                """,
        },
    )
    assert inert == set(), f"iteration-only enums were flagged (false red): {sorted(inert)}"


def test_an_enum_consumed_nowhere_is_still_flagged(tmp_path):
    """The rule can still fail: a genuinely unreferenced member is reported. Two members of
    the SAME class are declared and one is read as an attribute, so this also proves the
    per-member half survives — only iteration clears a whole class."""
    inert = _inert_in(
        tmp_path,
        {
            "orphan.py": """
                from enum import Enum

                class Orphan(Enum):
                    GHOST = "ghost"
                    PHANTOM = "phantom"
                """,
            "partial.py": """
                from enum import Enum

                class Partial(Enum):
                    USED = "used"
                    UNUSED = "unused"

                DEFAULT = Partial.USED
                """,
        },
    )
    assert inert == {"Orphan.GHOST", "Orphan.PHANTOM", "Partial.UNUSED"}, sorted(inert)


def test_iterating_one_enum_does_not_clear_a_same_named_enum_elsewhere(tmp_path):
    """Resolution is import-aware, not name-based. ``src/`` declares seven distinct
    ``Verdict`` enums; if iterating one cleared them all, a real inert member would go
    unreported the moment any namesake was iterated somewhere."""
    inert = _inert_in(
        tmp_path,
        {
            "shadow_a.py": """
                from enum import Enum

                class Shadow(Enum):
                    A_ONLY = "a"
                """,
            "shadow_b.py": """
                from enum import Enum

                class Shadow(Enum):
                    B_ONLY = "b"

                VALUES = [s.value for s in Shadow]
                """,
        },
    )
    assert inert == {"Shadow.A_ONLY"}, sorted(inert)


def test_the_lineage_false_red_is_gone_and_its_iteration_site_is_seen():
    """The finding itself, on the real tree: ``Lineage`` is recognised as iterated (its
    validation set in ``workflows/publish.py``) and no ``Lineage`` member is reported.

    If the validation that iterates ``Lineage`` is ever deleted, this test SHOULD red — the
    members would then genuinely be unreachable."""
    files = _src_py_files()
    enum_names: dict[Path, set[str]] = {}
    for f in files:
        tree = _parse(f)
        if tree is None:
            continue
        names = {cls for cls, _ in _enum_members(tree)}
        if names:
            enum_names[f.resolve()] = names
    iterated = _iterated_enum_classes(files, enum_names)
    publish = next(f for f in files if f.as_posix().endswith("workflows/publish.py"))
    assert (publish.resolve(), "Lineage") in iterated

    inert = {surface for _, surface in _inert_enum_members(files, _attribute_names_in_src(files))}
    assert not [s for s in inert if s.startswith("Lineage.")], sorted(inert)


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


# ── PHF-13: the value-lookup ruling (NOT a widening — a pinned decision) ─────────────────
#
# ``PHF-13`` audited every ``E(value)`` site behind the surviving enum surfaces and ruled
# AGAINST teaching the detector that shape: five of the six sites either never execute in
# production or read only values this codebase itself wrote, so a syntactic rule would FALSE-
# CLEAR them. A false clear passes the shrink-only ratchet silently (the count goes DOWN), so
# the ruling needs its own rail. These two tests are it.


def test_value_lookup_alone_does_not_clear_a_member(tmp_path):
    """A member reachable only through ``E(value)`` is STILL REPORTED — on purpose.

    ``E(value)`` does not prove reachability: the construction may never execute, and its value
    may come from state we wrote ourselves. Whoever wants to change this must first re-run
    ``PHF-13``'s per-site provenance audit (verdict table in the generator docstring and in
    ``PLATFORM-HARDENING-FLOORS``'s execution log) — not just make this test green.
    """
    inert = _inert_in(
        tmp_path,
        {
            "deserializer.py": """
                from enum import Enum

                class Coerced(str, Enum):
                    WRITTEN = "written"
                    NEVER_WRITTEN = "never_written"

                def load(row):
                    # Value lookup over a column WE wrote. Reaches NEVER_WRITTEN only if some
                    # writer ever produced it — and none does.
                    return Coerced(row["state"])

                def save(rec):
                    rec["state"] = Coerced.WRITTEN.value
                """,
        },
    )
    assert inert == {"Coerced.NEVER_WRITTEN"}, (
        "value-lookup construction cleared a member the census cannot prove is reachable; "
        f"got {sorted(inert)} — see PHF-13's verdict table before widening this rule"
    )


def test_the_audited_value_lookup_call_sites_are_wired_and_the_members_re_verdicted():
    """``PHF-13``'s ruling, RE-VERDICTED after ``WF2LOO-13`` wired the judge contract.

    PHF-13 reported ``Verdict.REPLAN``, ``Ratchet.RELAXED`` and ``Actor.WORKER`` inert even though
    each sits on a class with an ``E(value)`` construction, because the FUNCTIONS holding those
    constructions had no production caller — ``engine.py`` restated the judge aggregation rule
    instead of importing it. This test used to assert that dead-call-site premise and told the next
    reader to re-verdict if it ever changed. WF2LOO-13 changed it: ``validate_verdict``,
    ``hints_from_dict`` and ``resolve_transition`` are all called from the live judge path now.

    So the assertion is inverted rather than dropped, and it pins the re-verdict:

    * ``Verdict.REPLAN`` and ``Actor.WORKER`` left the baseline — the judge gate names both
      explicitly (``_judge_gate_outcome`` maps REPLAN; the actor ruling picks WORKER for a
      ``self_judge`` gate), so they are reachable by NAME, not merely by value lookup.
    * ``Ratchet.RELAXED`` stays inert, and that is the honest verdict: nothing names it, and no
      bundled template declares ``ratchet: relaxed``. Its only route in is a user template, which
      is exactly what "externally reachable, internal only" means in the detector's table.
    """
    owners = {
        "validate_verdict": "workflows/judge_contract.py",
        "hints_from_dict": "workflows/judge_contract.py",
        "resolve_transition": "workflows/judge_actors.py",
    }
    callers: dict[str, list[str]] = {name: [] for name in owners}
    for f in _src_py_files():
        rel = f.as_posix()
        text = f.read_text(encoding="utf-8")
        for name, owner in owners.items():
            if rel.endswith(owner):
                continue
            if f"{name}(" in text or f"import {name}" in text:
                callers[name].append(rel)
    stranded = [name for name, found in callers.items() if not found]
    assert not stranded, (
        f"{stranded} lost its production caller — the judge contract is authored-and-unrun again "
        "(the WF2LOO-12 defect). Re-verdict the members PHF-13 covers before touching the baseline."
    )

    baseline = _committed_inventory()
    flagged = {
        surface for entry in baseline["per_file"].values() for surface in entry.get("surfaces", [])
    }
    for cleared in ("enum:Verdict.REPLAN", "enum:Actor.WORKER"):
        assert cleared not in flagged, (
            f"{cleared} is flagged inert again while its call site is wired — either the wiring "
            "regressed or the detector's verdict did; read the code before regenerating"
        )
    assert "enum:Ratchet.RELAXED" in flagged, (
        "Ratchet.RELAXED left the baseline — if a template or a call site now names it, say so "
        "here; if not, this is a false clear and the ratchet just lost a member it was watching"
    )


def test_the_value_lookup_ruling_is_recorded_in_the_generator():
    """The verdicts are the deliverable, so they must live where the next reader lands: in the
    detector that produces the flags, not only in a plan log."""
    from scripts import generate_inert_surface_baseline as gen

    doc = (gen._inert_enum_members.__doc__ or "").lower()
    assert "deliberately not taught" in doc, "the PHF-13 ruling is missing from the detector"
    for marker in ("externally reachable", "internal only", "dead call site"):
        assert marker in doc, f"the per-site verdict vocabulary lost {marker!r}"
    assert "construction is the known remaining false-red shape" not in doc, (
        "PHF-12's superseded premise is asserted again in the detector docstring; "
        "judge_contract.py:342 has no production caller, so it does not make REPLAN reachable"
    )


def test_forbidden_to_raise_doc_line_is_present():
    """done_when: "the forbidden-to-raise doc line is present" — in BOTH the generator and
    this test, so neither can drop it unnoticed."""
    from scripts import generate_inert_surface_baseline as gen

    assert _FORBIDDEN_TO_RAISE in (gen.__doc__ or "").lower()
    assert _FORBIDDEN_TO_RAISE in (__doc__ or "").lower()
