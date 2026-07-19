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
        ["/bin/sh", "-c",
         f'"{sys.executable}" "{entry}" >/dev/null 2>&1 </dev/null & echo $!'],
        capture_output=True, text=True, check=True,
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


def _wait_visible(entry: Path, pid: int) -> None:
    for _ in range(50):
        if any(p == pid for p, _ in BackendSupervisor._pids_running(entry)):
            return
        time.sleep(0.05)


def test_pids_running_finds_the_process(tmp_path):
    proc, entry = _spawn_child_proc(tmp_path)
    try:
        found: list[tuple[int, int]] = []
        for _ in range(50):
            found = BackendSupervisor._pids_running(entry)
            if any(p == proc.pid for p, _ in found):
                break
            time.sleep(0.05)
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
        _wait_visible(entry, pid)
        reaped = sup.reap_orphans("myapp", entry)
        assert reaped >= 1
        for _ in range(50):
            if not _pid_alive(pid):
                break
            time.sleep(0.1)
        assert not _pid_alive(pid), "orphaned backend was not reaped"
    finally:
        _kill_quiet(pid)


def test_reap_orphans_kills_a_whole_pile(tmp_path):
    """The real bug: repeated ungraceful restarts stacked MANY orphans for one
    app. A single reap must clear the whole pile, not just one."""
    pids = [_spawn_orphan_proc(tmp_path)[0] for _ in range(4)]
    entry = (tmp_path / "apps" / "myapp" / "backend" / "server.py").resolve()
    try:
        sup = BackendSupervisor()
        for _ in range(50):
            if len(BackendSupervisor._pids_running(entry)) >= 4:
                break
            time.sleep(0.05)
        reaped = sup.reap_orphans("myapp", entry)
        assert reaped >= 4, f"expected to reap the whole pile, got {reaped}"
        for _ in range(50):
            if all(not _pid_alive(p) for p in pids):
                break
            time.sleep(0.1)
        assert all(not _pid_alive(p) for p in pids), "some piled orphans survived"
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
        for _ in range(50):
            if any(p == proc.pid for p, _ in BackendSupervisor._pids_running(entry)):
                break
            time.sleep(0.05)
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
        for _ in range(50):
            if any(p == proc.pid for p, _ in BackendSupervisor._pids_running(entry)):
                break
            time.sleep(0.05)
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
