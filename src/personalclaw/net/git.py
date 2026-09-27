"""Git over HTTPS through the egress guard, for a fetch whose URL came from someone else's data.

``net/client.fetch`` closes the DNS-rebind window by dialing the addresses it validated, which it
can do because it owns the socket. ``git`` owns its own: it resolves the name itself, after any
check made before it ran, and follows a redirect to wherever the server says. So a check in front
of ``git clone`` checks a name, not the connection.

This module hands git no socket to the outside world. It points git at a loopback HTTP CONNECT
tunnel (``http.proxy``), so every connection git makes (the first, and each redirect hop) arrives
here as ``CONNECT host:443``. The tunnel asks :func:`personalclaw.net.guard.evaluate` about that
host at that moment, dials only an address the guard returned, and refuses everything else. What
git may speak is pinned as well: HTTPS only (``GIT_ALLOW_PROTOCOL``), no saved credentials, no
configuration from the owner's global or system files, and none of the environment that would send
it around the tunnel (a proxy of the environment's own, ``NO_PROXY``, injected ``-c`` settings).
Like every git that fetches an app source (:func:`source_git_env`), it starts from the child
allowlist rather than from the gateway's environment.

Only port 443. A registry listing's URL names no port (``apps/catalog.listing_repo_refusal``), so
another port can only arrive in a redirect, which is the server's choice and not the owner's.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import selectors
import socket
import socketserver
import subprocess
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from personalclaw.net.guard import GuardDecision, evaluate
from personalclaw.net.policy import EgressPolicy

logger = logging.getLogger(__name__)

HTTPS_PORT = 443

_HEAD_LIMIT = 16 * 1024  # a CONNECT request is one line and a few headers
_HEAD_TIMEOUT_S = 10.0
_DIAL_TIMEOUT_S = 15.0
_RELAY_CHUNK = 65536

#: Environment a plain ``git`` honours that would route it around the tunnel, feed it settings, or
#: run a program to ask for a password. A ``GIT_CONFIG_KEY_<n>``/``GIT_CONFIG_VALUE_<n>`` pair is
#: dropped by prefix in :func:`guarded_git_env`.
_SCRUBBED_ENV = (
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
    "GIT_PROXY_COMMAND",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)
_SCRUBBED_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


@dataclass(frozen=True)
class TunnelRefusal:
    """One connection git asked for and did not get."""

    host: str
    port: int
    # The forbidden address the host is or resolved to, "" when it was refused before resolving.
    address: str
    # The guard's category (``loopback``, ``private``, ``metadata``, ``deny_list``, …), or
    # ``port`` / ``method`` for a request that was never an HTTPS connection to 443.
    category: str
    reason: str


class GitEgressError(Exception):
    """A guarded git fetch that the tunnel did not let through."""


class GitEgressRefused(GitEgressError):
    """Git asked to reach an address the policy forbids. ``refusal`` is the first such request."""

    def __init__(self, refusal: TunnelRefusal) -> None:
        super().__init__(refusal.reason)
        self.refusal = refusal


class GitHostUnreachable(GitEgressError):
    """Git failed, and a host it asked for did not resolve or did not answer."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(reason)
        self.host = host
        self.reason = reason


def source_git_env(*, site: str) -> dict[str, str]:
    """The environment a git that fetches an app source runs with: the Store's catalog reading a
    source, an install cloning one, and the guarded fetch of a registry listing.

    The child allowlist (``sandbox.build_child_env``), never a copy of the gateway's environment.
    Git talks to a server someone else runs and starts helpers of its own (a remote helper, a
    credential helper, an LFS filter), and the gateway's environment holds every secret saved in
    PersonalClaw; a clone needs none of them. What it does need arrives: ``PATH``, the home its
    configuration and credential helpers read, and the proxy and certificate settings. It also
    leaves out an inherited ``GIT_DIR``, ``GIT_WORK_TREE`` or ``GIT_INDEX_FILE``, which would point
    a clone or a ``git -C`` read at another repository. A clone never waits on a password prompt:
    nobody is at the gateway's terminal to answer it.
    """
    from personalclaw.sandbox import build_child_env

    return build_child_env(site=site, extra={"GIT_TERMINAL_PROMPT": "0"})


def guarded_git_env() -> dict[str, str]:
    """The environment a guarded git runs with (see the module docstring for why each part)."""
    env = {
        k: v
        for k, v in source_git_env(site="app-listing-git").items()
        if k not in _SCRUBBED_ENV and not k.startswith(_SCRUBBED_ENV_PREFIXES)
    }
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    # Wins over any protocol.* setting, including one passed with -c.
    env["GIT_ALLOW_PROTOCOL"] = "https"
    return env


def _audit(policy: EgressPolicy, target: str, *, outcome: str, reason: str = "") -> None:
    """One SEL row per connection git asked for, allowed or not (best-effort, like net.fetch)."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=f"net.git:{policy.name}",
            operation="egress_git",
            outcome=outcome,
            source="net",
            resources=target[:200],
            error=reason[:200],
        )
    except Exception:
        logger.debug("egress SEL audit failed", exc_info=True)


def _read_head(conn: socket.socket) -> tuple[bytes, bytes]:
    """The request head up to the blank line, and whatever arrived after it."""
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > _HEAD_LIMIT:
            raise ValueError("request head too large")
        chunk = conn.recv(4096)
        if not chunk:
            raise ValueError("connection closed before the request head ended")
        data += chunk
    head, rest = data.split(b"\r\n\r\n", 1)
    return head, rest


_HOSTNAME = re.compile(r"[a-z0-9._-]+")


def _split_target(target: str) -> tuple[str, int] | None:
    """``host:port`` (``[v6]:port`` for an IPv6 literal) → ``(host, port)``, or None.

    The host must be a plain hostname or an IP literal: it is put into a URL for the guard, and a
    host carrying ``/``, ``@`` or ``#`` would have the guard judge a different host than the one
    named here (the dial goes to what the guard judged, so that is a refusal, not a bypass)."""
    host, sep, port = target.rpartition(":")
    if not sep or not host or not port.isdigit():
        return None
    host = host.lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return None
    elif not _HOSTNAME.fullmatch(host):
        return None  # includes an unbracketed IPv6 literal, which is ambiguous
    return host, int(port)


def _relay(a: socket.socket, b: socket.socket, idle_timeout: float) -> None:
    """Copy bytes both ways until either side closes or the link sits idle for ``idle_timeout``."""
    selector = selectors.DefaultSelector()  # not select(): the gateway may hold >1024 descriptors
    try:
        selector.register(a, selectors.EVENT_READ, b)
        selector.register(b, selectors.EVENT_READ, a)
        while True:
            events = selector.select(idle_timeout)
            if not events:
                return
            for key, _mask in events:
                data = key.fileobj.recv(_RELAY_CHUNK)  # type: ignore[union-attr]
                if not data:
                    return
                key.data.sendall(data)
    except OSError:
        return
    finally:
        selector.close()
        a.close()
        b.close()


class _TunnelServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False


class GuardedTunnel:
    """A loopback CONNECT proxy that lets git reach only what ``policy`` allows, per connection.

    Use as a context manager; ``port`` is where it listens. ``refused`` lists the connections it
    refused on the policy's grounds and ``unreachable`` the hosts it could not resolve or dial.
    """

    def __init__(self, policy: EgressPolicy) -> None:
        self.policy = policy
        self.refused: list[TunnelRefusal] = []
        self.unreachable: list[tuple[str, str]] = []
        self._server: _TunnelServer | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("the tunnel is not running")
        return int(self._server.server_address[1])

    def __enter__(self) -> "GuardedTunnel":
        tunnel = self

        class _Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                tunnel._serve(self.request)

        self._server = _TunnelServer(("127.0.0.1", 0), _Handler)
        threading.Thread(
            target=self._server.serve_forever, name="pclaw-git-tunnel", daemon=True
        ).start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _refuse(self, conn: socket.socket, status: str, refusal: TunnelRefusal) -> None:
        self.refused.append(refusal)
        _audit(
            self.policy, f"{refusal.host}:{refusal.port}", outcome="denied", reason=refusal.reason
        )
        _answer(conn, status)

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(_HEAD_TIMEOUT_S)
            try:
                head, early = _read_head(conn)
            except (OSError, ValueError):
                return
            parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
            if len(parts) != 3:
                _answer(conn, "400 Bad Request")
                return
            method, target = parts[0].upper(), parts[1]
            if method != "CONNECT":
                host = (urlsplit(target).hostname or target).lower()
                reason = "only HTTPS connections are allowed"
                self._refuse(
                    conn, "405 Method Not Allowed", TunnelRefusal(host, 0, "", "method", reason)
                )
                return
            split = _split_target(target)
            if split is None:
                _answer(conn, "400 Bad Request")
                return
            host, port = split
            if port != HTTPS_PORT:
                reason = f"only port {HTTPS_PORT} is allowed, not {port}"
                self._refuse(conn, "403 Forbidden", TunnelRefusal(host, port, "", "port", reason))
                return
            self._connect(conn, host, port, early)
        finally:
            conn.close()

    def _connect(self, conn: socket.socket, host: str, port: int, early: bytes) -> None:
        url_host = f"[{host}]" if ":" in host else host
        decision = evaluate(f"https://{url_host}/", self.policy)
        target = f"{host}:{port}"
        if not decision.allow:
            if decision.category == "unresolvable":
                self.unreachable.append((host, "it does not resolve"))
                _audit(self.policy, target, outcome="failed", reason=decision.reason)
                _answer(conn, "502 Bad Gateway")
                return
            refusal = TunnelRefusal(
                host, port, decision.address, decision.category, decision.reason
            )
            self._refuse(conn, "403 Forbidden", refusal)
            return
        upstream: socket.socket | None = None
        failure = ""
        for ip in decision.pinned_ips:
            try:
                # Module attribute, looked up per call: a test decides where an address leads.
                upstream = socket.create_connection((ip, port), timeout=_DIAL_TIMEOUT_S)
                break
            except OSError as exc:
                failure = f"{ip}: {exc}"
        if upstream is None:
            reason = failure or "no address answered"
            self.unreachable.append((host, reason))
            _audit(self.policy, target, outcome="failed", reason=reason)
            _answer(conn, "502 Bad Gateway")
            return
        _audit(self.policy, target, outcome="allowed")
        try:
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            if early:
                upstream.sendall(early)
        except OSError:
            upstream.close()
            return
        conn.settimeout(None)
        upstream.settimeout(None)
        _relay(conn, upstream, idle_timeout=self.policy.timeout_s)


def _answer(conn: socket.socket, status: str) -> None:
    try:
        conn.sendall(
            f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
    except OSError:
        pass


def preflight(url: str, policy: EgressPolicy) -> GuardDecision:
    """Judge *url* before git runs, and audit a refusal the way the tunnel does.

    Resolves the host now and checks every address, so a refusal lands before a process is
    spawned. It is not what makes the fetch safe: the tunnel judges the host again when git
    connects to it, which is the check a name that rebinds in between cannot pass."""
    decision = evaluate(url, policy)
    if not decision.allow:
        outcome = "failed" if decision.category == "unresolvable" else "denied"
        _audit(policy, url, outcome=outcome, reason=decision.reason)
    return decision


def run_git_guarded(
    args: list[str], *, policy: EgressPolicy, timeout: float, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` with every connection it makes held to ``policy``.

    Returns the finished process, whatever its exit status, when the tunnel refused nothing.
    Raises :class:`GitEgressRefused` when git asked for a forbidden address (even if git then
    exited 0: something tried to reach where it may not), :class:`GitHostUnreachable` when git
    failed and a host did not resolve or answer, and ``subprocess.TimeoutExpired`` past ``timeout``.
    """
    with GuardedTunnel(policy) as tunnel:
        proc = subprocess.run(
            [
                "git",
                "-c",
                f"http.proxy=http://127.0.0.1:{tunnel.port}",
                # A listing fetch presents none of the owner's saved credentials to anyone.
                "-c",
                "credential.helper=",
                *args,
            ],
            env=guarded_git_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    if tunnel.refused:
        raise GitEgressRefused(tunnel.refused[0])
    if proc.returncode != 0 and tunnel.unreachable:
        raise GitHostUnreachable(*tunnel.unreachable[0])
    return proc
