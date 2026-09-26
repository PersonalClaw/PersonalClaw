"""A loop that is created, changes status or is deleted says so to every open dashboard.

The surfaces that show loops (Home's work, the agent-activity feed, the rail badge) re-read on
the gateway's `loops` refresh hint and poll only as a slow safety net, so an idle tab stops
flooding the gateway. That is only safe if the hint is sent, and pause, stop, start and create
sent none (only delete, the queue, autopilot and the watchdog did). So the loop store's own
writes send it: one seam, every caller.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.loop import store as loop_store
from personalclaw.loop.loop import Loop, LoopStatus


class _Dashboard:
    def __init__(self) -> None:
        self.hints: list[tuple[str, ...]] = []

    def push_refresh(self, *kinds: str) -> None:
        self.hints.append(kinds)


@pytest.fixture
def dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Dashboard:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    from personalclaw.inbox_providers import native_source

    board = _Dashboard()
    monkeypatch.setattr(native_source, "_dashboard_state", board, raising=False)
    return board


def test_creating_a_loop_is_announced(dashboard):
    loop_store.create(Loop(id="", kind="goal", name="G", task="investigate the regression"))
    assert ("loops",) in dashboard.hints


@pytest.mark.parametrize("status", [LoopStatus.RUNNING, LoopStatus.PAUSED, LoopStatus.STOPPED])
def test_every_status_change_is_announced(dashboard, status):
    loop = loop_store.create(Loop(id="", kind="goal", name="G", task="investigate the regression"))
    dashboard.hints.clear()
    loop_store.update_status(loop.id, status)
    assert dashboard.hints == [("loops",)]


def test_deleting_a_loop_is_announced_and_a_missing_one_is_not(dashboard):
    loop = loop_store.create(Loop(id="", kind="goal", name="G", task="investigate the regression"))
    dashboard.hints.clear()
    assert loop_store.delete(loop.id) is True
    assert dashboard.hints == [("loops",)]
    assert loop_store.delete(loop.id) is False
    assert dashboard.hints == [("loops",)], "nothing changed, so nothing is announced"
