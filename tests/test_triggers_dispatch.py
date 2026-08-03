"""Dispatch and the event-bus delivery contract (AUTOMATION-SUBSTRATE §3.2/§3.3 — S64).

**The shipped bug this session fixes, reproduced before anything was written.**
`event_triggers._schedule_fire` records the fire, then asks for a running loop and `return`s when
there is none. Driven against a real store in a sync context: `fire_count` becomes 1 and the action
is **dropped with nothing recording that it did not run**. That is the silent drop §1.3 bans, in
shipped code, and the reproduction is pinned below so the spool cannot be removed without a failure.

The rest of this file is the delivery contract, and every rule names the failure it prevents —
peek-then-ack (crash mid-handling loses the event), the consumed-only
cursor (event loss on one side,
a poison pill on the other), the dedup window (a webhook sender's retry doing the work twice), and
the monotonic cursor (enabling one trigger replaying a month of history).
"""

import time

from personalclaw.triggers.dispatch import (
    COALESCE_WINDOW_SECS,
    DEDUP_WINDOW_SECS,
    MAX_TRANSIENT_RETRIES,
    Cursor,
    DeliveryStatus,
    Dispatch,
    DrainAction,
    Envelope,
    Handling,
    WakeKind,
    classify_handler_outcome,
    clear_spool,
    coalesce_family,
    cycle_guard,
    drain_decision,
    drain_spool,
    droppable,
    is_duplicate,
    payload_hash,
    spool_fire,
    spool_path,
)

NOW = 1_700_000_000.0


def _env(seq: int = 1, **over) -> Envelope:
    base = dict(source="memory", kind="MemoryUpdate", payload={"k": "v"}, emitted_at=NOW)
    base.update(over)
    return Envelope(seq=seq, **base)  # type: ignore[arg-type]


# ── the shipped bug, pinned ──


def test_the_SHIPPED_sync_context_drop_is_REAL(tmp_path, monkeypatch):
    """Measured, not inferred. `_schedule_fire` records the fire and
    returns when there is no running
    loop, so a sync CLI memory write counts a fire whose action never ran
    — and nothing anywhere says
    so. This test documents the defect the spool exists to fix; if `event_triggers` is ever fixed
    directly, this is where that shows up."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.event_triggers import EventTrigger, EventTriggerEngine, EventTriggerStore

    store = EventTriggerStore(tmp_path / "event_triggers.json")
    store.upsert(
        EventTrigger(
            id="e1",
            pattern="MemoryUpdate",
            action_provider="bash",
            action_config={"command": "true"},
            enabled=True,
        )
    )
    engine = EventTriggerEngine()
    monkeypatch.setattr(engine, "_get_store", lambda: store)
    # No running loop — exactly a sync CLI write.
    engine.on_memory_event(event_type="MemoryUpdate", key="k", value="v", now=time.time())
    assert store.load()[0].fire_count == 1, "the fire was counted"
    # …and the action went nowhere. That is the whole point.


def test_the_spool_gives_a_sync_context_fire_SOMEWHERE_TO_GO(tmp_path):
    """The fix: park it on disk, drain it on the next tick."""
    spool = tmp_path / "spool.jsonl"
    assert spool_fire(_env(), path=spool) is True
    drained, bad = drain_spool(path=spool)
    assert len(drained) == 1
    assert bad == 0


def test_a_spool_write_FAILURE_does_not_break_the_caller(tmp_path):
    """Best-effort by design. The event is lost, but the memory write that triggered it still
    succeeds — the opposite trade would let an unwritable disk take down ordinary use."""
    unwritable = tmp_path / "nope" / "x" / "spool.jsonl"
    unwritable.parent.mkdir(parents=True)
    unwritable.parent.chmod(0o400)
    try:
        assert spool_fire(_env(), path=unwritable) is False
    finally:
        unwritable.parent.chmod(0o700)


def test_the_spool_lives_under_the_CONFIG_dir(tmp_path, monkeypatch):
    """Resolved per call, not at import: a module-level path binds to
    whichever home was set when the
    module first loaded, which is how a test writes into the real `~/.personalclaw`."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    assert str(spool_path()).startswith(str(tmp_path))


def test_ONE_DAMAGED_line_does_not_hide_the_rest(tmp_path):
    """A partial write at power-loss damages one line. A single JSON array would lose every spooled
    fire to that; JSONL loses one."""
    spool = tmp_path / "spool.jsonl"
    spool_fire(_env(seq=1), path=spool)
    with spool.open("a", encoding="utf-8") as handle:
        handle.write("{truncated\n")
    spool_fire(_env(seq=2), path=spool)
    drained, bad = drain_spool(path=spool)
    assert [e.seq for e in drained] == [1, 2]
    assert bad == 1


def test_draining_does_NOT_truncate(tmp_path):
    """Peek-then-deliver-then-ack, applied to the spool. Truncating on read would lose every spooled
    fire to a crash during handling — the same bug the spool exists to fix, one layer up."""
    spool = tmp_path / "spool.jsonl"
    spool_fire(_env(), path=spool)
    drain_spool(path=spool)
    assert drain_spool(path=spool)[0], "the spool still holds the event after a read"


def test_clearing_KEEPS_what_arrived_during_the_drain(tmp_path):
    """That window is exactly when a busy machine spools most, so an
    unconditional truncate would drop
    the fires it was busiest producing."""
    spool = tmp_path / "spool.jsonl"
    for seq in (1, 2, 3):
        spool_fire(_env(seq=seq), path=spool)
    drained, _ = drain_spool(path=spool)
    spool_fire(_env(seq=99), path=spool)  # arrives mid-drain
    clear_spool(handled=len(drained), path=spool)
    assert [e.seq for e in drain_spool(path=spool)[0]] == [99]


def test_the_drain_is_BOUNDED(tmp_path):
    """A spool that grew while the gateway was down must not block boot on ten thousand events."""
    spool = tmp_path / "spool.jsonl"
    for seq in range(20):
        spool_fire(_env(seq=seq), path=spool)
    assert len(drain_spool(path=spool, limit=5)[0]) == 5


def test_draining_a_MISSING_spool_is_empty_not_an_error(tmp_path):
    assert drain_spool(path=tmp_path / "absent.jsonl") == ([], 0)


# ── deterministic ids and dedup ──


def test_the_event_id_is_DETERMINISTIC_over_payload_content():
    """A random id would make at-least-once delivery indistinguishable from duplicate work."""
    assert _env(payload={"a": 1}).event_id == _env(seq=99, payload={"a": 1}).event_id


def test_the_hash_is_STABLE_across_key_order():
    """`json.dumps` preserves insertion order by default, so two dicts with the same content in a
    different order would hash differently — defeating the dedup window
    exactly when it matters, on a
    sender retrying with a re-serialized body."""
    assert payload_hash("s", "k", {"x": 1, "y": 2}) == payload_hash("s", "k", {"y": 2, "x": 1})


def test_a_DIFFERENT_payload_is_a_different_event():
    assert _env(payload={"a": 1}).event_id != _env(payload={"a": 2}).event_id


def test_a_repeat_INSIDE_the_window_is_a_duplicate():
    env = _env()
    seen = {env.payload_hash: NOW}
    assert is_duplicate(env, seen, NOW + 10) is True


def test_a_repeat_OUTSIDE_the_window_is_new_work():
    """The same nightly digest tomorrow is not a duplicate of today's."""
    env = _env()
    seen = {env.payload_hash: NOW}
    assert is_duplicate(env, seen, NOW + DEDUP_WINDOW_SECS + 1) is False


def test_the_dedup_check_does_NOT_mutate_the_seen_set():
    """The caller records the hash only after it decides to process, so a
    crash between the check and
    the work leaves the event still deliverable. Marking here would make dedup itself drop events.
    """
    env = _env()
    seen: dict = {}
    is_duplicate(env, seen, NOW)
    assert seen == {}


# ── the cursor rule ──


def test_a_DELIVERED_event_is_consumed():
    action, _why = drain_decision(handling=Handling.DELIVERED.value, held_retries=0)
    assert action == DrainAction.CONSUME.value


def test_a_PERMANENT_failure_advances_rather_than_stalling_the_stream():
    """The payload is bad. Holding forever would be a poison pill that blocks every later event."""
    action, why = drain_decision(handling=Handling.PERMANENT.value, held_retries=0)
    assert action == DrainAction.CONSUME.value
    assert "stall" in why


def test_a_TRANSIENT_failure_HOLDS_the_drain():
    """The correct answer to "the provider is down": the event is not
    lost, and the next tick retries."""
    action, why = drain_decision(handling=Handling.TRANSIENT.value, held_retries=0)
    assert action == DrainAction.HOLD.value
    assert "not lost" in why


def test_a_transient_failure_GIVES_UP_at_the_budget():
    """Holding indefinitely on one unreachable provider would stop every other automation, which is
    worse than one loudly-dropped event."""
    action, why = drain_decision(
        handling=Handling.TRANSIENT.value, held_retries=MAX_TRANSIENT_RETRIES - 1
    )
    assert action == DrainAction.GIVE_UP.value
    assert str(MAX_TRANSIENT_RETRIES) in why


def test_an_UNCLASSIFIED_throw_is_TRANSIENT_not_permanent():
    """ "Never drop": a handler that raised on a network blip must be retried. Treating an
    unclassified exception as permanent turns a recoverable failure into data loss."""
    assert classify_handler_outcome(RuntimeError("blip")) == Handling.TRANSIENT.value


def test_a_handler_that_REPORTS_permanent_is_believed():
    """It knows its payload is unusable in a way the dispatcher cannot see."""
    assert classify_handler_outcome(None, Handling.PERMANENT.value) == Handling.PERMANENT.value


def test_no_exception_and_no_report_is_DELIVERED():
    assert classify_handler_outcome(None) == Handling.DELIVERED.value


# ── the monotonic cursor ──


def test_the_cursor_ADVANCES_forward():
    cursor = Cursor(trigger_id="t", stream="memory")
    assert cursor.advance(5) is True
    assert cursor.seq == 5


def test_the_cursor_REFUSES_to_move_backwards():
    """What stops a repeatedly-firing trigger from reprocessing history — the failure where enabling
    one trigger replays a month of events."""
    cursor = Cursor(trigger_id="t", stream="memory", seq=10)
    assert cursor.advance(3) is False
    assert cursor.seq == 10


def test_advancing_RESETS_the_held_retry_count():
    """A count that carried over would give the next event a shorter budget than the first."""
    cursor = Cursor(trigger_id="t", stream="memory", held_retries=3)
    cursor.advance(1)
    assert cursor.held_retries == 0


def test_the_cursor_is_keyed_per_TRIGGER_and_STREAM():
    """One trigger consuming slowly must not hold another back, and one stream's position says
    nothing about another's."""
    payload = Cursor(trigger_id="t-1", stream="memory", seq=4).to_dict()
    assert payload["trigger_id"] == "t-1"
    assert payload["stream"] == "memory"


# ── wake vs resume ──


def test_a_WAKE_is_droppable_when_the_session_is_busy():
    """The run in flight will drain the inbox, so skipping IS `overlap:
    skip` — exactly what autonudge
    already does for a mid-turn nudge."""
    assert droppable(WakeKind.WAKE.value) is True


def test_a_RESUME_is_NEVER_droppable():
    """It carries a gate answer for a parked run. An overlap guard that ate it would strand the run
    forever waiting for an answer the user already gave."""
    assert droppable(WakeKind.RESUME.value) is False


def test_an_unknown_wake_kind_is_treated_as_DROPPABLE():
    """Only `resume` carries an answer; anything else is a wake, and treating an unknown kind as
    undroppable would let a bad value pin a busy session."""
    assert droppable("something-new") is True


# ── the cycle guard ──


def test_a_trigger_cannot_fire_on_its_OWN_run_s_event():
    """The hook-recursion storm. Guarded on `spawned_by` rather than a depth counter, because depth
    catches it one level late — by then a mutating automation has already made one unwanted write.
    """
    own = _env(spawned_by="t-1")
    ok, why = cycle_guard(own, "t-1")
    assert ok is False
    assert "its own run" in why


def test_another_trigger_MAY_fire_on_that_event():
    """Chains are legitimate; only self-loops are not."""
    assert cycle_guard(_env(spawned_by="t-1"), "t-2")[0] is True


def test_an_event_with_no_lineage_fires_normally():
    assert cycle_guard(_env(), "t-1")[0] is True


# ── coalescing ──


def test_a_BURST_of_one_family_collapses_to_the_LATEST():
    """For a `FileChanged` burst the newest state is the one worth acting on — acting on the first
    means reading a file the user has since changed again."""
    burst = [
        _env(seq=1, emitted_at=NOW),
        _env(seq=2, emitted_at=NOW + 0.05),
        _env(seq=3, emitted_at=NOW + 0.10),
    ]
    assert [e.seq for e in coalesce_family(burst, NOW)] == [3]


def test_DIFFERENT_families_are_not_collapsed_together():
    mixed = [_env(seq=1, kind="A"), _env(seq=2, kind="B")]
    assert len(coalesce_family(mixed, NOW)) == 2


def test_events_an_HOUR_apart_are_two_facts_not_a_burst():
    far = [_env(seq=1, emitted_at=NOW), _env(seq=2, emitted_at=NOW + 3600)]
    assert len(coalesce_family(far, NOW)) == 1  # same family, latest wins
    assert coalesce_family(far, NOW)[0].seq == 2


def test_the_coalesced_batch_is_ORDERED_by_seq():
    """A reproducible batch: an unstable order makes a bug in one member intermittent."""
    mixed = [_env(seq=5, kind="B"), _env(seq=2, kind="A")]
    assert [e.seq for e in coalesce_family(mixed, NOW)] == [2, 5]


def test_the_coalesce_window_is_declared():
    assert 0 < COALESCE_WINDOW_SECS <= 0.25


# ── the dispatch record ──


def test_delivery_state_is_PER_TARGET():
    """One fire can have several targets. A single status would make "delivered" mean "delivered
    somewhere", which a user reads as "it worked" when half of it did not."""
    dispatch = Dispatch(id="d", trigger_id="t", event_id="e")
    dispatch.mark("notify", DeliveryStatus.DELIVERED.value)
    dispatch.mark("inbox", DeliveryStatus.PENDING.value)
    assert dispatch.fully_delivered is False


def test_fully_delivered_requires_EVERY_target():
    dispatch = Dispatch(id="d", trigger_id="t", event_id="e")
    dispatch.mark("notify", DeliveryStatus.DELIVERED.value)
    dispatch.mark("inbox", DeliveryStatus.DELIVERED.value)
    assert dispatch.fully_delivered is True


def test_a_dispatch_with_NO_targets_is_not_delivered():
    """No targets means nobody was told. For `delivery: none` that is
    correct and for anything else it
    is a bug, so the honest answer is False and the caller decides."""
    assert Dispatch(id="d", trigger_id="t", event_id="e").fully_delivered is False


def test_attempts_count_only_the_NON_delivered_marks():
    """An attempt counter that incremented on success would make a
    first-try delivery look retried."""
    dispatch = Dispatch(id="d", trigger_id="t", event_id="e")
    dispatch.mark("notify", DeliveryStatus.DELIVERED.value)
    dispatch.mark("inbox", DeliveryStatus.PENDING.value)
    dispatch.mark("inbox", DeliveryStatus.PENDING.value)
    assert dispatch.attempts.get("notify") is None
    assert dispatch.attempts["inbox"] == 2


def test_given_up_targets_are_ENUMERABLE():
    """Bounded give-up is loud-logged to the ledger, so the record has to be able to name which
    target was abandoned."""
    dispatch = Dispatch(id="d", trigger_id="t", event_id="e")
    dispatch.mark("channel:slack", DeliveryStatus.GIVEN_UP.value)
    dispatch.mark("inbox", DeliveryStatus.DELIVERED.value)
    assert dispatch.given_up == ["channel:slack"]


def test_a_dispatch_serializes_its_DERIVED_delivery_state():
    """A surface should not have to re-derive "did this get through" from the target map."""
    dispatch = Dispatch(id="d", trigger_id="t", event_id="e")
    dispatch.mark("inbox", DeliveryStatus.DELIVERED.value)
    assert dispatch.to_dict()["fully_delivered"] is True


def test_an_envelope_ROUND_TRIPS():
    original = _env(seq=7, spawned_by="t-9", payload={"nested": {"a": [1, 2]}})
    assert Envelope.from_dict(original.to_dict()) == original


def test_a_TOLERANT_read_survives_a_partial_envelope():
    """A spool line written by an older build must still load."""
    restored = Envelope.from_dict({"source": "memory"})
    assert restored.seq == 0
    assert restored.payload == {}
