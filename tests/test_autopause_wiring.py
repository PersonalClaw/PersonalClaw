"""Criterion 3: a failing automation autopauses after 5 TRUE failures (§3.7 — S139).

Criterion 3: *"A failing automation autopauses after 5 **true** failures (typed exits —
auth/transport outages **park** instead) and surfaces in the Runs inbox."*

🔴 THE DEFECT — three layers, each dead:

1. **`triggers/autopause.py` was imported by NO production module.** Thirteen functions implementing
   the whole decision — typed exits, the 5-failure budget, parking for transport outages, immediate
   pause for config errors, the attention card — reachable only from tests.
2. **The fire path DISCARDED the provider's result.** `await provider.execute(...)` threw its return
   value away, so nothing knew whether a fire succeeded.
3. **No ledger row was written per fire.** `_record_run` died with `ScheduleService` (S112) and
nothing
   replaced it on this path, so even a wired counter would have counted zero forever.

Driven before writing: six consecutive failing provider runs left the trigger `enabled: True`,
`health_status: 'ok'`, `last_failure_at: ''`. The decision engine was complete and unreachable.

**Two bugs found by driving the fix, both worth recording.** Parking worked before the budget did —
because parking is STATELESS (derived from the exception type) while the budget is STATEFUL, so the
missing ledger row broke only the half that needed history. And the first working version paused
after
**four** failures: `evaluate` adds its own unit (`count = consecutive_failures + 1`), so the count
passed in must be the streak BEFORE this fire, not including the row just written.
"""

from __future__ import annotations

import asyncio
import types

import personalclaw.action_providers as AP
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import autopause
from personalclaw.triggers.models import Trigger, TriggerState
from personalclaw.triggers.store import TriggerStore


class _Provider:
    """A provider whose outcome the test drives per fire."""

    mode = "fail"

    async def execute(self, config, ctx, timeout=30):
        if self.mode == "raise":
            raise RuntimeError("boom")
        if self.mode == "transport":
            raise ConnectionError("network down")
        return types.SimpleNamespace(success=self.mode == "ok", error="nope")


def _drive(tmp_path, monkeypatch, sequence: list[str], tid: str = "clock:f") -> Trigger:
    """Fire `sequence` through the REAL dispatch and return the trigger's final state."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    provider = _Provider()
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: provider
        orch = object.__new__(GatewayOrchestrator)
        for mode in sequence:
            provider.mode = mode
            asyncio.run(orch._fire_store_trigger(store.get(tid).trigger, {"trigger_id": tid}))
    finally:
        AP.get_action_provider = real
    row = store.get(tid)
    assert row is not None
    return row.trigger


# ── the budget ──


def test_FIVE_true_failures_autopause(tmp_path, monkeypatch):
    """🔴 THE DEFECT, pinned. Six failures left the trigger enabled before this."""
    trigger = _drive(tmp_path, monkeypatch, ["fail"] * 5)
    assert trigger.enabled is False
    assert trigger.state == TriggerState.AUTOPAUSED.value


def test_FOUR_failures_do_NOT_pause(tmp_path, monkeypatch):
    """🔴 The off-by-one guard. The first working version paused here, because `evaluate`
    adds its own
    unit and I passed a count that already included the row just written."""
    trigger = _drive(tmp_path, monkeypatch, ["fail"] * 4)
    assert trigger.enabled is True
    assert trigger.state == TriggerState.ACTIVE.value


def test_a_RAISING_provider_also_spends_the_budget(tmp_path, monkeypatch):
    """A provider that throws is as much a failure as one returning `success=False`."""
    assert _drive(tmp_path, monkeypatch, ["raise"] * 5).enabled is False


def test_the_budget_matches_the_DECLARED_constant():
    """A test hardcoding 5 while the module said 3 would pass and mean nothing."""
    assert autopause.FAILURE_BUDGET == 5


# ── consecutive means consecutive ──


def test_a_SUCCESS_RESETS_the_streak(tmp_path, monkeypatch):
    """🔴 §3.7's own words: "four failures then a success then one failure is not five". A
    counter that
    only ever climbed would eventually pause every long-lived automation that had one bad week."""
    trigger = _drive(tmp_path, monkeypatch, ["fail"] * 4 + ["ok"] + ["fail"])
    assert trigger.enabled is True


def test_the_streak_can_rebuild_after_a_reset(tmp_path, monkeypatch):
    """The reset must not become an amnesty: five fresh failures still pause."""
    trigger = _drive(tmp_path, monkeypatch, ["fail"] * 4 + ["ok"] + ["fail"] * 5)
    assert trigger.enabled is False


def test_a_CLEAN_run_records_success(tmp_path, monkeypatch):
    trigger = _drive(tmp_path, monkeypatch, ["ok"] * 3)
    assert trigger.health_status == "ok"
    assert trigger.last_success_at != ""


# ── parking: an outage is not a failure ──


def test_a_TRANSPORT_outage_PARKS_and_stays_ENABLED(tmp_path, monkeypatch):
    """🔴 Criterion 3's parenthetical, and the important half: "auth/transport outages PARK instead".
    Pausing a trigger because the network blipped would make the user re-enable it by hand
    after every
    outage — and parking is reversible on its own."""
    trigger = _drive(tmp_path, monkeypatch, ["transport"] * 6)
    assert trigger.enabled is True, "a parked trigger must not be disabled"
    assert trigger.state == TriggerState.PARKED.value


def test_parking_does_NOT_spend_the_budget(tmp_path, monkeypatch):
    """Six outages then five real failures must still pause exactly on the fifth — if outages spent
    budget, the trigger would have paused during the outage."""
    trigger = _drive(tmp_path, monkeypatch, ["transport"] * 6 + ["fail"] * 4)
    assert trigger.enabled is True


def test_a_transport_exception_classifies_as_PARKING():
    """Asserted against the module's own taxonomy rather than a literal."""
    assert autopause.classify_exception(ConnectionError("x")) in autopause.PARKING_EXITS


# ── the ledger row that makes the counter possible ──


def test_each_fire_writes_a_LEDGER_ROW(tmp_path, monkeypatch):
    """🔴 The third dead layer. The counter derives from the run ledger, and this path wrote no row —
    so the count was permanently 0. This is also why parking worked while the budget did
    not: parking
    is stateless, the budget is not."""
    _drive(tmp_path, monkeypatch, ["fail"] * 3)
    _runs, total = asyncio.run(ScheduleRunStore(tmp_path).list_for_job("clock:f", 0, 20))
    assert total == 3


def test_the_row_carries_the_TYPED_exit(tmp_path, monkeypatch):
    _drive(tmp_path, monkeypatch, ["fail"])
    runs, _total = asyncio.run(ScheduleRunStore(tmp_path).list_for_job("clock:f", 0, 5))
    assert runs[0]["trigger"] in {e.value for e in autopause.ExitType}
    assert runs[0]["status"] == "failure"


def test_a_clean_fire_records_SUCCESS_status(tmp_path, monkeypatch):
    _drive(tmp_path, monkeypatch, ["ok"])
    runs, _total = asyncio.run(ScheduleRunStore(tmp_path).list_for_job("clock:f", 0, 5))
    assert runs[0]["status"] == "success"


# ── the derived counter ──


def test_the_counter_STOPS_at_a_clean_exit():
    """ "Consecutive" is a property of the sequence, so the walk must stop rather than total."""
    rows = [
        {"status": "failure"},
        {"status": "failure"},
        {"status": "success"},
        {"status": "failure"},
    ]
    assert autopause.consecutive_failures_from(rows) == 2


def test_the_counter_SKIPS_a_suppressed_fire():
    """🔴 A quiet-hours skip is neither a recovery (it would forgive a real streak) nor a failure (it
    would pause a healthy trigger for being configured)."""
    rows = [{"status": "failure"}, {"trigger": "skipped_gate"}, {"status": "failure"}]
    assert autopause.consecutive_failures_from(rows) == 2


def test_the_counter_is_ZERO_when_the_newest_run_succeeded():
    assert autopause.consecutive_failures_from([{"status": "success"}, {"status": "failure"}]) == 0


def test_the_counter_handles_an_EMPTY_ledger():
    assert autopause.consecutive_failures_from([]) == 0


def test_the_counter_is_DERIVED_not_stored():
    """`LEGACY_FIELD_MAP` says so outright: "autopause counter is derived from fire
    records". A copy on
    the trigger row would be a second truth that can disagree with the ledger it summarises."""
    import dataclasses

    assert not [f for f in dataclasses.fields(Trigger) if f.name == "consecutive_failures"]


# ── the wiring, and its safety ──


def test_the_fire_path_RECORDS_the_outcome():
    """A recorder nothing calls is the state this session found."""
    import inspect

    src = inspect.getsource(GatewayOrchestrator._fire_store_trigger)
    assert "_record_fire_outcome" in src
    assert "result = await provider.execute" in src, "the result must be CAPTURED, not discarded"


def test_a_RECORDING_FAILURE_does_not_crash_the_fire(tmp_path, monkeypatch):
    """Bookkeeping must never turn a completed fire into a crashed one: the outcome already
    happened,
    and losing the record beats losing the loop."""
    monkeypatch.setattr(
        "personalclaw.schedule_history.ScheduleRunStore.append",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")),
    )
    trigger = _drive(tmp_path, monkeypatch, ["fail"] * 5)
    assert trigger.enabled is True, "no state change is possible, but nothing raised either"


def test_a_QUARANTINED_trigger_is_never_resumed_by_a_clean_run(tmp_path, monkeypatch):
    """§3.7's first rule: quarantine wins outright — an injection-screened fire must never
    auto-retry,
    so nothing below may put the trigger back in a firing state."""
    decision = autopause.evaluate(
        exit_type=autopause.ExitType.OK.value, consecutive_failures=0, quarantined=True
    )
    assert decision.state == TriggerState.QUARANTINED.value


# ── criterion 3's second clause: "and surfaces in the Runs inbox" (S141) ──


class _State:
    """Records `notify` calls.

    Kwargs match `Delivery.to_notify_kwargs()`. A fake with the wrong shape records
    nothing and looks exactly like the feature still being dead — S140's lesson.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def notify(self, *, kind, title, body, meta=None):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})
        return True


def _fire_with_state(tmp_path, monkeypatch, sequence, tid="clock:f") -> _State:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(
        Trigger(
            id=tid,
            name="nightly index",
            kind="clock",
            enabled=True,
            spec={"kind": "interval", "interval_secs": 60},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    state = _State()
    provider = _Provider()
    real = AP.get_action_provider
    try:
        AP.get_action_provider = lambda name: provider
        orch = object.__new__(GatewayOrchestrator)
        orch.dashboard_state = state
        for mode in sequence:
            provider.mode = mode
            row = store.get(tid)
            asyncio.run(orch._fire_store_trigger(row.trigger, {"trigger_id": tid}))
    finally:
        AP.get_action_provider = real
    return state


def _cards(state: _State) -> list[dict]:
    return [n for n in state.sent if n["meta"].get("event") == "automation.needs_attention"]


def test_an_AUTOPAUSED_trigger_surfaces_a_CARD(tmp_path, monkeypatch):
    """🔴 Criterion 3's second clause.

    `attention_card`, `inbox_fingerprint` and `is_duplicate_card` were all dead, so an
    autopaused automation stopped SILENTLY — and a trigger that stops without saying so
    is indistinguishable from one that finished.
    """
    cards = _cards(_fire_with_state(tmp_path, monkeypatch, ["fail"] * 5))
    assert len(cards) == 1
    assert cards[0]["meta"]["state"] == TriggerState.AUTOPAUSED.value


def test_the_card_offers_ACTIONS(tmp_path, monkeypatch):
    """An alert with no remedy is a notification the user can only dismiss."""
    card = _cards(_fire_with_state(tmp_path, monkeypatch, ["fail"] * 5))[0]
    assert "resume" in card["meta"]["actions"]


def test_the_card_DEEP_LINKS_to_the_trigger(tmp_path, monkeypatch):
    card = _cards(_fire_with_state(tmp_path, monkeypatch, ["fail"] * 5))[0]
    assert card["meta"]["statusUrl"] == "#/triggers?open=clock:f"


def test_the_card_is_NOT_re_alerted_on_every_later_fire(tmp_path, monkeypatch):
    """🔴 Deduped on the card's FINGERPRINT — `(trigger_id, state)`.

    Re-entering the same paused state must not re-alert; without this a paused trigger
    would alert on every tick forever.
    """
    assert len(_cards(_fire_with_state(tmp_path, monkeypatch, ["fail"] * 8))) == 1


def test_a_HEALTHY_trigger_surfaces_NO_card(tmp_path, monkeypatch):
    assert _cards(_fire_with_state(tmp_path, monkeypatch, ["ok"] * 3)) == []


def test_a_PARKED_trigger_surfaces_NO_card(tmp_path, monkeypatch):
    """`attention_card` returns None for a parked trigger, deliberately.

    Parking resolves on its own, so alerting would train the user to ignore the card
    that matters.
    """
    assert _cards(_fire_with_state(tmp_path, monkeypatch, ["transport"] * 6)) == []


def test_a_trigger_UNDER_budget_surfaces_no_card(tmp_path, monkeypatch):
    assert _cards(_fire_with_state(tmp_path, monkeypatch, ["fail"] * 4)) == []


def test_the_card_goes_through_STATE_NOTIFY():
    """R18: no second notification path, so a muted channel stays muted."""
    import inspect

    src = inspect.getsource(GatewayOrchestrator._surface_attention_card)
    assert "state.notify" in src


def test_the_PAUSE_still_happens_without_a_dashboard(tmp_path, monkeypatch):
    """A `--no-dashboard` gateway must still autopause, just without announcing it."""
    trigger = _drive(tmp_path, monkeypatch, ["fail"] * 5)
    assert trigger.enabled is False
