"""Incident mode holds a running loop, the way every other runner honours the switch.

Measured on a running Unattended goal loop: with `personalclaw incident on` thrown, the loop page
kept reading "Working · Streaming · cycle 2/30", its status stayed `running`, and the worker went
on making model calls for minutes. Cron, hooks, triggers, subagents, auto-execution and app workers
each ask `guardrails.incident.incident_active()` before they act; nothing on a loop's path did.

A loop's work reaches the model three ways, and each is pinned here:

* **The next cycle** — the idle runtime hands a due nudge row to the loop's cycle driver. It now
  holds every due row while the switch is on, and leaves its state alone so the cycle fires once
  the switch is off (the cron / worker behaviour: suspended, then carrying on by itself).
* **The cycle in flight** — the loop watchdog stops the turn running on each worker, through the
  same stop a Pause uses, and the cycle driver abandons the cycle's re-prompts.
* **The watchdog's own work** — it does no cycle bookkeeping (no judge call, no stage hook) while
  the loop is held, and a hold is not the silence that fails a worker as unresponsive.

The loop's views carry the plain sentence that says why, and the gateway tells open pages when the
switch moves, so they need not poll it. Every test drives the REAL incident flag under ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.guardrails import incident
from personalclaw.loop import files as loop_files
from personalclaw.loop import manager, store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.triggers import idle_poll as IP
from personalclaw.triggers import wakeup as WK
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The incident flag, the loop store and the trigger sidecars all under ``tmp_path``."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.triggers.nudge._INSTANCE", None)
    incident.reset_incident_mirror()
    yield tmp_path
    incident.reset_incident_mirror()


# ── the next cycle: the idle runtime ──────────────────────────────────────────


def _nudge_row(tid: str = "nudge:worker", *, session: str = "loop-abcd1234") -> Trigger:
    """A message-bearing idle row — what a loop worker's next cycle is."""
    return Trigger(
        id=tid,
        name=f"N-{tid}",
        kind="idle",
        created_by="system",
        spec={"scope": f"session:{session}", "idle_secs": 60, "message": "next cycle"},
        session=f"conversation:{session}",
        overlap="skip",
    )


class _Deliverer:
    """Stands in for the nudge service: records what the idle runtime hands it."""

    def __init__(self) -> None:
        self.delivered: list[str] = []

    async def deliver(self, trigger: Any, *, now: float = 0.0) -> tuple[bool, str]:
        self.delivered.append(trigger.id)
        return True, ""


async def _ok(_payload: dict) -> dict:
    return {"status": "ok"}


def test_a_loop_workers_next_cycle_is_held_while_incident_mode_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_ = TriggerStore(base_dir=tmp_path)
    store_.upsert(_nudge_row())
    IP.save_state("nudge:worker", IP.IdleState(armed_at=NOW), base_dir=tmp_path)
    svc = _Deliverer()
    monkeypatch.setattr("personalclaw.triggers.nudge._INSTANCE", svc)

    incident.activate("a drill")
    delivered, skipped = asyncio.run(IP.poll(store_, None, _ok, now=NOW + 61, base_dir=tmp_path))
    assert delivered == 0
    assert svc.delivered == [], "a loop started a new cycle with incident mode on"
    assert [r["reason"] for r in skipped] == [IP.SKIP_INCIDENT]
    state = IP.load_state("nudge:worker", base_dir=tmp_path)
    assert (state.cycle_count, state.armed_at) == (0, NOW), "a held cycle advanced its state"

    # The switch off: the SAME cycle fires on the next poll, with nothing for the owner to do.
    incident.resume()
    delivered, _skipped = asyncio.run(IP.poll(store_, None, _ok, now=NOW + 62, base_dir=tmp_path))
    assert delivered == 1
    assert svc.delivered == ["nudge:worker"]


def test_a_plain_idle_trigger_is_held_too(tmp_path: Path) -> None:
    """An idle trigger fires through the wake path, not the loop driver, and skipped the switch
    the same way — the clock path checks it first (`firepath`), this one never did."""
    store_ = TriggerStore(base_dir=tmp_path)
    store_.upsert(
        Trigger(
            id="idle:standup",
            name="standup",
            kind="idle",
            spec={"idle_secs": 60},
            workflow={"provider": "run-prompt", "config": {"message": "still there?"}},
            capabilities={"providers": ["run-prompt"]},
        )
    )
    IP.save_state("idle:standup", IP.IdleState(armed_at=NOW), base_dir=tmp_path)
    from personalclaw.session import SessionManager, _Session

    class _Provider:
        async def shutdown(self) -> None:
            return None

    sessions = SessionManager.__new__(SessionManager)
    sessions._sessions = {WK.session_key_for("idle:standup"): _Session(provider=_Provider())}
    ran: list[str] = []

    async def runner(payload: dict) -> dict:
        ran.append(payload.get("trigger_id", ""))
        return {"status": "ok"}

    incident.activate("a drill")
    delivered, skipped = asyncio.run(
        IP.poll(store_, sessions, runner, now=NOW + 61, base_dir=tmp_path)
    )
    assert (delivered, ran) == (0, [])
    assert [r["reason"] for r in skipped] == [IP.SKIP_INCIDENT]
    assert IP.load_state("idle:standup", base_dir=tmp_path).cycle_count == 0


# ── the cycle in flight, and the watchdog's own work ─────────────────────────


class _Session:
    def __init__(self, key: str, *, running: bool) -> None:
        self.key = key
        self._running = running
        self._queue: deque[Any] = deque()
        self.messages: list[dict] = []

    @property
    def running(self) -> bool:
        return self._running


class _Sessions:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def stop_turn(self, key: str, *, force: bool = False, **_: Any) -> str:
        self.stopped.append(key)
        return "soft"


class _State:
    def __init__(self) -> None:
        from personalclaw.dashboard.sse import SseRegistry

        self._sessions: dict[str, _Session] = {}
        self.sessions = _Sessions()
        self._sse = SseRegistry()
        self.refreshed: list[tuple[str, ...]] = []
        self.notes: list[tuple] = []

    def loop_sse(self) -> Any:
        return self._sse

    def push_refresh(self, *kinds: str) -> None:
        self.refreshed.append(kinds)

    def notify(self, kind: str, title: str, body: str, *, meta: Any = None) -> None:
        self.notes.append((kind, title, body))


class _Nudge:
    def __init__(self, lid: str, session_name: str) -> None:
        self.id, self.session_name, self.active, self.cycle_count = lid, session_name, True, 0


class _Svc:
    def __init__(self, *names: str) -> None:
        self._rows = {f"N{i}": _Nudge(f"N{i}", n) for i, n in enumerate(names)}

    def get_by_session(self, name: str) -> _Nudge | None:
        return next((r for r in self._rows.values() if r.session_name == name), None)

    def list_all(self) -> list[_Nudge]:
        return list(self._rows.values())

    async def update(self, loop_id: str, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self._rows[loop_id], k, v)

    async def remove(self, loop_id: str) -> None:
        self._rows.pop(loop_id, None)


def _running_loop() -> Loop:
    loop = store.create(
        Loop(
            id="",
            name="G",
            kind="goal",
            task="compare three rain jackets",
            kind_config={"goal_type": "verifiable", "verify_command": "false"},
            max_cycles=30,
        )
    )
    return store.update_status(loop.id, LoopStatus.RUNNING)


def _write_finding(loop_id: str, cycle: int) -> None:
    d = loop_files.loop_dir(loop_id)
    (d / "findings" / f"cycle_{cycle:03d}.json").write_text(
        json.dumps({"cycle": cycle, "new_findings_count": 1, "summary": f"cycle {cycle} notes"})
    )


def _watchdog(key: str, *, running: bool) -> W.LoopWatchdog:
    wd = W.LoopWatchdog(_State(), _Svc(key))
    wd._state._sessions[key] = _Session(key, running=running)
    wd._swept = True
    return wd


def test_the_turn_in_flight_is_stopped_and_the_loop_stays_running() -> None:
    loop = _running_loop()
    key = manager.session_key(loop.id)
    wd = _watchdog(key, running=True)

    incident.activate("a drill")
    asyncio.run(wd._poll_once())
    assert wd._state.sessions.stopped == [
        f"dashboard:{key}"
    ], "the worker's turn kept making model calls under the kill switch"
    assert (
        store.get(loop.id).status == LoopStatus.RUNNING.value
    ), "a hold is not a Pause: the loop must carry on by itself once the switch is off"


def test_a_held_loop_is_not_failed_as_a_silent_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hold starts no cycle, so the worker goes quiet — which is not the worker stalling."""
    loop = _running_loop()
    key = manager.session_key(loop.id)
    wd = _watchdog(key, running=False)
    wd._last_count[loop.id] = 0
    wd._last_activity[loop.id] = float(loop.started_at or 0.0)
    # The worker has been silent for longer than the deadline allows.
    monkeypatch.setattr(W, "_unresponsive_deadline", lambda _idle: -1)

    incident.activate("a drill")
    asyncio.run(wd._poll_once())
    fresh = store.get(loop.id)
    assert fresh.status == LoopStatus.RUNNING.value, fresh.error_message


def test_the_watchdog_does_no_cycle_work_while_held_and_resumes_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finding that landed as the switch was thrown is credited — and its done-ness check run —
    only once the switch is off. The check is a model call (a judge) or a command, which is
    exactly the unattended work the switch suspends."""
    loop = _running_loop()
    key = manager.session_key(loop.id)
    wd = _watchdog(key, running=False)
    asyncio.run(wd._poll_once())  # seed the loop's liveness and credit baseline
    checks: list[str] = []

    async def _done_signal(lp: Loop, findings: list, policy: Any) -> bool:
        checks.append(lp.id)
        return False

    monkeypatch.setattr(W.supervisor, "done_signal", _done_signal)
    _write_finding(loop.id, 1)

    incident.activate("a drill")
    asyncio.run(wd._poll_once())
    assert checks == [], "the watchdog ran a done-ness check with incident mode on"
    assert loop_files.credited_cycles(loop.id) == 0

    incident.resume()
    asyncio.run(wd._poll_once())
    assert checks == [loop.id], "the held cycle was never credited once the switch was off"
    assert loop_files.credited_cycles(loop.id) == 1


def test_a_turn_the_hold_ended_is_not_counted_as_a_worker_failure() -> None:
    """Two failed turns in a row fail a loop. A turn that ends while the switch is on was ended by
    the hold, whatever its runtime reported on the way out."""
    loop = _running_loop()
    wd = _watchdog(manager.session_key(loop.id), running=False)
    incident.activate("a drill")
    wd.record_turn_outcome(loop.id, ok=False)
    wd.record_turn_outcome(loop.id, ok=False)
    assert store.get(loop.id).status == LoopStatus.RUNNING.value

    incident.resume()  # and the count still works when the switch is off
    wd.record_turn_outcome(loop.id, ok=False)
    wd.record_turn_outcome(loop.id, ok=False)
    assert store.get(loop.id).status == LoopStatus.FAILED.value


# ── the cycle driver's half ──


class _FakeNudgeService:
    """Stands in for `AutoNudgeService` so the gateway hands it the REAL `_fire` callback."""

    last: "_FakeNudgeService | None" = None

    def __init__(self, *, base_dir: Any, on_fire: Any) -> None:
        self.on_fire = on_fire
        self.rows: dict[str, _Nudge] = {}
        self.completed: list[tuple[str, bool]] = []
        _FakeNudgeService.last = self

    async def start(self) -> None:
        return None

    def subscribe(self, _observer: Any) -> None:
        return None

    def get_by_session(self, name: str) -> _Nudge | None:
        return self.rows.get(name)

    async def remove(self, loop_id: str) -> None:
        return None

    def notify_turn_complete(self, name: str, *, errored: bool = False) -> None:
        self.completed.append((name, errored))


class _LoopSession:
    def __init__(self, key: str) -> None:
        self.key = key
        self._app = "loop"
        self.task: Any = None
        self._last_turn_errored = False
        self._suppress_autonudge_rearm = False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def append(self, role: str, content: str, cls: str = "") -> None:
        return None


def _drive_one_cycle(tmp_path: Path, on_turn: Any) -> tuple[list[str], list[tuple[str, bool]]]:
    """Fire one cycle through the gateway's REAL cycle driver; return its turns and re-arms."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
    key = "loop-abcd1234"
    session = _LoopSession(key)
    dstate = MagicMock()
    dstate._sessions = {key: session}
    dstate._background_tasks = set()
    dstate.sessions.get_provider.return_value = MagicMock(start_fresh_turn_session=AsyncMock())
    orch.dashboard_state = dstate
    orch.loop_watchdog = None
    calls: list[str] = []

    async def _fake_run_chat(_state: Any, sess: Any, msg: str) -> None:
        calls.append(msg)
        on_turn(sess)

    nudge = MagicMock(
        id="N1", session_name=key, message="do one cycle", stop_sentinel_path="", cycle_count=0
    )

    async def _go() -> None:
        with (
            patch("personalclaw.gateway.autonudge_enabled", return_value=True),
            patch("personalclaw.gateway.AutoNudgeService", _FakeNudgeService),
            patch("personalclaw.dashboard.chat.run_chat", _fake_run_chat),
            patch("personalclaw.loop.files.get_findings", lambda _lid: []),
            patch("personalclaw.loop.files.loop_dir", lambda _lid: tmp_path),
        ):
            await orch._init_autonudge()
            svc = _FakeNudgeService.last
            svc.rows[key] = _Nudge("N1", key)
            # The driver re-arms through the module singleton, as the chat paths do.
            with patch("personalclaw.triggers.nudge._INSTANCE", svc):
                assert await svc.on_fire(nudge) is True
                await asyncio.wait_for(session.task, timeout=10)

    asyncio.run(_go())
    return calls, _FakeNudgeService.last.completed


def test_a_cycle_abandons_its_reprompts_once_incident_mode_is_on(tmp_path: Path) -> None:
    """A cycle whose turn wrote no finding re-prompts the worker up to three times — each one a
    fresh run of model calls. Thrown during the turn, the switch ends the cycle there."""
    calls, _rearms = _drive_one_cycle(tmp_path, lambda _sess: incident.activate("a drill"))
    assert len(calls) == 1, f"a held loop's cycle ran {len(calls)} turns"


def test_the_turn_the_hold_stopped_is_not_booked_as_the_workers_error(tmp_path: Path) -> None:
    """Three errored turns in a row switch a loop's cycles off for good, so the re-arm that ends a
    held cycle must not count the turn the hold stopped against the worker."""

    def _stopped_by_the_hold(sess: Any) -> None:
        incident.activate("a drill")
        sess._last_turn_errored = True  # what a runtime killed mid-call may report

    _calls, rearms = _drive_one_cycle(tmp_path, _stopped_by_the_hold)
    assert rearms == [("loop-abcd1234", False)]


# ── what the loop's page reads ──


def test_a_running_loops_views_say_it_is_held_and_why() -> None:
    from personalclaw.loop.loop import INCIDENT_HOLD

    loop = _running_loop()
    paused = store.create(Loop(id="", name="P", kind="goal", task="plan a picnic"))
    store.update_status(paused.id, LoopStatus.RUNNING)
    store.update_status(paused.id, LoopStatus.PAUSED)
    assert store.get_redacted(loop.id)["held"] == ""

    incident.activate("a drill")
    assert store.get_redacted(loop.id)["held"] == INCIDENT_HOLD
    rows = {row["id"]: row for row in store.list_redacted()}
    assert rows[loop.id]["held"] == INCIDENT_HOLD
    # Only a loop that would otherwise be working is held; a paused one already waits on its owner.
    assert rows[paused.id]["held"] == ""
    assert "incident mode" in INCIDENT_HOLD.lower() and "turned off" in INCIDENT_HOLD

    incident.resume()
    assert store.get_redacted(loop.id)["held"] == ""


# ── the planner: a loop's pre-launch passes ──


def test_a_planner_pass_waits_out_the_switch_without_timing_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planner runs unattended (it trusts every tool), so its turns are held with the rest. A
    held pass must neither report the time-out a hold is not, nor spend its time budget on it —
    it carries on once the switch is off. A planner turn running as the switch is thrown stops."""
    from personalclaw.planning import runner

    monkeypatch.setattr(runner, "PLANNER_POLL_SECS", 0.01)
    files_dir = tmp_path / "loopdir"
    files_dir.mkdir()
    planner = _Session("loop-plan-abcd1234", running=True)
    state = MagicMock()
    state._sessions = {planner.key: planner}
    state.sessions = _Sessions()
    svc = MagicMock()
    svc.add = AsyncMock()
    svc.remove = AsyncMock()
    svc.get_by_session.return_value = _Nudge("N1", planner.key)
    state.get_or_create_session.return_value = MagicMock(_extra_tool_roots=[])

    async def _go() -> str:
        incident.activate("a drill")
        task = asyncio.create_task(
            runner.run_planner_pass(
                state,
                svc,
                session_key=planner.key,
                agent_name="PersonalClaw",
                workspace_dir="",
                files_dir=str(files_dir),
                sentinel="plan_steps.json",
                brief="plan it",
                app="loops",
                timeout_secs=0.2,
            )
        )
        await asyncio.sleep(0.5)  # well past the pass's own time budget
        assert not task.done(), "a held planner pass timed out"
        incident.resume()
        (files_dir / "plan_steps.json").write_text('{"steps": []}')
        return (await asyncio.wait_for(task, timeout=5)).text

    assert asyncio.run(_go()) == '{"steps": []}'
    assert state.sessions.stopped == [f"dashboard:{planner.key}"]


# ── pages follow the switch ──


def test_the_watch_reports_a_flip_another_process_made(tmp_path: Path) -> None:
    """The CLI flips the flag from another process. The gateway's watch sees it and says so,
    once per flip, which is what lets an open page follow the switch without polling it."""
    seen: list[bool] = []

    async def _go() -> None:
        task = asyncio.create_task(incident.watch(lambda st: seen.append(st.active), interval=0.01))
        try:
            await asyncio.sleep(0.05)
            assert seen == [], "the watch reported a change nobody made"
            flag = tmp_path / "incident.json"
            flag.write_text(json.dumps({"active": True, "reason": "a drill", "started_at": "t"}))
            for _ in range(200):
                if seen:
                    break
                await asyncio.sleep(0.01)
            flag.write_text(json.dumps({"active": False, "reason": "", "started_at": ""}))
            for _ in range(200):
                if len(seen) > 1:
                    break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
        finally:
            task.cancel()

    asyncio.run(_go())
    assert seen == [True, False]


def test_the_gateway_tells_open_pages_when_the_switch_moves() -> None:
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_dashboard=False, no_crons=True, no_open=True)
    orch.dashboard_state = _State()

    async def _go() -> None:
        with patch.object(incident, "WATCH_INTERVAL_SECS", 0.01):
            orch._start_incident_watch()
            try:
                await asyncio.sleep(0.05)
                incident.activate("a drill")
                for _ in range(200):
                    if orch.dashboard_state.refreshed:
                        break
                    await asyncio.sleep(0.01)
            finally:
                orch._incident_watch_task.cancel()

    asyncio.run(_go())
    assert orch.dashboard_state.refreshed == [("incident", "loops")]


# ── what a stopped loop turn still set off ──


@pytest.mark.parametrize("app", ["loop", "loops"])
def test_no_follow_up_chips_are_generated_for_a_loops_hidden_session(app: str) -> None:
    """Every chat turn ends by asking the background model for follow-up chips — a loop's
    worker and planner turns too, where no one ever reads them. So each cycle spent a model call
    for nothing, and each turn a hold stopped set one off: measured, the call reached the model
    the moment the held turn ended, with incident mode on."""
    from personalclaw.dashboard import chat_followups

    session = MagicMock(
        key="chat-1", memory_mode="persistent", _app=app, _queue=[], _last_turn_errored=False
    )
    state = MagicMock(conversation_log=None)
    with (
        patch.object(chat_followups, "_followups_enabled", return_value=True),
        patch.object(chat_followups, "_generate_followups", new=AsyncMock(return_value=[])) as gen,
    ):
        asyncio.run(chat_followups._maybe_followups(state, session))
        gen.assert_not_awaited()
        # A person's chat still gets them.
        session._app = "chat"
        asyncio.run(chat_followups._maybe_followups(state, session))
        gen.assert_awaited_once()
