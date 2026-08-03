"""App-backend process-leak fix: boot-time orphan reaping by path identity.

The gateway spawns app backends on enable. A fresh gateway (empty in-memory
table, auto-ports) can't reclaim a prior gateway's backends by port, and if the
prior gateway died ungracefully (crash / ``kill -9`` / the double-signal
force-exit path) it orphaned them (reparented to init) — repeated hard-kills pile
up MANY orphans per app. The reliable, self-healing signal is the OS process
table: on boot, reap every live process whose command line runs THIS app's exact
entry path AND whose parent is dead (PPID 1). The parent-alive guard is load-
bearing: a process whose parent still lives belongs to a live supervisor — a
concurrently-running gateway or a test process — and reaping it kills a working
backend out from under that supervisor (the exact incident this guards against:
a pytest run's supervisor SIGTERMing the real gateway's live backends).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from personalclaw.apps.backend_runtime import BackendSupervisor


def _write_entry(tmp_path: Path, app: str = "myapp") -> Path:
    entry = tmp_path / "apps" / app / "backend" / "server.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("import time\nwhile True: time.sleep(1)\n")
    return entry.resolve()


def _spawn_child_proc(tmp_path: Path, app: str = "myapp") -> tuple[subprocess.Popen, Path]:
    """Spawn a marker process as OUR direct child (parent alive → not an
    orphan). Simulates a backend owned by a live supervisor elsewhere."""
    entry = _write_entry(tmp_path, app)
    proc = subprocess.Popen([sys.executable, str(entry)])
    return proc, entry


def _spawn_orphan_proc(tmp_path: Path, app: str = "myapp") -> tuple[int, Path]:
    """Spawn a marker process as a TRUE ORPHAN (reparented to init): an
    intermediate shell backgrounds the python and exits immediately."""
    entry = _write_entry(tmp_path, app)
    # The background child must NOT inherit the capture pipe (subprocess.run
    # would block on stdout EOF forever) — detach its fds to /dev/null.
    out = subprocess.run(  # noqa: S603 — test fixture
        ["/bin/sh", "-c", f'"{sys.executable}" "{entry}" >/dev/null 2>&1 </dev/null & echo $!'],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip()), entry


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _kill_quiet(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# Process-visibility waits. Generous on CI: these poll the OS process table for
# freshly-spawned children, and a contended runner can take far longer than a laptop
# to publish them (the failure this budget exists for showed only in CI).
#
# Raised 20s -> 60s after CI still timed out mid-reparent, twice, with the pile only
# PARTLY orphaned (observed 2-of-4 and 3-of-4 at the deadline). Reparenting to init is
# asynchronous and unordered: the intermediate shell exits and the kernel re-points each
# orphan independently, so on a runner executing ~18 xdist workers the tail of a 4-child
# pile can lag seconds behind the head. The old budget was fine for "is it running" and
# too tight for "have they ALL been re-parented" — a different, slower event. This costs
# nothing when the wait succeeds (it returns as soon as the predicate holds) and only
# lengthens a genuine failure, which is the right trade for a test whose whole job is to
# prove no orphan escapes.
_WAIT_TIMEOUT_S = 60.0
_WAIT_STEP_S = 0.05


def _wait_visible(entry: Path, pid: int) -> None:
    """Wait until *pid* appears in the process table for *entry*, or FAIL saying so."""
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        if any(p == pid for p, _ in BackendSupervisor._pids_running(entry)):
            return
        time.sleep(_WAIT_STEP_S)
    raise AssertionError(
        f"pid {pid} never became visible for {entry} within {_WAIT_TIMEOUT_S}s; "
        f"table now: {BackendSupervisor._pids_running(entry)}"
    )


def _wait_pid_reapable(entry: Path, pid: int) -> None:
    """Wait until THIS pid is reapable for *entry*, i.e. re-parented to init.

    Per-pid, not a cumulative count. The previous version waited for "N processes at ppid=1",
    which cannot distinguish two different failures: a reparenting that has not happened yet,
    and an EARLIER orphan that died. Under CI memory pressure the second is real — an
    interpreter fails to start or is killed — and once one of the pile is gone the cumulative
    count can never reach N, so the test fails on a subsequent spawn while blaming a
    reparenting race that did not occur. That is the observed CI shape exactly: three rows at
    ppid=1 with the fourth pid absent from `ps` altogether, not present-with-the-wrong-parent.

    Waiting per-pid makes each failure name its own cause: this pid never reparented, or this
    pid is gone.
    """
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        rows = BackendSupervisor._pids_running(entry)
        for row_pid, ppid in rows:
            if row_pid == pid:
                if ppid == 1:
                    return
                break
        else:
            # The pid is not in the table at all. It either has not appeared yet or it exited;
            # `_pid_alive` distinguishes them, and a dead one can never become reapable.
            if not _pid_alive(pid):
                raise AssertionError(
                    f"pid {pid} exited before it could be reaped (entry={entry}); the process "
                    "died rather than failing to reparent — likely resource pressure on the "
                    f"runner. Table now: {rows}"
                )
        time.sleep(_WAIT_STEP_S)
    raise AssertionError(
        f"pid {pid} never reparented to init for {entry} within {_WAIT_TIMEOUT_S}s; "
        f"table now: {BackendSupervisor._pids_running(entry)}"
    )


def _wait_all_dead(pids: list[int]) -> None:
    """Wait for every pid to exit, or FAIL naming the survivors.

    SIGTERM delivery plus interpreter teardown is not instant, and on a loaded runner it
    is much slower than the fixed 5s budget this used to allow.
    """
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        alive = [p for p in pids if _pid_alive(p)]
        if not alive:
            return
        time.sleep(0.1)
    raise AssertionError(f"processes survived the reap: {[p for p in pids if _pid_alive(p)]}")


def test_pids_running_finds_the_process(tmp_path):
    proc, entry = _spawn_child_proc(tmp_path)
    try:
        _wait_visible(entry, proc.pid)
        found = BackendSupervisor._pids_running(entry)
        assert any(p == proc.pid for p, _ in found), "running backend not found by path identity"
        # and it reports our live pid as the parent (not 1)
        ppid = next(pp for p, pp in found if p == proc.pid)
        assert ppid == os.getpid()
    finally:
        proc.kill()


def test_reap_orphans_kills_matching_orphan(tmp_path):
    pid, entry = _spawn_orphan_proc(tmp_path)
    try:
        sup = BackendSupervisor()
        _wait_pid_reapable(entry, pid)
        reaped = sup.reap_orphans("myapp", entry)
        assert reaped >= 1
        _wait_all_dead([pid])
    finally:
        _kill_quiet(pid)


def test_reap_orphans_kills_a_whole_pile(tmp_path):
    """The real bug: repeated ungraceful restarts stacked MANY orphans for one
    app. A single reap must clear the whole pile, not just one.

    **Each orphan is confirmed reparented BEFORE the next is spawned.** Spawning all four
    and then waiting for four races four independent reparentings against one clock: on a
    loaded CI runner three land and the fourth is still pointing at its intermediate shell,
    so `reap_orphans` correctly skips it and the count comes up short. Raising the timeout
    (this test was already moved 20s → 60s once) only makes that window less likely — it
    does not remove the race, and a test that passes because the machine was fast is not
    testing anything. Serializing the waits removes it: N sequential single-process waits
    have no interleaving to lose.
    """
    entry = (tmp_path / "apps" / "myapp" / "backend" / "server.py").resolve()
    pids: list[int] = []
    try:
        for _n in range(1, 5):
            pid = _spawn_orphan_proc(tmp_path)[0]
            pids.append(pid)
            # Assert THIS pid is reapable before adding another to the pile. Per-pid rather than
            # a cumulative count: see `_wait_pid_reapable` — a cumulative wait cannot tell a
            # pending reparenting from an earlier orphan that died, and CI hits the second.
            _wait_pid_reapable(entry, pid)
        sup = BackendSupervisor()
        reaped = sup.reap_orphans("myapp", entry)
        assert reaped >= 4, f"expected to reap the whole pile, got {reaped}"
        _wait_all_dead(pids)
    finally:
        for p in pids:
            _kill_quiet(p)


def test_reap_orphans_spares_owned_process(tmp_path):
    """A backend this supervisor owns (in its _procs table) must NOT be reaped —
    reap targets only orphans from a prior gateway, never the live table."""
    proc, entry = _spawn_child_proc(tmp_path)
    try:
        sup = BackendSupervisor()
        from personalclaw.apps.backend_runtime import RunningBackend

        # register the process as owned
        sup._procs["myapp"] = RunningBackend(name="myapp", port=1234, pid=proc.pid, proc=proc)
        # Wait DETERMINISTICALLY. These "nothing was reaped" assertions pass vacuously
        # if the process was never visible in the first place — a timed-out poll would
        # make the test green while proving nothing about sparing.
        _wait_visible(entry, proc.pid)
        reaped = sup.reap_orphans("myapp", entry)
        assert reaped == 0, "owned backend must be spared"
        assert proc.poll() is None, "owned backend was wrongly killed"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_reap_orphans_spares_live_foreign_children(tmp_path):
    """REGRESSION (the incident): a matching process whose PARENT IS STILL ALIVE
    belongs to another live supervisor (a second gateway, or a test run) and
    must be spared — reaping it kills a working backend out from under that
    supervisor. Only true orphans (PPID 1) may be reaped."""
    proc, entry = _spawn_child_proc(tmp_path)
    try:
        sup = BackendSupervisor()  # fresh table — does NOT own the process
        # Same vacuous-pass hazard as the owned-process test above.
        _wait_visible(entry, proc.pid)
        reaped = sup.reap_orphans("myapp", entry)
        assert reaped == 0, "live-parent process must be spared"
        assert proc.poll() is None, "another supervisor's live backend was killed"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_pids_running_empty_for_unknown_path(tmp_path):
    assert BackendSupervisor._pids_running(tmp_path / "nope" / "server.py") == []


def test_stop_with_no_tracked_proc_is_safe(tmp_path):
    sup = BackendSupervisor()
    # stop with nothing tracked is a no-op (returns False), never raises
    assert sup.stop("myapp") is False
