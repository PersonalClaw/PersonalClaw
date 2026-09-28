"""No test reaches a real local model server: a connection to one's port is refused, and charged.

A test that reaches the host's own Ollama loads a model on a shared machine, and what it proves
depends on what that host has installed. One did: the browse vision-grounding test's live leg
asked the host's Ollama which models it had and, finding the recommended vision model pulled, ran
it (8.6 GB loaded) on every suite run. Nothing stopped it, because a test that names
``localhost:11434`` and one that talks to it look the same to a reader.

So every ``connect`` in the test process is checked: a TCP connection to a port a local model
server listens on by default (:data:`LOCAL_MODEL_PORTS`) is refused with ``ECONNREFUSED`` before it
is made, unless this process is itself listening on that port (a test's own fake). The code under
test sees what it would see with no server there, and ``conftest`` fails the test that asked, by
name. Refused rather than recorded, because the harm is the call itself: a model loaded on the
host is not undone by a red test.

What it cannot see: a connection made by a child process (it has its own sockets), and a server
on a port that is not in the list.
"""

from __future__ import annotations

import errno
import os
import socket
import threading
import weakref

#: The default ports of the local model servers PersonalClaw's first-party providers speak to:
#: Ollama (11434), vLLM (8000) and ComfyUI (8188).
LOCAL_MODEL_PORTS = frozenset({11434, 8000, 8188})

_INET = (socket.AF_INET, socket.AF_INET6)


class Guard:
    """Refuses a ``connect`` to a local model port nobody in this process listens on."""

    def __init__(self, ports: frozenset[int] = LOCAL_MODEL_PORTS) -> None:
        self.ports = ports
        self._listeners: weakref.WeakSet[socket.socket] = weakref.WeakSet()
        self._refused: list[str] = []
        self._lock = threading.Lock()

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
        with self._lock:
            self._listeners.add(sock)

    def check(self, sock: socket.socket, address: object) -> None:
        """Raise ``ConnectionRefusedError`` for a local model port this process does not serve."""
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
            f"refused by the test suite: {host}:{port} is a local model server's port, and a test "
            "must not reach a real one. Fake it: a server this test starts on its own port, or a "
            "mocked transport.",
        )

    def take(self) -> list[str]:
        """The refusals since the last take, cleared."""
        with self._lock:
            refused, self._refused = self._refused, []
        return refused

    def install(self) -> None:
        """Wrap ``socket.socket``'s ``listen``, ``connect`` and ``connect_ex`` for this process."""
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

        socket.socket.listen = listen
        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex


GUARD = Guard()


def failure(refused: list[str]) -> str:
    """The failure a test that tried to reach a local model server is given."""
    return (
        "this test tried to reach a real local model server, and was refused: "
        + "; ".join(refused)
        + ". Fake the endpoint: a server the test starts on a port of its own, or a mocked "
        "transport (tests/local_model_port_guard.py)."
    )
