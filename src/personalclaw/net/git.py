"""How PersonalClaw runs git: its environment, its settings, and the guarded fetch.

**Every git PersonalClaw spawns** (the state history, the updater, the loop and workflow
worktrees, the Store reading and cloning a source, the file browser, the review and self-QA
reads, the doctor's probe) runs with the environment :func:`git_env` builds: the child allowlist
(``sandbox.build_child_env``), never a copy of the gateway's environment, which holds every
secret saved in PersonalClaw. Git starts helpers of its own (a remote helper, a credential helper,
a filter, ssh), and none of them needs those secrets.

**A git that runs inside a repository an agent can write** also runs with :func:`git_argv`: the
settings that stop the repository's own configuration from running a program, given on git's
command line, where no configuration file can override them, and after the caller's own options,
where no caller option can either. An agent's shell can write a repository's ``.git`` directory
as easily as its files, so a hook, an fsmonitor command or an ssh command set there would
otherwise run as the gateway the next time PersonalClaw asked git anything.

**The guarded fetch.** ``net/client.fetch`` closes the DNS-rebind window by dialing the addresses
it validated, which it can do because it owns the socket. ``git`` owns its own: it resolves the
name itself, after any check made before it ran, and follows a redirect to wherever the server
says. So a check in front of ``git clone`` checks a name, not the connection.
:func:`run_git_guarded` hands git no socket to the outside world. It points git at a loopback
HTTP CONNECT tunnel (``http.proxy``), so every connection git makes (the first, and each redirect
hop) arrives here as ``CONNECT host:443``. The tunnel asks :func:`personalclaw.net.guard.evaluate`
about that host at that moment, dials only an address the guard returned, and refuses everything
else. What git may speak is pinned as well: HTTPS only (``GIT_ALLOW_PROTOCOL``), no saved
credentials, no configuration from the owner's global or system files, and none of the
environment that would send it around the tunnel (a proxy of the environment's own, ``NO_PROXY``,
injected ``-c`` settings).

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
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from personalclaw.net.guard import GuardDecision, evaluate
from personalclaw.net.policy import EgressPolicy

logger = logging.getLogger(__name__)

#: The git subcommands that talk to a remote. Only these get the SSH agent (:func:`git_env`) and
#: the owner's own ssh command and credential helpers (:func:`git_argv`).
REMOTE_SUBCOMMANDS = frozenset(
    {"clone", "fetch", "ls-remote", "pull", "push", "remote", "submodule"}
)

#: git's options that come before the subcommand and take the next argument as their value.
_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--attr-source"}
)

#: Subcommands that print a diff, and so would run an external diff program or a textconv
#: filter the repository configures for a path. Each gets ``--no-ext-diff --no-textconv``.
_DIFF_SUBCOMMANDS = frozenset({"diff", "diff-files", "diff-index", "diff-tree", "log", "show"})

#: The ``-c`` settings that stop a repository's own configuration from running a program. Each is
#: a key git reads a program (or a switch that runs one) from; a value given on the command line
#: wins over every configuration file. ``core.sshCommand`` and the credential helpers are set by
#: :func:`git_argv` itself, because a command that talks to a remote keeps the owner's own.
_NEUTRAL_SETTINGS: tuple[str, ...] = (
    # Hooks: none runs, from the repository's hooks directory or any other.
    f"core.hooksPath={os.devnull}",
    # A file-system monitor is a program git starts for status, diff and add.
    "core.fsmonitor=false",
    # The programs git asks for a password or an edit. Nobody is at the gateway's terminal.
    "core.askPass=",
    "core.editor=true",
    "sequence.editor=true",
    # An external diff program.
    "diff.external=",
    # Transports that run a program named by a URL or a remote's settings: `ext::` runs a
    # command, a local path runs `uploadpack` or `receivepack` (and a push there runs the other
    # repository's hooks), and `git://` runs `core.gitProxy`. Refused by name, because a
    # repository's own setting for a named transport beats a default policy.
    "protocol.ext.allow=never",
    "protocol.file.allow=never",
    "protocol.git.allow=never",
    # A fetch in a repository that borrows objects from another (`objects/info/alternates`)
    # lists that repository's refs with this command. `true` lists none.
    "core.alternateRefsCommand=true",
    # Signing and signature checks run `gpg.program`. PersonalClaw's git signs and checks
    # nothing, and prints no signature status (`%G?`) in a format the repository chose.
    "commit.gpgSign=false",
    "tag.gpgSign=false",
    "tag.forceSignAnnotated=false",
    "push.gpgSign=false",
    "log.showSignature=false",
    "merge.verifySignatures=false",
    "format.pretty=medium",
    # A garbage collection git starts on its own after a commit or a fetch asks
    # `gc.recentObjectsHook` which old objects to keep. Settings can only add to that hook, so
    # the collection prunes nothing instead, and never needs to ask.
    "gc.pruneExpire=never",
)

#: The owner's own settings a command that talks to a remote keeps: their ssh command and their
#: credential helpers, read from their own configuration files only.
_OWNER_AUTH_KEYS = r"^(core\.sshcommand|credential\..*helper)$"

#: Why each transport the neutral settings refuse is refused, and what to use instead: what the
#: owner reads when PersonalClaw's git will not reach a remote, in place of git's own
#: ``transport 'file' not allowed``.
TRANSPORT_REFUSALS: dict[str, str] = {
    "file": (
        "PersonalClaw's git does not reach a remote at a local path, because git would run that "
        "repository's own hooks and its upload or receive program on this machine, as "
        "PersonalClaw. Reach it over ssh or https instead."
    ),
    "ext": (
        "PersonalClaw's git does not reach an ext:: remote, because git would run the command its "
        "URL names. Reach it over ssh or https instead."
    ),
    "git": (
        "PersonalClaw's git does not reach a git:// remote, because the proxy command a "
        "repository sets for it would run, and git:// signs nothing in. Reach it over ssh or "
        "https instead."
    ),
}

_REFUSED_TRANSPORT = re.compile(r"transport '([A-Za-z0-9+.-]+)' not allowed")
_URL_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")
_DOS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def remote_refusal(url: str) -> str:
    """Why PersonalClaw's git will not reach the remote at *url*, or ``""`` when it will.

    Read the way git reads a remote: ``<transport>::<address>`` names a remote helper, a URL names
    its scheme, a ``host:path`` with no slash before the colon is ssh, and anything else is a path
    on this machine. For a caller that knows the URL before it runs git (a setting the owner
    typed), so the refusal is said where the URL was given."""
    text = (url or "").strip()
    if not text:
        return ""
    head = text.split("/", 1)[0]
    if "::" in head:
        return TRANSPORT_REFUSALS["ext"] if head.split("::", 1)[0].lower() == "ext" else ""
    scheme = _URL_SCHEME.match(text)
    if scheme:
        name = scheme.group(1).lower()
        return TRANSPORT_REFUSALS[name] if name in ("file", "git") else ""
    colon, slash = text.find(":"), text.find("/")
    if colon == -1 or (slash != -1 and slash < colon) or _DOS_DRIVE.match(text):
        return TRANSPORT_REFUSALS["file"]
    return ""


def transport_refusal(stderr: "str | bytes | None") -> str:
    """git's refusal of a transport in *stderr*, in PersonalClaw's words, or ``""`` when git
    refused none (or refused one only the owner's own configuration does)."""
    if not stderr:
        return ""
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else str(stderr)
    found = _REFUSED_TRANSPORT.search(text)
    return TRANSPORT_REFUSALS.get(found.group(1).lower(), "") if found else ""


def git_env(*, site: str, remote: bool = False) -> dict[str, str]:
    """The environment every git PersonalClaw runs gets.

    The child allowlist (``sandbox.build_child_env``), never a copy of the gateway's environment:
    ``PATH``, the home its configuration reads, the proxy and certificate settings
    (``GIT_SSL_CAINFO`` and ``GIT_SSL_CAPATH`` included). It leaves out an inherited ``GIT_DIR``,
    ``GIT_WORK_TREE`` or ``GIT_INDEX_FILE``, which would point git at another repository, and
    every ``GIT_*`` variable that would override a setting of :func:`git_argv`. Git never waits on
    a password prompt: nobody is at the gateway's terminal to answer it.

    git's messages are in English whatever the owner's locale (``LANGUAGE=en``): PersonalClaw reads
    some of them (a refused transport, a rejected push, nothing to commit) and shows them beside its
    own words.

    *remote* is for a command that talks to a remote (:data:`REMOTE_SUBCOMMANDS`). It adds the SSH
    agent's socket, ``SSH_AUTH_SOCK``, which git over SSH needs to sign in with the owner's keys
    (``build_child_env``'s ``ssh_agent``): a command that stays on this machine never gets it.
    """
    from personalclaw.sandbox import build_child_env

    return build_child_env(
        site=site, extra={"GIT_TERMINAL_PROMPT": "0", "LANGUAGE": "en"}, ssh_agent=remote
    )


def _subcommand_index(args: Sequence[str]) -> int:
    """Where the subcommand is in *args*, past git's own leading options; ``len(args)`` if none."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _GLOBAL_OPTIONS_WITH_VALUE:
            i += 2
        elif arg.startswith("-"):
            i += 1
        else:
            return i
    return len(args)


def talks_to_remote(args: Sequence[str]) -> bool:
    """Whether ``git <args>`` is a command that talks to a remote."""
    i = _subcommand_index(args)
    return i < len(args) and args[i] in REMOTE_SUBCOMMANDS


def _owner_auth_settings() -> list[str]:
    """``key=value`` for the owner's own ssh command and credential helpers, in git's order.

    Read by a git that runs in an empty directory of its own, below which no repository is looked
    for, so every file it reads is the owner's: the system file (and the one a git distribution
    bundles), the global file, the XDG one and whatever they include. What the read's own command
    line sets (``git_argv``'s settings) is dropped by its origin. A read that fails gives nothing:
    the command then signs in with no helper, and says so if the remote needs one.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="pclaw-gitconfig-") as outside:
            env = git_env(site="git-owner-config")
            env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(outside)
            proc = subprocess.run(
                git_argv(["config", "--show-origin", "--null", "--get-regexp", _OWNER_AUTH_KEYS]),
                cwd=outside,
                env=env,
                capture_output=True,
                timeout=10,
            )
        out = proc.stdout
        text = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out or "")
    except Exception:  # noqa: BLE001 - a read that fails keeps none of the owner's settings
        logger.debug("could not read the owner's git sign-in settings", exc_info=True)
        return []
    if proc.returncode not in (0, 1):  # 1 is "nothing matched"
        return []
    fields = text.split("\0")
    settings: list[str] = []
    for origin, entry in zip(fields[0::2], fields[1::2]):
        key, _, value = entry.partition("\n")
        if origin.startswith("file:") and key:
            settings.append(f"{key}={value}")
    return settings


def git_argv(args: Sequence[str], *, git: str = "git") -> list[str]:
    """The argv of a git PersonalClaw runs inside a repository an agent can write.

    *args* with the settings that stop the repository's own configuration from running a program
    put in front of the subcommand: after the caller's own leading options, so none of those can
    undo one, and on the command line, where a configuration file cannot either. What is stopped:
    hooks, a file-system monitor, the password and editor programs, an external diff (and, for a
    command that prints a diff, the repository's diff drivers and textconv filters), the pager,
    the ``ext``, ``file`` and ``git`` transports (so a remote at a local path is refused), the
    command that lists a borrowed repository's refs, signing and signature checks, the hook a
    garbage collection asks about old objects, the repository's ssh command and its credential
    helpers.

    A command that talks to a remote (:data:`REMOTE_SUBCOMMANDS`) signs in as the owner would:
    their own ssh command and credential helpers, read from their own configuration files (never
    the repository's), are set again after the repository's are cleared. Any other command gets
    plain ``ssh`` and no helper at all.

    What this cannot stop: a filter or merge driver the repository defines and assigns to its own
    files through its attributes. Git names those drivers by the repository's own words, so no
    fixed setting reaches them; they run with :func:`git_env`'s environment. And over ssh, the
    upload-pack or receive-pack command a repository sets for a remote is what the remote host is
    asked to run.
    """
    args = list(args)
    i = _subcommand_index(args)
    if i == len(args):
        # `git --version -c …` reads the settings as `version`'s own arguments and fails, so a
        # command without a subcommand has nowhere to put them. Ask for `version` instead.
        raise ValueError(f"git_argv needs a subcommand, got {args!r}")
    remote = args[i] in REMOTE_SUBCOMMANDS
    settings = list(_NEUTRAL_SETTINGS)
    if remote:
        owner = _owner_auth_settings()
        ssh = [s for s in owner if s.lower().startswith("core.sshcommand=")]
        settings.append(ssh[-1] if ssh else "core.sshCommand=ssh")
        settings.append("credential.helper=")
        settings += [s for s in owner if not s.lower().startswith("core.sshcommand=")]
    else:
        settings += ["core.sshCommand=ssh", "credential.helper="]
    neutral = ["--no-pager"]
    for setting in settings:
        neutral += ["-c", setting]
    tail = args[i:]
    if tail[0] in _DIFF_SUBCOMMANDS:
        tail = [tail[0], "--no-ext-diff", "--no-textconv", *tail[1:]]
    return [git, *args[:i], *neutral, *tail]


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


def guarded_git_env() -> dict[str, str]:
    """The environment a guarded git runs with (see the module docstring for why each part).

    Built on :func:`git_env` without the SSH agent: the listing fetch speaks HTTPS only."""
    env = {
        k: v
        for k, v in git_env(site="app-listing-git").items()
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
