"""The source-tree checks split from
``tests/test_durability_inventory_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import functools

import source_index
from durability_inventory_census_shared import _census_of, _HomeCensus, _shown, _unaccounted
from rails_repo import REPO

from personalclaw.durability import inventory as inv

_SRC = REPO / "src" / "personalclaw"


@functools.cache
def _real() -> _HomeCensus:
    """The census of every package module, its trees from the suite's one source index."""
    return _census_of(
        {p.relative_to(_SRC.parent).as_posix(): tree for p, tree in source_index.package_trees()}
    )


def _root_blind(census: _HomeCensus) -> list[str]:
    return sorted(where for where in census.blind if where.startswith("/"))


def test_the_census_is_not_vacuous():
    census = _real()
    tops = {loc.split("/", 1)[0] for loc in census.found}
    assert len(census.found) >= 250, f"only {len(census.found)} home locations censused"
    assert len(tops) >= 120, f"only {len(tops)} top-level home locations censused"
    assert len({e.path.split("/", 1)[0] for e in inv.all_entries()}) >= 90, "inventory read empty"


def test_the_five_stores_a_week_of_use_found_are_visible_to_the_real_census():
    """After normal use `audit_home()` named these as in NO snapshot. Each
    is here so a census that loses the spelling behind it fails by name, not by silence."""
    found = {_shown(loc) for loc in _real().found}
    for location in (
        "capture",  # Path(config_dir()) / "capture"
        "trigger-idle",  # root = Path(base_dir) if base_dir else config_dir()
        "routing_stats.json",  # home=config_dir() passed three calls deep
        "incident.json",  # was pinned as debt
        "sel_archive/security_events.{…}.jsonl",  # the rotated audit log
    ):
        assert location in found, f"the census no longer sees {location}"


def test_every_censused_location_is_classified():
    """A home location the code names must be claimed, deliberately excluded, or regenerable —
    each a decision recorded in `durability/inventory.py`. There is no pin list to add to."""
    census = _real()
    missing = _unaccounted(census)
    assert not missing, (
        "these home locations are in no inventory class, so `personalclaw snapshot` will not "
        "carry them and the Doctor will report them: "
        + ", ".join(f"{_shown(loc)} (named in {sorted(census.found[loc])[:2]})" for loc in missing)
    )


#: Every place the home is joined with a segment that is WHOLLY a runtime value, keyed by the
#: census's own spelling of it, with the modules it appears in and where the value comes from, each
#: read at its call site. Pinned EXACTLY as a set, not as a count: a new spelling, or a known one in
#: a new module, reds by name here, where a count would absorb it.
#:
#: Most of these are the durability machinery walking the inventory ITSELF, so the value is a path
#: some entry already declares; #3647's one home resolver is what let the census follow the home
#: into those modules and see them.
_BLIND_BY_DESIGN: dict[str, tuple[frozenset[str], str]] = {
    "/candidate": (
        frozenset({"personalclaw/evals/gate.py"}),
        "the eval gate staging a caller-supplied artifact",
    ),
    "/key": (
        frozenset({"personalclaw/evals/ablation.py"}),
        "the ablation harness digesting the paths it was handed",
    ),
    "/lock.get('path', '')": (
        frozenset({"personalclaw/packs/update.py", "personalclaw/packs/uninstall.py"}),
        "a pack lockfile's recorded install path, re-read by update and removed by uninstall",
    ),
    "/rel": (
        frozenset({"personalclaw/memory_vault.py", "personalclaw/snapshot.py"}),
        "the memory vault's CONFIGURED path (its default is declared), and the snapshot's sweep "
        "over `_everything_paths`, which are inventory entries",
    ),
    "/entry": (
        frozenset({"personalclaw/portability.py"}),
        "the export's sweep over `_remaining_export_paths`, derived from the inventory",
    ),
    "/entry.path": (
        frozenset(
            {
                "personalclaw/durability/conflict_resolve.py",
                "personalclaw/durability/footprint.py",
                "personalclaw/durability/reconcile.py",
                "personalclaw/durability/shards.py",
                "personalclaw/portability.py",
                "personalclaw/snapshot.py",
            }
        ),
        "an inventory entry's own declared path, joined by the code that walks the manifest",
    ),
    "/e.path": (frozenset({"personalclaw/snapshot.py"}), "an inventory entry's declared path"),
    "/child.path": (
        frozenset({"personalclaw/durability/footprint.py"}),
        "a declared entry's nested entry (`inventory.nested_entries`)",
    ),
    "/_db": (
        frozenset({"personalclaw/snapshot.py"}),
        "a database from `_declared_db_paths`, the inventory's own database entries",
    ),
    "/db_name": (
        frozenset({"personalclaw/portability.py"}),
        "a database the export projects, from the same declared set",
    ),
    "/f": (frozenset({"personalclaw/snapshot.py"}), "a name from `CORE_FILES`, a fixed list"),
    **dict.fromkeys(
        ("/part", "/*parts", "/parts[-1]"),
        (
            frozenset({"personalclaw/durability/home_paths.py"}),
            "the path a restore, an import or a sync writes, joined one name at a time by the one "
            "check that no link is on the way (`home_paths.home_path`): its caller's inventory "
            "entry, a name from `CORE_FILES`, or a file of a store an archive or another machine "
            "brings",
        ),
    ),
    "/path": (
        frozenset({"personalclaw/snapshot.py"}),
        "a restore-plan row for a store the inventory's merge selectors name",
    ),
    "/destination": (
        frozenset({"personalclaw/standing_instructions.py"}),
        "an instruction file the import wrote, as its ledger records it (home-relative, from "
        "`onboarding_import.writers._rel_to_home`); only one inside the memory folder's "
        "`instructions/` is read",
    ),
    "/path.name + suffix": (
        frozenset({"personalclaw/memory.py"}),
        "a file beside the home's keyword index (`memory._beside`): SQLite's own `-journal`, "
        "`-wal` and `-shm`, and a damaged index moved aside as `memory_index.db.broken-<UTC "
        "instant>`, which the inventory ignores (`*.broken-*`)",
    ),
}


def test_the_blind_spot_is_bounded():
    """What this scan cannot see, recorded site by site rather than left implied.

    A segment that is wholly a runtime value at the top of the home (`config_dir() / name`) is
    invisible to any static scan. This used to be a count pinned at five; since then seven more
    spellings arrived, almost all of them the durability code joining the home with paths it read
    from the inventory. Recording them as a set with a reason each keeps the scan honest about what
    it covers, and a new one still has to be read and written down here.
    """
    census = _real()
    blind = {where: frozenset(census.blind[where]) for where in _root_blind(census)}
    expected = {where: modules for where, (modules, _why) in _BLIND_BY_DESIGN.items()}
    new = {where: sorted(mods - expected.get(where, frozenset())) for where, mods in blind.items()}
    new = {where: mods for where, mods in new.items() if mods}
    gone = sorted(where for where in expected if where not in blind)
    assert not new, (
        "a home path is now built from a runtime value the census cannot follow; read it at its "
        f"call site and record where the value comes from in _BLIND_BY_DESIGN: {new}"
    )
    assert not gone, f"recorded blind spots the census no longer finds; drop them: {gone}"
