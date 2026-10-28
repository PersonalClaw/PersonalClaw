"""The Linux sandbox's namespace probe never forks the process that asks.

``sandbox._probe_unshare`` forked the gateway, or a test worker, and called ``unshare`` in the
copy. Both have other threads, and a forked copy that runs Python inherits every lock one of them
held at the fork, with no thread left to release it: the security log's writer, say, holding the
log's lock mid-write. A copy that needed such a lock never exited, and the probe waited on it with
no bound. That fits both hangs of #5638's first Merge run, which were on Linux, the one platform
the probe forked on. The probe now asks a new process (``_namespace_probe``) and waits for it at
most ``_NAMESPACE_PROBE_TIMEOUT``.

A child that does not answer is not a no. A no turns the sandbox off for the process (the backend
is ``none`` and commands run unwrapped), so only the child's own refusal is one. A child that
times out, cannot start, is killed or exits any other way leaves the answer unknown: the command
that asked is refused, never run unwrapped, and the next one asks again.
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
    bound = 20.0  # the probes answer in about a second; a fork stuck on the lock never does
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


def _child(answer: object):
    """A ``subprocess.run`` whose namespace probe child answers *answer*: an exit code, or the
    exception the wait raised. It keeps each command it was asked to start."""
    asked: list[list[str]] = []

    def run(argv: list[str], **how: object) -> subprocess.CompletedProcess[bytes]:
        asked.append(argv)
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, int)
        return subprocess.CompletedProcess(argv, answer)

    return run, asked


_UNKNOWN = {
    "no answer in time": subprocess.TimeoutExpired(["probe"], sandbox._NAMESPACE_PROBE_TIMEOUT),
    "no child": OSError("no interpreter"),
    "the interpreter could not import the probe": 1,
    "the probe could not ask": _namespace_probe.UNASKED,
    "killed": -9,
}


@pytest.fixture
def linux_probes(monkeypatch: pytest.MonkeyPatch):
    """A host the probes treat as Linux, with no sandbox-exec, and every cached answer forgotten
    before and after."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sandbox, "_probe_sandbox_exec", sandbox._HostFact(lambda: False))
    sandbox.reset_backend()
    yield monkeypatch
    sandbox.reset_backend()


def test_only_the_childs_own_refusal_is_a_no(linux_probes: pytest.MonkeyPatch) -> None:
    run, _ = _child(_namespace_probe.REFUSED)
    linux_probes.setattr(sandbox.subprocess, "run", run)
    assert sandbox._probe_unshare.__wrapped__() is False
    assert sandbox._probe_unshare_net.__wrapped__() is False
    for what, answer in _UNKNOWN.items():
        run, _ = _child(answer)
        linux_probes.setattr(sandbox.subprocess, "run", run)
        assert sandbox._probe_unshare.__wrapped__() is None, what
        assert sandbox._probe_unshare_net.__wrapped__() is None, what


@pytest.mark.parametrize("answer", list(_UNKNOWN.values()), ids=list(_UNKNOWN))
def test_a_command_is_refused_while_the_check_has_not_answered_and_the_next_asks_again(
    linux_probes: pytest.MonkeyPatch, answer: object
) -> None:
    run, asked = _child(answer)
    linux_probes.setattr(sandbox.subprocess, "run", run)
    for _ in range(2):
        with pytest.raises(sandbox.SandboxEnforcementUnavailable) as refused:
            sandbox.wrap_argv(["echo", "hi"], "auto")
        assert str(refused.value) == sandbox.UNCHECKED_REFUSAL
    assert len(asked) == 2, "an unknown answer was kept"
    assert sandbox.wrap_refusal("auto") == sandbox.UNCHECKED_REFUSAL
    assert sandbox.detect_backend("auto") == sandbox.UNCHECKED != "none"

    from personalclaw.dashboard.chat_boundary import _on_this_computer

    reading = _on_this_computer("auto", subject="the commands it runs")
    assert reading["label"] == "Sandbox not checked" and reading["confined"] is True


def test_a_refusing_host_falls_back_as_before_and_is_asked_once(
    linux_probes: pytest.MonkeyPatch,
) -> None:
    run, asked = _child(_namespace_probe.REFUSED)
    linux_probes.setattr(sandbox.subprocess, "run", run)
    assert sandbox.wrap_argv(["echo", "hi"], "auto") == (["echo", "hi"], None)
    assert sandbox.wrap_argv(["echo", "hi"], "auto") == (["echo", "hi"], None)
    assert sandbox.detect_backend("auto") == "none"
    assert len(asked) == 1, "a no is asked once for the process"


def test_a_command_with_no_network_is_refused_while_the_network_check_has_not_answered(
    linux_probes: pytest.MonkeyPatch,
) -> None:
    asked: list[list[str]] = []

    def run(argv: list[str], **how: object) -> subprocess.CompletedProcess[bytes]:
        asked.append(argv)
        if "net" in argv:
            raise subprocess.TimeoutExpired(argv, sandbox._NAMESPACE_PROBE_TIMEOUT)
        return subprocess.CompletedProcess(argv, _namespace_probe.TAKEN)

    linux_probes.setattr(sandbox.subprocess, "run", run)
    linux_probes.setattr(
        "personalclaw.net.policy.no_network_for_commands", lambda *a: "its run takes it away"
    )
    for _ in range(2):
        with pytest.raises(sandbox.SandboxEnforcementUnavailable) as refused:
            sandbox.wrap_argv(["curl", "example.com"], "auto")
        assert "take a command's network away did not answer in time" in str(refused.value)
    assert [argv[-1] for argv in asked] == ["mount", "net", "net"], "the network's unknown was kept"


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
    assert _namespace_probe.main(["user", "mount", "net"]) == _namespace_probe.TAKEN
    assert granted.calls == [flags["user"], flags["mount"], flags["net"]]

    refused = _Libc({flags["mount"]})
    monkeypatch.setattr(_namespace_probe.ctypes, "CDLL", lambda *a, **k: refused)
    assert _namespace_probe.main(["user", "mount", "net"]) == _namespace_probe.REFUSED
    assert refused.calls == [flags["user"], flags["mount"]]
