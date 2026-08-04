"""Criterion 7: kill the gateway mid-fire and restart — the boot sweep that had no caller (S142).

Criterion 7: *"Kill the gateway mid-fire and restart: no double-fire, no lost fire, missed slots
appear in the review card, pending approvals re-arm, `catch_up` triggers fire exactly once,
staggered."*

🔴 **THE DEFECT — five dead layers, the deepest chain this program has found.** The dead-seam sweep
(the technique from S141, generalised) flagged four functions in the criterion-7 chain with no
caller outside their own module. Following each one up found the criterion inert end to end:

1. **`service.boot` had ZERO callers.** Boot ran `migrate_and_arm`, which only arms rows with NO
   `next_fire_at` (`arm.needs_arming`) — so a trigger that WAS armed and went overdue while the lid
   was shut kept its stale past fire, and the first tick found it due. Measured on ten minutely
   triggers overdue by an hour: **10 of 10 due in the same instant at boot**, the restart stampede
   `boot_recovery`'s deterministic per-id stagger exists to prevent (108-179s apart, when called).
2. **`review_at_boot` and `catch_up_plan` read four keys nothing produces.** `last_fire_at`,
   `interval_secs`, `missed_last_slot`, `fires_automatically` — `Trigger.to_dict()` emits **none of
   them**. So even when called, the review was EMPTY however long the lid had been shut, and every
   trigger answered "nothing was missed".
3. **`drain_spooled_fires` had no caller.** A fire parked by a sync CLI memory write sat on disk
   forever — the silent drop the spool was written to fix, one layer up.
4. **`wakeup.retry_queue` had no caller.** A resume whose session was not ready was built,
   classified `REQUEUED`, and thrown away. §3.2 refuses to let anyone drop one: it carries a gate
   answer, and eating it strands the parked run forever waiting for a reply the user already gave.
5. **The loop's own drop check read a key its producer does not emit.** `summary.get("dropped")` —
   `summary()` returns `{total, delivered, by_disposition, retry}`, so a `no_session` delivery (a
   fire that reached nobody) was logged nowhere.

Three of those five are the same shape: a live reader asking for a name its producer never writes.
That is worse than an unread constant, because the silence reads as "nothing wrong".

**And two bugs the wiring itself exposed**, both latent only because nothing called `plan_boot`:
the review was snapshot AFTER recovery had already overwritten the evidence, and `missed_dropped`
re-armed a 03:00 backup to 09:02 — firing off-schedule the very slot it had decided to drop.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from personalclaw.gateway import GatewayOrchestrator
from personalclaw.triggers import service as SVC
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_700_000_000.0
HOUR = 3600.0


class _State:
    """A dashboard state recording `notify` kwargs.

    `kind`/`title`/`body`/`meta`, matching the real contract — S140 recorded that a fake with the
    wrong SHAPE reproduces the bug you are confirming you fixed.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def notify(self, *, kind, title, body, meta=None):
        self.sent.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})
        return True


def _clock(tid, *, next_at=0.0, interval=60, catch_up=False, spec=None):
    trigger = Trigger(
        id=tid,
        name=f"T-{tid}",
        kind="clock",
        enabled=True,
        catch_up=catch_up,
        spec=spec or {"kind": "interval", "interval_secs": interval},
        workflow={"provider": "run-prompt", "config": {"message": "go"}},
        capabilities={"providers": ["run-prompt"]},
    )
    if next_at:
        trigger.next_fire_at = SVC.to_iso(next_at)
    return trigger


def _orchestrator(state=None):
    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.dashboard_state = state
    return gateway


# ── the sweep itself ──


def test_the_boot_sweep_LEAVES_NOTHING_immediately_due(tmp_path):
    """🔴 The restart stampede, as the property that matters. Ten minutely triggers overdue by an
    hour were 10 of 10 due in the same instant before the sweep was wired."""
    store = TriggerStore(base_dir=tmp_path)
    store.save_all([_clock(f"t{i}", next_at=NOW - HOUR) for i in range(10)])
    assert len(SVC.due_ids([r.trigger for r in store.load()], now=NOW)) == 10

    SVC.boot(store, now=NOW)
    assert SVC.due_ids([r.trigger for r in store.load()], now=NOW) == []


def test_the_sweep_STAGGERS_rather_than_bunching(tmp_path):
    """§3.1 needs both halves — recovered on boot AND spread, so a restart does not fire everything
    in one second."""
    store = TriggerStore(base_dir=tmp_path)
    store.save_all([_clock(f"t{i}", next_at=NOW - HOUR) for i in range(6)])
    report = SVC.boot(store, now=NOW)
    assert len({row["next_fire_at"] for row in report["rearmed"]}) == 6


def test_a_DROPPED_slot_resumes_on_the_triggers_OWN_schedule(tmp_path):
    """🔴 Found by driving the newly-wired sweep. A `catch_up: false` 03:00 daily backup, overdue
    because the laptop was shut, was re-armed to **09:02** — the slot `missed_dropped` had just
    decided to DROP fired six hours late anyway, ignoring the trigger's own cron expression."""
    now = dt.datetime(2023, 11, 15, 9, 0, tzinfo=dt.timezone.utc).timestamp()
    missed = dt.datetime(2023, 11, 15, 3, 0, tzinfo=dt.timezone.utc).timestamp()
    store = TriggerStore(base_dir=tmp_path)
    store.save_all([_clock("backup", spec={"kind": "cron", "expr": "0 3 * * *"}, next_at=missed)])

    SVC.boot(store, now=now)
    landed = dt.datetime.fromtimestamp(
        SVC.to_epoch(store.get("backup").trigger.next_fire_at), dt.timezone.utc
    )
    assert (landed.hour, landed.day) == (3, 16), landed.isoformat()


def test_catch_up_fires_ONCE_and_the_refusals_are_EXPLAINED(tmp_path):
    """§3.4: at most one catch-up however many slots were missed, and a `catch_up: true` trigger
    that did NOT catch up needs an explanation as much as one that did."""
    store = TriggerStore(base_dir=tmp_path)
    store.save_all(
        [
            _clock("yes", next_at=NOW - HOUR, catch_up=True),
            _clock("no", next_at=NOW - HOUR, catch_up=False),
        ]
    )
    plan = {row["id"]: row for row in SVC.boot(store, now=NOW)["catch_up"]}
    assert [k for k, v in plan.items() if v["catching_up"]] == ["yes"]
    assert plan["yes"]["fire_at"] > NOW, "not inline — the gateway is still starting"
    assert plan["no"]["reason"]


# ── the review reaches the user ──


def test_the_missed_review_SURFACES_as_one_notification():
    """Criterion 7's "missed slots appear in the review card". ONE notification naming the count,
    not one per slot: a laptop opened after a weekend would otherwise deliver hundreds."""
    state = _State()
    _orchestrator(state)._surface_missed_review(
        {
            "review": {
                "rows": [{"trigger_id": "a", "scheduled_for": NOW - 60}],
                "summaries": [{"trigger_id": "a", "count": 40}],
                "truncated": False,
            },
            "catch_up": [{"id": "a", "catching_up": True, "fire_at": NOW + 90}],
        }
    )
    assert len(state.sent) == 1
    sent = state.sent[0]
    assert sent["meta"]["event"] == "automation.missed_review"
    assert sent["meta"]["missed"] == 41, sent["meta"]
    assert sent["meta"]["statusUrl"] == "#/triggers", "it must be reachable, not just announced"
    assert "41 scheduled runs were missed" in sent["body"]
    assert "catch-up" in sent["body"], "and it says which will fire on their own"


def test_NOTHING_missed_says_NOTHING():
    """ "0 automations missed a run" on every restart trains the user to dismiss the notification
    that matters."""
    state = _State()
    _orchestrator(state)._surface_missed_review(
        {"review": {"rows": [], "summaries": []}, "catch_up": []}
    )
    assert state.sent == []


def test_the_surface_NEVER_raises_without_a_dashboard():
    """The sweep already re-armed the schedule; failing to announce it must not undo that."""
    _orchestrator(None)._surface_missed_review({"review": {"rows": [{"trigger_id": "a"}]}})
    _orchestrator(_State())._surface_missed_review({})


# ── the boot report is what the gateway actually logs ──


def test_the_boot_report_carries_every_field_the_gateway_reads(tmp_path):
    """The gateway logs `rearmed`/`total`/`review.rows` and passes the whole report to the surface.
    A report missing one of those would make the log line lie rather than fail — the exact class of
    defect this session exists to close."""
    store = TriggerStore(base_dir=tmp_path)
    store.save_all([_clock("t1", next_at=NOW - HOUR)])
    report = SVC.boot(store, now=NOW)
    assert {"rearmed", "total", "review", "catch_up", "next_sleep"} <= set(report)
    assert {"rows", "summaries", "truncated"} <= set(report["review"])
    # Driven through the real surface, so the shapes are asserted together rather than assumed.
    state = _State()
    _orchestrator(state)._surface_missed_review(report)
    assert len(state.sent) == 1


def test_the_sweep_is_a_dry_run_when_asked(tmp_path):
    """`automation doctor` reports what a boot WOULD do without re-arming a live schedule."""
    store = TriggerStore(base_dir=tmp_path)
    store.save_all([_clock("t1", next_at=NOW - HOUR)])
    before = store.get("t1").trigger.next_fire_at
    report = SVC.boot(store, now=NOW, persist=False)
    assert report["rearmed"], "it still SAYS what it would do"
    assert store.get("t1").trigger.next_fire_at == before


# ── crash-safety: the spool survives a restart ──


def test_a_fire_with_NO_RUNNING_LOOP_is_spooled_not_dropped(tmp_path, monkeypatch):
    """🔴 `event_triggers._schedule_fire` recorded the fire, asked for a running loop, and `return`ed
    when there was none — so a sync CLI memory write incremented `fire_count` and dropped the action
    with nothing recording that it did not run. `dispatch.spool_fire` was written for exactly this
    path and its docstring calls it "THE fix for the measured bug"; it had no caller, so the bug it
    names was still live."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)

    import personalclaw.event_triggers as et
    from personalclaw.triggers.dispatch import drain_spool

    store = et.EventTriggerStore(tmp_path / "event_triggers.json")
    store.save(
        [
            et.EventTrigger(
                id="e-1",
                pattern=et.MEMORY_UPDATE,
                action_provider="bash",
                action_config={"command": "true"},
                max_fires=3,
            )
        ]
    )
    engine = et.EventTriggerEngine()
    engine._store = store

    # No running loop — this IS the sync CLI write.
    engine.on_memory_event(event_type="memory_write", key="notes/x", value="hi", now=NOW)

    assert store.load()[0].fire_count == 1, "the fire was counted against max_fires either way"
    envelopes, bad = drain_spool()
    assert bad == 0
    assert [e.payload["key"] for e in envelopes] == ["notes/x"], "so it must not be lost"


def test_a_spool_failure_does_not_BREAK_THE_MEMORY_WRITE(tmp_path, monkeypatch):
    """The write is the user's actual work and this is bookkeeping layered on top of it. An
    unwritable disk must not take down ordinary use."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)

    import personalclaw.event_triggers as et
    from personalclaw.triggers import dispatch

    def _boom(*a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(dispatch, "spool_fire", _boom)

    store = et.EventTriggerStore(tmp_path / "event_triggers.json")
    store.save(
        [
            et.EventTrigger(
                id="e-1",
                pattern=et.MEMORY_UPDATE,
                action_provider="bash",
                action_config={"command": "true"},
            )
        ]
    )
    engine = et.EventTriggerEngine()
    engine._store = store
    engine.on_memory_event(event_type="memory_write", key="k", value="v", now=NOW)  # must not raise


def test_a_LIVE_fire_still_dispatches_rather_than_spooling(tmp_path, monkeypatch):
    """The spool is the no-loop path ONLY. Spooling a fire that could have run now would turn every
    live memory-trigger fire into a delayed one."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)

    import personalclaw.event_triggers as et
    from personalclaw.triggers.dispatch import drain_spool

    store = et.EventTriggerStore(tmp_path / "event_triggers.json")
    store.save(
        [
            et.EventTrigger(
                id="e-1",
                pattern=et.MEMORY_UPDATE,
                action_provider="bash",
                action_config={"command": "true"},
            )
        ]
    )

    async def _with_loop():
        engine = et.EventTriggerEngine()
        engine._store = store
        fired: list[str] = []
        monkeypatch.setattr(
            et,
            "execute_event_action",
            lambda t, **kw: _record(fired, kw["key"]),
        )
        engine.on_memory_event(event_type="memory_write", key="live", value="v", now=NOW)
        await asyncio.sleep(0)  # let the created task run
        return fired

    async def _record(sink, key):
        sink.append(key)
        return et.FireOutcome(True, "")

    fired = asyncio.run(_with_loop())
    assert fired == ["live"]
    assert drain_spool()[0] == [], "a live fire must not also be spooled"
