"""SDK: what an app's test suite needs to keep its tests off the machine it runs on.

An app's tests run on a developer's machine, and some of what core reaches there is not
inside any PersonalClaw home. ``keychain_off()`` is the one such switch today. The OS keychain
serves every home on the machine, and core reads it, writes it and deletes from it whenever
``keyring`` is importable, so a scratch ``PERSONALCLAW_HOME`` alone leaves a test able to read
or delete the owner's real secrets. After the call, core finds no keychain and credentials live
in the scratch home's ``.env``. It returns the call that lets the keychain back in. A
``conftest.py``'s ``pytest_configure`` turns it off before anything is collected, and
``pytest_unconfigure`` calls what it returned.

A test on a developer's machine can also reach a server running there: a local model server
answers on a well-known port (Ollama's 11434), and a test that asked it which models it has,
found one pulled, and ran it loaded gigabytes on every run. ``refuse_ports(ports, what=...)``
refuses, in this process, every TCP connection to one of *ports* before it is made, unless this
process is itself listening there (a test's own fake). The code under test sees what it would see
with no server there; ``take()`` names each refusal, with the test that asked, so a
``conftest.py`` fails that test by name. Each suite passes the ports it keeps out: which servers
a suite's code talks to is the suite's knowledge, not core's.

An ACP app's tests prove what its CLI is handed without launching the CLI.
``launch_acp_entry(options, work_dir)`` launches the command an ACP agent entry registers,
through the transport every spawn from an entry uses, with the environment such a spawn gets,
waits for it to exit and returns its exit code. The command is a stub standing in for the CLI:
it records what it received and exits. It is never spoken to over ACP, and no host sandbox wraps
it, because the environment is what it measures.

An app's harness imports core through ``personalclaw.sdk`` like the app does
(``tests/test_apps_import_boundary.py``), which is why these are published here rather than
reached for in core's internals by each test suite.
"""

from __future__ import annotations

import asyncio
import errno
import os
import socket
import threading
import weakref
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from personalclaw.config.credentials import keychain_off  # noqa: F401

__all__ = ["PortGuard", "keychain_off", "launch_acp_entry", "refuse_ports"]

_INET = (socket.AF_INET, socket.AF_INET6)


class PortGuard:
    """Refuses a TCP ``connect`` to one of :attr:`ports` that nobody in this process listens on.

    What it cannot see: a connection a child process makes (it has its own sockets), and a server
    on a port that is not in :attr:`ports`.
    """

    def __init__(self, ports: Iterable[int], *, what: str) -> None:
        self.ports = frozenset(int(port) for port in ports)
        self.what = what
        self._listeners: weakref.WeakSet[socket.socket] = weakref.WeakSet()
        self._refused: list[str] = []
        self._lock = threading.Lock()
        self._undo: list[Any] = []

    def own_ports(self) -> set[int]:
        """The ports this process is listening on now: a test's own fakes."""
        ports: set[int] = set()
        with self._lock:
            listeners = list(self._listeners)
        for sock in listeners:
            try:
                if sock.fileno() != -1:
                    ports.add(int(sock.getsockname()[1]))
            except (OSError, IndexError, TypeError, ValueError):
                continue
        return ports

    def listened(self, sock: socket.socket) -> None:
        """Note *sock* as listening: a port it holds is this process's own."""
        with self._lock:
            self._listeners.add(sock)

    def check(self, sock: socket.socket, address: object) -> None:
        """Raise ``ConnectionRefusedError`` for one of the ports this process does not serve."""
        if sock.family not in _INET or not isinstance(address, tuple) or len(address) < 2:
            return
        host, port = address[0], address[1]
        if not isinstance(port, int) or port not in self.ports or port in self.own_ports():
            return
        who = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0] or "(no test)"
        with self._lock:
            self._refused.append(f"{who} -> {host}:{port}")
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"refused by the test suite: {host}:{port} is {self.what}, and a test must not reach "
            "a real one. Fake it: a server this test starts on its own port, or a mocked "
            "transport.",
        )

    def take(self) -> list[str]:
        """The refusals since the last take (``"<test> -> <host>:<port>"``), cleared."""
        with self._lock:
            refused, self._refused = self._refused, []
        return refused

    def install(self) -> None:
        """Wrap ``socket.socket``'s ``listen``, ``connect`` and ``connect_ex`` for this process,
        until :meth:`undo`."""
        real_listen = socket.socket.listen
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        guard = self

        def listen(sock, *args):
            result = real_listen(sock, *args)
            guard.listened(sock)
            return result

        def connect(sock, address):
            guard.check(sock, address)
            return real_connect(sock, address)

        def connect_ex(sock, address):
            guard.check(sock, address)
            return real_connect_ex(sock, address)

        socket.socket.listen = listen  # type: ignore[method-assign]
        socket.socket.connect = connect  # type: ignore[method-assign]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
        self._undo.append((real_listen, real_connect, real_connect_ex))

    def undo(self) -> None:
        """Put ``socket.socket`` back as :meth:`install` found it."""
        while self._undo:
            real_listen, real_connect, real_connect_ex = self._undo.pop()
            socket.socket.listen = real_listen  # type: ignore[method-assign]
            socket.socket.connect = real_connect  # type: ignore[method-assign]
            socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]


def refuse_ports(ports: Iterable[int], *, what: str) -> PortGuard:
    """Refuse every connection this process makes to one of *ports* (*what* says what they are,
    for the refusal), and return the installed guard. A ``conftest.py`` calls it before anything is
    collected, asks :meth:`PortGuard.take` after each test, and calls :meth:`PortGuard.undo` when
    the run is done."""
    guard = PortGuard(ports, what=what)
    guard.install()
    return guard


async def launch_acp_entry(
    options: Mapping[str, Any], work_dir: Path, *, timeout: float = 60.0
) -> int:
    """Launch an ACP agent entry's ``command`` as a spawn from the entry would, and wait for it.

    *options* is the entry's options, as ``register_acp_cli_entry`` registered them. The child
    gets the child allowlist, the entry's ``env`` and the variables it declares in
    ``env_passthrough`` that are set here, and no other variable of this process's environment.
    Returns the exit code; a command still running after *timeout* seconds is killed and raises
    ``TimeoutError``.
    """
    from personalclaw.acp.transport import AcpProcess
    from personalclaw.llm.acp_agent import options_env

    entry = dict(options)
    proc = AcpProcess(
        command=[str(part) for part in entry.get("command") or []],
        work_dir=Path(work_dir),
        extra_env=options_env(entry) or None,
        sandbox_mode="off",
    )
    await proc.spawn()
    try:
        running = proc.process
        if running is None:  # pragma: no cover - spawn raises rather than leave no process
            raise RuntimeError("the entry's command did not start")
        return await asyncio.wait_for(running.wait(), timeout=timeout)
    finally:
        await proc.kill()
        proc.teardown()
