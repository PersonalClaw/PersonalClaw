"""WF2AUT-16 — a run orphaned by a gateway restart reads as interrupted AT BOOT.

🔴 THE DEFECT. `guardrails/self_destruct.py` states it in its own words: when the runner dies
mid-flight *"the ScheduleRunStore row never reaches a terminal state and the fire reads afterwards
as a HUNG run rather than as a self-inflicted stop. The user is left debugging a phantom."* It was
BOUNDED but only by a deadline: `reaper.overdue` compares `now - claimed_at` against
`RUN_DEADLINE_SECS` (1800), so a claim whose owning process died two seconds ago survives for the
next half hour. Measured before this pass existed: `sweep_once` over a claim whose pid was gone,
one second old, returned `[]`. `git grep -riE 'orphan|interrupted' -- src/personalclaw/triggers/`
found no restart-orphan concept at all. So the FIRST post-restart answer to "did it run?" was
wrong-but-eventually-corrected — the worst kind, because it is the answer a user reads.

🪤 THE VACUOUS VERSION OF THIS TEST writes a claim carrying a pid that was never alive (0, or a
short-lived writer's own pid) and asserts it gets terminalized. That passes for a pass that
terminalizes EVERYTHING, which is strictly worse than the deadline it replaces — it would free the
claim of a run that is still going and record it as interrupted while it works. This org has
already shipped exactly that shape once: a liveness check that measured the pid of the short-lived
CLI writer and so was always true.

So every discrimination test here uses **two real subprocesses**: one killed, one still running,
their pids taken from the OS rather than invented. The pass must terminalize the first and leave
the second alone, in one sweep, from one directory of claims.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from personalclaw.triggers import claims, reaper
from personalclaw.triggers.models import Trigger, TriggerHealth
from personalclaw.triggers.scheduling import Claim
from personalclaw.triggers.store import TriggerStore

# ── helpers: real processes, real pids ──


def _live_process() -> subprocess.Popen:
    """A real child that will still be running when the sweep reads its pid."""
    return subprocess.Popen(  # noqa: S603 - a fixed argv, no shell
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _dead_pid() -> int:
    """The pid of a child that HAS EXITED and been reaped — provably gone, never recycled by us."""
    proc = subprocess.Popen(  # noqa: S603 - a fixed argv, no shell
        [sys.executable, "-c", ""],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=30)
    return proc.pid


def _claim(trigger_id: str, *, pid: int, base_dir, age_secs: float = 1.0) -> None:
    claims.write_claim(
        Claim(
            trigger_id=trigger_id,
            holder=f"tick:{int(time.time())}",
            claimed_at=time.time() - age_secs,
            owner_pid=pid,
        ),
        base_dir=base_dir,
    )


def _store(tmp_path) -> TriggerStore:
    store = TriggerStore(base_dir=tmp_path)
    return store


def _trigger(store: TriggerStore, trigger_id: str) -> Trigger:
    trigger = Trigger(id=trigger_id, name=trigger_id, kind="clock", enabled=True)
    store.upsert(trigger)
    return trigger


# ── the closed run-status set the terminal row must stay inside ──


def test_schedule_run_status_still_has_exactly_four_members():
    """The atom's explicit constraint: do NOT mint a fifth `ScheduleRun.status` value.

    The set is consumed by `web/src/pages/schedule/scheduleMeta.ts` and pinned by
    `reapedRunReadsAsFailure.test.ts`; a fifth member would render as "never run" grey on every
    surface that has not learned it. The interrupted-by-restart distinction is carried in the
    row's `error`, not in a new status.
    """
    import inspect

    from personalclaw import schedule_history

    source = inspect.getsource(schedule_history.ScheduleRun)
    quoted = {
        word
        for word in ("success", "failure", "timeout", "launched", "interrupted", "orphaned")
        if f'"{word}"' in source
    }
    assert quoted == {"success", "failure", "timeout", "launched"}, (
        f"`ScheduleRun` documents the status values {sorted(quoted)}. The closed set is "
        "success|failure|timeout|launched — carry an interrupted run in the row's `error`."
    )
    assert reaper.RESTART_INTERRUPTED_STATUS in {"success", "failure", "timeout", "launched"}


# ── the boot pass, and its pid discrimination ──


def test_the_boot_pass_terminalizes_a_claim_whose_owner_is_gone(tmp_path):
    """RED FIRST: today the claim survives until the reaper deadline, 1800s away."""
    store = _store(tmp_path)
    _trigger(store, "orphan")
    _claim("orphan", pid=_dead_pid(), base_dir=tmp_path, age_secs=1.0)

    # The pre-existing terminalizer cannot see it: no deadline has elapsed.
    assert reaper.overdue(base_dir=tmp_path) == []
    assert claims.is_running("orphan", base_dir=tmp_path) is True

    records = reaper.terminalize_orphans_sync(store=store, base_dir=tmp_path)

    assert [r["trigger_id"] for r in records] == ["orphan"]
    assert records[0]["released"] is True
    assert claims.is_running("orphan", base_dir=tmp_path) is False


def test_the_boot_pass_leaves_a_claim_whose_owner_is_STILL_RUNNING(tmp_path):
    """🪤 The vacuity floor, and the whole reason this atom is easy to get wrong.

    One sweep, one claims directory, two claims: one owner exited, one owner is a live child
    process. A pass that terminalizes both would free a claim the running process still holds and
    record its run as interrupted while it works — a regression, not a fix.
    """
    store = _store(tmp_path)
    _trigger(store, "orphan")
    _trigger(store, "still-running")
    proc = _live_process()
    try:
        _claim("orphan", pid=_dead_pid(), base_dir=tmp_path, age_secs=1.0)
        _claim("still-running", pid=proc.pid, base_dir=tmp_path, age_secs=1.0)

        records = reaper.terminalize_orphans_sync(store=store, base_dir=tmp_path)

        assert [r["trigger_id"] for r in records] == ["orphan"]
        assert claims.is_running("orphan", base_dir=tmp_path) is False
        assert claims.is_running("still-running", base_dir=tmp_path) is True, (
            "the boot pass freed a claim whose owning process is alive — it measured something "
            "other than the owner's liveness"
        )
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_killing_the_owner_is_what_flips_the_verdict(tmp_path):
    """The same claim, the same file, the same pass — only the process changed.

    Holding everything else fixed is what makes this a measurement of liveness rather than of
    whichever incidental property a passing test might have latched onto.
    """
    store = _store(tmp_path)
    _trigger(store, "mid-run")
    proc = _live_process()
    _claim("mid-run", pid=proc.pid, base_dir=tmp_path, age_secs=1.0)

    assert reaper.terminalize_orphans_sync(store=store, base_dir=tmp_path) == []
    assert claims.is_running("mid-run", base_dir=tmp_path) is True

    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)

    records = reaper.terminalize_orphans_sync(store=store, base_dir=tmp_path)
    assert [r["trigger_id"] for r in records] == ["mid-run"]
    assert claims.is_running("mid-run", base_dir=tmp_path) is False


def test_an_unknown_owner_is_left_to_the_deadline(tmp_path):
    """`owner_pid` absent means "we cannot tell", which is NOT "provably gone".

    Fail-safe direction, and it is also what stops this pass collapsing into "terminalize every
    claim at boot": a record with no owner falls back to the 1800s deadline the reaper already
    enforces rather than being freed on a guess.
    """
    store = _store(tmp_path)
    _trigger(store, "unknown-owner")
    path = tmp_path / "trigger-claims" / "unknown-owner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"trigger_id": "unknown-owner", "holder": "tick:1", '
        f'"claimed_at": {time.time() - 1.0}, "max_duration_secs": 3600.0}}',
        encoding="utf-8",
    )

    assert reaper.terminalize_orphans_sync(store=store, base_dir=tmp_path) == []
    assert claims.is_running("unknown-owner", base_dir=tmp_path) is True


def test_the_claim_records_the_pid_of_the_process_that_grants_it(tmp_path):
    """A `Claim` stamps its creator, and `read_claim` reads the STORED pid back.

    🪤 The trap on the read side: rebuilding a `Claim` from disk without passing `owner_pid`
    through lets the dataclass default stamp the READER's pid, so every claim on the machine reads
    as owned by whoever asked — and the boot pass then never terminalizes anything. That is the
    same always-true liveness check as the vacuous write side, in the mirror.
    """
    assert Claim(trigger_id="t", holder="h", claimed_at=time.time()).owner_pid == os.getpid()

    dead = _dead_pid()
    _claim("t", pid=dead, base_dir=tmp_path)
    assert claims.read_claim("t", base_dir=tmp_path).owner_pid == dead


# ── what "terminal" means on the surfaces a user reads ──


@pytest.mark.asyncio
async def test_the_terminal_row_says_interrupted_by_a_restart_not_timed_out(tmp_path):
    """The run row the schedule detail's `Last run` section reads.

    `status` stays inside the closed four-member set; the distinction the atom asks for rides in
    `error`, which `ScheduleDetail`'s `Last run` block and `RunHistory` both already render.
    """
    from personalclaw.schedule_history import ScheduleRunStore

    store = _store(tmp_path)
    _trigger(store, "orphan")
    _claim("orphan", pid=_dead_pid(), base_dir=tmp_path, age_secs=42.0)

    records = reaper.terminalize_orphans_sync(store=store, base_dir=tmp_path)
    assert records[0]["recorded"] is True

    rows, total = await ScheduleRunStore(tmp_path).list_for_job("orphan", 0, 10)
    assert total == 1
    row = rows[0]
    assert row["status"] == reaper.RESTART_INTERRUPTED_STATUS
    assert "restart" in row["error"].lower()
    assert "interrupted" in row["error"].lower()
    assert row["finished_at"] >= row["started_at"] > 0


def test_the_trigger_health_reads_as_interrupted_on_the_detail_panel(tmp_path):
    """`health_status` + `last_error_summary` are what `to_schedule_row` publishes as
    `last_status` / `last_error`, and `lastRunMeta` renders the health rollup over the run row."""
    from personalclaw.triggers.schedule_view import to_schedule_row

    store = _store(tmp_path)
    _trigger(store, "orphan")
    _claim("orphan", pid=_dead_pid(), base_dir=tmp_path, age_secs=7.0)

    reaper.terminalize_orphans_sync(store=store, base_dir=tmp_path)

    trigger = store.get("orphan").trigger
    assert trigger.health_status == TriggerHealth.DEGRADED.value
    row = to_schedule_row(trigger, now=time.time(), base_dir=tmp_path)
    assert row["is_running"] is False, "a terminalized run must stop reading as in flight"
    assert "interrupted" in str(row["last_error"]).lower()
    assert "restart" in str(row["last_error"]).lower()


def test_the_pass_never_raises_on_an_unreadable_store(tmp_path):
    """It runs at boot, before the gateway serves. One bad row must not stop the gateway."""

    class Exploding:
        base_dir = tmp_path

        def get(self, _trigger_id):
            raise RuntimeError("unreadable row")

    _claim("orphan", pid=_dead_pid(), base_dir=tmp_path, age_secs=1.0)
    records = reaper.terminalize_orphans_sync(store=Exploding(), base_dir=tmp_path)
    assert [r["trigger_id"] for r in records] == ["orphan"]
    assert records[0]["released"] is True
    assert records[0]["recorded"] is False


# ── the live caller in the gateway boot path ──


def test_the_boot_pass_has_a_live_caller_in_the_gateway_boot_path():
    """The atom's own done_when: a symbol with no caller is the shape this program keeps finding.

    Asserted against source rather than by booting a gateway because the arming line is what
    matters and it sits inside a 200-line async initializer; `test_..._is_armed_before_the_clock`
    below pins the ORDER, which is the part a reader gets wrong.
    """
    import inspect

    from personalclaw import gateway

    source = inspect.getsource(gateway)
    assert "terminalize_orphans(" in source, (
        "nothing in the gateway calls the boot orphan pass. `reaper.run_forever` is then the only "
        "terminalizer again, and an orphaned run reads as hung for 1800s."
    )


def test_the_boot_pass_runs_before_the_clock_loop_is_armed():
    """Ordering, and it is load-bearing twice over.

    BEFORE the clock loop, so the first tick evaluates `existing_claim` against a claim store with
    no dead owners in it — otherwise `overlap: skip` suppresses the first real fire of every
    trigger a crash orphaned. And BEFORE the reaper, so the answer does not depend on which
    background task happens to sweep first.
    """
    import inspect

    from personalclaw import gateway

    source = inspect.getsource(gateway)
    boot_pass = source.index("terminalize_orphans(")
    clock = source.index("self._clock_task = asyncio.create_task")
    reaper_task = source.index("self._reaper_task = asyncio.create_task")
    assert boot_pass < clock < reaper_task, (
        "the boot orphan pass must run before the clock loop and the reaper are armed; a tick that "
        "sees a dead owner's claim skips the fire it should grant"
    )


@pytest.mark.asyncio
async def test_the_async_entry_point_is_the_one_the_gateway_awaits(tmp_path):
    """`terminalize_orphans` is the async face of the sync sweep, for the same reason
    `ScheduleRunStore.append` is async: the row write is file I/O and boot runs on the loop."""
    store = _store(tmp_path)
    _trigger(store, "orphan")
    _claim("orphan", pid=_dead_pid(), base_dir=tmp_path, age_secs=1.0)

    records = await reaper.terminalize_orphans(store=store, base_dir=tmp_path)
    assert [r["trigger_id"] for r in records] == ["orphan"]
    assert claims.is_running("orphan", base_dir=tmp_path) is False
