"""What waits on you interrupts you only when it may: one decision, the gateway's (#3888).

Every item of the waiting list carries ``interrupt``, decided once from mute, quiet hours and its
kind's notification rule (``waiting.interrupt_for``): ``deliver`` when it may interrupt now,
``quiet`` or ``hush`` when not now, ``drop`` when not at all. An approval is pushed to the phone
and asked on the channel you chose for approvals only when that decision is ``deliver``, and its
registry row and frame carry it, so the dashboard's toast and cue follow it too
(``useApprovalToasts``). It was pushed whatever mute and quiet hours said.

The clock is the gateway's own (``waiting._now``), set inside and outside a quiet-hours window by
the window's own moments (``entity_routes.quiet_window_moments``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from personalclaw import push, waiting
from personalclaw.approval_answer import YOU
from personalclaw.config.loader import config_dir
from personalclaw.providers import entity_routes

QUIET_HOURS = {
    "quiet_hours_enabled": True,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00",
}


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(root))
    assert config_dir() == root.resolve()
    return root


def _settings(home: Path, name: str, doc: dict[str, Any]) -> None:
    folder = home / "entity_settings"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(doc))


def _approvals_rule(home: Path, mode: str) -> None:
    """The Approval needed row, reaching the phone and the channel chosen for approvals."""
    rule = {"mode": mode, "targets": ["dashboard", "push", "channel_dm"]}
    _settings(home, "notification_rules", {"rules": {"approval/requested": rule}})


def _at(monkeypatch: pytest.MonkeyPatch, moment: Any) -> None:
    monkeypatch.setattr(waiting, "_now", lambda: moment.timestamp())


def _state():  # noqa: ANN201
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.dashboard.state import DashboardState

    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(sessions=sessions, start_time=0.0)


class _Asked:
    """What an approval reached: the phone, and whether the channel chosen for approvals asked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, state: Any) -> None:
        self.pushed: list[str] = []
        self.channel_dm: list[bool] = []
        monkeypatch.setattr(push, "deliver_async", lambda kind, item: self.pushed.append(item))
        real = state._asking_channels

        def asking(approval_id: str, channel_dm: bool) -> list[str]:
            self.channel_dm.append(channel_dm)
            return real(approval_id, channel_dm)

        monkeypatch.setattr(state, "_asking_channels", asking)


async def _ask(state: Any, approval_id: str) -> asyncio.Task:
    task = asyncio.create_task(state.request_approval(approval_id, source="cron", tool="Bash"))
    for _ in range(200):
        if approval_id in state._pending_approvals:
            return task
        await asyncio.sleep(0.005)
    raise AssertionError(f"{approval_id} was never asked")


@pytest.mark.asyncio
async def test_under_mute_an_approval_is_not_pushed_and_still_answers(home, monkeypatch):
    _approvals_rule(home, "immediate")
    _settings(home, "notifications", {"mute_all": True})
    state = _state()
    asked = _Asked(monkeypatch, state)

    pending = await _ask(state, "a-muted")
    entry = state._pending_approvals["a-muted"]
    assert entry["interrupt"] == "drop", "its row and frame say it may not interrupt"
    assert asked.pushed == [], "muted, the phone is not woken"
    assert asked.channel_dm == [False], "nor is the channel chosen for approvals asked"

    assert state.resolve_approval("a-muted", True, by=YOU) is True
    assert await asyncio.wait_for(pending, timeout=5) is True, "and it is answered as ever"


@pytest.mark.asyncio
async def test_inside_quiet_hours_an_approval_is_quiet_and_outside_them_it_pings(home, monkeypatch):
    _approvals_rule(home, "immediate")
    _settings(home, "notifications", QUIET_HOURS)
    inside, outside = entity_routes.quiet_window_moments(QUIET_HOURS)
    state = _state()
    asked = _Asked(monkeypatch, state)
    tasks = []
    try:
        _at(monkeypatch, inside)
        tasks.append(await _ask(state, "a-night"))
        assert state._pending_approvals["a-night"]["interrupt"] == "hush"
        assert asked.pushed == [] and asked.channel_dm == [False], "not now: kept, not pinged"

        _at(monkeypatch, outside)
        tasks.append(await _ask(state, "a-day"))
        assert state._pending_approvals["a-day"]["interrupt"] == "deliver"
        assert asked.pushed == ["a-day"] and asked.channel_dm == [False, True]
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "said"), [("badge", "quiet"), ("digest", "quiet"), ("never", "drop")]
)
async def test_a_rule_that_keeps_approvals_quiet_wakes_no_phone(home, monkeypatch, mode, said):
    _approvals_rule(home, mode)
    state = _state()
    asked = _Asked(monkeypatch, state)
    pending = await _ask(state, "a-rule")
    try:
        assert state._pending_approvals["a-rule"]["interrupt"] == said
        assert asked.pushed == [] and asked.channel_dm == [False]
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


def _items(snap: waiting.Snapshot) -> dict[str, str]:
    with patch("personalclaw.loop.store.list_all", return_value=[]):
        items, _ = waiting.read(snap)
    return {i["kind"]: i["interrupt"] for i in items if i["kind"] in ("approval", "question")}


def test_every_item_says_whether_it_may_interrupt_now(home, monkeypatch):
    _settings(home, "notifications", QUIET_HOURS)
    inside, outside = entity_routes.quiet_window_moments(QUIET_HOURS)

    def snap() -> waiting.Snapshot:
        now = waiting._now()
        return waiting.Snapshot(
            approvals=[{"id": "a1", "session": "", "tool": "shell", "ts": now - 5}],
            questions=[{"id": "q1", "session": "c1", "ts": now - 5, "questions": []}],
            window=3600.0,
        )

    _at(monkeypatch, outside)
    assert _items(snap()) == {"approval": "deliver", "question": "deliver"}
    _at(monkeypatch, inside)
    assert _items(snap()) == {"approval": "hush", "question": "quiet"}, "listed, not pinged"
    _settings(home, "notifications", {**QUIET_HOURS, "mute_all": True})
    assert _items(snap()) == {"approval": "drop", "question": "drop"}
    assert set(waiting.ITEM_FIELDS) >= {"interrupt"}
