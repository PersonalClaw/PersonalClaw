"""Updating an auto-nudge that stopped at its Max cycles with cycles to spare starts it again.

Measured before this was written: a nudge that fired its Max cycles switched itself off and was
kept, and opening it and pressing Update after raising Max cycles saved the new limit but left it
stopped, so the owner had to find another way to start it. Now an update that leaves a nudge that
ran out with cycles to spare (a higher limit than it has fired, or no limit) switches it back on,
and it carries on from the cycles it has fired.

A nudge stays off when the update leaves it no cycles, when it was switched off for another
reason (the owner's stop, its turns failing), and when it is an imported one still waiting for
the owner to switch it on.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import idle_poll as IP
from personalclaw.triggers import nudge as N
from personalclaw.triggers.legacy_import import IMPORTED_BY
from personalclaw.triggers.nudge import AutoNudgeService
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_AUTONUDGE", "1")
    yield
    N._INSTANCE = None


@pytest.fixture
def svc(tmp_path):
    return AutoNudgeService(base_dir=tmp_path)


async def _ran_out(svc: AutoNudgeService, tmp_path, *, cycles: int = 2) -> str:
    """A nudge that fired its *cycles* and switched itself off, as ``deliver`` leaves it."""
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15, max_cycles=cycles)
    state = IP.load_state(loop.id, base_dir=tmp_path)
    state.armed_at = NOW
    state.cycle_count = cycles
    IP.save_state(loop.id, state, base_dir=tmp_path)
    trigger = TriggerStore(base_dir=tmp_path).get(loop.id)
    assert trigger is not None
    assert await svc.deliver(trigger.trigger, now=NOW + 16) == (False, "max_cycles")
    assert svc.get_by_session("chat-1-123").active is False
    return loop.id


async def test_a_higher_limit_starts_it_again_from_the_cycles_it_fired(svc, tmp_path):
    """🔴 Red before: the new limit was saved and the nudge stayed stopped."""
    loop_id = await _ran_out(svc, tmp_path)

    loop = await svc.update(loop_id, message="go", idle_secs=15, max_cycles=5)

    assert loop.active is True
    assert (loop.cycle_count, loop.max_cycles) == (2, 5)


async def test_no_limit_starts_it_again(svc, tmp_path):
    """🔴 Red before, as above."""
    loop_id = await _ran_out(svc, tmp_path)

    assert (await svc.update(loop_id, max_cycles=0)).active is True


async def test_an_update_that_leaves_it_no_cycles_keeps_it_stopped(svc, tmp_path):
    loop_id = await _ran_out(svc, tmp_path)

    assert (await svc.update(loop_id, message="carry on", max_cycles=2)).active is False
    assert (await svc.update(loop_id, max_cycles=1)).active is False


async def test_a_nudge_the_owner_stopped_stays_stopped(svc, tmp_path):
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=15, max_cycles=2)
    await svc.update(loop.id, active=False)

    assert (await svc.update(loop.id, max_cycles=5)).active is False


async def test_an_explicit_stop_in_the_same_update_wins(svc, tmp_path):
    loop_id = await _ran_out(svc, tmp_path)

    assert (await svc.update(loop_id, max_cycles=5, active=False)).active is False


async def test_an_imported_nudge_waiting_for_its_owner_stays_off(svc, tmp_path):
    loop_id = await _ran_out(svc, tmp_path)
    store = TriggerStore(base_dir=tmp_path)
    row = store.get(loop_id).trigger
    row.created_by = IMPORTED_BY
    store.upsert(row)

    assert (await svc.update(loop_id, max_cycles=5)).active is False
