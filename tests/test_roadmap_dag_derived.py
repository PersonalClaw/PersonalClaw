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


def test_ready_frontier_is_startable_work(atoms: list[dict], fresh: dict) -> None:
    """Every frontier atom is unfinished, unblocked, and has all intra-repo deps done."""
    frontier = fresh["ready_frontier"]
    assert frontier, "ready frontier is empty — nothing would be startable"
    by_id = {a["id"]: a for a in atoms}
    known = set(by_id)
    for entry in frontier:
        atom = by_id[entry["id"]]
        assert atom["status"] not in NOT_STARTABLE, f"{entry['id']} is {atom['status']}"
        assert entry["title"] == atom["title"]
        for dep in atom.get("deps") or []:
            if dep in known:
                assert by_id[dep]["status"] == "done", (
                    f"{entry['id']} is on the ready frontier but its dep {dep} is "
                    f"{by_id[dep]['status']}"
                )
    # And nothing startable is missing: an atom whose deps are all done must be listed.
    listed = {e["id"] for e in frontier}
    for atom in atoms:
        if atom["status"] in NOT_STARTABLE:
            continue
        if all(by_id[d]["status"] == "done" for d in (atom.get("deps") or []) if d in known):
            assert atom["id"] in listed, f"{atom['id']} is startable but not on the frontier"


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
