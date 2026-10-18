"""The source-tree checks split from
``tests/test_wf_error_codes_registry.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from rails_repo import REPO
from wf_error_codes_registry_shared import scan

from personalclaw.workflows.error_codes import WF_ERROR_CODES

_CORE = REPO / "src" / "personalclaw"


#: The registry itself — every code appears here as a dict KEY, so counting this file would
#: make direction 2 vacuous. See the module docstring.
_REGISTRY_MODULE = _CORE / "workflows" / "error_codes.py"


#: Codes raised in core: 162 across 10 modules (159 at #3499, plus the three). A FLOOR: if
#: the scanner stops matching, both directions above pass trivially, and a rail that inspected
#: nothing must never read as clean.
_RAISED_CODE_FLOOR = 155


#: The `_STATUS_MAP` translation-table size measured at #3499. Also a floor, for the same
#: reason: a map check over an empty map proves nothing.
_MAPPED_CODE_FLOOR = 40


def _core_modules() -> list[Path]:
    """Every package module but the registry's own, from the suite's one source index."""
    return [p for p in source_index.package_files() if p != _REGISTRY_MODULE]


def _scan_core() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    raised: dict[str, list[str]] = {}
    mapped: dict[str, list[str]] = {}
    for p in _core_modules():
        rel = str(p.relative_to(_CORE.parents[1]))
        r, m = scan(source_index.tree(p), rel)
        for bucket, found in ((raised, r), (mapped, m)):
            for code, sites in found.items():
                bucket.setdefault(code, []).extend(sites)
    return raised, mapped


def _floor_guard(raised: dict[str, list[str]]) -> None:
    assert len(raised) >= _RAISED_CODE_FLOOR, (
        f"the scan found only {len(raised)} raised WF codes, below the {_RAISED_CODE_FLOOR} "
        f"measured at #3499 — the scanner stopped matching, so a clean result means nothing. "
        f"Fix the scan before trusting it."
    )


def test_every_raised_code_is_registered():
    """Direction 1: the registry cannot fall behind the emitters."""
    raised, _ = _scan_core()
    _floor_guard(raised)
    unregistered = sorted(c for c in raised if c not in WF_ERROR_CODES)
    assert not unregistered, (
        "these WF codes are raised in core with no WF_ERROR_CODES row. A WF code is a "
        "contract identifier a template author and an MCP caller branch on — register it, "
        "with a meaning DERIVED from the raise site, in the same change that ships it:\n"
        + "\n".join(f"  {c}  ({', '.join(raised[c][:3])})" for c in unregistered)
    )


def test_every_registered_code_is_still_raised():
    """Direction 2 — the half people skip, and the one that stops the registry rotting.

    A row nothing raises is a contract that quietly stopped existing: an author who reads
    it, or branches on it, is relying on a code the engine can no longer produce. A MAP
    entry does not count (see the module docstring): being translatable into a wire code is
    not the same as being emitted.
    """
    raised, _ = _scan_core()
    _floor_guard(raised)
    orphans = sorted(c for c in WF_ERROR_CODES if c not in raised)
    assert not orphans, (
        "these WF_ERROR_CODES rows are no longer raised anywhere in core. Remove the row in "
        "the same change that removed its last raise site — a registry of codes that no "
        "longer exist reads as authoritative and is not:\n" + "\n".join(f"  {c}" for c in orphans)
    )


def test_every_mapped_code_is_registered():
    """``handlers.py``'s ``_STATUS_MAP`` translates WF codes into the wire vocabulary, so a
    key it holds is a code the HTTP surface promises to answer for. An unregistered one
    would be a documented HTTP behaviour with no documented cause."""
    _, mapped = _scan_core()
    assert len(mapped) >= _MAPPED_CODE_FLOOR, (
        f"only {len(mapped)} WF codes found in a translation table, below the "
        f"{_MAPPED_CODE_FLOOR} measured at #3499 — the scanner is broken, not the code."
    )
    unregistered = sorted(c for c in mapped if c not in WF_ERROR_CODES)
    assert (
        not unregistered
    ), "these WF codes are translated to a wire code but have no registry row:\n" + "\n".join(
        f"  {c}" for c in unregistered
    )


def test_both_directions_have_teeth_against_main_state():
    """The two failures the rail exists to produce, produced.

    Neither direction is falsifiable from a green run alone, and the shape of this change
    makes that sharper than usual: on ``origin/main`` there was no registry at all, so
    "fails on main" cannot be a red — it is an ImportError, which is an absence of evidence,
    not a measurement. This test is the replacement artifact. It runs both
    directions against synthetic states standing in for the two ways the registry can be
    wrong, and asserts each produces the failure it promises:

    * **main's state** — an EMPTY registry against the real tree. Every code raised in core
      is unregistered, which is precisely the gap #3499 measured (159 raised, 0 registered,
      1 documented anywhere).
    * **a rotted registry** — a row no core module raises. This is the direction people skip,
      and the one that turns a registry into a list of codes that no longer exist.
    """
    raised, _ = _scan_core()
    _floor_guard(raised)

    empty_registry: dict[str, str] = {}
    unregistered = sorted(c for c in raised if c not in empty_registry)
    assert len(unregistered) == len(raised) >= _RAISED_CODE_FLOOR, (
        "direction 1 did not flag every code against an empty registry, so a green run "
        "against the real registry proves nothing"
    )

    rotted = {**WF_ERROR_CODES, "WF_CODE_DELETED_LAST_RELEASE": "a code nothing raises"}
    orphans = sorted(c for c in rotted if c not in raised)
    assert orphans == ["WF_CODE_DELETED_LAST_RELEASE"], (
        f"direction 2 did not flag an unraised registry row ({orphans}), so it is not "
        f"guarding against the registry rotting"
    )


def test_the_registry_module_is_excluded_from_the_scan():
    """The exclusion direction 2 rests on, asserted rather than trusted.

    ``error_codes.py`` holds all 162 codes as dict keys. If it were scanned, every row
    would have a 'raise site' in the registry itself and direction 2 would be a tautology —
    green on a registry whose codes had all been deleted from the engine.
    """
    assert _REGISTRY_MODULE.is_file()
    assert _REGISTRY_MODULE not in _core_modules(), "the registry module is being scanned"
    own_raised, own_mapped = scan(_REGISTRY_MODULE.read_text(encoding="utf-8"), "error_codes.py")
    assert len(own_mapped) == len(WF_ERROR_CODES), (
        "every registry row should read as a MAP key in its own module — if this drifts the "
        "exclusion above may no longer be what makes direction 2 honest"
    )
    assert not own_raised
