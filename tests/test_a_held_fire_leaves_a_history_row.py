"""A fire its automation's gates held or refused leaves a row in its history, and a held one-shot
waits on the review instead of going off with no word.

🔴 The defect (Backlog: "A fire held because its resource is busy leaves no history row"). Three
gates held a fire with the words "deferred until it frees": a resource slot it needs was busy
(`resource_slots`), its target looked busy (`skip_if_active`), or it yields to you while you are
active (`yield_to_user`). Their outcome was `deferred`, which this codebase reads as work that
still starts. But nothing retried one: a clock fire's next time was moved on before it was
admitted, an event fire was dropped, a webhook's caller was told to try again. And
`persist_suppression()` wrote only the six `skipped_*` outcomes, so:
- a held fire left no row in its automation's history;
- a fire refused by the incident or the capability gate (`refused`) left none either;
- a one-shot held at its time (a reminder) was switched off with no run, no row and no card.

Now a held fire is said as what it is, skipped this time (`skipped_gate`, the outcome the other
"should this fire at all" gates give, with a reason that says which gate held it and why). Every
outcome a gate can give is recorded, `refused` among them. And a one-shot held at its time goes to
the review on the Triggers page (a missed card whose cause is `held`), to be run now or dismissed.

Each test drives the real admission (the clock tick, the event router) over a real store, and reads
the automation's history back from the real run store.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from personalclaw.schedule_history import ScheduleRunStore
from personalclaw.triggers import claims, firepath
from personalclaw.triggers import service as svc
from personalclaw.triggers.models import TRUE_FAILURE_OUTCOMES, Outcome, Trigger
from personalclaw.triggers.scheduling import Claim
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture
def store(tmp_path: Path) -> TriggerStore:
    return TriggerStore(base_dir=tmp_path)


def _add(store: TriggerStore, tid: str, *, spec: dict | None = None, **fields: Any) -> Trigger:
    fields.setdefault("capabilities", {"providers": ["notify"]})
    fields.setdefault("workflow", {"inline": {"provider": "notify", "config": {}}})
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="clock",
            enabled=True,
            overlap="parallel",
            spec=spec or {"kind": "interval", "interval_secs": 3600},
            next_fire_at=svc.to_iso(NOW),
            **fields,
        )
    )
    found = store.get(tid)
    assert found is not None
    return found.trigger


def _hold_slot(tmp_path: Path, holder: str = "clock:index") -> None:
    """*holder* is running, and holds the `local-llm` slot it declares."""
    claims.write_claim(
        Claim(trigger_id=holder, holder="run-1", claimed_at=NOW, max_duration_secs=1800),
        base_dir=tmp_path,
    )


def _tick(store: TriggerStore, tmp_path: Path, *, user_active: bool = False) -> Any:
    return asyncio.run(
        svc.tick(store, now=NOW + 1, base_dir=tmp_path, persist=True, user_active=user_active)
    )


def _history(tmp_path: Path, tid: str) -> list[dict]:
    rows, _total = asyncio.run(ScheduleRunStore(tmp_path).list_for_job(tid, 0, 20))
    return rows


def _one_row(tmp_path: Path, tid: str) -> dict:
    rows = _history(tmp_path, tid)
    assert len(rows) == 1, rows
    return rows[0]


# ── a held fire is said, as what it is ──


def test_a_fire_held_by_a_busy_slot_leaves_a_row_naming_the_slot_and_its_holder(
    store: TriggerStore, tmp_path: Path
) -> None:
    """🔴 Before: no row, and the outcome said "deferred" for a fire nothing would run again."""
    _add(store, "clock:index", resource_slots=["local-llm"])
    _add(store, "clock:digest", resource_slots=["local-llm"])
    _hold_slot(tmp_path)
    result = _tick(store, tmp_path)
    assert "clock:digest" not in [fire.trigger.id for fire in result.fires]
    row = _one_row(tmp_path, "clock:digest")
    assert row["status"] == Outcome.SKIPPED_GATE.value, row
    assert row["error"].startswith("skipped this time: "), row
    assert "'local-llm'" in row["error"] and "clock:index" in row["error"], row


def test_a_fire_held_because_its_target_is_busy_leaves_a_row(
    store: TriggerStore, tmp_path: Path
) -> None:
    """🔴 Before: no row. The target's own reason (a lock file) is kept, said as a skip."""
    (tmp_path / "run.lock").write_text("")
    _add(store, "clock:mutate", skip_if_active={"lockfiles": ["run.lock"]})
    _tick(store, tmp_path)
    row = _one_row(tmp_path, "clock:mutate")
    assert row["status"] == Outcome.SKIPPED_GATE.value, row
    assert row["error"].startswith("skipped this time: ") and "lock file" in row["error"], row


def test_a_fire_that_yields_to_you_leaves_a_row(store: TriggerStore, tmp_path: Path) -> None:
    """🔴 Before: no row for a fire held because you were active."""
    _add(store, "clock:tidy", yield_to_user=True)
    _tick(store, tmp_path, user_active=True)
    row = _one_row(tmp_path, "clock:tidy")
    assert row["status"] == Outcome.SKIPPED_GATE.value, row
    assert row["error"] == "skipped this time: it yields to you, and you were active", row


def test_no_held_gate_says_its_fire_was_deferred() -> None:
    """Deferred is work that still starts (a launched run, a queued one, one waiting on you), and
    no door retries a held fire, so none of the three held gates gives it."""
    assert {firepath.GATE_OUTCOMES[gate] for gate in firepath.BUSY_GATES} == {
        Outcome.SKIPPED_GATE.value
    }
    assert firepath.BUSY_GATES == frozenset({"slot", "active", "yield"})


def test_an_event_fire_held_by_a_busy_slot_leaves_a_row(
    store: TriggerStore, tmp_path: Path
) -> None:
    """🔴 Before: the event router passed over the held fire, and it left nothing."""
    from personalclaw.event_triggers import MEMORY_UPDATE, SOURCE_MEMORY, BusEvent, event_spec
    from personalclaw.triggers.event_fire import EventRouter

    _add(store, "clock:index", resource_slots=["local-llm"])
    store.upsert(
        Trigger(
            id="event:notes",
            name="event:notes",
            kind="event",
            enabled=True,
            spec=event_spec(MEMORY_UPDATE),
            resource_slots=["local-llm"],
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    claims.write_claim(
        Claim(
            trigger_id="clock:index", holder="run-1", claimed_at=time.time(), max_duration_secs=1800
        ),
        base_dir=tmp_path,
    )
    dispatched: list[str] = []

    async def dispatch(trigger: Any, _payload: Any, **_kw: Any) -> str:
        dispatched.append(trigger.id)
        return ""

    async def deliver() -> list[str]:
        router = EventRouter(dispatch=dispatch, loop=asyncio.get_running_loop(), base_dir=tmp_path)
        event = BusEvent(
            source=SOURCE_MEMORY, event_type="update", key="notes.today", value="v", now=time.time()
        )
        fired = await router.deliver(event)
        await router.settle()
        return fired

    assert asyncio.run(deliver()) == [] and dispatched == []
    row = _one_row(tmp_path, "event:notes")
    assert row["status"] == Outcome.SKIPPED_GATE.value and "'local-llm'" in row["error"], row
    assert row["source"] == "event", row


# ── a refused fire is recorded too ──


def test_a_fire_the_incident_switch_refused_leaves_a_refused_row(
    store: TriggerStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """🔴 Before: `refused` was not among the outcomes the suppression writer kept, so a clock fire
    held by an incident left no trace."""
    monkeypatch.setattr("personalclaw.guardrails.incident.incident_active", lambda: True)
    _add(store, "clock:report")
    assert _tick(store, tmp_path).fires == []
    row = _one_row(tmp_path, "clock:report")
    assert (row["status"], row["trigger"]) == (Outcome.REFUSED.value, Outcome.REFUSED.value), row
    assert row["error"], "the refusal says why"


def test_a_fire_its_capability_fence_refused_leaves_a_refused_row(
    store: TriggerStore, tmp_path: Path
) -> None:
    """🔴 Before: no row for a fire its frozen capability set refused."""
    _add(
        store,
        "clock:shell",
        capabilities={"providers": ["notify"]},
        workflow={"inline": {"provider": "run-command", "config": {"command": "true"}}},
    )
    assert _tick(store, tmp_path).fires == []
    row = _one_row(tmp_path, "clock:shell")
    assert row["status"] == Outcome.REFUSED.value and "run-command" in row["error"], row


def test_every_outcome_a_gate_gives_is_recorded() -> None:
    """The next gate cannot ship an outcome the suppression writer drops."""
    assert set(firepath.GATE_OUTCOMES.values()) <= svc.RECORDED_SUPPRESSIONS


def test_the_new_rows_are_not_fires_and_not_failures(store: TriggerStore, tmp_path: Path) -> None:
    """What they record did no work: the hourly cap does not count them (`count_since`), and none
    counts toward autopause."""
    _add(store, "clock:index", resource_slots=["local-llm"])
    _add(store, "clock:digest", resource_slots=["local-llm"])
    _add(
        store,
        "clock:shell",
        capabilities={"providers": ["notify"]},
        workflow={"inline": {"provider": "run-command", "config": {"command": "true"}}},
    )
    _hold_slot(tmp_path)
    _tick(store, tmp_path)
    runs = ScheduleRunStore(tmp_path)
    for tid in ("clock:digest", "clock:shell"):
        assert len(_history(tmp_path, tid)) == 1, tid
        assert asyncio.run(runs.count_since(tid, NOW - 60)) == 0, tid
        assert _history(tmp_path, tid)[0]["status"] not in TRUE_FAILURE_OUTCOMES


# ── a held one-shot waits on the review ──


def _review(tmp_path: Path) -> list[Any]:
    from personalclaw.triggers import review

    return review.pending(base_dir=tmp_path)


def test_a_one_shot_held_at_its_time_waits_on_the_review(
    store: TriggerStore, tmp_path: Path
) -> None:
    """🔴 Before: a reminder that yields to you, due while you were active, was switched off at its
    time with no run, no row and no card: it never went off, and nothing said so."""
    _add(store, "clock:remind", spec={"kind": "at", "at": NOW}, yield_to_user=True)
    _tick(store, tmp_path, user_active=True)
    after = store.get("clock:remind")
    assert after is not None and after.trigger.enabled is False, "its slot is taken"
    assert _one_row(tmp_path, "clock:remind")["status"] == Outcome.SKIPPED_GATE.value
    (card,) = _review(tmp_path)
    assert (card.trigger_id, card.kind, card.cause) == ("clock:remind", "missed", "held"), card
    assert (card.count, card.latest) == (1, NOW), card
    assert card.reason == "it yields to you, and you were active", card


def test_a_one_shot_held_by_a_busy_slot_waits_on_the_review(
    store: TriggerStore, tmp_path: Path
) -> None:
    _add(store, "clock:index", resource_slots=["local-llm"])
    _add(store, "clock:once", spec={"kind": "at", "at": NOW}, resource_slots=["local-llm"])
    _hold_slot(tmp_path)
    _tick(store, tmp_path)
    (card,) = _review(tmp_path)
    assert (card.trigger_id, card.cause) == ("clock:once", "held"), card


def test_a_one_shot_its_quiet_hours_skipped_is_not_put_on_the_review(
    store: TriggerStore, tmp_path: Path
) -> None:
    """The control: quiet hours are your own rule that it must not fire then, so its row is its
    record, as before. Only a fire held because something was busy is a run you still wanted."""
    _add(
        store,
        "clock:quiet",
        spec={"kind": "at", "at": NOW},
        gates={"quiet_hours": [{"start": "00:00", "end": "23:59"}]},
    )
    _tick(store, tmp_path)
    assert _one_row(tmp_path, "clock:quiet")["status"] == Outcome.SKIPPED_GATE.value
    assert _review(tmp_path) == []


def test_the_review_says_why_a_held_card_did_not_run() -> None:
    """The card's cause is one the review knows, in words true of it."""
    from personalclaw.triggers import review

    assert review.HELD in review.CAUSES
    card = review.ReviewCard.from_dict(
        {
            "trigger_id": "t",
            "kind": "missed",
            "count": 1,
            "latest": NOW,
            "oldest": NOW,
            "cause": "held",
        }
    )
    assert card.cause == review.HELD
