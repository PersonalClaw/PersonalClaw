"""Which of `PP-16`'s six "one X" clauses have CONVERGED, measured rather than assumed.

The atom's `done_when` names six unifications: *"One status vocabulary, one adoption/reaping path,
one attention path, one ledger, one projection to tasks, one cockpit contract."* Two of those six
are **already satisfied** — `PP-5` made the loop a `personalclaw.ledger` writer, and
`workflows/attention.py` was built on the loop watchdog's own inbox seam. A clause that has already
converged is as load-bearing to record as one that has not: a later slice that "unifies the ledger"
would be writing code for a problem that no longer exists, and a slice that re-forks either seam
would silently undo a landed atom.

So this rail is a census with two directions, in the idiom `test_pp16_loop_field_map.py`
established for the field map:

* **The converged clauses are RATCHETS.** Ledger and attention are asserted to still funnel through
  one seam each. If a future change gives loops their own ledger writer or their own
  `state.notify` + `store.add` pair again, this reds — which is the only thing standing between
  "`PP-5` landed" and "`PP-5` landed and then rotted".
* **The unconverged clauses are PINNED COUNTS that must SHRINK.** Reaping, the task projection, the
  cockpit contract and the pluggable supervisor each still have exactly two implementations (or, for
  the supervisor, five registered strategies). Each count is pinned with the file:line of both
  sides, so the slice that unifies one of them reds HERE and updates the census as part of landing —
  rather than leaving a stale "still open" list in a plan nobody re-measures.

**Why source text and not imports.** These are structural facts about which modules exist and which
seams they call, and importing `loop.watchdog` or the dashboard handlers drags in a gateway's worth
of module-level state. Reading the shipped source is the cheaper probe for a structural claim, and
it cannot be fooled by a test-time monkeypatch.

Vacuity floors throughout: every scan asserts its own target file was found and non-empty before
concluding anything from a match count, and the seam probes are proved to REJECT a symbol that does
not exist (a substring scan that always returns "absent" would pass every convergence assertion by
being uniformly blind).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src" / "personalclaw"
_WEB = Path(__file__).resolve().parent.parent / "web" / "src"


def _text(rel: str, root: Path = _SRC) -> str:
    """The shipped source of `rel`, with a vacuity floor: a moved or emptied file is a
    failure to MEASURE, not a passing measurement."""
    path = root / rel
    assert path.is_file(), f"{path} does not exist — module moved? the census is measuring nothing"
    body = path.read_text(encoding="utf-8")
    assert body.strip(), f"{path} is empty — the census would pass by being blind"
    return body


# ── the two clauses that have already converged (ratchets) ────────────────────────────────


#: The loop-side and run-side modules that must remain `personalclaw.ledger` writers. `PP-5`
#: ("loops emit the ledger") is what put the first two on this list; `PP-4` extracted the primitive
#: they all write through.
_LEDGER_WRITERS = ("loop/journal.py", "loop/store.py", "workflows/journal.py")


def test_the_ledger_clause_is_converged_and_stays_converged():
    """`PP-16`'s "one ledger" clause is ALREADY satisfied — both work-unit nouns write
    `personalclaw.ledger`, so there is no second ledger left to unify."""
    assert (_SRC / "ledger").is_dir(), "personalclaw/ledger/ is gone — PP-4 extraction reverted?"
    for rel in _LEDGER_WRITERS:
        body = _text(rel)
        assert "personalclaw.ledger" in body, (
            f"{rel} no longer references personalclaw.ledger. PP-16's 'one ledger' clause was "
            f"satisfied by PP-5; a loop-side module that stops writing the shared ledger re-forks "
            f"it and un-lands that atom."
        )


def test_the_attention_clause_is_converged_and_stays_converged():
    """`PP-16`'s "one attention path" clause is ALREADY satisfied — the loop watchdog and the
    run-side gate both raise through `inbox.emit_attention_item`."""
    assert "def emit_attention_item" in _text(
        "inbox.py"
    ), "inbox.emit_attention_item is gone — the seam both nouns share no longer exists"
    for rel in ("loop/watchdog.py", "workflows/attention.py"):
        assert "emit_attention_item" in _text(rel), (
            f"{rel} no longer calls emit_attention_item. Both work-unit nouns raise attention "
            f"through the one seam today (workflows/attention.py's own header says it uses 'the "
            f"same seam the loop watchdog uses'); a caller that goes back to a separate "
            f"state.notify + store.add pair drifts the durable row from the notification."
        )


def test_the_seam_probe_rejects_a_symbol_that_does_not_exist():
    """Vacuity floor for the two ratchets above: a scan that reports every symbol as present —
    or every symbol as absent — would pass them without measuring anything."""
    body = _text("workflows/attention.py")
    assert "emit_attention_item" in body, "positive control failed — the probe sees nothing"
    assert (
        "emit_attention_item_that_never_existed" not in body
    ), "negative control failed — the probe matches a symbol that does not exist"


# ── the clauses that still have two implementations (counts that must SHRINK) ──────────────


#: One row per unconverged clause: the clause name, and the two implementations by
#: `path::symbol`. Each pair is a real pair TODAY; the slice that unifies one deletes a side and
#: reds this rail, which is how the census stays honest instead of rotting into a stale claim.
_UNCONVERGED: dict[str, tuple[tuple[str, str], ...]] = {
    # loop/manager.py:581 vs workflows/watchdog.py:338 + :423. The loop side reaps orphans from a
    # single gateway boot call; the run side sweeps and ADOPTS at boot, then holds a lease.
    "adoption/reaping": (
        ("loop/manager.py", "async def reap_orphaned_loops"),
        ("workflows/watchdog.py", "def _boot_sweep"),
    ),
    # loop/tasks_link.py:167 provisions imperatively; workflows/materialize.py:257 PLANS a
    # materialization and returns bindings. Two directions over one relationship.
    "projection to tasks": (
        ("loop/tasks_link.py", "def provision"),
        ("workflows/materialize.py", "def plan_materialization"),
    ),
    # The five kinds are still pluggable PYTHON, not templates+policies: a runtime-checkable
    # Protocol plus a module-level registry dict.
    "pluggable supervisor": (
        ("loop/kinds/__init__.py", "class LoopKindStrategy"),
        ("workflows/supervisor_policy.py", "class SupervisorPolicy"),
    ),
}


@pytest.mark.parametrize("clause", sorted(_UNCONVERGED))
def test_the_unconverged_clauses_still_have_exactly_two_implementations(clause: str):
    """Pins that both sides of each clause exist TODAY. When a `PP-16` slice unifies one, the
    deleted side reds this — update the census in the same commit; do not delete the assertion."""
    sides = _UNCONVERGED[clause]
    assert len(sides) == 2, f"{clause}: the census row should name exactly two sides"
    for rel, symbol in sides:
        assert symbol in _text(rel), (
            f"{clause}: {rel} no longer declares {symbol!r}. If a PP-16 slice unified this clause, "
            f"that is the intended outcome — re-home this row (or delete it) and record the "
            f"convergence in the plan's execution log. This count must SHRINK as PP-16 lands."
        )


#: The cockpit clause, measured differently: it is a FRONTEND duality, two stream hooks and two
#: fold pipelines over what PP-16 says is one noun. Pinned as file existence because the unification
#: deletes files rather than symbols.
_COCKPIT_PAIRS = (
    ("pages/loops/useRunStream.ts", "pages/workflows/useWorkflowStream.ts"),
    ("pages/loops/runFold.ts", "pages/workflows/workflowFold.ts"),
    ("pages/loops/LoopCockpitPage.tsx", "pages/workflows/WorkflowRunDetail.tsx"),
)


def test_the_cockpit_clause_still_has_two_frontend_implementations():
    """`PP-16`'s "one cockpit contract" clause is NOT converged: a loop run and a workflow run are
    streamed, folded and rendered by two separate frontend stacks."""
    assert _WEB.is_dir(), f"{_WEB} missing — cannot measure the cockpit clause"
    for loop_side, run_side in _COCKPIT_PAIRS:
        assert (_WEB / loop_side).is_file() and (_WEB / run_side).is_file(), (
            f"one side of the cockpit pair ({loop_side} vs {run_side}) is gone. If a PP-16 slice "
            f"unified the cockpit contract, update this census; the pair count must SHRINK."
        )


def test_the_five_kinds_are_still_pluggable_python_not_templates():
    """The atom wants the five kinds to become "bundled templates plus policies" so "the supervisor
    stops being pluggable Python". Today the pluggability is a Protocol + a registry dict, and all
    five kinds resolve to a bundled template ALREADY (`loop_aliases.KIND_TO_TEMPLATE`) — so the
    noun-level half is done and only the BEHAVIOUR half is outstanding."""
    from personalclaw.workflows.loop_aliases import KIND_TO_TEMPLATE

    body = _text("loop/kinds/__init__.py")
    assert (
        "_REGISTRY: dict[str, LoopKindStrategy] = {}" in body
    ), "the kind registry changed shape — re-measure what makes the supervisor pluggable"
    assert "def register(" in body, "the registry lost its register() entry point"

    # The noun-level half: every registered kind already has a template alias. This is the half of
    # clause 3 that has converged, and it is why the remaining work is behaviour, not naming.
    from personalclaw.loop.loop import KINDS

    assert KINDS, "LoopKind declares no members — import drift?"
    unaliased = sorted(KINDS - set(KIND_TO_TEMPLATE))
    assert not unaliased, (
        f"loop kinds with no bundled-template alias: {unaliased}. Every kind must resolve to a "
        f"template before the pluggable Python behind it can be retired."
    )
    assert len(KIND_TO_TEMPLATE) == len(KINDS) == 5, (
        f"the five kinds are now {len(KINDS)} kinds / {len(KIND_TO_TEMPLATE)} aliases — PP-16's "
        f"'five kinds become templates + policies' clause is sized against five."
    )
