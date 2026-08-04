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


class _State:
    """A dashboard state that records what `notify` was called with.

    The kwargs are `kind`/`title`/`body`/`meta` — matching `Delivery.to_notify_kwargs()`. My first
    probe used a positional `(source, payload)` signature and recorded zero notifications, which
    looked exactly like the feature still being dead. Worth the note: a fake with the wrong shape
    reproduces the very bug you are trying to confirm you fixed.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def notify(self, *, kind, title, body, meta=None):
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
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
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
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
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
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
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
