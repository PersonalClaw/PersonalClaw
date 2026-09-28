"""The local model port guard must be able to REFUSE, and must let a test's own fake through.

Every refusal here is on a port this test picks from the ephemeral range and stands in for a local
model port, so no assertion can ever reach a real model server if the guard were broken: the port
has nothing listening on it, and the worst a broken guard lets happen is a refused connection.
"""

from __future__ import annotations

import socket

import local_model_port_guard as lmpg
import pytest


def _free_port() -> int:
    """A port nothing listens on: bound, read, and let go."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_ollamas_port_is_guarded() -> None:
    assert 11434 in lmpg.LOCAL_MODEL_PORTS


def test_a_connection_to_a_model_port_nobody_here_serves_is_refused_and_named() -> None:
    port = _free_port()
    guard = lmpg.Guard(frozenset({port}))
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError) as refused:
        guard.check(sock, ("127.0.0.1", port))
    assert f"127.0.0.1:{port} is a local model server's port" in str(refused.value)
    [taken] = guard.take()
    assert taken.endswith(f"-> 127.0.0.1:{port}")
    assert "test_a_connection_to_a_model_port_nobody_here_serves" in taken
    assert guard.take() == [], "taken once"


def test_a_port_this_process_listens_on_is_its_own_fake_and_passes() -> None:
    guard = lmpg.Guard(frozenset())
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = int(server.getsockname()[1])
        guard.ports = frozenset({port})
        guard.listened(server)
        with socket.socket() as sock:
            guard.check(sock, ("127.0.0.1", port))  # no refusal
    # Closed, the fake no longer stands for the port.
    with socket.socket() as sock, pytest.raises(ConnectionRefusedError):
        guard.check(sock, ("127.0.0.1", port))
    guard.take()


def test_any_other_port_and_any_other_socket_kind_pass() -> None:
    guarded = _free_port()
    guard = lmpg.Guard(frozenset({guarded}))
    with socket.socket() as sock:
        guard.check(sock, ("127.0.0.1", guarded + 1 if guarded < 65535 else guarded - 1))
    with socket.socket(socket.AF_UNIX) as unix:
        guard.check(unix, "unrelated.sock")
    assert guard.take() == []


def test_the_installed_guard_refuses_a_real_connect_before_it_is_made(monkeypatch) -> None:
    """Through ``socket.create_connection``, the way ``urllib`` and ``httpx`` connect: the
    process-wide guard, with one stand-in port added for this test."""
    port = _free_port()
    monkeypatch.setattr(lmpg.GUARD, "ports", lmpg.GUARD.ports | {port})
    with pytest.raises(ConnectionRefusedError) as refused:
        socket.create_connection(("127.0.0.1", port), timeout=2)
    assert "local model server's port" in str(refused.value)
    # conftest would fail this test for it; taking the refusal here keeps the test's own.
    assert len(lmpg.GUARD.take()) == 1
