"""A slot the process sleeps through is decided the way a restart decides it.

The product's rule is "review, don't auto-run" (`triggers/missed.py`): with `catch_up` off, a missed
slot waits on the Triggers page for the owner to run or dismiss, and with it on, it fires once,
staggered, recorded as late. That held only across a RESTART. A gateway that stayed alive while the
computer slept (a closed lid, a stopped process) admitted the overdue slot at its first tick after
waking and ran it however late: a one-shot with `catch_up: false` was delivered 8 minutes late the
instant the process was continued.

Every case here drives the real tick over a real store with a FAKE clock: "the scheduler was
paused" is two ticks whose `now` are minutes apart, with no restart between them and no sleep.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from personalclaw.triggers import claims
from personalclaw.triggers import loop as LOOP
from personalclaw.triggers import review as RV
from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.scheduling import (
    BOOT_STAGGER_BASE_SECS,
    BOOT_STAGGER_WINDOW_SECS,
    LATE_THRESHOLD_SECS,
)
from personalclaw.triggers.store import TriggerStore

#: 08:00:00 UTC, so a daily `0 8 * * *` cron's slot is exactly here.
NOW = 1_800_000_000.0
DAY = 86_400.0
#: How long the validator's process stayed stopped past the slot: 8 min 30 s, no restart.
ASLEEP = 510.0


@pytest.fixture(autouse=True)
def _utc_host(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _notify(**over) -> dict:
    return dict(
        kind="clock",
        enabled=True,
        capabilities={"providers": ["notify"]},
        workflow={"inline": {"provider": "notify", "config": {}}},
        **over,
    )


def _one_shot(tid="clock:pack", *, at=NOW, catch_up=False, delete_after_run=False) -> Trigger:
    spec = {"kind": "at", "at": at, "timezone": "UTC"}
    if delete_after_run:
        spec["delete_after_run"] = True
    return Trigger(
        id=tid,
        name="Pack the soccer bag",
        catch_up=catch_up,
        spec=spec,
        next_fire_at=SVC.to_iso(at),
        **_notify(),
    )


def _daily(tid="clock:digest", *, catch_up=False) -> Trigger:
    return Trigger(
        id=tid,
        name="Morning digest",
        catch_up=catch_up,
        spec={"kind": "cron", "expr": "0 8 * * *", "timezone": "UTC"},
        next_fire_at=SVC.to_iso(NOW),
        **_notify(),
    )


def _tick(store, base, now, held=None):
    kwargs = {"now": now, "base_dir": base, "persist": True}
    if held is not None:
        kwargs["catching_up"] = held
    result = asyncio.run(SVC.tick(store, **kwargs))
    for fire in result.fires:
        claims.release_claim(fire.trigger.id, base_dir=base)
    return result


def _ran(result, tid):
    return [r for r in result.ledger_rows if r["trigger_id"] == tid]


# ── catch_up OFF: reviewed, never fired ──


def test_a_one_shot_whose_time_passes_while_paused_is_put_up_for_review(store, tmp_path):
    store.save_all([_one_shot()])
    assert _tick(store, tmp_path, NOW - 20).fires == [], "not due before its time"

    woke = _tick(store, tmp_path, NOW + ASLEEP)

    assert woke.fires == [], "a missed slot must not run late on its own"
    assert _ran(woke, "clock:pack") == [], "and no run of it is recorded"
    cards = RV.pending(base_dir=tmp_path)
    assert [(c.trigger_id, c.kind, c.count, c.latest, c.cause) for c in cards] == [
        ("clock:pack", RV.MISSED, 1, NOW, RV.PAUSED)
    ]
    # Its slot is taken, as a fired one-shot's is: switched off, with no next fire to re-run.
    row = store.get("clock:pack").trigger
    assert (row.enabled, row.next_fire_at) == (False, "")
    assert _tick(store, tmp_path, NOW + ASLEEP + 60).fires == []
    assert len(RV.pending(base_dir=tmp_path)) == 1, "one card, not one per tick"


def test_a_daily_cron_whose_slot_passes_while_paused_is_reviewed_and_resumes(store, tmp_path):
    store.save_all([_daily()])

    woke = _tick(store, tmp_path, NOW + ASLEEP)

    assert woke.fires == []
    cards = RV.pending(base_dir=tmp_path)
    assert [(c.trigger_id, c.count, c.latest, c.cause) for c in cards] == [
        ("clock:digest", 1, NOW, RV.PAUSED)
    ]
    # The schedule resumes on its OWN grid (tomorrow 08:00, inside the per-id spread), not "now".
    nxt = SVC.to_epoch(store.get("clock:digest").trigger.next_fire_at)
    assert NOW + DAY <= nxt < NOW + DAY + BOOT_STAGGER_WINDOW_SECS
    assert _tick(store, tmp_path, NOW + ASLEEP + 60).fires == []


def test_an_hourly_interval_s_card_names_the_slot_it_missed_not_the_one_that_ran(store, tmp_path):
    """The interval walk used to start AT the last fire, so the card's latest slot was the one
    before the miss — a slot that had run."""
    store.save_all(
        [
            Trigger(
                id="clock:hourly",
                name="Hourly sync",
                spec={"kind": "interval", "interval_secs": 3600},
                next_fire_at=SVC.to_iso(NOW),
                **_notify(),
            )
        ]
    )
    assert _tick(store, tmp_path, NOW + ASLEEP).fires == []
    (card,) = RV.pending(base_dir=tmp_path)
    assert (card.count, card.latest, card.oldest) == (1, NOW, NOW)


def test_the_notice_says_it_was_missed_while_paused_not_while_stopped(store, tmp_path):
    """One "Missed scheduled runs" notice, in words true of a sleep: PersonalClaw was running."""
    store.save_all([_one_shot(), _daily()])
    woke = _tick(store, tmp_path, NOW + ASLEEP)
    notice = RV.boot_notice(woke.missed, RV.pending(base_dir=tmp_path))
    assert notice is not None
    assert notice["title"] == "Missed scheduled runs"
    assert notice["meta"]["event"] == "automation.missed_review"
    assert notice["meta"]["missed"] == 2
    assert "while PersonalClaw was paused or the computer was asleep" in notice["body"]
    assert "not running" not in notice["body"]
    assert "Review them on the Triggers page" in notice["body"]


# ── catch_up ON: once, staggered, late ──


def test_a_catch_up_cron_fires_once_staggered_and_is_recorded_late(store, tmp_path):
    store.save_all([_daily(catch_up=True)])
    held: dict = {}

    woke = _tick(store, tmp_path, NOW + ASLEEP, held)

    assert woke.fires == [], "staggered, not fired inline on the wake"
    assert RV.pending(base_dir=tmp_path) == [], "already decided: it gets no card"
    fire_at = SVC.to_epoch(store.get("clock:digest").trigger.next_fire_at)
    assert (
        NOW + ASLEEP < fire_at <= NOW + ASLEEP + BOOT_STAGGER_BASE_SECS + BOOT_STAGGER_WINDOW_SECS
    )
    assert held["clock:digest"][0] == NOW, "the slot it stands in for is kept"

    ran = _tick(store, tmp_path, fire_at, held)

    assert [f.trigger.id for f in ran.fires] == ["clock:digest"]
    (row,) = _ran(ran, "clock:digest")
    assert row["outcome"] == "ran_late"
    assert row["scheduled_for"] == NOW, "late against the slot it replaced, not its stagger"
    assert "after its scheduled slot" in row["reason"]
    assert ran.fires[0].late == row["reason"]
    # ONCE: the next fire is tomorrow's slot, and nothing else fires today.
    assert _tick(store, tmp_path, fire_at + 60, held).fires == []
    assert SVC.to_epoch(store.get("clock:digest").trigger.next_fire_at) >= NOW + DAY
    notice = RV.boot_notice(woke.missed, [])
    assert notice is not None and "catch-up enabled will fire once" in notice["body"]


def test_a_catch_up_one_shot_fires_once_staggered_and_is_recorded_late(store, tmp_path):
    store.save_all([_one_shot(catch_up=True)])
    held: dict = {}

    assert _tick(store, tmp_path, NOW + ASLEEP, held).fires == []
    fire_at = SVC.to_epoch(store.get("clock:pack").trigger.next_fire_at)
    assert fire_at > NOW + ASLEEP

    ran = _tick(store, tmp_path, fire_at, held)

    assert [f.trigger.id for f in ran.fires] == ["clock:pack"]
    (row,) = _ran(ran, "clock:pack")
    assert (row["outcome"], row["scheduled_for"]) == ("ran_late", NOW)
    assert _tick(store, tmp_path, fire_at + 60, held).fires == []


# ── within the threshold: scheduling, not a miss ──


def test_a_slot_seconds_overdue_runs_and_one_at_the_threshold_is_missed(store, tmp_path):
    """Tick jitter is not a miss. The line is `LATE_THRESHOLD_SECS`, against the wall clock."""
    store.save_all([_one_shot("clock:early"), _daily("clock:early-cron")])
    on_time = _tick(store, tmp_path, NOW + 20)
    assert sorted(f.trigger.id for f in on_time.fires) == ["clock:early", "clock:early-cron"]
    assert {r["outcome"] for r in on_time.ledger_rows} == {"ran"}
    assert on_time.missed == {}

    store.save_all([_one_shot("clock:edge"), _daily("clock:edge-cron")])
    just_under = _tick(store, tmp_path, NOW + LATE_THRESHOLD_SECS - 1)
    assert sorted(f.trigger.id for f in just_under.fires) == ["clock:edge", "clock:edge-cron"]
    assert RV.pending(base_dir=tmp_path) == []

    store.save_all([_one_shot("clock:late"), _daily("clock:late-cron")])
    at_threshold = _tick(store, tmp_path, NOW + LATE_THRESHOLD_SECS)
    assert at_threshold.fires == []
    assert sorted(c.trigger_id for c in RV.pending(base_dir=tmp_path)) == [
        "clock:late",
        "clock:late-cron",
    ]


# ── the restart path answers the same way ──


def test_a_one_shot_missed_across_a_restart_is_reviewed_not_run_after_boot(store, tmp_path):
    """The same rule at boot: a one-shot used to be pushed into the boot stagger and run late."""
    store.save_all([_one_shot()])
    booted = NOW + 3600

    report = SVC.boot(store, now=booted)

    cards = RV.cards_from_boot(report)
    assert [(c.trigger_id, c.count, c.latest, c.cause) for c in cards] == [
        ("clock:pack", 1, NOW, RV.STOPPED)
    ]
    row = store.get("clock:pack").trigger
    assert (row.enabled, row.next_fire_at) == (False, "")
    for later in (booted + BOOT_STAGGER_BASE_SECS, booted + 300):
        assert _tick(store, tmp_path, later).fires == []
    notice = RV.boot_notice(report, cards)
    assert notice is not None and "while PersonalClaw was not running" in notice["body"]


def test_a_restart_a_few_seconds_across_a_slot_runs_it_rather_than_reviewing_it(store, tmp_path):
    store.save_all([_daily()])
    booted = NOW + 30

    report = SVC.boot(store, now=booted)

    assert RV.cards_from_boot(report) == []
    nxt = SVC.to_epoch(store.get("clock:digest").trigger.next_fire_at)
    assert booted < nxt <= booted + BOOT_STAGGER_BASE_SECS + BOOT_STAGGER_WINDOW_SECS
    assert [f.trigger.id for f in _tick(store, tmp_path, nxt).fires] == ["clock:digest"]


def test_a_reviewed_one_shot_that_retires_after_its_run_goes_once_run_from_the_review(
    store, tmp_path
):
    """The Triggers page's one-shot leaves the list after its run. Reviewed instead of run, its
    slot is taken with no grant, so the review's Run now is the run that retires it."""
    store.save_all([_one_shot(delete_after_run=True)])
    _tick(store, tmp_path, NOW + ASLEEP)
    row = store.get("clock:pack").trigger
    assert SVC.retire_after_run(store, row, status="success") is False, "a Run button: it stays"
    assert SVC.retire_after_run(store, row, status="success", from_review=True) is True
    assert store.get("clock:pack") is None


# ── the wiring that reaches the owner ──


def test_the_clock_loop_hands_what_a_wake_missed_to_its_announcer(store, tmp_path):
    store.save_all([_one_shot()])
    seen: list[dict] = []

    async def _never(_payload):  # pragma: no cover - nothing may run
        raise AssertionError("a missed slot ran")

    asyncio.run(
        LOOP.tick_once(
            store,
            runner=_never,
            base_dir=tmp_path,
            now=NOW + ASLEEP,
            catching_up={},
            on_missed=seen.append,
        )
    )
    assert len(seen) == 1
    assert seen[0]["cause"] == RV.PAUSED
    assert [r["trigger_id"] for r in seen[0]["review"]["rows"]] == ["clock:pack"]


class _State:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.pushed: list[tuple[str, ...]] = []

    def notify(self, *, kind, title, body, meta=None):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})
        return True

    def push_refresh(self, *keys: str) -> None:
        self.pushed.append(keys)


def test_the_gateway_announces_a_wake_in_the_one_missed_runs_notice(store, tmp_path):
    from personalclaw import notification_kinds
    from personalclaw.gateway import GatewayOrchestrator

    store.save_all([_daily()])
    woke = _tick(store, tmp_path, NOW + ASLEEP)
    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    state = _State()
    gateway.dashboard_state = state

    gateway._surface_wake_review(woke.missed)

    assert len(state.sent) == 1
    sent = state.sent[0]
    assert sent["kind"] == notification_kinds.RUN_REVIEW
    assert sent["title"] == "Missed scheduled runs"
    assert "paused or the computer was asleep" in sent["body"]
    assert ("crons",) in state.pushed, "an open Triggers page shows the card without a reload"
    # And the clock loop is handed both: the announcer, and the catch-ups the boot staggered.
    source = inspect.getsource(GatewayOrchestrator._clock_loop)
    assert "on_missed=self._surface_wake_review" in source
    assert 'catching_up=getattr(self, "_boot_catch_ups", None)' in source
