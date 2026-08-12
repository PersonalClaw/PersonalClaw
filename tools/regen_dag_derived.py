#!/usr/bin/env python3
"""Recompute the derived ``dag`` block of ``docs/roadmap/atomic/dag.json`` from ``plans``.

``dag.json`` has two halves with very different owners:

* ``plans[]`` — **authored** data. Every atom (id, title, scope, done_when, deps, status)
  is written by hand when a plan is decomposed and edited by hand when an atom lands.
  This tool NEVER touches it.
* ``dag{}`` — **derived** data: the ready frontier, the execution order, the validation
  fields the dashboard renders. Before this tool existed it was hand-maintained, and it
  rotted: ``plan_counts`` disagreed with ``plans[]`` for 34 of 69 plans, ``topo_order``
  held a stale 384-of-608-atom snapshot mixing four statuses, and nothing recomputed or
  checked any of it. Every autonomous roadmap tick reads this block to choose its next
  atom, so a stale block picks the wrong work.

Run it after any edit to ``plans[]`` (adding atoms, flipping a status)::

    python3 tools/regen_dag_derived.py            # rewrite the dag block in place
    python3 tools/regen_dag_derived.py --check    # exit 1 if the committed block is stale

It is idempotent: a second run over its own output changes nothing.
``tests/test_roadmap_dag_derived.py`` is the ratchet — it re-derives the block and reds if
the committed one differs, so the block can never silently rot again.

Derived-field semantics (each choice is a judgement; they are stated here because the
field names alone do not pin them down)
---------------------------------------------------------------------------------------
A dep string is either an **intra-repo** dep — a bare atom id like ``WV-6`` — or an
**EXT ref**: ``EXT:<PLAN-NAME>:<prose>``. An EXT ref names a cross-plan contract in prose;
which atom it actually lands on is a judgement, so that mapping is authored, not derived
(see ``resolved_edges`` below). Two graphs follow from that:

* the **ordering graph** — intra-repo edges only. Acyclic today. Drives ``topo_order`` and
  ``ready_frontier``. This is the same edge set the dashboard's tier layering uses
  (``tools/gen_roadmap_dashboard.py:dag_layers`` keeps only deps that name a known atom).
* the **full graph** — ordering edges plus each resolved EXT edge (``atom -> to_atom``).
  Drives ``cycles``. It is *cyclic* today (two cycles), which is exactly why it cannot
  drive the ordering.

``plan_counts``
    One record per plan, in ``plans[]`` order, shape
    ``{code, plan, total_atoms, done, in_progress, todo, blocked}``.
    ``blocked`` is a **seventh key added to the historical six-key shape**: two atoms carry
    ``status: "blocked"`` and folding them into ``todo`` would claim they are startable,
    while dropping them would break ``total_atoms == done + in_progress + todo + blocked``.
    Nothing consumes this field yet (the dashboard builds its own per-plan tally), so
    widening the record is safe. A status outside the four known values raises rather than
    silently vanishing from the tally.

``ready_frontier``
    ``{id, title, plan}`` (``plan`` holds the plan **code**, as it always has) for every
    atom that is startable right now: status is neither ``done`` nor ``blocked``, and every
    intra-repo dep is ``done``. Sorted by (plan code, atom number).

    **EXT refs do not gate readiness.** Two reasons: the resolved-EXT graph is cyclic
    (``CE-9 <-> ET-7``, ``CRE-4 -> DIST-3 -> DIST-1 -> CRE-4``), so treating EXT refs as
    hard prerequisites would make those atoms permanently unstartable; and many EXT refs
    are forward-looking notes ("federate when X lands"), not prerequisites. The ticks treat
    EXT separately, and ``resolved_edges``/``unresolved``/``cycles`` are where an EXT ref
    becomes visible. ``blocked`` atoms are excluded because an owner-declared block is not
    startable no matter what its deps say.

``topo_order``
    A real topological order over the ordering graph covering **all** atoms — deps strictly
    before dependents — with ties broken by (plan code, atom number) so the output is
    stable. ``tools/write_atomic_plans.py`` renders it as "Execution order (topological)",
    which only makes sense if it is a live total order over the whole catalog, not a
    snapshot of one status. If the ordering graph ever gains a cycle no order exists, and
    this raises naming the cycle instead of emitting a plausible-looking lie.

``cycles``
    Cycles in the **full** graph, each as a node list with the entry node repeated at the
    end (``["CE-9", "ET-7", "CE-9"]``), one per strongly-connected component, chosen
    deterministically. This is the field that surfaces the cross-plan deadlocks the
    ordering graph cannot see.

``dangling``
    ``{atom, dep}`` for every dep that is neither an EXT ref nor a known atom id — a typo'd
    or deleted dependency. Empty today.

``resolved_edges`` / ``unresolved``
    **Authored, not derived.** Resolving ``EXT:WORKFLOWS-V2:<prose>`` to ``WV-6`` rather
    than ``WV-3`` is a human reading of the prose; no rule recovers it. Both lists are
    therefore carried through from the input, re-emitted in ``plans[]`` traversal order so
    the output is canonical, and **validated**: together they must cover every EXT dep in
    ``plans[]`` exactly once, and every ``atom``/``to_atom`` must name a known atom. That
    keeps the fields checkable — a new EXT ref with no resolution decision reds the guard
    test instead of quietly leaving the graph incomplete.

``edge_count``
    ``len(ordering edges) + len(resolved_edges)`` — every dependency that resolves to a
    known atom. The committed value (826) matched no reading of the live data, so the field
    was genuinely ambiguous; this is the reading that makes it checkable and matches the
    README's "N dependency edges" prose. Unresolved EXT refs are not counted: they have no
    target atom, so there is no edge yet.
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
DAG_PATH = CORE / "docs/roadmap/atomic/dag.json"

EXT_PREFIX = "EXT:"
#: Every status an atom may carry. A value outside this set raises — see plan_counts.
KNOWN_STATUSES = ("done", "in_progress", "todo", "blocked")
#: Statuses that keep an atom out of the ready frontier.
NOT_STARTABLE = frozenset({"done", "blocked"})
#: Key order of the derived block, preserved so regeneration is a minimal diff.
DAG_KEY_ORDER = (
    "ready_frontier",
    "topo_order",
    "cycles",
    "dangling",
    "unresolved",
    "resolved_edges",
    "plan_counts",
    "edge_count",
)

REMEDY = "regenerate it: python3 tools/regen_dag_derived.py"


def id_key(atom_id: str) -> tuple[str, int, str]:
    """Sort key that orders ``AG-6`` before ``AG-11`` (plain string sort does not)."""
    code, _, tail = atom_id.rpartition("-")
    if code and tail.isdigit():
        return (code, int(tail), atom_id)
    return (atom_id, -1, atom_id)


def iter_atoms(plans: list[dict]) -> list[dict]:
    return [a for p in plans for a in (p.get("atoms") or [])]


def _atom_index(atoms: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for atom in atoms:
        aid = atom.get("id")
        if not aid:
            raise SystemExit(f"dag.json: atom without an id in {atom.get('title')!r}")
        if aid in index:
            raise SystemExit(f"dag.json: duplicate atom id {aid!r}")
        index[aid] = atom
    return index


def _statuses(atoms: list[dict]) -> None:
    bad = sorted({a["status"] for a in atoms if a.get("status") not in KNOWN_STATUSES})
    if bad:
        raise SystemExit(
            f"dag.json: unknown atom status(es) {bad} — add them to KNOWN_STATUSES in "
            f"{Path(__file__).name} and decide how plan_counts and the ready frontier "
            "should treat them; they must not be folded into an existing bucket silently"
        )


def plan_counts(plans: list[dict]) -> list[dict]:
    """Per-plan status tally, in ``plans[]`` order. Every status gets its own column."""
    records = []
    for plan in plans:
        atoms = plan.get("atoms") or []
        record = {
            "code": plan.get("code"),
            "plan": plan.get("plan"),
            "total_atoms": len(atoms),
        }
        for status in KNOWN_STATUSES:
            record[status] = sum(1 for a in atoms if a.get("status") == status)
        records.append(record)
    return records


def split_deps(
    atoms: list[dict], known: set[str]
) -> tuple[list[tuple[str, str]], list[dict], list[tuple[str, str]]]:
    """Partition every dep string into ordering edges, dangling deps and EXT refs."""
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    dangling: list[dict] = []
    ext: list[tuple[str, str]] = []
    for atom in atoms:
        aid = atom["id"]
        for dep in atom.get("deps") or []:
            if dep.startswith(EXT_PREFIX):
                ext.append((aid, dep))
            elif dep not in known:
                dangling.append({"atom": aid, "dep": dep})
            elif dep == aid:
                dangling.append({"atom": aid, "dep": dep})
            elif (aid, dep) not in seen:
                seen.add((aid, dep))
                edges.append((aid, dep))
    return edges, dangling, ext


def carry_ext_resolution(
    dag: dict, ext_refs: list[tuple[str, str]], known: set[str]
) -> tuple[list[dict], list[dict]]:
    """Re-emit the authored EXT resolution in traversal order, after validating it."""
    resolved = {(r["atom"], r["ext_ref"]): r for r in dag.get("resolved_edges") or []}
    unresolved = {(u["atom"], u["ext_ref"]): u for u in dag.get("unresolved") or []}

    both = sorted(resolved.keys() & unresolved.keys())
    if both:
        raise SystemExit(
            "dag.json: EXT ref(s) listed in BOTH resolved_edges and unresolved: "
            f"{[a for a, _ in both]} — an EXT ref resolves to an atom or it does not"
        )
    decided = resolved.keys() | unresolved.keys()
    live = set(ext_refs)
    missing = sorted(live - decided, key=lambda p: id_key(p[0]))
    if missing:
        first = missing[0]
        raise SystemExit(
            f"dag.json: {len(missing)} EXT ref(s) in plans[] have no entry in "
            f"resolved_edges or unresolved, e.g. {first[0]} -> {first[1]!r}. Resolving an "
            "EXT ref to a target atom is a human reading of its prose, so this tool cannot "
            "do it for you: add the atom id to resolved_edges (or the ref to unresolved if "
            "no atom owns it yet) and re-run."
        )
    stale = sorted(decided - live, key=lambda p: id_key(p[0]))
    if stale:
        raise SystemExit(
            f"dag.json: {len(stale)} EXT resolution(s) name a dep that plans[] no longer "
            f"has, e.g. {stale[0][0]} -> {stale[0][1]!r} — drop the entry"
        )
    bad_target = sorted(r["to_atom"] for r in resolved.values() if r.get("to_atom") not in known)
    if bad_target:
        raise SystemExit(f"dag.json: resolved_edges point at unknown atom(s) {bad_target}")
    unknown_owner = sorted({a for a, _ in decided if a not in known})
    if unknown_owner:
        raise SystemExit(
            f"dag.json: EXT resolution(s) attributed to unknown atom(s) {unknown_owner}"
        )

    # Traversal order == plans[] order, which is how the committed lists already read.
    return (
        [resolved[k] for k in ext_refs if k in resolved],
        [unresolved[k] for k in ext_refs if k in unresolved],
    )


def ready_frontier(
    atoms: list[dict],
    index: dict[str, dict],
    edges: list[tuple[str, str]],
    codes: dict[str, str],
) -> list[dict]:
    """Atoms startable now: not done, not blocked, every intra-repo dep done."""
    blockers: dict[str, list[str]] = {}
    for src, dst in edges:
        blockers.setdefault(src, []).append(dst)
    frontier = [
        atom
        for atom in atoms
        if atom.get("status") not in NOT_STARTABLE
        and all(index[d].get("status") == "done" for d in blockers.get(atom["id"], ()))
    ]
    frontier.sort(key=lambda a: id_key(a["id"]))
    return [{"id": a["id"], "title": a.get("title"), "plan": codes[a["id"]]} for a in frontier]


def topo_order(index: dict[str, dict], edges: list[tuple[str, str]]) -> list[str]:
    """Kahn over the ordering graph: every dep strictly before its dependents."""
    dependents: dict[str, list[str]] = {aid: [] for aid in index}
    indegree = {aid: 0 for aid in index}
    for src, dst in edges:  # src depends on dst
        dependents[dst].append(src)
        indegree[src] += 1

    queue = [id_key(aid) for aid, deg in indegree.items() if deg == 0]
    heapq.heapify(queue)
    order: list[str] = []
    while queue:
        aid = heapq.heappop(queue)[2]
        order.append(aid)
        for nxt in dependents[aid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(queue, id_key(nxt))
    if len(order) != len(index):
        stuck = sorted((aid for aid, deg in indegree.items() if deg > 0), key=id_key)
        raise SystemExit(
            "dag.json: the intra-repo dependency graph has a cycle, so no execution order "
            f"exists. {len(stuck)} atom(s) are involved, starting at {stuck[0]}. Break the "
            "cycle in plans[] — a cycle here means two atoms each claim to need the other, "
            "which no ordering can satisfy."
        )
    return order


def find_cycles(index: dict[str, dict], full_edges: dict[str, set[str]]) -> list[list[str]]:
    """One cycle per strongly-connected component of the full graph, deterministically."""
    order = sorted(index, key=id_key)
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    components: list[list[str]] = []

    for root in order:
        if root in index_of:
            continue
        # Iterative Tarjan — 600+ atoms with long chains would blow the recursion limit.
        work: list[tuple[str, list[str]]] = [(root, sorted(full_edges.get(root, ()), key=id_key))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            if pending:
                nxt = pending.pop(0)
                if nxt not in index_of:
                    index_of[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(full_edges.get(nxt, ()), key=id_key)))
                elif nxt in on_stack:
                    low[node] = min(low[node], index_of[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index_of[node]:
                component = []
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    component.append(popped)
                    if popped == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component, key=id_key))

    cycles = []
    for component in sorted(components, key=lambda c: id_key(c[0])):
        cycles.append(_shortest_cycle(component[0], set(component), full_edges))
    return cycles


def _shortest_cycle(start: str, scope: set[str], full_edges: dict[str, set[str]]) -> list[str]:
    """Shortest cycle through ``start`` inside its component — a stable representative."""
    paths: list[list[str]] = [[start]]
    while paths:
        nxt_paths: list[list[str]] = []
        for path in paths:
            for succ in sorted(full_edges.get(path[-1], ()), key=id_key):
                if succ == start:
                    return path + [start]
                if succ in scope and succ not in path:
                    nxt_paths.append(path + [succ])
        paths = nxt_paths
    return [start, start]  # unreachable: every SCC member sits on a cycle


def derive(data: dict) -> dict:
    """Recompute the whole derived block from ``plans[]``. Pure — no I/O, no mutation."""
    plans = data["plans"]
    atoms = iter_atoms(plans)
    index = _atom_index(atoms)
    _statuses(atoms)
    codes = {a["id"]: p.get("code") for p in plans for a in (p.get("atoms") or [])}

    edges, dangling, ext_refs = split_deps(atoms, set(index))
    resolved, unresolved = carry_ext_resolution(data.get("dag") or {}, ext_refs, set(index))

    full: dict[str, set[str]] = {}
    for src, dst in edges:
        full.setdefault(src, set()).add(dst)
    for edge in resolved:
        full.setdefault(edge["atom"], set()).add(edge["to_atom"])

    derived = {
        "ready_frontier": ready_frontier(atoms, index, edges, codes),
        "topo_order": topo_order(index, edges),
        "cycles": find_cycles(index, full),
        "dangling": dangling,
        "unresolved": unresolved,
        "resolved_edges": resolved,
        "plan_counts": plan_counts(plans),
        "edge_count": len(edges) + len(resolved),
    }
    assert tuple(derived) == DAG_KEY_ORDER, "derived block key order drifted"
    return derived


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "plans" not in data:
        raise SystemExit(f"{path}: no 'plans' key — is this the atomic plan catalog?")
    return data


def dump(data: dict, path: Path) -> None:
    """Write with the catalog's byte conventions: indent 2, real UTF-8, trailing newline."""
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed derived block is stale",
    )
    parser.add_argument("--path", type=Path, default=DAG_PATH, help="dag.json to operate on")
    args = parser.parse_args(argv)

    data = load(args.path)
    fresh = derive(data)
    committed = data.get("dag") or {}
    if fresh == committed:
        print(f"{args.path.name}: derived block already current ({fresh['edge_count']} edges)")
        return 0
    if args.check:
        drifted = [k for k in DAG_KEY_ORDER if committed.get(k) != fresh[k]]
        print(
            f"{args.path}: derived block is STALE — {', '.join(drifted)} — {REMEDY}",
            file=sys.stderr,
        )
        return 1
    data["dag"] = fresh
    dump(data, args.path)
    print(
        f"{args.path.name}: rewrote dag block — "
        f"{len(fresh['topo_order'])} atoms, {len(fresh['ready_frontier'])} ready, "
        f"{fresh['edge_count']} edges, {len(fresh['cycles'])} cycle(s), "
        f"{len(fresh['dangling'])} dangling, {len(fresh['unresolved'])} unresolved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
