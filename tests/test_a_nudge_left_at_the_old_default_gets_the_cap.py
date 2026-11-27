"""An auto-nudge whose no-limit came from the old default stops after 10 cycles like a new one.

v0.3.9 gave a nudge armed without a number a limit of 10 cycles, and kept an owner's explicit 0
(no limit). A nudge stored before it with 0 got that 0 from the old default, not from its owner,
but read the same as an explicit 0 and kept running without a cap after the upgrade.

Now a 0 the owner chooses (Auto-nudge's Max cycles, ``POST /api/autonudge``, ``PATCH``) is stored
with ``no_limit``, and the gateway's boot pass gives a nudge left at 0 without it the default
limit. A nudge with a limit of its own, an explicit no limit, and an idle trigger that is not a
nudge are left as they are.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers import boot_migrate
from personalclaw.triggers import nudge as N
from personalclaw.triggers.nudge import DEFAULT_MAX_CYCLES, AutoNudgeService
from personalclaw.triggers.store import TriggerStore

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_AUTONUDGE", "1")
    yield
    N._INSTANCE = None


def _stored_before_the_cap(tmp_path, *, max_cycles: int = 0, message: str = "go") -> str:
    """A nudge row as a release before v0.3.9 stored it: its 0 is the old default."""
    row = N._nudge_row(
        loop_id="1a2b3c4d",
        session_name="chat-1-123",
        message=message,
        idle_secs=60,
        max_cycles=max_cycles,
        stop_sentinel_path="",
        first_idle_secs=0,
    )
    TriggerStore(base_dir=tmp_path).upsert(row)
    return row.id


def _max_cycles(tmp_path, loop_id: str) -> int:
    return int(TriggerStore(base_dir=tmp_path).get(loop_id).trigger.spec.get("max_cycles", 0))


async def test_a_nudge_stored_with_the_old_default_gets_the_cap_at_boot(tmp_path):
    """🔴 Red before: it kept 0 and ran without a cap."""
    loop_id = _stored_before_the_cap(tmp_path)

    boot_migrate.migrate_and_arm(tmp_path)

    assert _max_cycles(tmp_path, loop_id) == DEFAULT_MAX_CYCLES
    assert AutoNudgeService(base_dir=tmp_path).get_by_session("chat-1-123").max_cycles == 10


async def test_a_second_boot_changes_nothing_more(tmp_path):
    """🔴 Red before, as above."""
    loop_id = _stored_before_the_cap(tmp_path)
    boot_migrate.migrate_and_arm(tmp_path)
    boot_migrate.migrate_and_arm(tmp_path)

    assert _max_cycles(tmp_path, loop_id) == DEFAULT_MAX_CYCLES


async def test_no_limit_the_owner_chooses_is_kept_across_a_boot(tmp_path):
    svc = AutoNudgeService(base_dir=tmp_path)
    armed = await svc.add(session_name="chat-1-123", message="go", idle_secs=60, max_cycles=0)
    edited = await svc.add(session_name="chat-2-456", message="go", idle_secs=60, max_cycles=3)
    await svc.update(edited.id, max_cycles=0)

    boot_migrate.migrate_and_arm(tmp_path)

    assert _max_cycles(tmp_path, armed.id) == 0
    assert _max_cycles(tmp_path, edited.id) == 0


async def test_a_limit_chosen_after_no_limit_is_kept(tmp_path):
    svc = AutoNudgeService(base_dir=tmp_path)
    loop = await svc.add(session_name="chat-1-123", message="go", idle_secs=60, max_cycles=0)
    await svc.update(loop.id, max_cycles=4)
    await svc.update(loop.id, message="carry on")

    boot_migrate.migrate_and_arm(tmp_path)

    assert _max_cycles(tmp_path, loop.id) == 4


async def test_a_nudge_with_a_limit_of_its_own_and_a_wake_trigger_are_left_alone(tmp_path):
    own = _stored_before_the_cap(tmp_path, max_cycles=3)
    store = TriggerStore(base_dir=tmp_path)
    wake = N._nudge_row(
        loop_id="5e6f7a8b",
        session_name="chat-9-999",
        message="",
        idle_secs=60,
        max_cycles=0,
        stop_sentinel_path="",
        first_idle_secs=0,
    )
    store.upsert(wake)

    boot_migrate.migrate_and_arm(tmp_path)

    assert _max_cycles(tmp_path, own) == 3
    assert _max_cycles(tmp_path, wake.id) == 0
