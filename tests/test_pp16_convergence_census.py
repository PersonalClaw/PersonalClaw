"""Which of `PP-16`'s six "one X" clauses have CONVERGED, measured rather than assumed.

The atom's `done_when` names six unifications: *"One status vocabulary, one adoption/reaping path,
one attention path, one ledger, one projection to tasks, one cockpit contract."* Three of those six
are **satisfied** — `PP-5` made the loop a `personalclaw.ledger` writer, `workflows/attention.py`
was built on the loop watchdog's own inbox seam, and the adoption/reaping slice converged both
nouns' boot sweeps onto `concurrency.boot_sweep`. A clause that has already converged is as
load-bearing to record as one that has not: a later slice that "unifies the ledger" would be
writing code for a problem that no longer exists, and a slice that re-forks either seam would
silently undo a landed atom.

So this rail is a census with two directions, in the idiom `test_pp16_loop_field_map.py`
established for the field map:

* **The converged clauses are RATCHETS.** Ledger, attention and boot adoption are asserted to still
  funnel through one seam each. If a future change gives loops their own ledger writer, their own
  `state.notify` + `store.add` pair, or their own private boot sweep again, this reds — which is the
  only thing standing between "`PP-5` landed" and "`PP-5` landed and then rotted". `PP-16` seam 3
  moved the PLUGGABLE SUPERVISOR into this group too: the convergence decision is declared data plus
  one evaluator, and the ratchet is keyed on the retired MEMBERS rather than on a class name (see
  below for why that distinction is load-bearing).
* **The unconverged clauses are PINNED COUNTS that must SHRINK.** The task projection and the
  cockpit contract each still have exactly two implementations. Each count is pinned with the
  file:line of both sides, so the slice that unifies one of them reds HERE and updates the census as
  part of landing — rather than leaving a stale "still open" list in a plan nobody re-measures.

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


# ── the three clauses that have already converged (ratchets) ──────────────────────────────


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


#: The two watchdogs whose boot sweep must keep running through `concurrency.boot_sweep`. This
#: row was in `_UNCONVERGED` — `loop/manager.py::reap_orphaned_loops` vs
#: `workflows/watchdog.py::_boot_sweep` — until the adoption/reaping slice retired the loop side's
#: gateway hook onto the shared primitive. Kept as a ratchet, not deleted: the census's whole
#: point is that a converged clause can rot, and a private sweep re-added to either watchdog
#: would silently restore the duplication with nothing else objecting.
_BOOT_SWEEPERS = ("loop/watchdog.py", "workflows/watchdog.py")


def test_the_adoption_clause_is_converged_and_stays_converged():
    """`PP-16`'s "one adoption/reaping path" clause is satisfied — both work-unit nouns decide
    their crash survivors through `concurrency.boot_sweep`, each from the first poll of the
    supervisor that owns the noun, with no second boot hook anywhere."""
    assert "async def boot_sweep(" in _text(
        "concurrency.py"
    ), "concurrency.boot_sweep is gone — the primitive both nouns share no longer exists"
    for rel in _BOOT_SWEEPERS:
        assert "concurrency.boot_sweep(" in _text(rel), (
            f"{rel} no longer sweeps through concurrency.boot_sweep. A private boot-adoption "
            f"loop here re-forks the path PP-16's adoption slice unified."
        )
    assert "reap_orphaned_loops" not in _text("gateway.py"), (
        "gateway.py awaits a loop boot-adoption hook again — the second INVOCATION is back even "
        "if the shared primitive is still used. See tests/test_pp16_boot_adoption.py for why "
        "that shape loses a failed sweep for the life of the process."
    )


def test_the_seam_probe_rejects_a_symbol_that_does_not_exist():
    """Vacuity floor for the three ratchets above: a scan that reports every symbol as present —
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
    # loop/tasks_link.py:167 provisions imperatively; workflows/materialize.py:257 PLANS a
    # materialization and returns bindings. Two directions over one relationship.
    "projection to tasks": (
        ("loop/tasks_link.py", "def provision"),
        ("workflows/materialize.py", "def plan_materialization"),
    ),
}


# ── clause 3, CONVERGED by PP-16 seam 3 (a ratchet from here on) ───────────────────────────
#
# This row used to live in `_UNCONVERGED` above, pinning `loop/kinds/__init__.py::class
# LoopKindStrategy` against `workflows/supervisor_policy.py::class SupervisorPolicy` as two
# implementations of one concept. Seam 3 unified the SUPERVISOR half: the convergence decision is
# declared in `KIND_CONVERGENCE` and evaluated once in `loop/supervisor.py`, and no strategy
# carries a done-ness member any more.
#
# Worth recording precisely, because the old row would NOT have caught this: it asserted the
# CLASS still existed, and `LoopKindStrategy` deliberately survives as the intake / worker-framing
# / projection seam. A row keyed on a class name could never have measured a per-METHOD retirement,
# which is why the ratchet below is keyed on the retired MEMBERS and on the dispatch actually
# reaching the policy.

#: The supervisor members that were retired from the plugin seam. A kind that re-grows any of them
#: has shipped a second convergence path beside the declared one.
_RETIRED_SUPERVISOR_MEMBERS = ("is_done_signal", "has_done_check", "budget_stop_genuine")


def test_the_pluggable_supervisor_clause_is_converged_and_stays_converged():
    """`PP-16`'s "the supervisor stops being pluggable Python" clause is satisfied for all five
    kinds: the declaration is data, the evaluator is one module, and no strategy answers a
    convergence question."""
    from personalclaw.loop import kinds
    from personalclaw.loop.loop import KINDS
    from personalclaw.workflows.supervisor_policy import DONE_SIGNALS, KIND_CONVERGENCE

    assert "def done_signal" in _text("loop/supervisor.py"), (
        "loop/supervisor.py no longer declares done_signal — the ONE convergence evaluator is "
        "gone, so either the seam was reverted or a second one was minted."
    )
    kinds.ensure_loaded()
    for kind in sorted(KINDS):
        strategy = kinds.get(kind)
        for member in _RETIRED_SUPERVISOR_MEMBERS:
            assert not hasattr(strategy, member), (
                f"{kind} strategy re-grew {member!r}. PP-16 seam 3 moved every convergence "
                f"decision onto the declared SupervisorPolicy; a kind that answers one in Python "
                f"is the two-path shape the clean-break tenet refuses."
            )
    # Every declared row names a mechanism from the CLOSED vocabulary — a table row with a typo'd
    # signal would otherwise be a loop that raises on its first finding.
    assert (
        KIND_CONVERGENCE
    ), "KIND_CONVERGENCE is empty — the declaration would be measuring nothing"
    for key, spec in KIND_CONVERGENCE.items():
        assert spec.signal in DONE_SIGNALS, f"{key} declares unknown signal {spec.signal!r}"


def test_every_kind_resolves_to_a_declared_convergence_row():
    """The vacuity floor for the ratchet above: a kind with no row would silently get the default
    policy, whose ORCHESTRATED signal never completes anything — a loop that runs forever rather
    than a test that fails."""
    from personalclaw.loop.loop import KINDS
    from personalclaw.workflows.supervisor_policy import (
        KIND_CONVERGENCE,
        convergence_key,
        policy_for_kind,
    )

    assert KINDS, "LoopKind declares no members — import drift?"
    for kind in sorted(KINDS):
        key = convergence_key(kind, {})
        assert key in KIND_CONVERGENCE, (
            f"loop kind {kind!r} resolves to convergence key {key!r}, which KIND_CONVERGENCE does "
            f"not declare — it would fall back to the default ORCHESTRATED policy and never "
            f"self-complete."
        )
        assert policy_for_kind(kind, {}).convergence is KIND_CONVERGENCE[key]
    # Negative control: a kind that does not exist must NOT resolve to a row, else the check above
    # would pass for anything.
    assert convergence_key("not-a-loop-kind", {}) not in KIND_CONVERGENCE


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


def test_the_five_kinds_are_templates_plus_policies_and_the_plugin_keeps_only_the_rest():
    """`PP-16`'s clause 3 — "the five kinds are bundled templates plus policies, so the domain
    intelligence lives in the policy and the supervisor stops being pluggable Python" — now holds
    on BOTH halves: every kind resolves to a bundled template (`loop_aliases.KIND_TO_TEMPLATE`,
    already true before seam 3) AND to a declared convergence policy.

    What deliberately REMAINS pluggable is named here rather than left implicit, so the next slice
    inherits a measurement instead of re-deriving it: intake (`classify`), worker framing
    (`build_brief` / `cycle_nudge`), planning (`walkthrough`), the multi-cycle orchestration hook
    (`on_new_cycle`) and the projection keys. Those are the bundled TEMPLATE's node prompts and
    graph, not the supervisor."""
    from personalclaw.loop.loop import KINDS
    from personalclaw.workflows.loop_aliases import KIND_TO_TEMPLATE
    from personalclaw.workflows.supervisor_policy import KIND_CONVERGENCE, convergence_key

    body = _text("loop/kinds/__init__.py")
    assert (
        "_REGISTRY: dict[str, LoopKindStrategy] = {}" in body
    ), "the kind registry changed shape — re-measure what the plugin seam still carries"
    assert "def register(" in body, "the registry lost its register() entry point"

    assert KINDS, "LoopKind declares no members — import drift?"
    unaliased = sorted(KINDS - set(KIND_TO_TEMPLATE))
    assert not unaliased, f"loop kinds with no bundled-template alias: {unaliased}."
    unpoliced = sorted(k for k in KINDS if convergence_key(k, {}) not in KIND_CONVERGENCE)
    assert not unpoliced, (
        f"loop kinds with no declared convergence policy: {unpoliced}. Both halves of clause 3 "
        f"must hold for every kind — a kind with a template but no policy is a half-migration."
    )
    assert len(KIND_TO_TEMPLATE) == len(KINDS) == 5, (
        f"the five kinds are now {len(KINDS)} kinds / {len(KIND_TO_TEMPLATE)} aliases — PP-16's "
        f"'five kinds become templates + policies' clause is sized against five."
    )

    # The plugin seam keeps exactly the non-supervisor concerns. Asserted as a POSITIVE list so
    # that retiring one of them later reds here and the census stays a measurement, not a memory.
    for member in ("classify", "build_brief", "cycle_nudge", "phase_key", "default_kind_config"):
        assert f"def {member}(" in body, (
            f"LoopKindStrategy no longer declares {member!r}. If a later PP-16 seam moved it into "
            f"the bundled template, that is the intended outcome — update this list."
        )
