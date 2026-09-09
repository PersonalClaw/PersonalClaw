"""The derived ``dag`` block of the atomic plan catalog must agree with ``plans[]``.

``docs/roadmap/atomic/dag.json`` is the substrate every autonomous roadmap tick reads to
choose its next atom. Its ``dag`` block (ready frontier, execution order, validation
fields) is derived from ``plans[]`` — but it used to be hand-maintained, and it rotted
silently: ``plan_counts`` disagreed with the live atoms for 34 of 69 plans, ``topo_order``
was a stale 384-of-608 snapshot mixing four statuses, and no test looked. A stale block
means a tick picks work that is already done, or skips work that is ready.

This module is the ratchet. It re-derives the block with ``tools/regen_dag_derived.py`` and
reds if the committed block differs, so the only way the block can drift is if someone
edits ``plans[]`` and ignores a red test. It also asserts the invariants that make the
derived fields *meaningful* rather than merely present, and proves it has teeth by
re-deriving over a deliberately mutated copy.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.regen_dag_derived import (
    DAG_KEY_ORDER,
    DAG_PATH,
    KNOWN_STATUSES,
    NOT_STARTABLE,
    derive,
    dump,
    load,
    main,
)

REMEDY = "run `python3 tools/regen_dag_derived.py` and commit the result"


@pytest.fixture(scope="module")
def catalog() -> dict:
    return load(DAG_PATH)


@pytest.fixture(scope="module")
def atoms(catalog: dict) -> list[dict]:
    found = [a for p in catalog["plans"] for a in (p.get("atoms") or [])]
    # Vacuity guard: an empty or truncated catalog would make every assertion below pass.
    assert len(found) > 500, f"only {len(found)} atoms parsed — did dag.json's shape change?"
    return found


@pytest.fixture(scope="module")
def fresh(catalog: dict) -> dict:
    return derive(catalog)


def test_committed_derived_block_matches_plans(catalog: dict, fresh: dict) -> None:
    """The whole point: the committed block is what plans[] implies, field by field."""
    committed = catalog["dag"]
    assert tuple(committed) == DAG_KEY_ORDER, f"dag key order changed — {REMEDY}"
    for field in DAG_KEY_ORDER:
        assert committed[field] == fresh[field], (
            f"dag.json's derived `{field}` is stale — it disagrees with plans[]. "
            f"Nothing recomputes this block automatically, so {REMEDY}."
        )


def test_plan_counts_account_for_every_atom(catalog: dict, fresh: dict) -> None:
    """No status may be dropped or folded into another — the columns must sum."""
    assert len(fresh["plan_counts"]) == len(catalog["plans"])
    for record in fresh["plan_counts"]:
        buckets = sum(record[status] for status in KNOWN_STATUSES)
        assert buckets == record["total_atoms"], (
            f"{record['code']}: per-status columns sum to {buckets} but total_atoms is "
            f"{record['total_atoms']} — a status is being dropped from the tally"
        )
    total = sum(r["total_atoms"] for r in fresh["plan_counts"])
    assert total == sum(len(p.get("atoms") or []) for p in catalog["plans"])


def test_topo_order_is_a_valid_total_order(atoms: list[dict], fresh: dict) -> None:
    """Covers every atom, and every intra-repo dep precedes its dependent."""
    order = fresh["topo_order"]
    assert len(order) == len(atoms), "topo_order must cover every atom, not a subset"
    assert len(set(order)) == len(order), "topo_order repeats an atom"
    position = {aid: i for i, aid in enumerate(order)}
    known = {a["id"] for a in atoms}
    checked = 0
    for atom in atoms:
        for dep in atom.get("deps") or []:
            if dep in known:
                checked += 1
                assert (
                    position[dep] < position[atom["id"]]
                ), f"{atom['id']} is ordered before its dependency {dep}"
    assert checked > 100, f"only {checked} edges checked — the dep edges vanished"


#: An INDEPENDENT re-statement of the gate rule, so these tests are an oracle rather than a
#: re-call of the code under test. Must agree with regen_dag_derived.atom_gates by construction.
_OWNER_GATE = re.compile(r"owner[-\s]?(?:gated|only)", re.IGNORECASE)


def _gates(atom: dict) -> list[str]:
    gates = []
    if any(str(d).startswith("EXT:") for d in atom.get("deps") or []):
        gates.append("ext")
    if _OWNER_GATE.search(atom.get("blocked_reason") or ""):
        gates.append("owner")
    return gates


def _startable(atom: dict, by_id: dict[str, dict], known: set[str]) -> bool:
    if atom["status"] in NOT_STARTABLE:
        return False
    return all(by_id[d]["status"] == "done" for d in (atom.get("deps") or []) if d in known)


def test_ready_frontier_is_startable_work(atoms: list[dict], fresh: dict) -> None:
    """A ready-frontier atom is unfinished, unblocked, all intra-repo deps done — and ungated."""
    frontier = fresh["ready_frontier"]
    assert frontier, "ready frontier is empty — nothing would be startable"
    by_id = {a["id"]: a for a in atoms}
    known = set(by_id)
    for entry in frontier:
        atom = by_id[entry["id"]]
        assert atom["status"] not in NOT_STARTABLE, f"{entry['id']} is {atom['status']}"
        assert entry["title"] == atom["title"]
        assert _startable(atom, by_id, known), f"{entry['id']} has an unfinished intra-repo dep"
        # The new invariant: a gated atom must NOT be on the ready frontier — it over-reports.
        assert not _gates(atom), (
            f"{entry['id']} is on ready_frontier but is gated {_gates(atom)} — it belongs in "
            "gated_frontier"
        )
    # Nothing startable is missing: an ungated startable atom is on ready_frontier, a gated one
    # is on gated_frontier — the two lists partition the startable-by-deps set with no leaks.
    ready = {e["id"] for e in frontier}
    gated = {e["id"] for e in fresh["gated_frontier"]}
    assert ready.isdisjoint(gated), f"an atom is on BOTH frontiers: {ready & gated}"
    for atom in atoms:
        if not _startable(atom, by_id, known):
            continue
        where = ready if not _gates(atom) else gated
        assert atom["id"] in where, (
            f"{atom['id']} is startable but on neither the correct frontier "
            f"(gated={_gates(atom)})"
        )


def test_gated_frontier_is_gated_startable_work(atoms: list[dict], fresh: dict) -> None:
    """Every gated-frontier atom is startable-by-deps, actually gated, and names its gate(s)."""
    gated = fresh["gated_frontier"]
    assert gated, "gated frontier is empty — the roadmap has owner/EXT-gated atoms, so this is off"
    by_id = {a["id"]: a for a in atoms}
    known = set(by_id)
    for entry in gated:
        atom = by_id[entry["id"]]
        assert entry["title"] == atom["title"]
        assert _startable(
            atom, by_id, known
        ), f"{entry['id']} is on gated_frontier but not startable by its intra-repo deps"
        expected = _gates(atom)
        assert expected, f"{entry['id']} is on gated_frontier but has no gate"
        assert (
            entry["gate"] == expected
        ), f"{entry['id']} gate {entry['gate']!r} disagrees with the oracle {expected!r}"
        assert entry["gate"] == sorted(entry["gate"]), f"{entry['id']} gate is not sorted"
        assert set(entry["gate"]) <= {"ext", "owner"}, f"{entry['id']} has an unknown gate tag"
    # Vacuity floor + the founding example: an EXT-gated atom (e.g. ET-8) must be represented
    # here rather than on the ready frontier.
    assert any("ext" in e["gate"] for e in gated), "no ext-gated atom found — the split is inert"


def test_no_dangling_deps_and_every_ext_ref_is_decided(atoms: list[dict], fresh: dict) -> None:
    """A dep must name a known atom or be an EXT ref with a recorded resolution."""
    assert (
        fresh["dangling"] == []
    ), f"deps naming no known atom: {fresh['dangling']} — fix the typo in plans[]"
    ext = [(a["id"], dep) for a in atoms for dep in (a.get("deps") or []) if dep.startswith("EXT:")]
    assert ext, "no EXT refs parsed — the cross-plan ref convention changed"
    decided = {(r["atom"], r["ext_ref"]) for r in fresh["resolved_edges"]}
    decided |= {(u["atom"], u["ext_ref"]) for u in fresh["unresolved"]}
    assert set(ext) == decided, "every EXT ref needs exactly one resolution decision"


def test_edge_count_matches_the_edges_it_counts(atoms: list[dict], fresh: dict) -> None:
    """edge_count's documented reading: ordering edges plus resolved EXT edges."""
    known = {a["id"] for a in atoms}
    ordering = {
        (a["id"], dep)
        for a in atoms
        for dep in (a.get("deps") or [])
        if dep in known and dep != a["id"]
    }
    assert ordering, "no intra-repo dep edges parsed"
    assert fresh["edge_count"] == len(ordering) + len(fresh["resolved_edges"])


def test_cycles_are_real_cycles_in_the_full_graph(atoms: list[dict], fresh: dict) -> None:
    """Each reported cycle must actually traverse existing edges and close on itself."""
    known = {a["id"] for a in atoms}
    edges: dict[str, set[str]] = {}
    for atom in atoms:
        edges[atom["id"]] = {d for d in (atom.get("deps") or []) if d in known}
    for edge in fresh["resolved_edges"]:
        edges.setdefault(edge["atom"], set()).add(edge["to_atom"])
    for cycle in fresh["cycles"]:
        assert cycle[0] == cycle[-1], f"{cycle} is not closed"
        for src, dst in zip(cycle, cycle[1:]):
            assert dst in edges.get(src, set()), f"{cycle}: no edge {src} -> {dst}"


def test_a_stale_block_is_detected(catalog: dict, fresh: dict, tmp_path: Path) -> None:
    """Prove the ratchet has teeth: mutate plans[] and the derived block must move."""
    mutated = json.loads(json.dumps(catalog))
    mutated["dag"] = json.loads(json.dumps(fresh))
    victim = next(
        a
        for p in mutated["plans"]
        for a in (p.get("atoms") or [])
        if a["status"] == "done" and not any(d.startswith("EXT:") for d in (a.get("deps") or []))
    )
    victim["status"] = "todo"

    restaled = derive(mutated)
    assert restaled != mutated["dag"], "flipping an atom's status changed nothing — no teeth"
    assert restaled["plan_counts"] != mutated["dag"]["plan_counts"]

    path = tmp_path / "dag.json"  # tmp_path only: the real catalog is never written here
    dump(mutated, path)
    assert main(["--check", "--path", str(path)]) == 1
    assert main(["--path", str(path)]) == 0
    assert main(["--check", "--path", str(path)]) == 0, "regenerating must settle the file"
    assert json.loads(path.read_text(encoding="utf-8"))["dag"] == restaled


def test_regenerating_the_committed_file_is_a_no_op() -> None:
    """`--check` on the committed catalog passes, so the tool and the file agree."""
    proc = subprocess.run(
        [sys.executable, "tools/regen_dag_derived.py", "--check"],
        cwd=DAG_PATH.parents[3],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"{proc.stdout}{proc.stderr}\n{REMEDY}"


# ── The catalog's front page is derived too, and nothing read it ──────────────────────────
#
# `docs/roadmap/atomic/README.md` renders entirely from `plans[]` + the derived `dag` block, but
# its renderer lived in `tools/write_atomic_plans.py`, whose entry point requires the original
# `workflow-output.json` decomposition — absent from the repo. So it could not be re-run and it
# froze at authoring time. Measured on `2ec3ae86e`, every headline figure was wrong:
#
#     claimed  640 atoms · 356 done · 284 remaining · 876 edges · 2 cycles · 1 unresolved
#     actual   684 atoms · 650 done ·  34 remaining · 850 edges · 1 cycle  · 0 unresolved
#
# Read literally the page said the project was 44% done when it was 95% done. Two sibling rails
# already existed — `test_roadmap_atomic_status_sync.py` for the per-plan `<CODE>.md` tables and
# the block ratchet above — and between them they covered every derived surface EXCEPT the one a
# human opens first. `render_readme` now lives beside the block it describes, so this ratchet
# covers both.
def test_the_committed_front_page_matches_its_own_renderer(catalog: dict) -> None:
    from tools.regen_dag_derived import README_PATH, render_readme

    assert README_PATH.read_text(encoding="utf-8") == render_readme(catalog), REMEDY


def test_the_front_page_ratchet_has_teeth(catalog: dict) -> None:
    """Falsify it: a status flip must change the rendered page, or the test above proves nothing.

    Without this, `render_readme` could ignore `plans[]` entirely and the assertion would still
    pass on any catalog — the same vacuity trap the `--check` ratchet guards with its own
    `no teeth` assertion.
    """
    from tools.regen_dag_derived import render_readme

    before = render_readme(catalog)
    mutated = json.loads(json.dumps(catalog))
    victim = next(
        a for p in mutated["plans"] for a in (p.get("atoms") or []) if a["status"] == "done"
    )
    victim["status"] = "todo"
    after = render_readme(mutated)
    assert after != before, "flipping an atom's status changed nothing on the page — no teeth"
    assert "650 done" in before and "649 done" in after, "the done count must be the live one"


def test_the_front_page_is_written_beside_the_catalog_it_was_given(tmp_path) -> None:
    """A throwaway catalog gets a throwaway page — the real front page is never collateral.

    `--readme` defaults to `README.md` BESIDE `--path`. A fixed default would make every
    tmp_path regeneration in this module rewrite the committed page from mutated data.
    """
    from tools.regen_dag_derived import README_PATH

    real_before = README_PATH.read_text(encoding="utf-8")
    catalog = load(DAG_PATH)
    # Force staleness with a STATUS FLIP, not by blanking the block: `resolved_edges` is authored
    # data `derive()` refuses to invent, so `dag = {}` raises before the write path is reached.
    next(a for p in catalog["plans"] for a in (p.get("atoms") or []) if a["status"] == "done")[
        "status"
    ] = "todo"
    path = tmp_path / "dag.json"
    dump(catalog, path)

    assert main(["--path", str(path)]) == 0
    assert (tmp_path / "README.md").exists(), "the page must land beside the catalog given"
    assert README_PATH.read_text(encoding="utf-8") == real_before, "real page was overwritten"
