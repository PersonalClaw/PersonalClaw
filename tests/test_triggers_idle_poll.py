"""The idle runtime — the fourth declared-but-inert kind, and the wiring that closes it
(§1.2 / §7 step 9 — WF2AUT-11).

**🔴 MEASURED BEFORE A LINE WAS WRITTEN.** `idle` has been fully declared since S87 — in
`models.KINDS`, with `SPEC_KEYS['idle'] == {scope, idle_secs, first_idle_secs}` and NL phrasings in
`nl_kind` — and **nothing fired it**. `dispatch.py`, `executor.py` and `firepath.py` never mention
it, and `idle_secs` was read only by `autonudge.py`. The clock tick could not reach one either:
`service.due_ids` skips any trigger with no `next_fire_at`, and an idle trigger has none because
its due-ness is a predicate over session activity, not a schedule.

So these tests prove the WIRING, not a helper: the first one drives `loop.tick_once` — the real
tick path, the one whose docstring records that a trigger written the new way once had "no firing
path at all" because nothing CALLED the tick — and asserts a `kind:idle` trigger reaches the
runner. Every time is INJECTED; there is not one `sleep` in this file.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers import idle_poll as IP
from personalclaw.triggers import loop as L
from personalclaw.triggers import wakeup as WK
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


class _Provider:
    async def shutdown(self):
        return None


def _manager(*keys):
    from personalclaw.session import SessionManager, _Session

    manager = SessionManager.__new__(SessionManager)
    manager._sessions = {}
    for key in keys:
        manager._sessions[key] = _Session(provider=_Provider())
    return manager


def _idle(tid="idle:standup", *, idle_secs=60, first_idle_secs=0, scope="", enabled=True):
    spec = {"idle_secs": idle_secs, "first_idle_secs": first_idle_secs}
    if scope:
        spec["scope"] = scope
    return Trigger(
        id=tid,
        name=f"I-{tid}",
        kind="idle",
        enabled=enabled,
        spec=spec,
        workflow={"provider": "run-prompt", "config": {"message": "still there?"}},
        capabilities={"providers": ["run-prompt"]},
    )


@pytest.fixture(autouse=True)
def _no_autonudge(monkeypatch):
    """No AutoNudgeService in a test process. Asserted explicitly rather than assumed: the
    anti-double-fire fence reads the live singleton, and a leaked instance from another test would
    make every fire here defer for the wrong reason."""
    monkeypatch.setattr("personalclaw.autonudge.get_instance", lambda: None)


# ── 🔴 the wiring: a kind:idle trigger fires through the REAL tick ──


def test_kind_idle_FIRES_THROUGH_TICK_ONCE(tmp_path):
    """🔴 THE gap this atom closes. Not "the helper computes True" — the real `loop.tick_once`, with
    no clock trigger in the store at all, reaching the injected runner."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle())
    manager = _manager(WK.session_key_for("idle:standup"))
    ran: list[str] = []

    async def runner(payload):
        ran.append(payload.get("trigger_id", ""))
        return {"status": "ok"}

    # First tick ARMS (a trigger just seen has observed no quiet period) and must not fire.
    result = asyncio.run(
        L.tick_once(store, runner=runner, sessions=manager, base_dir=tmp_path, now=NOW)
    )
    assert result.fires == []
    assert ran == [], "an unarmed idle trigger fired on sight"

    # Second tick, a full quiet period later: it fires, through the tick, with no clock involved.
    asyncio.run(
        L.tick_once(store, runner=runner, sessions=manager, base_dir=tmp_path, now=NOW + 61)
    )
    assert ran == ["idle:standup"], "kind:idle still has no firing path through the tick"


def test_the_tick_SURVIVES_a_broken_idle_poll(tmp_path, monkeypatch):
    """One bad idle trigger must not stop the clock for every automation on the machine."""

    async def boom(*_a, **_k):
        raise RuntimeError("idle exploded")

    monkeypatch.setattr(IP, "poll", boom)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle())
    result = asyncio.run(
        L.tick_once(store, runner=_ok, sessions=_manager(), base_dir=tmp_path, now=NOW)
    )
    assert result is not None


async def _ok(_payload):
    return {"status": "ok"}


# ── 🔴 delivered-only counting + the mid-turn drop ──


def test_a_MID_TURN_fire_is_dropped_and_does_NOT_increment_the_counter(tmp_path):
    """🔴 The rule autonudge lines 343-352 exist for: "skipped nudges (e.g. session mid-turn) inflate
    cycle_count and prematurely trip max_cycles". A dropped fire must advance NOTHING."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle())
    key = WK.session_key_for("idle:standup")
    manager = _manager(key)
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)

    # Mid-turn: the per-session semaphore is HELD, which is what `wakeup.is_running` reads.
    asyncio.run(manager._sessions[key].semaphore.acquire())
    assert WK.is_running(manager, key) is True

    delivered, skipped = asyncio.run(IP.poll(store, manager, _ok, now=NOW + 61, base_dir=tmp_path))
    assert delivered == 0
    assert [r["reason"] for r in skipped] == [WK.Disposition.SKIPPED_RUNNING.value]

    state = IP.load_state("idle:standup", base_dir=tmp_path)
    assert state.cycle_count == 0, "a dropped mid-turn fire inflated cycle_count"
    assert state.last_fire == 0.0
    assert state.armed_at == NOW, "a dropped fire re-armed, so the retry waits another full period"

    # And because nothing advanced, it is STILL due — the retry the drop must not cost.
    due, _why = IP.is_idle(_idle(), state, now=NOW + 61)
    assert due is True


def test_a_DELIVERED_fire_increments_the_counter(tmp_path):
    """The other half of delivered-only: the counter moves exactly when someone received it."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle())
    manager = _manager(WK.session_key_for("idle:standup"))
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)

    delivered, _skipped = asyncio.run(IP.poll(store, manager, _ok, now=NOW + 61, base_dir=tmp_path))
    assert delivered == 1
    state = IP.load_state("idle:standup", base_dir=tmp_path)
    assert state.cycle_count == 1
    assert state.last_fire == NOW + 61


# ── 🔴 reactive re-arm ──


def test_a_fire_RE_ARMS_from_the_fire_instant(tmp_path):
    """Autonudge re-arms from `notify_turn_complete`, so the next fire is `idle_secs` after THIS one
    — not on a fixed grid. Expressed here as state: `armed_at` moves to the fire."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle())
    manager = _manager(WK.session_key_for("idle:standup"))
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)

    asyncio.run(IP.poll(store, manager, _ok, now=NOW + 61, base_dir=tmp_path))
    state = IP.load_state("idle:standup", base_dir=tmp_path)
    assert state.armed_at == NOW + 61

    # Immediately after: NOT due again. The quiet period restarted at the fire.
    assert IP.is_idle(_idle(), state, now=NOW + 62)[0] is False
    # A full period after the FIRE (not after the original arm): due again.
    assert IP.is_idle(_idle(), state, now=NOW + 122)[0] is True


def test_user_activity_RE_ARMS(tmp_path):
    """Autonudge's `notify_user_input` CANCELLED a pending timer; a poll has no timer, so the
    equivalent is moving the arm point forward — which also survives a restart."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle(scope="session:chat-1"))
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)
    assert IP.is_idle(_idle(), IP.load_state("idle:standup", base_dir=tmp_path), now=NOW + 61)[0]

    rearmed = IP.notify_activity(session_key="chat-1", store=store, now=NOW + 50, base_dir=tmp_path)
    assert rearmed == ["idle:standup"]
    state = IP.load_state("idle:standup", base_dir=tmp_path)
    assert state.armed_at == NOW + 50
    assert IP.is_idle(_idle(), state, now=NOW + 61)[0] is False, "user input did not re-arm"


def test_activity_on_an_UNRELATED_session_re_arms_nothing(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle(scope="session:chat-1"))
    assert IP.notify_activity(session_key="other", store=store, now=NOW, base_dir=tmp_path) == []


# ── 🔴 first_idle_secs: honored on the FIRST fire, and 0 disables it ──


def test_first_idle_secs_is_HONORED_on_the_first_fire(tmp_path):
    """A freshly-armed trigger starts promptly instead of sitting the full `idle_secs`."""
    trigger = _idle(idle_secs=600, first_idle_secs=10)
    state = IP.IdleState(armed_at=NOW)
    assert IP.wait_secs(trigger, state) == 10
    assert IP.is_idle(trigger, state, now=NOW + 11)[0] is True, "first_idle_secs was ignored"


def test_first_idle_secs_is_a_ONE_SHOT_spent_by_a_DELIVERED_fire(tmp_path):
    """Autonudge clears it after the first fire so later fires wait the full `idle_secs`. Keyed on
    `cycle_count`, which only a DELIVERED fire moves — so a mid-turn drop does NOT spend it."""
    trigger = _idle(idle_secs=600, first_idle_secs=10)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    manager = _manager(WK.session_key_for("idle:standup"))
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)

    delivered, _ = asyncio.run(IP.poll(store, manager, _ok, now=NOW + 11, base_dir=tmp_path))
    assert delivered == 1
    state = IP.load_state("idle:standup", base_dir=tmp_path)
    assert state.cycle_count == 1
    assert IP.wait_secs(trigger, state) == 600, "the short first wait was not spent"


def test_first_idle_secs_ZERO_DISABLES_it(tmp_path):
    """`0 = disabled` (autonudge's documented default): the first fire waits the whole
    `idle_secs`."""
    trigger = _idle(idle_secs=60, first_idle_secs=0)
    state = IP.IdleState(armed_at=NOW)
    assert IP.wait_secs(trigger, state) == 60
    assert IP.is_idle(trigger, state, now=NOW + 30)[0] is False
    assert IP.is_idle(trigger, state, now=NOW + 61)[0] is True


def test_a_mid_turn_drop_does_NOT_spend_the_short_first_wait(tmp_path):
    """The two rules composed: an undelivered first fire leaves `cycle_count` at 0, so the trigger
    still gets its prompt start once someone can receive it."""
    trigger = _idle(idle_secs=600, first_idle_secs=10)
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(trigger)
    key = WK.session_key_for("idle:standup")
    manager = _manager(key)
    asyncio.run(manager._sessions[key].semaphore.acquire())
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)

    delivered, _ = asyncio.run(IP.poll(store, manager, _ok, now=NOW + 11, base_dir=tmp_path))
    assert delivered == 0
    state = IP.load_state("idle:standup", base_dir=tmp_path)
    assert IP.wait_secs(trigger, state) == 10


# ── 🔴 the anti-double-fire fence against autonudge (half 2 is BLOCKED, so both are alive) ──


def test_a_session_AUTONUDGE_owns_is_SKIPPED_with_a_reason(tmp_path, monkeypatch):
    """🔴 `autonudge.py` is NOT deleted (half 2 waits on LOOPS-EVOLUTION Phase 4 — there is no
    loop-ticker in the tree). The stores are disjoint, so the only reachable overlap is an idle
    trigger scoped to a session that also has a nudge loop. That one defers, LOUDLY."""

    class _Svc:
        def get_by_session(self, key):
            return object() if key == "chat-1" else None

    monkeypatch.setattr("personalclaw.autonudge.get_instance", lambda: _Svc())
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle(scope="session:chat-1"))
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)

    fires, skipped = IP.due_fires(store, now=NOW + 61, base_dir=tmp_path)
    assert fires == [], "an idle trigger double-fired a session autonudge already nudges"
    assert [r["reason"] for r in skipped] == [IP.SKIP_AUTONUDGE]


def test_a_session_autonudge_does_NOT_own_still_fires(tmp_path, monkeypatch):
    """The fence is narrow: another session's nudge loop must not silence this trigger."""

    class _Svc:
        def get_by_session(self, key):
            return object() if key == "someone-else" else None

    monkeypatch.setattr("personalclaw.autonudge.get_instance", lambda: _Svc())
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle(scope="session:chat-1"))
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)
    fires, _ = IP.due_fires(store, now=NOW + 61, base_dir=tmp_path)
    assert [f.trigger.id for f in fires] == ["idle:standup"]


def test_the_autonudge_probe_FAILS_OPEN(tmp_path, monkeypatch):
    """A broken probe must not retire every idle automation. This fence prevents a duplicate nudge;
    it is not the gate that authorises the kind."""

    class _Svc:
        def get_by_session(self, key):
            raise RuntimeError("autonudge is broken")

    monkeypatch.setattr("personalclaw.autonudge.get_instance", lambda: _Svc())
    assert IP._autonudge_owns("chat-1") is False


# ── the ordinary refusals ──


def test_a_DISABLED_idle_trigger_never_fires(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle(enabled=False))
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)
    fires, _ = IP.due_fires(store, now=NOW + 999, base_dir=tmp_path)
    assert fires == []


def test_a_trigger_still_INSIDE_its_quiet_period_is_skipped_with_a_reason(tmp_path):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle())
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)
    fires, skipped = IP.due_fires(store, now=NOW + 30, base_dir=tmp_path)
    assert fires == []
    assert [r["reason"] for r in skipped] == [IP.SKIP_NOT_IDLE]


def test_NO_SESSION_MANAGER_is_reported_not_counted_as_a_delivery(tmp_path):
    """An API-only process must not spend a cycle nobody received."""
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle())
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)
    delivered, skipped = asyncio.run(IP.poll(store, None, _ok, now=NOW + 61, base_dir=tmp_path))
    assert delivered == 0
    assert [r["reason"] for r in skipped] == ["no_session_manager"]
    assert IP.load_state("idle:standup", base_dir=tmp_path).cycle_count == 0


def test_a_DAMAGED_sidecar_reads_as_fresh_rather_than_stopping_the_trigger(tmp_path):
    """Fail-open: the worst case is one extra quiet period, versus a trigger that never fires."""
    path = IP._state_path("idle:standup", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert IP.load_state("idle:standup", base_dir=tmp_path) == IP.IdleState()


def test_ONE_bad_trigger_does_not_stop_the_others(tmp_path, monkeypatch):
    store = TriggerStore(base_dir=tmp_path)
    store.upsert(_idle("idle:bad"))
    store.upsert(_idle("idle:good"))
    for tid in ("idle:bad", "idle:good"):
        IP.save_state(tid, IP.IdleState(armed_at=NOW), base_dir=tmp_path)

    real = IP.is_idle

    def selective(trigger, state, *, now):
        if trigger.id == "idle:bad":
            raise RuntimeError("bad trigger")
        return real(trigger, state, now=now)

    monkeypatch.setattr(IP, "is_idle", selective)
    fires, _ = IP.due_fires(store, now=NOW + 61, base_dir=tmp_path)
    assert [f.trigger.id for f in fires] == ["idle:good"]


def test_notify_activity_HAS_A_CALL_SITE_on_the_user_input_path(tmp_path):
    """🔴 The writer, not just the reader. A re-arm nobody calls is the worst inert shape: `armed_at`
    would only ever advance on a fire, so a user typing all afternoon would still be told they had
    gone quiet. Asserted at the same handler where autonudge cancels its timer — the one place that
    knows the user just spoke."""
    import inspect

    from personalclaw.dashboard.chat_handlers import api_chat

    src = inspect.getsource(api_chat)
    assert "notify_activity" in src, "kind:idle has no re-arm-on-user-input writer"
    assert "notify_user_input" in src, "the autonudge cancel it sits beside vanished"


def test_the_sidecar_lands_beside_the_STORE_never_in_the_real_home(tmp_path):
    """The `file_poll`/claims lesson: state describing one store must not live in another."""
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)
    assert (tmp_path / "trigger-idle" / "idle-standup.json").exists()
