"""The Linux sandbox's namespace probe never forks the process that asks.

``sandbox._probe_unshare`` forked the gateway, or a test worker, and called ``unshare`` in the
copy. Both have other threads, and a forked copy that runs Python inherits every lock one of them
held at the fork, with no thread left to release it: the security log's writer, say, holding the
log's lock mid-write. A copy that needed such a lock never exited, and the probe waited on it with
no bound. That fits both hangs of #5638's first Merge run, which were on Linux, the one platform
the probe forked on. The probe now asks a new process (``_namespace_probe``) and waits for it at
most ``_NAMESPACE_PROBE_TIMEOUT``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import personalclaw
from personalclaw import _namespace_probe, sandbox
from personalclaw.self_update import python_child_argv

#: Run in a process of its own, so the at-fork hook it adds dies with it. The security log's writer
#: thread holds the log's lock mid-write; a hook stands in for whatever a forked copy of this
#: process would need that lock for. Then both probes are asked, on a host they treat as Linux,
#: with an ``unshare`` that would succeed, so a probe that forks gets as far as forking.
_PLANT = r"""
import ctypes, os, sys, threading, time
from personalclaw import sandbox, sel

log = sel.sel()
inside, finish = threading.Event(), threading.Event()

def a_write_that_holds_the_log():
    with log._lock:
        inside.set()
        finish.wait(60)

sel._WRITER.submit(a_write_that_holds_the_log)
assert inside.wait(10), "the writer never took the log's lock"

def a_forked_copy_needs_the_log():
    with log._lock:
        pass

os.register_at_fork(after_in_child=a_forked_copy_needs_the_log)

class _Libc:
    def __init__(self, *args, **kwargs):
        self.unshare = lambda flag: 0

ctypes.CDLL = _Libc
sys.platform = "linux"
started = time.monotonic()
sandbox._probe_unshare.__wrapped__()
sandbox._probe_unshare_net.__wrapped__()
print(f"{time.monotonic() - started:.1f}")
finish.set()
"""


def test_a_probe_answers_in_time_while_the_security_logs_writer_holds_its_lock(
    tmp_path: Path,
) -> None:
    home, user = tmp_path / "home", tmp_path / "user"
    user.mkdir()
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(personalclaw.__file__).resolve().parents[1]),
        "PERSONALCLAW_HOME": str(home),
        "HOME": str(user),
    }
    child = subprocess.Popen(
        [sys.executable, "-c", _PLANT],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    bound = sandbox._NAMESPACE_PROBE_TIMEOUT * 2
    try:
        out, err = child.communicate(timeout=bound)
    except subprocess.TimeoutExpired:
        # The whole group: a forked copy stuck on the lock is in it too.
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        pytest.fail(f"the probes still waited after {bound:.0f} s: a forked copy took the lock")
    assert child.returncode == 0, err
    assert float(out.strip()) < bound


@pytest.fixture
def on_linux(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, object]]]:
    """Each command a probe starts, with how it waits for it; every one answers yes."""
    asked: list[tuple[list[str], dict[str, object]]] = []

    def run(argv: list[str], **how: object) -> subprocess.CompletedProcess[bytes]:
        asked.append((argv, how))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    monkeypatch.setattr(os, "fork", lambda: pytest.fail("the probe forked this process"))
    return asked


def test_each_probe_asks_a_new_process_for_the_launchers_namespaces_in_its_order(
    on_linux: list[tuple[list[str], dict[str, object]]],
) -> None:
    assert sandbox._probe_unshare.__wrapped__() is True
    assert sandbox._probe_unshare_net.__wrapped__() is True
    module = "personalclaw._namespace_probe"
    assert [argv for argv, _ in on_linux] == [
        python_child_argv(module, "user", "mount"),
        python_child_argv(module, "user", "mount", "net"),
    ]
    for _, how in on_linux:
        assert how["timeout"] == sandbox._NAMESPACE_PROBE_TIMEOUT
        assert how.get("preexec_fn") is None


@pytest.mark.parametrize(
    "answer",
    [
        subprocess.TimeoutExpired(["probe"], sandbox._NAMESPACE_PROBE_TIMEOUT),
        OSError("no interpreter"),
        subprocess.CompletedProcess(["probe"], 1),
    ],
    ids=["no answer in time", "no child", "a namespace refused"],
)
def test_a_probe_child_that_does_not_say_yes_reads_as_no(
    monkeypatch: pytest.MonkeyPatch, answer: object
) -> None:
    def run(argv: list[str], **how: object) -> object:
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    assert sandbox._probe_unshare.__wrapped__() is False
    assert sandbox._probe_unshare_net.__wrapped__() is False


class _Libc:
    """An ``unshare`` that refuses the flags in *refused* and keeps every call it was given."""

    def __init__(self, refused: set[int]) -> None:
        self.calls: list[int] = []

        def unshare(flag: int) -> int:  # a function, so the child can set its argtypes
            self.calls.append(flag)
            return -1 if flag in refused else 0

        self.unshare = unshare


def test_the_child_calls_unshare_once_each_in_order_and_stops_at_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flags = _namespace_probe.NAMESPACES
    granted = _Libc(set())
    monkeypatch.setattr(_namespace_probe.ctypes, "CDLL", lambda *a, **k: granted)
    assert _namespace_probe.main(["user", "mount", "net"]) == 0
    assert granted.calls == [flags["user"], flags["mount"], flags["net"]]

    refused = _Libc({flags["mount"]})
    monkeypatch.setattr(_namespace_probe.ctypes, "CDLL", lambda *a, **k: refused)
    assert _namespace_probe.main(["user", "mount", "net"]) == 1
    assert refused.calls == [flags["user"], flags["mount"]]
