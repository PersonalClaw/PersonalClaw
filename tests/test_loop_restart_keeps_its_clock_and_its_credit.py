"""A gateway restart neither resets a running loop's clock nor re-credits a cycle it credited.

Measured 2026-09-25, one Ctrl-C and restart of a gateway with a running Goal loop:

* the cockpit's elapsed time fell from 17m to 2m — the restart re-armed the loop through
  `manager.start`, whose RUNNING -> RUNNING write re-stamped `started_at` and threw the running
  stretch away. The same stamp is what the `deadline_secs` ceiling and the TRUST WINDOW (the
  expiry of an unattended loop's auto-approval grant) are measured from, so a restart silently
  renewed both;
* "Cycle 1/30 complete" was notified twice — the watchdog's credited-cycle baseline lived only in
  memory, and a fresh process seeded it at 0, so the latest finding was credited again: a second
  notification, a second done-ness judgement, a second run of the kind's per-cycle hook.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from personalclaw.loop import files as loop_files
from personalclaw.loop import manager, store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    import personalclaw.tasks.native as nat

    monkeypatch.setattr(nat, "config_dir", lambda: tmp_path, raising=False)
    return tmp_path


class _Session:
    def __init__(self, key: str) -> None:
        self.key = key
        self._trust = False
        self._running = False
        self.acp_provider = ""
        self.acp_provider_agent = ""
        self.reasoning_effort = ""
        self.acp_mode = ""

    @property
    def running(self) -> bool:
        return self._running


class _State:
    """What `manager.start` (the re-arm) and `LoopWatchdog._poll_once` reach."""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self.events: list[str] = []

    def get_or_create_session(
        self,
        *,
        name: str,
        agent: Any,
        model: Any,
        workspace_dir: Any,
        app: str,
        project_id: str = "",
    ) -> _Session:
        s = self._sessions.get(name) or _Session(name)
        self._sessions[name] = s
        return s

    def push_sessions_update(self) -> None:
        return None

    def push_refresh(self, *kinds: str) -> None:
        return None

    def notify(self, *_: Any, **__: Any) -> None:
        return None

    def loop_sse(self) -> Any:
        state = self

        class _Sse:
            def publish(self, _key: str, event: str, _data: Any) -> None:
                state.events.append(event)

        return _Sse()


class _Nudge:
    def __init__(self, lid: str, session_name: str) -> None:
        self.id, self.session_name, self.active, self.cycle_count = lid, session_name, True, 0


class _Svc:
    def __init__(self) -> None:
        self._rows: dict[str, _Nudge] = {}

    async def add(self, *, session_name: str, **_: Any) -> _Nudge:
        for lid in [k for k, v in self._rows.items() if v.session_name == session_name]:
            del self._rows[lid]
        row = _Nudge(f"N{len(self._rows) + 1}", session_name)
        self._rows[row.id] = row
        return row

    def get_by_session(self, session_name: str) -> _Nudge | None:
        return next((r for r in self._rows.values() if r.session_name == session_name), None)

    def list_all(self) -> list[_Nudge]:
        return list(self._rows.values())

    async def update(self, loop_id: str, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self._rows[loop_id], k, v)

    async def remove(self, loop_id: str) -> None:
        self._rows.pop(loop_id, None)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _running_loop(**over: Any) -> Loop:
    base: dict[str, Any] = dict(
        id="",
        name="Packing",
        kind="goal",
        task="write a packing note for a weekend trip",
        kind_config={"goal_type": "verifiable", "verify_command": "false"},
        max_cycles=30,
    )
    base.update(over)
    loop = store.create(Loop(**base))
    return store.update_status(loop.id, LoopStatus.RUNNING)


# ── the clock ──


def test_running_to_running_keeps_the_stretch() -> None:
    """At `origin/main` the second write re-stamps `started_at` to now."""
    loop = _running_loop()
    stretch_start = time.time() - 17 * 60
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=stretch_start)
    after = store.update_status(loop.id, LoopStatus.RUNNING)
    assert after.started_at == stretch_start, after.started_at
    assert W.active_runtime_secs(after, time.time()) >= 17 * 60


def test_a_pause_still_banks_and_a_resume_still_starts_a_new_stretch() -> None:
    """Controls: leaving RUNNING banks the stretch, re-entering it from a pause starts one."""
    loop = _running_loop()
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=time.time() - 600)
    paused = store.update_status(loop.id, LoopStatus.PAUSED)
    assert paused.elapsed_seconds >= 600
    before_resume = time.time()
    resumed = store.update_status(loop.id, LoopStatus.RUNNING)
    assert resumed.started_at >= before_resume
    assert resumed.elapsed_seconds == paused.elapsed_seconds


def test_a_restart_re_arm_continues_the_running_stretch() -> None:
    """The real path: the boot sweep re-arms a RUNNING loop whose worker died with the process."""
    loop = _running_loop()
    stretch_start = time.time() - 17 * 60
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=stretch_start)
    state, svc = _State(), _Svc()  # a new process: no worker session
    assert _run(W.LoopWatchdog(state, svc)._boot_sweep()) == {loop.id}
    assert svc.get_by_session(manager.session_key(loop.id)) is not None, "not re-armed"
    after = store.get(loop.id)
    assert after.started_at == stretch_start, "the re-arm threw the running stretch away"


def test_a_restart_does_not_renew_an_expired_trust_window() -> None:
    """The unattended auto-approval grant expires `trust_ttl_secs` after it was given. At
    `origin/main` a restart re-armed the loop with a fresh `started_at`, so the expiry check on the
    very next poll passed and the grant ran for another full window without the user."""
    from personalclaw.config.loader import AppConfig

    ttl = AppConfig.load().loops.trust_ttl_secs
    loop = _running_loop()
    store.update_status(loop.id, LoopStatus.RUNNING, started_at=time.time() - ttl - 60)
    state, svc = _State(), _Svc()
    _run(W.LoopWatchdog(state, svc)._poll_once())  # boot sweep re-arms, then the checks run
    after = store.get(loop.id)
    assert after.status == LoopStatus.NEEDS_INPUT.value, after.status
    assert state._sessions[manager.session_key(loop.id)]._trust is False


# ── the credit ──


def _finding(loop_id: str, cycle: int) -> None:
    import json

    d = loop_files.loop_dir(loop_id)
    (d / "findings" / f"cycle_{cycle:03d}.json").write_text(
        json.dumps({"cycle": cycle, "new_findings_count": 1})
    )


def _live(loop: Loop) -> tuple[_State, _Svc]:
    """A process whose worker for ``loop`` is armed: its session AND its nudge row."""
    state, svc = _State(), _Svc()
    key = manager.session_key(loop.id)
    state._sessions[key] = _Session(key)
    svc._rows["N1"] = _Nudge("N1", key)
    return state, svc


def test_a_restart_does_not_re_credit_a_cycle() -> None:
    """At `origin/main` the second process credits cycle 1 again: two `new_finding` events."""
    loop = _running_loop()
    first, svc = _live(loop)
    wd = W.LoopWatchdog(first, svc)
    _run(wd._poll_once())
    _finding(loop.id, 1)
    _run(wd._poll_once())
    assert first.events.count("new_finding") == 1, first.events

    second, svc2 = _live(loop)  # the restarted process; the worker is live again
    wd2 = W.LoopWatchdog(second, svc2)
    _run(wd2._poll_once())
    _run(wd2._poll_once())
    assert second.events.count("new_finding") == 0, f"cycle 1 credited again: {second.events}"
    assert store.get(loop.id).status == LoopStatus.RUNNING.value


def test_after_a_restart_the_next_cycle_is_still_credited() -> None:
    """Positive control: the durable baseline must not swallow the NEXT real cycle."""
    loop = _running_loop()
    first, svc = _live(loop)
    wd = W.LoopWatchdog(first, svc)
    _run(wd._poll_once())
    _finding(loop.id, 1)
    _run(wd._poll_once())

    second, svc2 = _live(loop)
    wd2 = W.LoopWatchdog(second, svc2)
    _run(wd2._poll_once())
    _finding(loop.id, 2)
    _run(wd2._poll_once())
    assert second.events.count("new_finding") == 1, second.events


def test_a_fast_first_cycle_is_still_credited_on_first_sight() -> None:
    """The baseline seeds at 0 when nothing was ever credited — a finding already on disk at the
    first poll is a cycle to credit, not one to absorb."""
    loop = _running_loop()
    _finding(loop.id, 1)
    state, svc = _live(loop)
    _run(W.LoopWatchdog(state, svc)._poll_once())
    assert state.events.count("new_finding") == 1, state.events


def test_a_baseline_above_the_findings_on_disk_cannot_stop_crediting() -> None:
    """The record lives in the worker-writable loop dir; an inflated one is capped at what is."""
    loop = _running_loop()
    loop_files.write_credited_cycles(loop.id, 99)
    state, svc = _live(loop)
    wd = W.LoopWatchdog(state, svc)
    _run(wd._poll_once())
    _finding(loop.id, 1)
    _run(wd._poll_once())
    assert state.events.count("new_finding") == 1, state.events
