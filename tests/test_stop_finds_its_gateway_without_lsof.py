"""`personalclaw stop` finds this home's gateway from the gateway's own record: no `lsof`, no `ps`.

It used to ask `lsof` which process listened on the port and `ps` whether that process was a
gateway, and on a host with neither (the published container image, a minimal Linux) it could
only fail: "❌ `lsof` not found — cannot look up gateway process". `restart` then swallowed that
failure and started a second gateway beside the first. The gateway already records its own pid
and port in its home once it listens (``gateway_base``), so `stop` reads that record, confirms
the pid still runs a PersonalClaw gateway (``/proc`` on Linux), signals it and waits for it to
exit; `restart` starts a fresh one only once none is left.

``subprocess.check_output`` is trapped in every test here, because it is what the old lookup
ran: against the code before the fix these tests fail on the trap and can never reach a real
`lsof` that might find, and signal, a gateway this machine is actually running.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from personalclaw import cli_server, gateway_base, process_facts

_GATEWAY_ARGV = ["/opt/venv/bin/python", "/opt/venv/bin/personalclaw", "gateway"]


@pytest.fixture(autouse=True)
def _no_service_no_container_no_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PORT", raising=False)
    monkeypatch.setattr(cli_server.service_controller, "this_homes_service", lambda: None)

    def no_lookup(*args: object, **_kwargs: object) -> str:
        raise AssertionError(f"ran a lookup program: {args[0] if args else '?'}")

    monkeypatch.setattr(subprocess, "check_output", no_lookup)


def _record(port: int, pid: int) -> None:
    """This home's runtime record, as a gateway writes it once it listens."""
    gateway_base._runtime_path().write_text(json.dumps({"port": port, "pid": pid}), "utf-8")


@pytest.fixture
def no_programs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host with no `ps` or `lsof`: running any program at all fails the test."""

    def refuse(*args: object, **_kwargs: object) -> object:
        raise AssertionError(f"ran a program: {args[0] if args else '?'}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.fixture
def fake_proc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A Linux ``/proc`` holding the processes a test names, and nothing else."""
    root = tmp_path / "proc"
    root.mkdir()
    monkeypatch.setattr(process_facts, "PROC", root)

    def add(pid: int, argv: list[str]) -> None:
        (root / str(pid)).mkdir()
        (root / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    return add


@pytest.fixture
def signals(monkeypatch: pytest.MonkeyPatch):
    """Record the signals `stop` sends; a SIGTERM makes the recorded pid exit."""
    sent: list[tuple[int, int]] = []
    alive: set[int] = set()

    def kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        if sig == signal.SIGTERM:
            alive.discard(pid)

    monkeypatch.setattr(cli_server.os, "kill", kill)
    monkeypatch.setattr(gateway_base, "pid_is_alive", lambda pid: pid in alive)
    monkeypatch.setattr(cli_server.time, "sleep", lambda _s: None)
    return sent, alive


# ── stop ───────────────────────────────────────────────────────────────────────────────────


def test_stop_signals_the_recorded_gateway_with_no_lookup_program(
    no_programs, fake_proc, signals, capsys
) -> None:
    sent, alive = signals
    fake_proc(4242, _GATEWAY_ARGV)
    alive.add(4242)
    _record(10000, 4242)

    cli_server._stop(None)

    assert sent == [(4242, signal.SIGTERM)]
    assert "Stopped the gateway (pid 4242, port 10000)." in capsys.readouterr().out


def test_stop_ends_a_real_gateway_process_it_found_by_its_record(capsys) -> None:
    """End to end on this host: a real process whose command line is a gateway's, a real
    signal, a real wait. Its exit is reaped at once, so `stop` sees it gone."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)", "personalclaw", "gateway"]
    )
    threading.Thread(target=proc.wait, daemon=True).start()
    try:
        _record(10000, proc.pid)
        cli_server._stop(None)
        assert proc.wait(timeout=10) == -signal.SIGTERM
        assert f"Stopped the gateway (pid {proc.pid}, port 10000)." in capsys.readouterr().out
    finally:
        if proc.poll() is None:
            proc.kill()


def test_a_recorded_pid_that_is_not_a_gateway_now_is_never_signalled(capsys) -> None:
    """A crashed gateway leaves its record, and the system may give the pid to anything."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        _record(10000, proc.pid)
        with pytest.raises(SystemExit) as exc:
            cli_server._stop(None)
        assert exc.value.code == 1
        assert "is not a PersonalClaw gateway now, so it was not signalled" in (
            capsys.readouterr().err
        )
        assert proc.poll() is None, "the unrelated process was signalled"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_no_record_means_no_gateway_of_this_home(no_programs, signals, capsys) -> None:
    sent, _alive = signals
    with pytest.raises(SystemExit) as exc:
        cli_server._stop(None)
    assert exc.value.code == 1
    assert "No PersonalClaw gateway is running for" in capsys.readouterr().err
    assert sent == []


def test_a_typed_port_the_gateway_does_not_listen_on_stops_nothing(
    no_programs, fake_proc, signals, capsys
) -> None:
    sent, alive = signals
    fake_proc(4242, _GATEWAY_ARGV)
    alive.add(4242)
    _record(10000, 4242)
    with pytest.raises(SystemExit) as exc:
        cli_server._stop(12345)
    assert exc.value.code == 1
    assert "listens on port 10000, not 12345, so nothing was stopped" in capsys.readouterr().err
    assert sent == []


def test_the_typed_port_it_does_listen_on_stops_it(no_programs, fake_proc, signals) -> None:
    sent, alive = signals
    fake_proc(4242, _GATEWAY_ARGV)
    alive.add(4242)
    _record(10000, 4242)
    cli_server._stop(10000)
    assert sent == [(4242, signal.SIGTERM)]


def test_a_gateway_of_another_user_is_reported_not_retried_under_sudo(
    no_programs, fake_proc, monkeypatch, capsys
) -> None:
    fake_proc(4242, _GATEWAY_ARGV)
    _record(10000, 4242)
    monkeypatch.setattr(gateway_base, "pid_is_alive", lambda pid: True)

    def kill(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(cli_server.os, "kill", kill)
    with pytest.raises(SystemExit) as exc:
        cli_server._stop(None)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "No permission to stop the gateway (pid 4242)" in err
    assert "sudo" not in err


def test_a_gateway_that_does_not_exit_is_said_and_not_waited_on_forever(
    no_programs, fake_proc, signals, monkeypatch, capsys
) -> None:
    sent, _alive = signals
    fake_proc(4242, _GATEWAY_ARGV)
    _record(10000, 4242)
    monkeypatch.setattr(gateway_base, "pid_is_alive", lambda pid: True)
    with pytest.raises(SystemExit) as exc:
        cli_server._stop(None)
    assert exc.value.code == 1
    assert sent == [(4242, signal.SIGTERM)]
    assert "it is still running after 15 seconds" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command, runs_it",
    [
        ("/opt/venv/bin/python /opt/venv/bin/personalclaw gateway", True),
        ("/usr/bin/python3 -m personalclaw gateway --port 10000", True),
        ("/home/u/.local/bin/personalclaw gateway --no-open", True),
        (
            "/Applications/PersonalClaw.app/Contents/Resources/backend-dist/personalclaw-backend "
            "gateway --port auto --json-ready --no-open",
            True,
        ),
        ("/opt/venv/bin/python /opt/venv/bin/personalclaw token", False),
        ("vim /tmp/personalclaw-notes.txt", False),
        ("sh -c sleep 60 gateway", False),
        ("nginx: worker process", False),
        ("", False),
    ],
)
def test_what_counts_as_a_gateway_command_line(command: str, runs_it: bool) -> None:
    assert cli_server._runs_the_gateway(command) is runs_it


# ── restart ────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    ports: list[int] = []
    monkeypatch.setattr(cli_server, "_spawn_detached_gateway", ports.append)
    return ports


def test_restart_stops_the_gateway_then_starts_one_on_the_port_it_had(
    no_programs, fake_proc, signals, spawned
) -> None:
    sent, alive = signals
    fake_proc(4242, _GATEWAY_ARGV)
    alive.add(4242)
    _record(12345, 4242)
    cli_server._restart(None)
    assert sent == [(4242, signal.SIGTERM)]
    assert spawned == [12345]


def test_restart_starts_no_second_gateway_beside_one_it_could_not_stop(
    no_programs, fake_proc, monkeypatch, spawned, capsys
) -> None:
    fake_proc(4242, _GATEWAY_ARGV)
    _record(10000, 4242)
    monkeypatch.setattr(gateway_base, "pid_is_alive", lambda pid: True)

    def kill(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(cli_server.os, "kill", kill)
    with pytest.raises(SystemExit) as exc:
        cli_server._restart(None)
    assert exc.value.code == 1
    assert spawned == []


def test_restart_with_nothing_running_starts_one_where_a_gateway_binds(
    no_programs, signals, spawned, monkeypatch
) -> None:
    """With none running, the fresh gateway starts where a gateway of this home binds when it is
    not told a port: the gateway's own decision, ``PERSONALCLAW_PORT`` first."""
    sent, _alive = signals
    monkeypatch.setenv("PERSONALCLAW_PORT", "12399")
    cli_server._restart(None)
    assert sent == []
    assert spawned == [12399]


def test_restart_with_nothing_running_starts_one_on_the_port_typed(
    no_programs, signals, spawned
) -> None:
    sent, _alive = signals
    cli_server._restart(12345)
    assert sent == []
    assert spawned == [12345]
