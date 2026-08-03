"""The WakeupDispatcher — inbox + wakeup with `wake`/`resume` semantics (§3.2 — S89).

§3.2: "A fired trigger enqueues a typed payload onto the target session's inbox queue + a
wakeup signal;
a **WakeupDispatcher** claims and drives runs. … **wake** — drain inbox; skipped entirely if
the session
is already running … **resume** — a gate-answer/HITL result for a parked run; **must re-queue
until the
parked lock releases** — overlap guards must never eat gate answers intended for parked runs."

**Two hazards measured in the shipped `enqueue` before this module existed**, both invisible
to reading:

1. It DROPS the payload for an idle session — returns False and appends nothing unless the
semaphore is
   locked or `force=True`. A 3am cron fires precisely when the session is idle, so an unforced
   enqueue
   would silently lose the fires this subsystem delivers.
2. It returns False when the session does not exist, which is normal for a trigger whose session was
   never opened. So "could not queue" is REPORTED rather than assumed to be a delivery.

Every test drives a real `SessionManager`. A mock cannot show either hazard, because both live in
`enqueue`'s own branch on `session.semaphore.locked()`.
"""

from __future__ import annotations

import asyncio

from personalclaw.triggers import wakeup as W
from personalclaw.triggers.models import Trigger

NOW = 1_800_000_000.0


class _Provider:
    async def shutdown(self):
        return None


def _manager(*keys):
    """A real `SessionManager` with real `_Session` rows — not a mock.

    Constructed via `__new__` to skip the provider factory and event loop the full constructor
    wants:
    the dispatcher only touches `_sessions`, `enqueue` and the semaphore, and a real gateway
    boot in a
    unit test would be a different subsystem's setup.
    """
    from personalclaw.session import SessionManager, _Session

    manager = SessionManager.__new__(SessionManager)
    manager._sessions = {}
    for key in keys:
        manager._sessions[key] = _Session(provider=_Provider())
    return manager


def _lock(manager, key):
    """Mark a session mid-turn, the way a running turn does."""
    asyncio.run(manager._sessions[key].semaphore.acquire())


def _trigger(tid="schedule:j1", **over):
    base = dict(
        id=tid,
        name="Nightly",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 3600},
        workflow={"provider": "run-prompt", "config": {}},
    )
    base.update(over)
    return Trigger(**base)


class _Fire:
    """S88's `DueFire` shape, minimally."""

    def __init__(self, trigger, scheduled_for=NOW - 10, reason="due"):
        self.trigger = trigger
        self.scheduled_for = scheduled_for
        self.reason = reason
        self.claim = object()


# ── §3.2: key formats centralized, not reinvented ──


def test_a_trigger_targets_the_shipped_cron_prefix():
    """§3.2 says to extend the session-key conventions table rather than invent a parallel
    one. `cron:`
    is preserved verbatim: `_STATELESS_PREFIXES`, the `cron-{id}` dashboard pairing and
    `schedule_trigger`'s HTTP path all key off it, so a new prefix would silently opt every migrated
    trigger out of conventions it already relies on."""
    assert W.session_key_for("schedule:j1") == "cron:j1"
    assert W.session_key_for("j2") == "cron:j2"
    assert W.KEY_PREFIX_TRIGGER == "cron:"


def test_a_pinned_session_renders_as_the_same_cron_key():
    """`pinned:cron:{id}` is the stateful per-trigger session — the shipped `cron:{id}` key under
    another name, not a second session."""
    assert W.session_key_for("j4", session="pinned:cron:j4") == "cron:j4"


def test_a_conversation_binding_targets_the_live_chat():
    """An in-chat nudge renders into the conversation, not into a background session."""
    assert W.session_key_for("j3", session="conversation:dashboard:main") == "dashboard:main"


def test_an_empty_conversation_binding_falls_back_to_the_trigger_key():
    """A malformed binding must not produce an empty session key, which would queue into nothing."""
    assert W.session_key_for("j5", session="conversation:") == "cron:j5"


def test_a_namespaced_id_keeps_its_namespace_for_non_schedule_kinds():
    """The id namespace IS §6's migration map; rewriting it here would break the mapping."""
    assert W.session_key_for("event:e1") == "cron:event:e1"


# ── the two wakeup kinds ──


def test_a_fire_becomes_a_droppable_wake():
    """A fire is always a `wake`, never a `resume`: a resume answers a question a parked run
    asked, and a
    trigger firing on schedule has asked nothing. Conflating them would make every scheduled fire
    un-droppable and defeat `overlap: skip`."""
    wakeup = W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW)
    assert wakeup.kind == W.WakeKind.WAKE.value
    assert wakeup.droppable is True


def test_a_resume_is_never_droppable():
    """🔴 §3.2: "overlap guards must never eat gate answers intended for parked runs"."""
    resume = W.resume_for(
        trigger_id="j1", session_key="cron:j1", answer={"approved": True}, now=NOW
    )
    assert resume.kind == W.WakeKind.RESUME.value
    assert resume.droppable is False


def test_droppability_delegates_to_the_shipped_predicate():
    """`dispatch.droppable` is the shipped rule. Re-deriving it here would let the spool and the
    dispatcher disagree about which payloads may be discarded."""
    from personalclaw.triggers.dispatch import droppable

    assert droppable("wake") is True
    assert droppable("resume") is False


def test_the_fire_payload_carries_the_slot_not_a_rendered_prompt():
    """The executor owns rendering; a prompt baked here would be the second place a fire's wording
    lives."""
    payload = W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW).payload
    assert payload["trigger_id"] == "schedule:j1"
    assert payload["scheduled_for"] == NOW - 10
    assert "message" not in payload


def test_a_resume_targets_the_session_that_PARKED():
    """Passed in rather than derived: a workflow gate parks the RUN's session, not necessarily the
    trigger's. Deriving it would deliver the answer to the wrong place, which reads as "the
    gate never
    got my reply"."""
    resume = W.resume_for(
        trigger_id="schedule:j1", session_key="workflow:run-7", answer={"ok": True}, now=NOW
    )
    assert resume.session_key == "workflow:run-7"


# ── 🔴 hazard 1: the idle-session drop ──


def test_a_wake_reaches_an_IDLE_session():
    """🔴 THE measured hazard. The shipped `enqueue` returns False and appends NOTHING for an idle
    session unless `force=True` — driven and confirmed: the queue stayed at length 0. A 3am
    cron fires
    precisely because the session is idle, so an unforced enqueue would silently lose exactly
    the fires
    this subsystem exists to deliver."""
    manager = _manager("cron:j1")
    delivery = W.deliver(manager, W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW))
    assert delivery.disposition == W.Disposition.QUEUED.value
    assert delivery.delivered is True
    assert len(manager._sessions["cron:j1"].queue) == 1


def test_the_structured_payload_rides_the_queue_kwargs():
    """`enqueue` forwards `**kwargs` verbatim, which is how the typed payload survives without
    widening
    the queue's tuple shape."""
    manager = _manager("cron:j1")
    W.deliver(manager, W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW))
    _ts, _text, kwargs = manager._sessions["cron:j1"].queue[0]
    assert kwargs["wakeup"]["trigger_id"] == "schedule:j1"
    assert kwargs["wakeup"]["kind"] == "wake"


# ── §3.2: wake vs resume drop semantics ──


def test_a_wake_for_a_RUNNING_session_is_dropped():
    """§3.2's "natural implementation of `overlap: skip`" — the running session drains the inbox
    itself, so a second wake is noise."""
    manager = _manager("cron:j1")
    _lock(manager, "cron:j1")
    delivery = W.deliver(manager, W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW))
    assert delivery.disposition == W.Disposition.SKIPPED_RUNNING.value
    assert len(manager._sessions["cron:j1"].queue) == 0


def test_a_RESUME_for_a_running_session_is_still_queued():
    """🔴 The asymmetry that makes R11 resume-targets and R13 approvals safe. Dropping a gate answer
    because the session looks busy would strand the parked run forever."""
    manager = _manager("cron:j1")
    _lock(manager, "cron:j1")
    delivery = W.deliver(
        manager, W.resume_for(trigger_id="j1", session_key="cron:j1", answer={"ok": 1}, now=NOW)
    )
    assert delivery.disposition == W.Disposition.QUEUED.value
    assert len(manager._sessions["cron:j1"].queue) == 1


def test_is_running_reads_the_same_semaphore_enqueue_checks():
    """Asking a different question (provider alive, session exists) would make the dispatcher
    and the
    queue disagree about "busy", landing the payload on the wrong side of the drop rule."""
    manager = _manager("cron:j1")
    assert W.is_running(manager, "cron:j1") is False
    _lock(manager, "cron:j1")
    assert W.is_running(manager, "cron:j1") is True


def test_a_missing_session_is_not_running():
    """Nothing is executing, so a `wake` for it is not redundant."""
    assert W.is_running(_manager(), "cron:nope") is False


# ── 🔴 hazard 2: a missing session is reported, never assumed ──


def test_a_wake_with_no_session_reports_NO_SESSION():
    """🔴 `enqueue` returns False when the key has no session — normal for a trigger whose
    session was
    never opened. Reporting it lets the caller create the session or spool; assuming success
    would lose
    the fire silently."""
    delivery = W.deliver(_manager(), W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW))
    assert delivery.disposition == W.Disposition.NO_SESSION.value
    assert delivery.delivered is False


def test_a_resume_with_no_session_is_REQUEUED_not_lost():
    """§3.2: "must re-queue until the parked lock releases"."""
    delivery = W.deliver(
        _manager(), W.resume_for(trigger_id="j1", session_key="cron:gone", answer={}, now=NOW)
    )
    assert delivery.disposition == W.Disposition.REQUEUED.value
    assert delivery.needs_retry is True


def test_no_session_manager_at_all_is_survivable():
    """The manager is absent in CLI paths; a dispatcher that raised would take them with it."""
    delivery = W.deliver(None, W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW))
    assert delivery.disposition == W.Disposition.NO_SESSION.value


def test_a_raising_enqueue_never_propagates():
    """A broken queue must not lose the REASON it failed — the delivery still comes back typed."""

    class _Exploding:
        _sessions: dict = {}

        def enqueue(self, *a, **kw):
            raise RuntimeError("queue exploded")

    delivery = W.deliver(_Exploding(), W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW))
    assert delivery.delivered is False
    assert delivery.disposition in {
        W.Disposition.NO_SESSION.value,
        W.Disposition.REQUEUED.value,
    }


# ── the message id ──


def test_the_message_id_is_stable_across_a_redelivery():
    """The queue's `cancelled` set keys on the message id, so a fresh id per attempt would make a
    cancelled fire un-cancellable on retry."""
    wakeup = W.wakeup_for(_Fire(_trigger()), seq=3, now=NOW)
    first = W._msg_ts(wakeup)
    second = W._msg_ts(wakeup)
    assert first == second
    assert "schedule:j1" in first


def test_different_fires_get_different_ids():
    a = W._msg_ts(W.wakeup_for(_Fire(_trigger()), seq=1, now=NOW))
    b = W._msg_ts(W.wakeup_for(_Fire(_trigger()), seq=2, now=NOW))
    assert a != b


# ── batching: the tick → dispatch seam ──


def test_dispatch_fires_preserves_tick_order():
    """A five-trigger coalesced wake must drain in order — without sequence numbers a user
    watching two
    dependent automations would see them run backwards."""
    manager = _manager("cron:a", "cron:b", "cron:c")
    fires = [_Fire(_trigger(f"schedule:{k}")) for k in ("a", "b", "c")]
    deliveries = W.dispatch_fires(manager, fires, now=NOW)
    assert [d.wakeup.seq for d in deliveries] == [1, 2, 3]
    assert all(d.delivered for d in deliveries)


def test_every_wakeup_yields_exactly_one_delivery():
    """One result per input, always — a caller diffing counts to find what happened would be
    doing the
    dispatcher's job."""
    manager = _manager("cron:a")
    fires = [_Fire(_trigger("schedule:a")), _Fire(_trigger("schedule:missing"))]
    deliveries = W.dispatch_fires(manager, fires, now=NOW)
    assert len(deliveries) == 2
    assert {d.disposition for d in deliveries} == {
        W.Disposition.QUEUED.value,
        W.Disposition.NO_SESSION.value,
    }


def test_dispatching_nothing_is_an_empty_list():
    assert W.dispatch_fires(_manager(), [], now=NOW) == []


def test_the_retry_queue_holds_only_the_resumes_that_must_come_back():
    manager = _manager("cron:a")
    deliveries = [
        W.deliver(manager, W.wakeup_for(_Fire(_trigger("schedule:a")), seq=1, now=NOW)),
        W.deliver(
            manager, W.resume_for(trigger_id="b", session_key="cron:gone", answer={}, now=NOW)
        ),
    ]
    retry = W.retry_queue(deliveries)
    assert [w.kind for w in retry] == ["resume"]


def test_the_summary_names_every_disposition_even_at_zero():
    """A surface renders a fixed set of chips instead of discovering dispositions at runtime."""
    manager = _manager("cron:a")
    deliveries = W.dispatch_fires(manager, [_Fire(_trigger("schedule:a"))], now=NOW)
    report = W.summary(deliveries)
    assert set(report["by_disposition"]) == {d.value for d in W.Disposition}
    assert report["delivered"] == 1
    assert report["total"] == 1


# ── end to end: S88's tick into this dispatcher ──


def test_a_real_tick_dispatches_through_to_the_session_queues(tmp_path):
    """The whole seam, driven: store → tick → dispatch → session inbox. §3.2's crash-safety is
    that the
    payload lives in the inbox, which is only checkable if the payload actually arrives there."""
    from personalclaw.triggers import service as SVC
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    store.save_all(
        [
            Trigger(
                id=f"t{i}",
                name=f"T{i}",
                kind="clock",
                enabled=True,
                spec={"kind": "interval", "interval_secs": 3600},
                workflow={"provider": "run-prompt", "config": {}},
                next_fire_at=SVC.to_iso(NOW - 5),
            )
            for i in range(3)
        ]
    )
    result = asyncio.run(SVC.tick(store, now=NOW))
    assert len(result.fires) == 3

    manager = _manager(*[W.session_key_for(f.trigger.id) for f in result.fires])
    deliveries = W.dispatch_fires(manager, result.fires, now=NOW)
    assert W.summary(deliveries)["delivered"] == 3
    for key in manager._sessions:
        assert len(manager._sessions[key].queue) == 1


def test_a_dispatch_result_serializes_for_the_ledger():
    manager = _manager("cron:a")
    delivery = W.deliver(manager, W.wakeup_for(_Fire(_trigger("schedule:a")), seq=1, now=NOW))
    payload = delivery.to_dict()
    assert payload["disposition"] == "queued"
    assert payload["session_key"] == "cron:a"
    assert payload["droppable"] is True
