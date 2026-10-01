"""The port guard must be able to REFUSE, and must let a test's own servers through.

Every refusal here is on a port this test picks from the ephemeral range and stands in for a port
the guard keeps out, so no assertion can ever reach a real server if the guard were broken: the
port has nothing listening on it, and the worst a broken guard lets happen is a refused connection.

The mechanism is the SDK's (``personalclaw.sdk.testing.PortGuard``), the one the apps suite installs
too, so these are its tests; the suite's own ports and rules are ``port_guard``'s.
"""

from __future__ import annotations

import socket

import port_guard
import pytest

from personalclaw.sdk.testing import PortGuard


def _free_port() -> int:
    """A port nothing listens on: bound, read, and let go."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_ollamas_port_is_guarded_and_so_is_every_port_on_this_machine() -> None:
    assert 11434 in port_guard.LOCAL_MODEL_PORTS
    assert port_guard.GUARD.ports == port_guard.LOCAL_MODEL_PORTS
    assert port_guard.GUARD.loopback is True


def test_a_connection_to_a_guarded_port_nobody_here_serves_is_refused_and_named() -> None:
    port = _free_port()
    guard = PortGuard({port}, what="a scripted server's port")
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError) as refused:
        guard.check(sock, ("127.0.0.1", port))
    assert f"127.0.0.1:{port} is a scripted server's port" in str(refused.value)
    [taken] = guard.take()
    assert taken.endswith(f"-> 127.0.0.1:{port}")
    assert "test_a_connection_to_a_guarded_port_nobody_here_serves" in taken
    assert guard.take() == [], "taken once"


def test_a_port_this_process_listens_on_is_its_own_fake_and_passes() -> None:
    guard = PortGuard((), what="a scripted server's port")
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = int(server.getsockname()[1])
        guard.ports = frozenset({port})
        guard.listened(server)
        guard.bound(server)
        with socket.socket() as sock:
            guard.check(sock, ("127.0.0.1", port))  # no refusal
    # Closed, the fake no longer stands for the port: a guarded port must be SERVED here, and
    # having bound it once is not enough, because the real server may be the one there now.
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
        guard.check(sock, ("127.0.0.1", port))
    guard.take()


def test_any_other_port_and_any_other_socket_kind_pass() -> None:
    guarded = _free_port()
    guard = PortGuard({guarded}, what="a scripted server's port")
    with socket.socket() as sock:
        guard.check(sock, ("127.0.0.1", guarded + 1 if guarded < 65535 else guarded - 1))
    with socket.socket(socket.AF_UNIX) as unix:
        guard.check(unix, "unrelated.sock")
    assert guard.take() == []


# ── every port on this machine ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.8.9.10",
        "::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
        "::",
        "",
        "localhost",
        "LOCALHOST.",
        "api.localhost",
    ],
)
def test_a_port_on_this_machine_nobody_here_opened_is_refused(host: str) -> None:
    guard = PortGuard((), what="a scripted server's port", loopback=True)
    port = _free_port()  # held by the SUITE's guard, not by this fresh one
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as sock, pytest.raises(ConnectionRefusedError) as refused:
        guard.check(sock, (host, port))
    assert "a port on this machine that this process did not open" in str(refused.value)
    assert "a server it did not start" in str(refused.value)
    [taken] = guard.take()
    assert taken.endswith(f"-> {host}:{port}")


@pytest.mark.parametrize("host", ["192.0.2.10", "10.1.2.3", "2001:db8::1", "example.com"])
def test_a_port_elsewhere_is_not_this_machines(host: str) -> None:
    """Checked only, never connected: the loopback rule is about this machine's servers."""
    guard = PortGuard((), what="a scripted server's port", loopback=True)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as sock:
        guard.check(sock, (host, _free_port()))
    assert guard.take() == []


def test_a_port_this_process_bound_is_its_own_after_it_lets_it_go() -> None:
    """A fake it serves, a free port it picked for a child, a port it let go to stand for a server
    that is not there: each was bound here, so each is this process's to connect to."""
    guard = PortGuard((), what="a scripted server's port", loopback=True)
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        guard.bound(server)
        port = int(server.getsockname()[1])
    with socket.socket() as sock:
        guard.check(sock, ("127.0.0.1", port))
        guard.check(sock, ("localhost", port))
    assert guard.take() == []


def test_a_port_a_child_chose_is_held_once_the_test_says_so() -> None:
    guard = PortGuard((), what="a scripted server's port", loopback=True)
    port = _free_port()
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
        guard.check(sock, ("127.0.0.1", port))
    guard.take()
    guard.own(port)
    with socket.socket() as sock:
        guard.check(sock, ("127.0.0.1", port))
    assert guard.take() == []


def test_without_the_loopback_rule_only_the_listed_ports_are_kept_out() -> None:
    """What the apps suite installs today: the listed ports, and nothing else."""
    guard = PortGuard((), what="a scripted server's port")
    with socket.socket() as sock:
        guard.check(sock, ("127.0.0.1", _free_port()))
    assert guard.take() == []


# ── the installed guard ───────────────────────────────────────────────────────


def test_the_installed_guard_refuses_a_real_connect_before_it_is_made(monkeypatch) -> None:
    """Through ``socket.create_connection``, the way ``urllib`` and ``httpx`` connect: the
    process-wide guard, with one stand-in port added for this test."""
    installed = port_guard.GUARD
    port = _free_port()
    monkeypatch.setattr(installed, "ports", installed.ports | {port})
    with pytest.raises(ConnectionRefusedError) as refused:
        socket.create_connection(("127.0.0.1", port), timeout=2)
    assert "local model server's port" in str(refused.value)
    # conftest would fail this test for it; taking the refusal here keeps the test's own.
    assert len(installed.take()) == 1


def test_the_installed_guard_refuses_a_port_on_this_machine_it_never_held(monkeypatch) -> None:
    installed = port_guard.GUARD
    port = _free_port()
    # The bind above made the port this process's own; take it back, so it stands for a port a
    # server this test did not start could be listening on.
    monkeypatch.setattr(installed, "_held", installed.held_ports() - {port})
    with pytest.raises(ConnectionRefusedError) as refused:
        socket.create_connection(("127.0.0.1", port), timeout=2)
    assert "did not open" in str(refused.value)
    [taken] = installed.take()
    assert taken.endswith(f"-> 127.0.0.1:{port}")
    assert "test_the_installed_guard_refuses_a_port_on_this_machine_it_never_held" in taken


def test_the_installed_guard_lets_a_test_reach_what_it_started() -> None:
    installed = port_guard.GUARD
    # A port it bound and let go: the OS refuses, not the guard, and nothing is charged.
    let_go = _free_port()
    with pytest.raises(ConnectionRefusedError) as refused:
        socket.create_connection(("127.0.0.1", let_go), timeout=2)
    assert "refused by the test suite" not in str(refused.value)
    # A fake it serves: connected.
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with socket.create_connection(("127.0.0.1", server.getsockname()[1]), timeout=2):
            pass
    assert installed.take() == []


def test_undo_gives_the_socket_back_as_it_was(monkeypatch) -> None:
    """A second guard stacked on the suite's, undone: ``socket`` is the suite's again, whose own
    refusals still hold (on a stand-in port, as everywhere here)."""
    before = (
        socket.socket.bind,
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.socket.listen,
    )
    stacked_port, suite_port = _free_port(), _free_port()
    stacked = PortGuard({stacked_port}, what="a scripted server's port")
    stacked.install()
    assert socket.socket.connect is not before[1]
    assert socket.socket.bind is not before[0]
    stacked.undo()
    assert (
        socket.socket.bind,
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.socket.listen,
    ) == before
    monkeypatch.setattr(port_guard.GUARD, "ports", port_guard.GUARD.ports | {suite_port})
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
        sock.connect(("127.0.0.1", suite_port))
    assert len(port_guard.GUARD.take()) == 1
