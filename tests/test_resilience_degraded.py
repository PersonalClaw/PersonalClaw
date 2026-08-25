"""No-model degraded-contract tests (PLATFORM-RESILIENCE §5).

Pins the contract registry, the availability derivation (every needed use-case must
resolve), the read-only/fail-safe backlog + availability probes, and the
one-notification-per-transition rule (silent baseline on first sight → warning on
down → info on recovery).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from personalclaw.resilience import degraded
from personalclaw.resilience.degraded import DegradedContract


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Snapshot + restore the process-global contract registry and transition
    baseline, so a test's throwaway ``t_*`` contracts never leak into sibling tests
    (or other files) under xdist."""
    saved = dict(degraded._CONTRACTS)
    degraded.reset_transition_state()
    yield
    degraded._CONTRACTS.clear()
    degraded._CONTRACTS.update(saved)
    degraded.reset_transition_state()


# ── the built-in contract set ────────────────────────────────────────────────


def test_builtin_contracts_registered():
    surfaces = {c.surface for c in degraded.all_contracts()}
    assert {
        "chat",
        "inbox_enrichment",
        "memory_extraction",
        "knowledge_ingest",
        "search_ranking",
        "transcription",
        "assistant_reasoning",
    } <= surfaces


def test_the_synthesis_watcher_contract_is_registered_with_a_drain():
    """PR2-9. The synthesis-watcher floor was deferred as "future infra"; it isn't.

    ``mode: append_evidence`` persists dated evidence with no model (semantics.py) and
    ``knowledge.staleness`` counts what the compiled section has not caught up with — both
    shipped, so the surface declares its floor like every other one, and its floor names
    the mechanism (``append_evidence``) rather than gesturing at one.
    """
    contract = degraded.get_contract("synthesis_watchers")
    assert contract is not None, "the synthesis-watcher floor must be declared"
    assert "append_evidence" in contract.floor
    assert contract.drain is degraded._synthesis_evidence_drain


def test_the_three_reenrichment_surfaces_carry_both_halves_of_the_contract():
    """A queue with no drain is a promise nobody keeps; a drain with no count is a promise
    nobody can check. The three surfaces with a real deficit carry BOTH.

    Asserted by identity against the module's own callables, so a contract re-registered
    with a placeholder (``lambda: 0``, ``None``) fails here rather than passing on shape.
    """
    expected = {
        "memory_extraction": (degraded._memory_staging_backlog, degraded._memory_staging_drain),
        "knowledge_ingest": (
            degraded._knowledge_heuristic_backlog,
            degraded._knowledge_heuristic_drain,
        ),
        "synthesis_watchers": (
            degraded._synthesis_stale_backlog,
            degraded._synthesis_evidence_drain,
        ),
    }
    for surface, (probe, drain) in expected.items():
        contract = degraded.get_contract(surface)
        assert contract is not None, surface
        assert contract.backlog_probe is probe, surface
        assert contract.drain is drain, surface


def test_feature_off_surfaces_still_have_no_drain():
    """VACUITY for the rule above: "has a drain" must not be a property of every contract.

    ``chat`` is honestly unavailable and ``transcription`` is a declared feature-off tier —
    neither queues anything, so a drain hook on either would be a control nothing can ever
    move. A blanket "every contract has a drain" assertion would pass on that mistake.
    """
    for surface in ("chat", "transcription"):
        contract = degraded.get_contract(surface)
        assert contract is not None and contract.drain is None, surface


# ── availability derivation ──────────────────────────────────────────────────


def test_availability_all_use_cases_must_resolve(monkeypatch):
    """A surface is available only when EVERY use-case it needs resolves."""
    resolvable = {"chat"}
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: uc in resolvable,
    )
    degraded.register_contract(DegradedContract(surface="t_one", use_cases=("chat",), floor="f"))
    degraded.register_contract(
        DegradedContract(surface="t_both", use_cases=("chat", "embedding"), floor="f")
    )
    rows = {r["surface"]: r for r in degraded.evaluate()}
    assert rows["t_one"]["available"] is True  # chat resolves
    assert rows["t_both"]["available"] is False  # embedding does not


def test_availability_probe_fault_fails_available_not_down(monkeypatch):
    """A raising probe must not make a surface look falsely degraded (avoid a false
    alarm from an unrelated bug)."""

    def _boom(uc):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr("personalclaw.providers.provider_bridge.can_resolve_use_case", _boom)
    degraded.register_contract(DegradedContract(surface="t_fault", use_cases=("chat",), floor="f"))
    row = next(r for r in degraded.evaluate() if r["surface"] == "t_fault")
    assert row["available"] is True


def test_backlog_probe_is_fail_safe(monkeypatch):
    """A raising backlog probe reports 0, never propagates."""
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: False
    )

    def _boom() -> int:
        raise RuntimeError("store gone")

    degraded.register_contract(
        DegradedContract(surface="t_backlog", use_cases=("chat",), floor="f", backlog_probe=_boom)
    )
    row = next(r for r in degraded.evaluate() if r["surface"] == "t_backlog")
    assert row["backlog"] == 0


def test_degraded_surfaces_lists_only_unavailable(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: uc == "chat",
    )
    degraded.register_contract(DegradedContract(surface="t_up", use_cases=("chat",), floor="f"))
    degraded.register_contract(
        DegradedContract(surface="t_down", use_cases=("embedding",), floor="f")
    )
    down = degraded.degraded_surfaces()
    assert "t_down" in down and "t_up" not in down


# ── transition notifications (one per change; silent baseline) ───────────────


class _RecordingState:
    def __init__(self):
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body))


def test_first_evaluation_is_silent_baseline(monkeypatch):
    """No boot storm — the first sight of a surface only seeds the baseline."""
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: False
    )
    degraded.register_contract(DegradedContract(surface="t_new", use_cases=("chat",), floor="f"))
    state = _RecordingState()
    degraded.evaluate(notify=True, state=state)
    assert state.notes == []  # baseline seeded, nothing emitted


def test_down_then_recovery_emits_warning_then_info(monkeypatch):
    available = {"value": True}
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: available["value"],
    )
    degraded.register_contract(
        DegradedContract(surface="t_flap", use_cases=("chat",), floor="the floor")
    )
    state = _RecordingState()
    # Filter to THIS surface's notes — the built-in contracts share the monkeypatched
    # probe and transition alongside t_flap, which is not what this test measures.
    flap = lambda: [n for n in state.notes if "t_flap" in n[1]]  # noqa: E731

    degraded.evaluate(notify=True, state=state)  # baseline: available
    assert flap() == []

    available["value"] = False
    degraded.evaluate(notify=True, state=state)  # went down → warning
    assert len(flap()) == 1
    assert flap()[0][0] == "warning" and "t_flap" in flap()[0][1]

    available["value"] = True
    degraded.evaluate(notify=True, state=state)  # recovered → info
    assert len(flap()) == 2
    assert flap()[1][0] == "info" and "recovered" in flap()[1][1]


def test_no_change_emits_nothing(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: False
    )
    degraded.register_contract(DegradedContract(surface="t_stable", use_cases=("chat",), floor="f"))
    state = _RecordingState()
    degraded.evaluate(notify=True, state=state)  # baseline
    degraded.evaluate(notify=True, state=state)  # still down — no new note
    degraded.evaluate(notify=True, state=state)
    assert state.notes == []


def test_evaluate_without_notify_never_touches_state(monkeypatch):
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: False
    )
    degraded.register_contract(DegradedContract(surface="t_quiet", use_cases=("chat",), floor="f"))
    # notify defaults False; a plain rollup for the Doctor must not notify.
    rows = degraded.evaluate()
    assert any(r["surface"] == "t_quiet" for r in rows)


# ── the three drains: the CALL SITE, not the hook (PR2-9) ────────────────────
#
# A registered drain proves nothing on its own. What these pin is that the recovery
# transition RUNS it, and that each drain moves real rows in a real store: a lesson batch
# carrying its staging refs, a re-enqueued partial item, a queued recompile. Each has a
# vacuity case that must NOT move anything, because a drain that reports work it did not do
# is worse than one that reports none.


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home for the drains that WRITE.

    Both ``config_dir`` bindings are patched: ``config/__init__.py`` binds the name at
    import, so patching only the loader leaves an import-bound store pointed at the
    developer's real home. The redirect is then ASSERTED rather than assumed — a patch that
    silently missed would let this file write into ``~/.personalclaw``.
    """
    from personalclaw.learning import staging as _staging

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.config_dir", lambda: tmp_path, raising=False)
    from personalclaw.config.loader import config_dir

    assert config_dir() == tmp_path, "config_dir redirect did not take"
    _staging.reset_store()
    yield tmp_path
    _staging.reset_store()


def _flip(monkeypatch, available: dict):
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: available["value"],
    )


def test_recovery_fires_the_contracts_drain(home, monkeypatch):
    """The call site. §5.1: the unavailable→available flip is what fires the drain.

    Without this, every drain below is a callable nothing ever calls — the shape of an
    inert control. The live state object is handed through, because the knowledge drain
    needs the ingest queue that lives on it.
    """
    calls: list[object] = []

    async def _drain(state=None) -> int:
        calls.append(state)
        return 3

    available = {"value": False}
    _flip(monkeypatch, available)
    degraded.register_contract(
        DegradedContract(surface="t_drain", use_cases=("chat",), floor="f", drain=_drain)
    )
    state = _RecordingState()

    degraded.evaluate(notify=True, state=state)  # baseline: down
    assert calls == []
    available["value"] = True
    degraded.evaluate(notify=True, state=state)  # recovered → drain

    assert calls == [state], "the recovery transition must run the drain, with the live state"


def test_going_down_and_holding_steady_never_fire_the_drain(home, monkeypatch):
    """VACUITY for the call site. A drain that fired on ANY evaluation would look identical
    in the test above and re-enrich on every poll of a surface that is still down."""
    calls: list[object] = []

    async def _drain(state=None) -> int:
        calls.append(state)
        return 0

    available = {"value": True}
    _flip(monkeypatch, available)
    degraded.register_contract(
        DegradedContract(surface="t_nodrain", use_cases=("chat",), floor="f", drain=_drain)
    )
    state = _RecordingState()

    degraded.evaluate(notify=True, state=state)  # baseline: up
    available["value"] = False
    degraded.evaluate(notify=True, state=state)  # went DOWN
    degraded.evaluate(notify=True, state=state)  # still down
    degraded.evaluate(notify=True, state=state)  # still down

    assert calls == []


def test_a_raising_drain_never_breaks_the_recovery(home, monkeypatch):
    """A broken drain must not turn a RECOVERY into an error — the notification the user
    was waiting for still has to arrive."""

    async def _boom(state=None) -> int:
        raise RuntimeError("drain exploded")

    available = {"value": False}
    _flip(monkeypatch, available)
    degraded.register_contract(
        DegradedContract(surface="t_boom", use_cases=("chat",), floor="f", drain=_boom)
    )
    state = _RecordingState()
    degraded.evaluate(notify=True, state=state)
    available["value"] = True

    degraded.evaluate(notify=True, state=state)  # must not raise

    assert any("t_boom recovered" == title for _kind, title, _body in state.notes)


def _recovered_body(state, surface: str) -> str:
    return next(body for _kind, title, body in state.notes if title == f"{surface} recovered")


def _recover(monkeypatch, contract) -> "_RecordingState":
    available = {"value": False}
    _flip(monkeypatch, available)
    degraded.register_contract(contract)
    state = _RecordingState()
    degraded.evaluate(notify=True, state=state)  # baseline: down
    available["value"] = True
    degraded.evaluate(notify=True, state=state)  # recovered
    return state


def test_the_recovery_notification_reports_what_was_REENRICHED(home, monkeypatch):
    """criterion #3's last clause ("a recovery notification summarizes what was
    re-enriched"). `backlog` is measured BEFORE the drain runs, so a recovery that just
    cleared the queue must not announce that queue as still pending."""

    async def _drain(state=None) -> int:
        return 7

    state = _recover(
        monkeypatch,
        DegradedContract(
            surface="t_summary",
            use_cases=("chat",),
            floor="f",
            backlog_probe=lambda: 7,
            drain=_drain,
        ),
    )

    assert "7 item(s) re-enriched" in _recovered_body(state, "t_summary")


def test_a_drain_that_moved_nothing_still_reports_the_STANDING_backlog(home, monkeypatch):
    """VACUITY. "re-enriched" has to be a claim about work actually done — a drain that
    could move nothing (no live worker behind it) must leave the backlog visible instead of
    swallowing it into a recovery message that sounds finished."""

    async def _drain(state=None) -> int:
        return 0

    state = _recover(
        monkeypatch,
        DegradedContract(
            surface="t_stuck",
            use_cases=("chat",),
            floor="f",
            backlog_probe=lambda: 5,
            drain=_drain,
        ),
    )

    body = _recovered_body(state, "t_stuck")
    assert "5 item(s) awaiting re-enrichment" in body and "re-enriched" not in body


# ── memory_extraction: the LEARN-R19 staging drain ───────────────────────────


def _stage(home, contents):
    from personalclaw.learning.staging import get_store

    store = get_store()
    for content in contents:
        store.stage(cadence="per_turn", kind="lesson", content=content)
    return store


def test_the_memory_drain_compiles_pending_captures_into_a_lesson_batch(home):
    """The staging log's `pending`/`staging_refs`/`mark_consumed` trio had no caller at all.
    This is it: the captures that piled up while no model was bound become ONE propose-only
    lesson batch that still points back at the entries it came from."""
    from personalclaw.learning import proposals
    from personalclaw.learning.staging import get_store

    _stage(home, ["prefers tabs", "hates emoji", "ships on fridays"])

    moved = asyncio.run(degraded._memory_staging_drain(None))

    assert moved == 3
    assert get_store().pending_count() == 0, "drained entries must be marked consumed"
    filed = proposals.list_pending("lesson_batch")
    assert len(filed) == 1, [p.title for p in filed]
    assert filed[0].staging_refs, "the proposal must carry the staging refs it compiled"
    assert "hates emoji" in filed[0].body


def test_the_memory_drain_leaves_entries_pending_when_nothing_was_filed(home, monkeypatch):
    """VACUITY, and the important one: consuming entries in exchange for a proposal that
    does NOT exist would delete the only record of those captures. A SKIP must cost nothing.
    """
    from personalclaw.learning import proposals
    from personalclaw.learning.staging import get_store

    _stage(home, ["prefers tabs", "hates emoji"])
    monkeypatch.setattr(proposals, "enqueue", lambda **kw: (proposals.Verdict.SKIP, None))

    moved = asyncio.run(degraded._memory_staging_drain(None))

    assert moved == 0
    assert get_store().pending_count() == 2, "a skipped proposal must not consume the entries"


def test_the_memory_backlog_probe_does_not_create_the_staging_log(home):
    """The probe runs on every poll of a read-only rollup, so it must answer from the absent
    file rather than opening (and thereby writing) one."""
    from personalclaw.learning.staging import DB_FILE

    assert degraded._memory_staging_backlog() == 0
    assert not (home / DB_FILE).exists()


def test_the_memory_backlog_probe_counts_past_the_page_limit(home):
    """A backlog read through `pending(limit=...)` reports the CAP once the queue passes it,
    so a growing queue looks perfectly stable. `pending_count` is why this is a real count."""
    _stage(home, [f"lesson {i}" for i in range(degraded.DRAIN_BATCH + 7)])

    assert degraded._memory_staging_backlog() == degraded.DRAIN_BATCH + 7


# ── knowledge_ingest: the KNOW-R17 heuristic tier's re-extraction ────────────


class _RecordingQueue:
    def __init__(self):
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)


def _knowledge_store(tmp_path, monkeypatch):
    import personalclaw.knowledge as K
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    monkeypatch.setattr(K, "get_knowledge_store", lambda: store)
    return store


def _heuristic_item(store, title: str) -> str:
    """An item as the LLM-free ingest graph leaves it: captured and indexed, insights not
    refreshed, `partial` with the reason the runner records verbatim."""
    item_id = store.create_typed_item(item_type="note", title=title, content=f"body of {title}")
    store.update_item(
        item_id,
        processing_status="partial",
        processing_error="insights: model unavailable (insights not refreshed — try regenerating)",
        touch=False,
    )
    return item_id


def test_the_knowledge_drain_reenqueues_the_heuristic_tier_items(tmp_path, monkeypatch):
    """The heuristic-tier stamp IS the queue marker, so the drain re-extracts in place
    through the one ingestion path rather than inventing a second one."""
    store = _knowledge_store(tmp_path, monkeypatch)
    stamped = _heuristic_item(store, "read without a model")
    queue = _RecordingQueue()

    moved = asyncio.run(
        degraded._knowledge_heuristic_drain(SimpleNamespace(knowledge_ingest_queue=lambda: queue))
    )

    assert moved == 1
    assert queue.enqueued == [stamped]
    assert store.get_item(stamped)["processing_status"] == "queued"


def test_the_knowledge_drain_leaves_a_healthy_item_alone(tmp_path, monkeypatch):
    """VACUITY. A fully enriched item, and a `partial` one that failed for an unrelated
    reason, are not this surface's backlog — re-enqueueing them would spend the model the
    user just got back on work nothing asked for."""
    store = _knowledge_store(tmp_path, monkeypatch)
    done = store.create_typed_item(item_type="note", title="fully enriched", content="body")
    store.update_item(done, processing_status="done", touch=False)
    unrelated = store.create_typed_item(item_type="note", title="broken reader", content="body")
    store.update_item(
        unrelated, processing_status="partial", processing_error="pdf_read: bad header", touch=False
    )
    queue = _RecordingQueue()

    moved = asyncio.run(
        degraded._knowledge_heuristic_drain(SimpleNamespace(knowledge_ingest_queue=lambda: queue))
    )

    assert (moved, queue.enqueued) == (0, [])
    assert degraded._knowledge_heuristic_backlog() == 0


def test_the_knowledge_drain_moves_nothing_without_a_live_queue(tmp_path, monkeypatch):
    """VACUITY for the worker: a Doctor run outside the gateway has no ingest queue, and
    claiming a re-enrichment with no worker behind it is worse than reporting none."""
    store = _knowledge_store(tmp_path, monkeypatch)
    stamped = _heuristic_item(store, "read without a model")

    assert asyncio.run(degraded._knowledge_heuristic_drain(None)) == 0
    assert store.get_item(stamped)["processing_status"] == "partial", "nothing may be touched"
    assert degraded._knowledge_heuristic_backlog() == 1, "and the backlog must still report it"


# ── synthesis_watchers: the append_evidence floor's recompile queue ──────────


def test_the_synthesis_drain_queues_one_recompile_per_stale_synthesis(home, monkeypatch):
    """criterion #3, the full re-enrichment flow: the evidence landed with no model, the
    compiled section above it fell behind, and the recovery queues a PROPOSED recompile —
    never an in-place rewrite of a document the reader may already have acted on."""
    from personalclaw.learning import proposals

    store = _knowledge_store(home, monkeypatch)
    synthesis = store.create_typed_item(
        item_type="insight", title="Overview of alpha", content="compiled body", tags=["alpha"]
    )
    store.create_typed_item(
        item_type="note", title="new evidence", content="appended later", tags=["alpha"]
    )
    assert degraded._synthesis_stale_backlog() == 1, "precondition: the synthesis is stale"

    queued = asyncio.run(degraded._synthesis_evidence_drain(None))

    assert queued == 1
    filed = proposals.list_pending("knowledge_draft")
    assert [p.target for p in filed] == [synthesis]
    assert "Recompile" in filed[0].title


def test_the_synthesis_drain_ignores_a_fresh_synthesis(home, monkeypatch):
    """VACUITY. Material that predates a synthesis is not new material, and an observed
    item is never stale — so neither the backlog nor the drain may move on them."""
    from personalclaw.learning import proposals

    store = _knowledge_store(home, monkeypatch)
    store.create_typed_item(item_type="note", title="pre-existing", content="body", tags=["beta"])
    store.create_typed_item(
        item_type="insight", title="Overview of beta", content="compiled", tags=["beta"]
    )

    assert degraded._synthesis_stale_backlog() == 0
    assert asyncio.run(degraded._synthesis_evidence_drain(None)) == 0
    assert proposals.list_pending("knowledge_draft") == []
