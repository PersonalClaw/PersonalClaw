"""The `run_completed` chain runtime — "when X finishes, run Y" (§7 item 8 — S122).

🔴 THE DEFECT. `run_completed` was a declared kind with no firing path. It is in `KINDS`, `SPEC_KEYS`
accepts `{source_trigger, source_def}`, the store persists it, `/api/triggers` lists it and the
Automations page renders it. Nothing ever fired one. Measured with a real `clock:nightly` and a
`run_completed:after` pointed at it:

    clock tick considered: ['clock:nightly']    # the source fires
    file poller:           []
    web poller:            []
    → run_completed:after is reached by NOTHING

So "when my nightly backup finishes, notify me" was creatable, listed, and permanently silent.

**This is the THIRD kind found in this state** — `file` (S93), `web_watch` (S121), and now this.
Three instances is a pattern, not a coincidence, so this file also carries the completeness test
that makes it checkable: every kind in `KINDS` must have a runtime or a documented reason.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import chain
from personalclaw.triggers.models import KINDS, Trigger
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _add(store, tid, kind, spec):
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind=kind,
            enabled=True,
            spec=spec,
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get(tid).trigger


# ── matching ──


def test_a_chain_trigger_is_FOUND_for_its_source(store):
    """🔴 The defect at its root: nothing looked for these, so nothing could fire one."""
    _add(store, "clock:nightly", "clock", {"kind": "interval", "interval_secs": 60})
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "clock:nightly"})
    assert [t.id for t in chain.chain_triggers(store, source_id="clock:nightly")] == [
        "run_completed:b"
    ]


def test_a_chain_on_a_DIFFERENT_source_does_not_match(store):
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "clock:other"})
    assert chain.chain_triggers(store, source_id="clock:nightly") == []


def test_a_chain_with_NO_source_matches_NOTHING(store):
    """🔴 Deliberate, and the important direction. A chain that fired on every run in the system
    would be a fire storm authored by omission — the user left a field blank."""
    _add(store, "run_completed:any", "run_completed", {})
    assert chain.chain_triggers(store, source_id="clock:nightly") == []


def test_a_DISABLED_chain_does_not_fire(store):
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "clock:nightly"})
    row = store.get("run_completed:b").trigger
    row.enabled = False
    store.upsert(row)
    assert chain.chain_triggers(store, source_id="clock:nightly") == []


def test_a_DEF_keyed_chain_matches_the_workflow_ref(store):
    """The second key `SPEC_KEYS` declares: "after any run of that workflow"."""
    _add(store, "run_completed:d", "run_completed", {"source_def": "nightly-backup"})
    assert [t.id for t in chain.chain_triggers_for_def(store, source_def="nightly-backup")] == [
        "run_completed:d"
    ]


def test_a_def_keyed_chain_is_NOT_matched_by_trigger_id(store):
    """Kept separate on purpose: a caller that knew only the trigger id must not accidentally match
    def-keyed rows, because "after that automation" and "after that workflow" are different
    questions."""
    _add(store, "run_completed:d", "run_completed", {"source_def": "nightly-backup"})
    assert chain.chain_triggers(store, source_id="nightly-backup") == []


def test_an_empty_def_matches_nothing(store):
    _add(store, "run_completed:d", "run_completed", {"source_def": "x"})
    assert chain.chain_triggers_for_def(store, source_def="") == []


# ── the chain actually chains ──


def test_a_completed_run_produces_the_NEXT_fire(store):
    _add(store, "clock:nightly", "clock", {"kind": "interval", "interval_secs": 60})
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "clock:nightly"})
    fires, refused = chain.next_fires(
        store, source_id="clock:nightly", source_payload={"trigger_id": "clock:nightly"}
    )
    assert [t.id for t, _ in fires] == ["run_completed:b"]
    assert refused == []


def test_a_chain_carries_TWO_links(store):
    """A → B → C is the case chaining exists for."""
    _add(store, "clock:nightly", "clock", {"kind": "interval", "interval_secs": 60})
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "clock:nightly"})
    _add(store, "run_completed:c", "run_completed", {"source_trigger": "run_completed:b"})

    fires, _ = chain.next_fires(store, source_id="clock:nightly", source_payload={})
    payload_b = fires[0][1]
    fires2, _ = chain.next_fires(store, source_id="run_completed:b", source_payload=payload_b)
    assert [t.id for t, _ in fires2] == ["run_completed:c"]
    assert fires2[0][1][chain.PATH_KEY] == ["clock:nightly", "run_completed:b"]


def test_the_payload_names_WHAT_FINISHED(store):
    """Without it the chained action cannot say why it is running at all."""
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "clock:nightly"})
    fires, _ = chain.next_fires(store, source_id="clock:nightly", source_payload={})
    assert fires[0][1]["source_trigger_id"] == "clock:nightly"


def test_the_depth_INCREMENTS_along_the_chain(store):
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "a"})
    fires, _ = chain.next_fires(
        store, source_id="a", source_payload={chain.DEPTH_KEY: 1, chain.PATH_KEY: ["z"]}
    )
    assert fires[0][1][chain.DEPTH_KEY] == 2


# ── the two controls ──


def test_a_CYCLE_is_refused_AND_NAMED_as_a_cycle(store):
    """🔴 A → B → A is an infinite fire loop a scheduler cannot distinguish from enthusiasm.

    Named as a CYCLE rather than reported as a depth overflow, deliberately: "too deep" sends the
    user off to raise a limit that was never the problem, while "this loops" is the actual fix.
    """
    _add(store, "run_completed:loop", "run_completed", {"source_trigger": "run_completed:loop"})
    fires, refused = chain.next_fires(
        store,
        source_id="run_completed:loop",
        source_payload={chain.PATH_KEY: ["run_completed:loop"], chain.DEPTH_KEY: 1},
    )
    assert fires == []
    assert "cycle" in refused[0]["reason"]


def test_the_DEPTH_CAP_refuses_with_a_visible_reason(store):
    """§7 criterion 8: zero silent drops. A chain that stopped with no row is indistinguishable from
    one that was never configured."""
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "clock:nightly"})
    fires, refused = chain.next_fires(
        store,
        source_id="clock:nightly",
        source_payload={chain.DEPTH_KEY: chain.MAX_CHAIN_DEPTH, chain.PATH_KEY: ["x", "y", "z"]},
    )
    assert fires == []
    assert "depth limit" in refused[0]["reason"]
    assert "x → y → z" in refused[0]["reason"], "the reason must show the chain so far"


def test_a_chain_INSIDE_the_cap_still_fires(store):
    """The cap must bound abuse without breaking the legitimate A → B → C case."""
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "a"})
    fires, refused = chain.next_fires(
        store, source_id="a", source_payload={chain.DEPTH_KEY: chain.MAX_CHAIN_DEPTH - 1}
    )
    assert [t.id for t, _ in fires] == ["run_completed:b"]
    assert refused == []


def test_a_MALFORMED_depth_is_treated_as_zero_not_crashed(store):
    """A hand-edited payload must not take the chain offline."""
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "a"})
    fires, _ = chain.next_fires(
        store, source_id="a", source_payload={chain.DEPTH_KEY: "not a number"}
    )
    assert [t.id for t, _ in fires] == ["run_completed:b"]


def test_a_MALFORMED_path_is_treated_as_empty(store):
    _add(store, "run_completed:b", "run_completed", {"source_trigger": "a"})
    fires, _ = chain.next_fires(store, source_id="a", source_payload={chain.PATH_KEY: "nope"})
    assert [t.id for t, _ in fires] == ["run_completed:b"]


# ── the wiring ──


def test_the_gateway_CHAINS_after_a_completed_run():
    """🔴 The wiring, not the helper. A runtime nothing calls is the state this kind was already in.

    Chained from `_fire_store_trigger` because that is the single point every store-backed run
    completes — so a chain inherits the same dispatch, and therefore the same gates.
    """
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._fire_store_trigger)
    assert "_fire_chained_triggers" in src


def test_a_chained_fire_goes_through_THE_SAME_dispatch():
    """A chain with its own dispatch path would be a second place for the kill switch and the
    capability fence to be forgotten — exactly how the `web_watch` gap happened."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._fire_chained_triggers)
    assert "_fire_store_trigger" in src


def test_a_failing_chain_NEVER_fails_the_run_it_followed():
    """Chaining is a convenience layered on a completed run. Letting it fail that run would make
    chaining strictly worse than not chaining."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._fire_chained_triggers)
    assert "except Exception" in src


# ── the completeness check the pattern earned ──

#: Every kind, and how it fires. `file` (S93), `web_watch` (S121) and `run_completed` (S122) were
#: each found DECLARED-BUT-UNPOLLED, so this is a table rather than a comment: an entry is a live
#: runtime or a stated reason, and a new kind added without either fails the test below.
KIND_RUNTIMES: dict[str, str] = {
    "clock": "personalclaw.triggers.loop.run_forever (the tick)",
    "file": "personalclaw.triggers.file_poll.poll_all (S93)",
    "web_watch": "personalclaw.triggers.web_poll.poll_all (S121)",
    "run_completed": "personalclaw.triggers.chain.next_fires (S122)",
    "event": "personalclaw.event_triggers.execute_event_action (the data-event engine)",
    "manual": "the Run button / automation_run — fires on demand, needs no runtime",
    "view": "pull-on-view: fires from a surface RENDER, not a poll (§3's view kind)",
    "idle": "DEFERRED: autonudge absorbs it, gated on LOOPS-EVOLUTION Phase 4 (§7 item 9)",
    "webhook": "DEFERRED: needs POST /api/triggers/{id}/fire, which does not exist yet (see S119)",
}


def test_EVERY_declared_kind_has_a_runtime_or_a_STATED_REASON():
    """🔴 The test this pattern earned. Three kinds shipped declared-and-inert before anyone noticed,
    each found only by driving it. A kind added to `KINDS` without a runtime or a documented reason
    now fails here instead of silently becoming the fourth."""
    missing = sorted(set(KINDS) - set(KIND_RUNTIMES))
    assert not missing, (
        f"these kinds are declared in KINDS with no runtime and no stated reason: {missing}. "
        "Add the runtime, or record why it does not need one."
    )


def test_the_runtime_table_has_no_STALE_entries():
    """The other direction: an entry for a kind that no longer exists makes the table read as
    covering more than it does."""
    extra = sorted(set(KIND_RUNTIMES) - set(KINDS))
    assert not extra, f"the runtime table names kinds that are not in KINDS: {extra}"


@pytest.mark.parametrize(
    "kind,dotted",
    [(k, v) for k, v in KIND_RUNTIMES.items() if v.startswith("personalclaw.")],
)
def test_each_named_runtime_actually_EXISTS(kind, dotted):
    """A table naming a function nobody wrote would be the same false assurance one layer up."""
    import importlib

    path = dotted.split(" ")[0]
    module_name, _, attr = path.rpartition(".")
    module = importlib.import_module(module_name)
    assert hasattr(module, attr), f"{kind}'s runtime {path} does not exist"
