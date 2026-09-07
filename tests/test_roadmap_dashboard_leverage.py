"""``roadmap-dashboard.html`` must say which owner action buys the most — and must MEASURE it.

At 650/684 the board's remainder is almost entirely owner-gated. The page already says WHY each
atom is stuck (``test_roadmap_dashboard_states.py``); what it could not say is which of those
owner acts frees the most other work, so that ranking lived in an agent's head and was
re-derived by hand every cycle.

**The bug these tests exist to kill.** The obvious implementation asks, for each not-done atom,
"whose dependencies are satisfied once this one lands?" — i.e. ``all(dep in done for dep in
deps(a))``. That predicate is **vacuously true** for an atom whose deps were never an obstacle,
most obviously one with no deps at all, so flipping *anything* appears to free *every* dep-less
atom. Run against the live catalog, that version reported that all 34 not-done atoms each
unblocked the other 33: a confident, useless number that would have shipped.

The fix is a BASELINE — ``dep_blocked_ids``, the not-done atoms that have at least one not-done
dependency — with only crossings from that set counted. So the assertions below are deliberately
specific rather than comparative: "AR-1 ranks highest" is satisfied by the vacuous version too,
because under it everything ties. What kills it is an exact freed SET (whose members are only the
genuinely blocked atoms), and the fact that most atoms free nothing at all.

**Which assertions here are meant to survive a roadmap edit**, because a future reader editing
dag.json needs to know before a red confuses them:

* Everything computed from ``_ar_shaped_catalog()`` is pinned EXACTLY and must never move. It is
  a synthetic replica of the real AR sub-graph, so its arithmetic is fixed forever; a red there
  means the measurement broke, not the roadmap.
* Everything computed from ``parse_atoms()`` is pinned as SHAPE only, and is expected to hold
  through any roadmap edit: majority-zero, ``freed ⊆ dependency-blocked``, no atom in its own
  freed list, every not-done atom ranked. No population size appears in any of them, for the
  reason ``test_roadmap_dashboard_states.py`` gives: that population turns over daily, and a PR
  against dag.json is normally open.

A live pin on ``AR-1 == 7`` would red the day AR-1 lands, which is the outcome the whole panel
exists to encourage. That is why the exact numbers live on the replica.
"""

from __future__ import annotations

from tools.gen_roadmap_dashboard import (
    QueueStats,
    atom_deps,
    dep_blocked_ids,
    parse_atoms,
    render,
    unblock_leverage,
)


def _atom(aid: str, *, status: str = "todo", deps: list[str] | None = None, **extra) -> dict:
    a = {"id": aid, "title": f"{aid} scope", "status": status, "plan": "AR", "plan_code": "AR"}
    if deps is not None:
        a["deps"] = deps
    a.update(extra)
    return a


def _ar_shaped_catalog() -> dict[str, dict]:
    """The real AR sub-graph's shape, copied from ``docs/roadmap/atomic/dag.json``.

    AR-1 gates AR-2 and AR-3; AR-4 needs BOTH of those; AR-5/AR-6 hang off AR-3 alone; AR-7
    needs AR-5 and AR-6; AR-8 needs AR-2 and AR-5. Not a chain — which is the point: the branch
    is what makes AR-1 (7) and AR-3 (3) different numbers, and what makes AR-2 zero.

    ``DC-1`` and ``HC-3`` are the vacuity bait: real not-done atoms with NO deps at all. Nothing
    can free them, and an implementation that thinks otherwise fails here first.
    """
    catalog = [
        _atom("AR-0", status="done"),
        _atom("AR-1", deps=["AR-0", "EXT:WORKFLOWS-V2:rooms are the deliberative remainder"]),
        _atom("AR-2", deps=["AR-1"]),
        _atom("AR-3", deps=["AR-1"]),
        _atom("AR-4", deps=["AR-2", "AR-3"]),
        _atom("AR-5", deps=["AR-3"]),
        _atom("AR-6", deps=["AR-3"]),
        _atom("AR-7", deps=["AR-5", "AR-6"]),
        _atom("AR-8", deps=["AR-2", "AR-5"]),
        _atom("DC-1", deps=[]),
        _atom("HC-3", status="blocked"),
    ]
    return {a["id"]: a for a in catalog}


# ── the edge accessor ───────────────────────────────────────────────────────────────────


def test_edges_come_from_deps_and_ignore_ext_pseudo_refs() -> None:
    """One reading of an edge, shared with the tier chart: real ids in, ``EXT:`` prose out.

    An ``EXT:`` entry names work outside the decomposition. Counting it as an unmet dependency
    would park AR-1 — the highest-leverage atom on the board — in the "held by the graph" set it
    does not belong in.
    """
    atoms = _ar_shaped_catalog()
    assert atom_deps(atoms["AR-1"], atoms) == ["AR-0"]
    assert atom_deps(atoms["AR-4"], atoms) == ["AR-2", "AR-3"]
    assert atom_deps(atoms["DC-1"], atoms) == []
    assert atom_deps({"id": "X-1", "deps": ["NOPE-9"]}, atoms) == [], "dangling id is not an edge"


# ── the baseline (the anti-vacuity mechanism itself) ────────────────────────────────────


def test_the_baseline_is_only_atoms_an_unfinished_dep_actually_holds() -> None:
    atoms = _ar_shaped_catalog()
    assert dep_blocked_ids(atoms) == frozenset(
        {"AR-2", "AR-3", "AR-4", "AR-5", "AR-6", "AR-7", "AR-8"}
    )
    # AR-1's only in-catalog dep is done, so the graph is NOT what holds it; DC-1/HC-3 have no
    # deps at all. None of the three is freeable, and a done atom is never in the baseline.
    for aid in ("AR-0", "AR-1", "DC-1", "HC-3"):
        assert aid not in dep_blocked_ids(atoms), aid


# ── the measurement ─────────────────────────────────────────────────────────────────────


def test_leverage_is_exact_and_transitive_over_the_ar_shape() -> None:
    """The numbers, pinned. AR-1 = 7 through the whole branch; AR-3 = 3 down its own arm only.

    THE VACUITY REGRESSION. Under ``all(dep in done for dep in deps(a))`` every one of these
    would instead report the two dep-less atoms plus everything else, so each exact set below is
    a separate kill. Asserting only "AR-1 ranks highest" would NOT be: under the vacuous version
    every atom ties at the maximum, and AR-1 is still (jointly) top.
    """
    lev = unblock_leverage(_ar_shaped_catalog())
    assert lev["AR-1"] == ("AR-2", "AR-3", "AR-4", "AR-5", "AR-6", "AR-7", "AR-8")
    assert len(lev["AR-1"]) == 7
    assert lev["AR-3"] == ("AR-5", "AR-6", "AR-7")
    assert len(lev["AR-3"]) == 3
    # AR-2 alone frees nothing: AR-4 still needs AR-3, AR-8 still needs AR-5.
    assert lev["AR-2"] == ()
    assert lev["AR-5"] == ()  # AR-7 still needs AR-6; AR-8 still needs AR-2
    assert lev["AR-6"] == ()


def test_a_dep_less_atom_is_freed_by_nothing_and_frees_nothing() -> None:
    """The vacuous predicate's signature failure, isolated.

    ``DC-1`` and ``HC-3`` have no dependencies, so ``all(dep in done for dep in deps)`` is True
    for them before anything flips. They must therefore never appear in ANY freed list — nothing
    was ever holding them — and, having no dependents, they must free nothing themselves.
    """
    lev = unblock_leverage(_ar_shaped_catalog())
    for freed in lev.values():
        assert "DC-1" not in freed and "HC-3" not in freed, freed
    assert lev["DC-1"] == () and lev["HC-3"] == ()


def test_an_atom_never_appears_in_its_own_freed_list() -> None:
    """A dependency CYCLE must not let a flip take credit for freeing itself.

    This is defensive against a shape ``dag.json`` PERMITS, not one it currently contains at
    the level this module reads — and WHICH LEVEL is the whole point, because the two disagree.
    Measured at ``c5369c37a``:

    * **Raw graph** — atom→atom ``deps`` only, i.e. exactly what ``atom_deps`` returns and
      therefore the only graph ``unblock_leverage`` ever walks: 705 edges, **zero cycles**.
    * **Resolved graph** — those 705 plus the 143 ``EXT:<PLAN>:<prose>`` plan-refs turned into
      atom edges (705 + 143 = 848, the derived block's ``edge_count``): **one** cycle,
      ``['CRE-4', 'DIST-3', 'DIST-1', 'CRE-4']``, which is what ``dag.json``'s own ``cycles``
      reports and ``_render_validation`` renders. Of those three edges only ``DIST-3 → DIST-1``
      is a raw one; ``CRE-4 → DIST-3`` and ``DIST-1 → CRE-4`` are both resolver-created from
      ``EXT:`` refs.

    So do not delete this test after measuring raw ``deps`` and finding nothing. ``deps`` is
    free text, nothing rejects a cycle on write, and the deriver REPORTS cycles rather than
    refusing them — a raw one is one hand-edit away, and it would be silent here.

    Two atoms depending on each other is the whole construct: the cascade from ``X-1`` lands
    ``X-2``, ``X-2``'s dependents include ``X-1`` again, so ``X-1``'s last unmet dep "lands"
    and without the ``aid != src`` guard ``X-1`` reports itself as freed. The source is the one
    atom whose flip is the hypothesis; it cannot also be a consequence of it.
    """
    atoms = {
        "X-1": {"id": "X-1", "title": "half a cycle", "status": "todo", "deps": ["X-2"]},
        "X-2": {"id": "X-2", "title": "other half", "status": "todo", "deps": ["X-1"]},
    }
    assert dep_blocked_ids(atoms) == frozenset({"X-1", "X-2"}), "both are held, by each other"
    lev = unblock_leverage(atoms)
    assert lev["X-1"] == ("X-2",), "X-1 freed itself"
    assert lev["X-2"] == ("X-1",), "X-2 freed itself"


def test_done_atoms_are_not_ranked_and_a_landed_dep_stops_blocking() -> None:
    atoms = _ar_shaped_catalog()
    lev = unblock_leverage(atoms)
    assert "AR-0" not in lev, "a shipped atom has no leverage to spend"
    # Land AR-1: AR-2/AR-3 stop being blocked, so AR-1's leverage transfers to them.
    atoms["AR-1"]["status"] = "done"
    after = unblock_leverage(atoms)
    assert "AR-1" not in after
    assert after["AR-3"] == ("AR-5", "AR-6", "AR-7")
    assert after["AR-2"] == ()


# ── the live catalog: shape, not sizes ──────────────────────────────────────────────────


def test_over_the_live_catalog_most_not_done_atoms_free_nothing() -> None:
    """The graph is not what holds the remainder — and the vacuous version cannot say that.

    No population size is pinned (it turns over daily). What is pinned is the shape the vacuous
    implementation contradicts on every count: it reports ZERO zero-leverage atoms and gives
    every atom the same maximal score.
    """
    dag = parse_atoms()
    atoms = dag.get("atoms") or {}
    assert len(atoms) > 500, f"only {len(atoms)} atoms parsed — did dag.json's shape change?"
    lev = unblock_leverage(atoms)
    not_done = len(lev)
    assert not_done, "vacuity floor: nothing left to rank means this test proves nothing"
    zero = sum(1 for ids in lev.values() if not ids)
    assert zero * 2 > not_done, (
        f"only {zero} of {not_done} not-done atoms free nothing — a large majority should, "
        "since most of the remainder is not held by the graph at all"
    )
    # Vacuity leaves every atom tied at n-1. Real leverage does not.
    assert max(len(ids) for ids in lev.values()) < not_done - 1
    assert len({len(ids) for ids in lev.values()}) > 1, "every atom scored the same"


def test_no_already_satisfied_atom_is_ever_reported_as_freed() -> None:
    """The baseline invariant, over the real graph: freed ⊆ dependency-blocked.

    This is the single assertion the vacuous implementation cannot survive, whatever the catalog
    happens to hold that day.
    """
    atoms = parse_atoms().get("atoms") or {}
    assert len(atoms) > 500
    blocked = dep_blocked_ids(atoms)
    for aid, freed in unblock_leverage(atoms).items():
        assert set(freed) <= set(blocked), f"{aid} claims to free unblocked atoms: {freed}"
        assert aid not in freed, f"{aid} freed itself"


def test_the_live_census_accounts_for_the_whole_remainder() -> None:
    """Every not-done atom is either held by the graph or not — no third bucket, no overlap."""
    atoms = parse_atoms().get("atoms") or {}
    not_done = {aid for aid, a in atoms.items() if a.get("status") != "done"}
    blocked = set(dep_blocked_ids(atoms))
    assert blocked <= not_done, "a done atom cannot be dependency-blocked"
    assert set(unblock_leverage(atoms)) == not_done, "every not-done atom must be ranked"


# ── the panel ───────────────────────────────────────────────────────────────────────────


def _synthetic_dag() -> dict:
    """One owner atom with leverage, one owner atom with none, and a waiting atom."""
    atoms = {
        "P-1": {"id": "P-1", "title": "shipped", "status": "done", "plan": "P", "plan_code": "P"},
        "P-2": {
            "id": "P-2",
            "title": "owner sign-off",
            "status": "todo",
            "plan": "P",
            "plan_code": "P",
        },
        "P-3": {
            "id": "P-3",
            "title": "behind P-2",
            "status": "todo",
            "plan": "P",
            "plan_code": "P",
            "deps": ["P-2"],
        },
        "P-4": {
            "id": "P-4",
            "title": "owner release, frees nobody",
            "status": "todo",
            "plan": "P",
            "plan_code": "P",
            "deps": ["P-1"],
        },
    }
    return {
        "atoms": atoms,
        "ready": [],
        "gated": [
            {"id": "P-2", "title": "owner sign-off", "plan": "P", "gate": ["owner"]},
            {"id": "P-4", "title": "owner release", "plan": "P", "gate": ["owner"]},
        ],
        "topo": list(atoms),
        "cycles": [],
        "dangling": [],
        "unresolved": [],
        "edges": 2,
    }


def _page(dag: dict) -> str:
    return render([], QueueStats(), {"pr_by_branch": {}}, [], dag, {}, {})


def test_the_panel_ranks_the_owner_queue_and_names_what_each_frees() -> None:
    html = _page(_synthetic_dag())
    assert "Owner queue — by unblock leverage" in html
    # The count, the freed id by name, and the zero said out loud rather than dropped.
    assert "unblocks 1: <code>P-3</code>" in html
    assert "this unblocks nothing else" in html
    # Only P-3 is held by the graph; the two owner atoms are not, which is the census point.
    assert "2 owner-action atoms · 1 free another atom · 2 of 3 not-done atoms" in html
    # Ranked: the atom with leverage is rendered before the one without.
    assert html.index("owner sign-off") < html.index("owner release, frees nobody")


def test_a_zero_leverage_owner_atom_is_listed_not_omitted() -> None:
    """Saying "this unblocks nothing else" is information for an owner choosing where to look."""
    dag = _synthetic_dag()
    dag["atoms"].pop("P-3")  # now NOTHING in the queue frees anything
    html = _page(dag)
    assert "Owner queue — by unblock leverage" in html
    assert html.count("this unblocks nothing else") == 2
    assert "0 free another atom" in html
    assert "P-4" in html and "P-2" in html


def test_the_panel_is_absent_when_the_owner_queue_is_empty() -> None:
    """No owner-action atoms → no panel. An empty ranked list is decoration, not a decision."""
    dag = _synthetic_dag()
    dag["gated"] = []
    assert "Owner queue — by unblock leverage" not in _page(dag)
    assert "Owner queue — by unblock leverage" not in _page({})
