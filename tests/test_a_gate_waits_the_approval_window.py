"""A workflow approval gate waits the owner's approval window, like every other approval.

A background run's gate failed after 45 seconds with nobody's answer ("gate timed out with no
answer"). Background is how a run from the Workflows page, a trigger or a project runs, so the
owner who started one and switched to Mission Control got a failed run before they could press
Approve, while a tool approval in the same run waited the window they chose in Settings
(`agent.approval_timeout_minutes`, two hours by default). The 45 seconds predates that setting; its
reason was that a run nobody watches must not park forever, and a window is not forever.

What keeps the short wait is the case it was written for: a run started UNATTENDED (the explicit
`attended: false` grant) has nobody there to answer, so its gate still fails fast and the run
says so rather than wedging. An explicit `timeout_secs` on the gate still wins.
"""

from __future__ import annotations

import json

import pytest

import personalclaw.config.loader as loader
from personalclaw.workflows import engine
from personalclaw.workflows import human_input as HI
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.models import InstanceState, Node


def _window(minutes: int) -> None:
    path = loader.config_dir() / "config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault("agent", {})["approval_timeout_minutes"] = minutes
    path.write_text(json.dumps(data))


def test_a_gate_in_a_run_someone_started_waits_the_window():
    """Background or blocking alike: who is watching decides the wait, not the run's mode."""
    _window(90)
    assert HI.gate_timeout_secs({}) == 90 * 60


def test_a_change_to_the_window_reaches_the_next_gate():
    _window(90)
    assert HI.gate_timeout_secs({}) == 90 * 60
    _window(5)
    assert HI.gate_timeout_secs({}) == 5 * 60


def test_an_unattended_run_s_gate_still_fails_fast():
    """Nobody is there to answer, which is the one case the short wait was for."""
    _window(90)
    assert HI.gate_timeout_secs({}, unattended=True) == HI.UNATTENDED_GATE_TIMEOUT_SECS


@pytest.mark.parametrize("unattended", [False, True])
def test_an_explicit_timeout_still_wins(unattended):
    _window(90)
    assert HI.gate_timeout_secs({"timeout_secs": 7}, unattended=unattended) == 7
    assert HI.gate_timeout_secs({"timeout_secs": 0}, unattended=unattended) == 0


def _approval_gate() -> Node:
    return Node.from_dict({"kind": "gate", "id": "ship", "config": {"kind": "approval"}})


@pytest.mark.asyncio
async def test_the_gate_the_engine_parks_waits_the_window():
    """Through the dispatcher the run uses, not only the helper."""
    _window(90)
    result = await engine.dispatch_gate(_approval_gate(), BindingContext(), now=1000.0)
    assert result.state is InstanceState.WAITING
    assert result.wake_at == 1000.0 + 90 * 60


@pytest.mark.asyncio
async def test_an_unattended_run_s_gate_parks_on_the_short_wait():
    _window(90)
    result = await engine.dispatch_gate(
        _approval_gate(), BindingContext(), now=1000.0, unattended=True
    )
    assert result.wake_at == 1000.0 + HI.UNATTENDED_GATE_TIMEOUT_SECS
