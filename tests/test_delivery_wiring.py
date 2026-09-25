"""Criterion 10: a completed fire notifies with a deep link, and a retry does not double-ping
(S140).

Criterion 10: *"A completed-run notification deep-links (`statusUrl`) to the exact run journal
row; a
retried delivery does not double-ping."*

🔴 THE DEFECT — two dead layers, the same shape as S139's autopause chain. `triggers/delivery.py`
implements the criterion in full: `statusUrl` deep links, stable event ids for retry dedup,
`is_duplicate`, destination formatting. But `build_delivery`'s only caller was
`executor.delivery_for`, **which itself had no caller at all**. Driven before writing: a
completed fire
produced no notification and no `statusUrl` anywhere under the home.

**Routes through `state.notify`**, which is `deliver`'s own contract. R18: *"the substrate does not
build a second notification path"* — so the existing `notification_allowed` gate and the
per-(source,
kind) rule still apply, and a muted channel stays muted.
"""

from __future__ import annotations

import asyncio
import types

import personalclaw.action_providers as AP
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.triggers.delivery import build_delivery, deliver, is_duplicate
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

#: The action the fixtures below fire. NOT `notify`: a notify action's success already IS the
#: user's notification, so the substrate deliberately sends no second "finished" report for it (the
#: one-notification-per-fire tests at the bottom pin that). An ordinary action is what exercises the
#: completion report these tests are about. `create-task` because it is autonomous on the rung
#: ladder, so the fire is dispatched rather than held for approval.
_ACTION = "create-task"


class _State:
    """A dashboard state that records what `notify` was called with.

    `DashboardState.notify`'s own signature — `(kind, title, body, *, meta=None)` — so both callers
    are recorded: `delivery.deliver` passes keywords (`Delivery.to_notify_kwargs()`) and the notify
    action passes the first three positionally. My first probe used a positional `(source, payload)`
    signature and recorded zero notifications, which looked exactly like the feature still being
    dead. Worth the note: a fake with the wrong shape reproduces the very bug you are trying to
    confirm you fixed.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def notify(self, kind, title, body, *, meta=None):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})
        return True


class _Ok:
    async def execute(self, config, ctx, timeout=30):
        return types.SimpleNamespace(success=True)


class _Boom:
    async def execute(self, config, ctx, timeout=30):
        raise RuntimeError("boom")


def _fire(tmp_path, monkeypatch, provider, tid="clock:n") -> _State:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=tid,
            name="nightly index",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            delivery="inbox",
            capabilities={"providers": [_ACTION]},
            workflow={"inline": {"provider": _ACTION, "config": {}}},
        )
    )
    state = _State()
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: provider
        orch = object.__new__(GatewayOrchestrator)
        orch.dashboard_state = state
        asyncio.run(orch._fire_store_trigger(store.get(tid).trigger, {"trigger_id": tid}))
    finally:
        AP.get_action_provider = real
    return state


# ── the defect ──


def test_a_COMPLETED_fire_notifies(tmp_path, monkeypatch):
    """🔴 THE DEFECT, pinned. A completed fire notified nothing before this."""
    assert len(_fire(tmp_path, monkeypatch, _Ok()).sent) == 1


def test_the_notification_carries_a_STATUS_URL(tmp_path, monkeypatch):
    """Criterion 10's deep link — the whole point. A notification the user cannot click through to
    tells them something happened and not where."""
    note = _fire(tmp_path, monkeypatch, _Ok()).sent[0]
    assert note["meta"]["statusUrl"] == "#/triggers?open=clock:n"


def test_a_FAILED_fire_also_notifies(tmp_path, monkeypatch):
    """The half that matters most: a silent failure is the one a user needs told about."""
    note = _fire(tmp_path, monkeypatch, _Boom()).sent[0]
    assert note["meta"]["event"] == "automation.run.failed"
    assert "failed" in note["title"]


def test_the_two_outcomes_carry_DISTINCT_events(tmp_path, monkeypatch):
    """A surface switches on the typed event; one label for both would make success unfilterable."""
    ok = _fire(tmp_path, monkeypatch, _Ok()).sent[0]
    bad = _fire(tmp_path, monkeypatch, _Boom()).sent[0]
    assert ok["meta"]["event"] != bad["meta"]["event"]


def test_the_notification_NAMES_the_trigger(tmp_path, monkeypatch):
    """ "An automation finished" across twenty automations is not actionable."""
    assert "nightly index" in _fire(tmp_path, monkeypatch, _Ok()).sent[0]["title"]


# ── the no-double-ping half ──


def test_a_RETRY_of_the_same_run_does_NOT_double_ping():
    """🔴 Criterion 10's second clause. `event_id` is stable across retries by construction, so a
    redelivery is suppressed on identity rather than on a timestamp guess."""
    first = build_delivery(trigger_id="clock:n", trigger_name="n", ok=True, run_id="run-1")
    retry = build_delivery(trigger_id="clock:n", trigger_name="n", ok=True, run_id="run-1")
    seen: set[str] = set()
    state = _State()
    assert deliver(state, first, delivered_ids=seen) is True
    assert deliver(state, retry, delivered_ids=seen) is False
    assert len(state.sent) == 1


def test_a_DIFFERENT_run_still_pings():
    """Dedup must not silence the next legitimate run — that would be worse than double-pinging."""
    seen: set[str] = set()
    state = _State()
    deliver(state, build_delivery(trigger_id="t", ok=True, run_id="run-1"), delivered_ids=seen)
    deliver(state, build_delivery(trigger_id="t", ok=True, run_id="run-2"), delivered_ids=seen)
    assert len(state.sent) == 2


def test_the_event_id_is_STABLE_for_one_run():
    a = build_delivery(trigger_id="t", ok=True, run_id="r1")
    b = build_delivery(trigger_id="t", ok=True, run_id="r1")
    assert a.event_id == b.event_id


def test_is_duplicate_tolerates_NO_seen_set():
    """The caller owns the retry window; a caller that keeps none must still be able to deliver."""
    assert is_duplicate(build_delivery(trigger_id="t", ok=True), None) is False


# ── it must not build a second notification path ──


def test_it_routes_through_STATE_NOTIFY():
    """R18: "the substrate does not build a second notification path". Going around `notify` would
    bypass `notification_allowed` and the per-(source, kind) rule — a muted channel would start
    talking."""
    import inspect

    assert "state.notify" in inspect.getsource(deliver)


def test_NO_dashboard_state_is_survived(tmp_path, monkeypatch):
    """A `--no-dashboard` gateway has no state to notify through, and a fire must still succeed."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="clock:n",
            name="n",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            capabilities={"providers": [_ACTION]},
            workflow={"inline": {"provider": _ACTION, "config": {}}},
        )
    )
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: _Ok()
        orch = object.__new__(GatewayOrchestrator)  # no dashboard_state at all
        asyncio.run(
            orch._fire_store_trigger(store.get("clock:n").trigger, {"trigger_id": "clock:n"})
        )
    finally:
        AP.get_action_provider = real  # nothing raised


def test_a_NOTIFY_FAILURE_does_not_fail_the_fire(tmp_path, monkeypatch):
    """The run already completed; a failed ping must not undo it."""

    class Angry(_State):
        def notify(self, **kwargs):
            raise OSError("notification bus down")

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id="clock:n",
            name="n",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            capabilities={"providers": [_ACTION]},
            workflow={"inline": {"provider": _ACTION, "config": {}}},
        )
    )
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: _Ok()
        orch = object.__new__(GatewayOrchestrator)
        orch.dashboard_state = Angry()
        asyncio.run(
            orch._fire_store_trigger(store.get("clock:n").trigger, {"trigger_id": "clock:n"})
        )
    finally:
        AP.get_action_provider = real  # nothing raised


# ── the wiring ──


def test_the_FIRE_PATH_delivers():
    """A delivery contract nothing calls is the state this session found — twice over, since
    `executor.delivery_for` was itself uncalled."""
    import inspect

    src = inspect.getsource(GatewayOrchestrator._fire_store_trigger)
    assert "_deliver_fire_outcome" in src


# ── 🔴 every fire shared one event id, so a healthy automation notified ONCE (S161) ──


def _gw():
    from personalclaw.gateway import GatewayOrchestrator

    gw = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gw.dashboard_state = _State()
    return gw


def _trigger(tmp_path, *, policy=None, tid="clock:daily"):
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    t = Trigger(
        id=tid,
        name="daily digest",
        kind="clock",
        enabled=True,
        spec={"kind": "interval", "interval_secs": 60},
        capabilities={"providers": [_ACTION]},
        workflow={"inline": {"provider": _ACTION, "config": {}}},
    )
    t.delivery = "inbox"
    t.failure_delivery = "inbox"
    if policy is not None:
        t.failure_policy = policy
    store.upsert(t)
    return store, store.get(tid).trigger


def test_a_HEALTHY_automation_notifies_on_EVERY_fire(tmp_path, monkeypatch):
    """🔴 THE DEFECT, and it is severe. `_deliver_fire_outcome` passed neither `run_id` nor
    `attempt_key`, and `event_id` is derived from exactly those three parts — so every fire of a
    trigger produced the SAME id and `is_duplicate` dropped everything after the first.

    Measured: a healthy daily digest with `delivery: "inbox"` notified the user **once,
    ever**; fires 2-5 were silently discarded as "already sent". Criterion 10's dedup is for
    the same event REDELIVERED (a transport retry); applied to distinct fires it became a mute.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    _store, trigger = _trigger(tmp_path)
    gw = _gw()
    for _ in range(5):
        gw._deliver_fire_outcome(trigger, ok=True)
    assert len(gw.dashboard_state.sent) == 5, "each fire is a distinct event"


def test_the_attempt_key_is_a_COUNTER_not_a_TIMESTAMP(tmp_path, monkeypatch):
    """🔴 A bug in my own first fix. `int(time.time() * 1000)` collides for fires in the same tick —
    measured, 5 rapid reads returned ONE distinct value, so 5 fires still produced only 2
    notifications. A counter is monotonic whatever the clock's resolution."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    gw = _gw()
    keys = [gw._next_delivery_attempt() for _ in range(5)]
    assert len(set(keys)) == 5, f"attempt keys must be distinct, got {keys}"


def test_a_REPEATED_identical_failure_is_SUPPRESSED(tmp_path, monkeypatch):
    """🔴 `failure_policy.dedupe_hash` was written by the migration and read by nothing — the unified
    path kept the legacy `_FAILURE_REMINDER_SECS` constant and `_result_hash` helper and dropped the
    check that used them. `event_id` cannot cover this: it dedupes the same event redelivered, not
    different fires carrying an identical error."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    _store, trigger = _trigger(tmp_path, policy={"dedupe_hash": True})
    gw = _gw()
    for _ in range(6):
        gw._deliver_fire_outcome(trigger, ok=False, error="ConnectionError: host unreachable")
    assert len(gw.dashboard_state.sent) == 1


def test_dedup_is_OPT_IN(tmp_path, monkeypatch):
    """Gated on the declared key. Coalescing alerts for a user who did not ask would be the opposite
    failure — a broken automation going quieter than they expect."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    for policy in ({}, {"dedupe_hash": False}):
        _store, trigger = _trigger(tmp_path, policy=policy, tid=f"clock:{policy!r}")
        gw = _gw()
        for _ in range(6):
            gw._deliver_fire_outcome(trigger, ok=False, error="ConnectionError: host unreachable")
        assert len(gw.dashboard_state.sent) == 6, policy


def test_a_DIFFERENT_error_always_alerts(tmp_path, monkeypatch):
    """Dedup is per-error, not per-trigger: a second, different fault is news."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    _store, trigger = _trigger(tmp_path, policy={"dedupe_hash": True})
    gw = _gw()
    for i in range(6):
        gw._deliver_fire_outcome(trigger, ok=False, error=f"ConnectionError: host-{i} down")
    assert len(gw.dashboard_state.sent) == 6


def test_a_NEW_error_RESETS_the_window(tmp_path, monkeypatch):
    """A,A,B,B,A → 3 alerts. The hash is persisted on every non-suppressed alert, so a new
    error starts its own window instead of inheriting the previous one's remaining time — and
    a RETURN to the first error is news again, because the last alert the user saw was B."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    _store, trigger = _trigger(tmp_path, policy={"dedupe_hash": True})
    gw = _gw()
    for err in ("A: one", "A: one", "B: two", "B: two", "A: one"):
        gw._deliver_fire_outcome(trigger, ok=False, error=err)
    assert len(gw.dashboard_state.sent) == 3


def test_the_reminder_window_lets_a_still_broken_automation_RE_ALERT():
    """Suppression is capped, never unbounded: "it stopped telling me" and "it got fixed" must not
    look the same. Driven on the pure decision so the clock is an argument."""
    from personalclaw.triggers.delivery import FAILURE_REMINDER_SECS, suppress_repeat_failure

    _first, digest = suppress_repeat_failure(error="X: boom", last_hash="", last_at=0.0, now=1000.0)
    inside, _ = suppress_repeat_failure(
        error="X: boom", last_hash=digest, last_at=1000.0, now=1000.0 + FAILURE_REMINDER_SECS - 1
    )
    outside, _ = suppress_repeat_failure(
        error="X: boom", last_hash=digest, last_at=1000.0, now=1000.0 + FAILURE_REMINDER_SECS + 1
    )
    assert inside is True and outside is False


def test_dedup_does_NOT_touch_the_autopause_counter():
    """The legacy control advanced `consecutive_failures` while suppressing the notification,
    and that separation is the point: dedup is about how loudly the user is told, never about
    whether the failure counted. Coupling them lets a repeating error escape autopause."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    source = inspect.getsource(GatewayOrchestrator._dedupe_repeat_failure)
    assert "consecutive_failures" in source, "the reasoning must be recorded"
    assert "consecutive_failures =" not in source and "consecutive_failures=" not in source


def test_the_hash_normalises_VOLATILE_data():
    """The same outage must not produce a fresh hash every minute just because its message carries
    the clock — otherwise dedup never fires on the errors most likely to repeat."""
    from personalclaw.triggers.delivery import failure_hash

    a = "ConnectionError: host down at 2026-08-04T19:00:00Z"
    b = "ConnectionError: host down at 2026-08-04T20:00:00Z"
    assert failure_hash(a) == failure_hash(b)
    assert failure_hash(a) != failure_hash("ConnectionError: OTHER host down")


def test_an_EMPTY_error_never_suppresses():
    """The first alert of anything always goes out; a missing error text is not evidence of a
    repeat. Fail-LOUD, the safe direction for a notification."""
    from personalclaw.triggers.delivery import suppress_repeat_failure

    suppress, digest = suppress_repeat_failure(error="", last_hash="abc", last_at=1.0, now=2.0)
    assert suppress is False and digest == ""


# ── a CLOCK trigger's outcome reaches the `cron/*` rules rows (issue #415) ──


def test_a_clock_triggers_outcome_is_delivered_as_a_SCHEDULED_JOB(tmp_path, monkeypatch):
    """The call-site half of #415 — without it the `scheduled` flag is a dead parameter.

    The ScheduleService removal deleted every emitter of the `cron` kind but left its two rules
    rows, so "Scheduled job failed → Notify" was a configurable control nothing could trigger and
    the failure landed on `system/error` instead. Driven through the real
    `_deliver_fire_outcome` with a real `kind="clock"` trigger, and asserted on the kind that
    reaches `notify` — which is what the rules engine resolves a rule from.
    """
    from personalclaw import notification_kinds as nk

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    _store, trigger = _trigger(tmp_path)
    gw = _gw()

    gw._deliver_fire_outcome(trigger, ok=True)
    gw._deliver_fire_outcome(trigger, ok=False, error="boom")

    kinds = [n["kind"] for n in gw.dashboard_state.sent]
    assert kinds == [nk.CRON, nk.CRON_FAILED], kinds
    assert [nk.kind_for_legacy(k).key for k in kinds] == ["cron/result", "cron/failed"]


def test_a_NON_clock_triggers_outcome_is_NOT_a_scheduled_job(tmp_path, monkeypatch):
    """🪤 THE VACUITY LEG. Same substrate, same handler, a webhook instead of a clock.

    `_deliver_fire_outcome` carries EVERY trigger kind, so a blanket switch to the cron kinds would
    label a webhook's outcome "Scheduled job result" in the feed and route it through the
    scheduled-job rule — a second wrong answer in place of the first.
    """
    from personalclaw import notification_kinds as nk

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    _store, trigger = _trigger(tmp_path, tid="webhook:hook1")
    trigger.kind = "webhook"
    gw = _gw()

    gw._deliver_fire_outcome(trigger, ok=True)
    gw._deliver_fire_outcome(trigger, ok=False, error="boom")

    assert [n["kind"] for n in gw.dashboard_state.sent] == [nk.INFO, nk.ERROR]


# ── ONE notification per fire: a notify action's note IS the report (B8, 2026-09-25) ──


def _notify_trigger(tmp_path, *, config, tid="clock:standup-nudge"):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=tid,
            name="Standup nudge",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            delivery="inbox",
            workflow={"inline": {"provider": "notify", "config": config}},
        )
    )
    return store.get(tid).trigger


def _fire_notify(tmp_path, monkeypatch, *, config) -> _State:
    """Fire a notify trigger through the REAL notify provider — nothing about the action faked.

    The provider reaches `state.notify` through the action-services accessor and the completion
    report reaches it through `dashboard_state`; both are the one recorder, so `sent` is every
    notification the fire produced, from either path."""
    import personalclaw.action_providers.notify_provider as notify_mod

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    state = _State()
    monkeypatch.setattr(
        notify_mod, "get_action_services", lambda: types.SimpleNamespace(state=state)
    )
    trigger = _notify_trigger(tmp_path, config=config)
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    asyncio.run(orch._fire_store_trigger(trigger, {"trigger_id": trigger.id}))
    return state


def test_a_NOTIFY_fire_produces_exactly_ONE_notification(tmp_path, monkeypatch):
    """🔴 THE DEFECT. Measured live: 5 fires of a per-minute notify trigger made 10 notifications —
    each fire's own note ("Standup nudge: review Q4 tasks") followed by the substrate's empty
    "Standup nudge finished". Same event, twice, the second saying nothing new."""
    sent = _fire_notify(
        tmp_path,
        monkeypatch,
        config={"title_template": "Standup nudge: review Q4 tasks", "body_template": "Fired."},
    ).sent
    assert [n["title"] for n in sent] == ["Standup nudge: review Q4 tasks"], sent


def test_the_ONE_notification_links_back_to_its_trigger(tmp_path, monkeypatch):
    """The deep link the suppressed report used to carry has to survive on the note that remains,
    or fixing the double ping would cost the only route from a notification back to its trigger."""
    (note,) = _fire_notify(
        tmp_path, monkeypatch, config={"title_template": "Standup nudge: review Q4 tasks"}
    ).sent
    assert note["meta"]["statusUrl"] == "#/triggers?open=clock:standup-nudge"


def test_a_FAILED_notify_action_still_reports(tmp_path, monkeypatch):
    """The vacuity leg. A notify action with no title fails without notifying anything, so the
    failure report is the ONLY word the user gets — it must not be swallowed with the success
    report. Asserted on the event, not a count: a count of 1 would also pass on a success leak."""
    sent = _fire_notify(tmp_path, monkeypatch, config={}).sent
    assert [n["meta"].get("event") for n in sent] == ["automation.run.failed"], sent
    assert sent[0]["meta"]["statusUrl"] == "#/triggers?open=clock:standup-nudge"


def test_only_a_SELF_NOTIFYING_action_skips_the_success_report(tmp_path, monkeypatch):
    """The predicate itself, both shapes of stored action, plus the ordinary action every other test
    in this file fires — which must keep its report, or "one per fire" would have become zero."""
    from personalclaw.triggers.delivery import notifies_on_its_own

    inline = Trigger(id="a", name="a", kind="clock", workflow={"inline": {"provider": "notify"}})
    flat = Trigger(id="b", name="b", kind="clock", workflow={"provider": "notify", "config": {}})
    other = Trigger(id="c", name="c", kind="clock", workflow={"inline": {"provider": _ACTION}})
    assert notifies_on_its_own(inline) and notifies_on_its_own(flat)
    assert not notifies_on_its_own(other)
    assert len(_fire(tmp_path, monkeypatch, _Ok()).sent) == 1
