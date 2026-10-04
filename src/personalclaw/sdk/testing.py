"""SDK: what an app's test suite needs to keep its tests off the machine it runs on.

An app's tests run on a developer's machine, and some of what core reaches there is not
inside any PersonalClaw home. ``keychain_off()`` is the one such switch today. The OS keychain
is the machine's, not a home's: a scratch ``PERSONALCLAW_HOME`` keeps core to that home's own
namespace there, but core still writes the machine's keychain and deletes from it whenever
``keyring`` is importable, and a test that runs on the default home reads the owner's real
secrets. After the call, core finds no keychain and credentials live in the scratch home's
``.env``. It returns the call that lets the keychain back in. A
``conftest.py``'s ``pytest_configure`` turns it off before anything is collected, and
``pytest_unconfigure`` calls what it returned.

A test on a developer's machine can also reach a server running there: a local model server
answers on a well-known port (Ollama's 11434), and a test that asked it which models it has,
found one pulled, and ran it loaded gigabytes on every run. ``refuse_ports(ports, what=...)``
refuses, in this process, every TCP connection to one of *ports* before it is made, unless this
process is itself listening there (a test's own fake). The code under test sees what it would see
with no server there; ``take()`` names each refusal, with the test that asked, so a
``conftest.py`` fails that test by name. Each suite passes the ports it keeps out: which servers
a suite's code talks to is the suite's knowledge, not core's. Any other server on the machine is
reached the same way, and no port list names them all, so ``loopback=True`` refuses a connection
to any port on this machine the process has not opened itself: a test reaches only the servers
it started.

A test's git reaches the machine too: it reads the machine's git configuration, and on a Mac the
file Apple's git bundles names ``osxkeychain``, which hands every credential git signs in with to
the owner's real keychain, and asks the keychain for the ones it needs. A test's own ``git clone``
of a URL with a token in it once told that helper to keep the token, and it waited on the owner's
keychain for ten minutes. ``neutral_git_env(global_config)`` is the environment a suite's git
should inherit: no system configuration, a global file of the test's own, and an empty
``credential.helper`` on git's command line. ``refuse_git_helpers(real_home=..., own=...)``
refuses, before it starts, any git this process starts that could still sign in with a helper of
the machine's own; ``take()`` names each refusal, with the test that asked, so a ``conftest.py``
fails that test by name. Core's own git reads the same files (``personalclaw.sdk.git.git_env``
keeps ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_NOSYSTEM``).

A test that loads a model library reaches the machine as well. Some of those libraries write
outside any home by themselves, or report on their use to the people who make them: onnxruntime
starts its maker's telemetry as it loads, a device identifier and a queue of events about the
machine kept in a folder of the maker's, and huggingface_hub keeps a list it fetches in the
Hugging Face folder other tools share. Every ``personalclaw`` command tells them not to, with each
library's own setting. ``library_env()`` is those settings, for the home this process runs on: a
suite's ``pytest_configure`` sets them before anything is collected, so its tests load the
libraries the way PersonalClaw does.

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
import functools
import inspect
import ipaddress
import os
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import weakref
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from personalclaw.config.credentials import keychain_off  # noqa: F401
from personalclaw.library_env import library_env  # noqa: F401

__all__ = [
    "GitGuard",
    "PortGuard",
    "keychain_off",
    "launch_acp_entry",
    "library_env",
    "neutral_git_env",
    "refuse_git_helpers",
    "refuse_ports",
]

_INET = (socket.AF_INET, socket.AF_INET6)


def _on_this_machine(host: object) -> bool:
    """Whether a ``connect`` to *host* reaches this machine: a loopback address, the unspecified
    address (which a connect sends to this machine), or a ``localhost`` name (RFC 6761 keeps it and
    every name under it for this machine). A name is not looked up, so a name that only resolves
    here is not seen."""
    if not isinstance(host, str):
        return False
    name = host.split("%", 1)[0].rstrip(".").lower()
    if not name or name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return address.is_loopback or address.is_unspecified


class PortGuard:
    """Refuses a TCP ``connect`` to a port that is not this process's own.

    A connection to one of :attr:`ports`, at any address, is refused unless this process is
    listening on that port now (a test's own fake). With :attr:`loopback` set, a connection to ANY
    port on this machine is refused too unless this process holds it: it bound the port itself (a
    fake it serves, a free port it picked and handed a child it started, or one it bound and let go
    to stand for a server that is not there), or :meth:`own` declared it (a port a child it started
    chose for itself).

    What it cannot see: a connection a child process makes (it has its own sockets), a connection
    made below ``socket.socket``, a name that resolves to this machine other than ``localhost``, and
    a server on a port that is not in :attr:`ports` at one of this machine's network addresses.
    """

    def __init__(self, ports: Iterable[int], *, what: str, loopback: bool = False) -> None:
        self.ports = frozenset(int(port) for port in ports)
        self.what = what
        self.loopback = loopback
        self._listeners: weakref.WeakSet[socket.socket] = weakref.WeakSet()
        self._held: set[int] = set()
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

    def held_ports(self) -> set[int]:
        """The ports this process has held: every port it bound, and every port :meth:`own`
        declared."""
        with self._lock:
            return set(self._held)

    def listened(self, sock: socket.socket) -> None:
        """Note *sock* as listening: a port it holds is this process's own."""
        with self._lock:
            self._listeners.add(sock)

    def bound(self, sock: socket.socket) -> None:
        """Note the port *sock* is bound to as held by this process."""
        if sock.family not in _INET:
            return
        try:
            port = int(sock.getsockname()[1])
        except (OSError, IndexError, TypeError, ValueError):
            return
        self.own(port)

    def own(self, port: int) -> None:
        """Hold *port* for this process: a server on it is one this test started, such as a child
        that chose its port itself and said which."""
        if port:
            with self._lock:
                self._held.add(int(port))

    def check(self, sock: socket.socket, address: object) -> None:
        """Raise ``ConnectionRefusedError`` for a port that is not this process's own."""
        if sock.family not in _INET or not isinstance(address, tuple) or len(address) < 2:
            return
        host, port = address[0], address[1]
        if not isinstance(port, int):
            return
        if port in self.ports:
            if port not in self.own_ports():
                self._refuse(host, port, self.what, "a real one")
            return
        if (
            self.loopback
            and _on_this_machine(host)
            and port not in self.held_ports()
            and port not in self.own_ports()
        ):
            self._refuse(
                host,
                port,
                "a port on this machine that this process did not open",
                "a server it did not start",
            )

    def _refuse(self, host: object, port: int, what: str, reached: str) -> None:
        who = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0] or "(no test)"
        with self._lock:
            self._refused.append(f"{who} -> {host}:{port}")
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"refused by the test suite: {host}:{port} is {what}, and a test must not reach "
            f"{reached}. Fake it: a server this test starts on its own port, or a mocked "
            "transport.",
        )

    def take(self) -> list[str]:
        """The refusals since the last take (``"<test> -> <host>:<port>"``), cleared."""
        with self._lock:
            refused, self._refused = self._refused, []
        return refused

    def install(self) -> None:
        """Wrap ``socket.socket``'s ``bind``, ``listen``, ``connect`` and ``connect_ex`` for this
        process, until :meth:`undo`."""
        real_bind = socket.socket.bind
        real_listen = socket.socket.listen
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        guard = self

        def bind(sock, address):
            result = real_bind(sock, address)
            guard.bound(sock)
            return result

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

        socket.socket.bind = bind  # type: ignore[method-assign]
        socket.socket.listen = listen  # type: ignore[method-assign]
        socket.socket.connect = connect  # type: ignore[method-assign]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
        self._undo.append((real_bind, real_listen, real_connect, real_connect_ex))

    def undo(self) -> None:
        """Put ``socket.socket`` back as :meth:`install` found it."""
        while self._undo:
            real_bind, real_listen, real_connect, real_connect_ex = self._undo.pop()
            socket.socket.bind = real_bind  # type: ignore[method-assign]
            socket.socket.listen = real_listen  # type: ignore[method-assign]
            socket.socket.connect = real_connect  # type: ignore[method-assign]
            socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]


def refuse_ports(ports: Iterable[int], *, what: str, loopback: bool = False) -> PortGuard:
    """Refuse every connection this process makes to one of *ports* (*what* says what they are,
    for the refusal) and, with *loopback*, to any port on this machine it has not opened itself,
    and return the installed guard. A ``conftest.py`` calls it before anything is collected, asks
    :meth:`PortGuard.take` after each test, and calls :meth:`PortGuard.undo` when the run is
    done."""
    guard = PortGuard(ports, what=what, loopback=loopback)
    guard.install()
    return guard


#: The git subcommands that can sign in, and so ask a credential helper: the ones that talk to a
#: remote, and the ones that run git's credential machinery for another program. A
#: ``remote-<transport>`` helper, which a fetch or a push starts, can too; a ``credential-<helper>``
#: subcommand is a helper itself.
_SIGNS_IN = frozenset(
    {
        "archive",
        "clone",
        "credential",
        "fetch",
        "fetch-pack",
        "http-fetch",
        "http-push",
        "imap-send",
        "lfs",
        "ls-remote",
        "maintenance",
        "pull",
        "push",
        "remote",
        "request-pull",
        "send-email",
        "send-pack",
        "submodule",
    }
)

#: git's options before the subcommand that take the next argument as their value.
_VALUE_OPTIONS = frozenset(
    {"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--attr-source"}
)


def neutral_git_env(global_config: "os.PathLike[str] | str") -> dict[str, str]:
    """The variables every git a test suite runs should inherit: no system configuration (the
    file a git distribution bundles included), *global_config* in place of ``~/.gitconfig`` (it
    need not exist: git reads a missing one as empty, and makes it on a write), and an empty
    ``credential.helper`` on git's command line, which clears any helper a file names."""
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.fspath(global_config),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
    }


def _true(value: str | None) -> bool:
    """git's reading of a boolean variable."""
    if value is None:
        return False
    text = value.strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    try:
        return int(text) != 0
    except ValueError:
        return False


def _git_words(
    args: Any, executable: Any, env: Mapping[str, str]
) -> tuple[list[str], dict[str, str]] | None:
    """``(argv, env)`` of the git *args* starts — through ``env`` and its assignments, if that is
    how it starts — or ``None`` when it does not start git. *args* and *executable* are what a
    ``subprocess.Popen`` was given."""
    if isinstance(args, (str, bytes, os.PathLike)):
        args = [args]
    try:
        words = [os.fsdecode(arg) for arg in args]
        program = os.fsdecode(executable) if executable is not None else ""
    except TypeError:
        return None
    if os.path.basename(program) == "git":
        return ["git", *words[1:]], dict(env)
    env = dict(env)
    while words and os.path.basename(words[0]) == "env":
        words = words[1:]
        while words:
            word = words[0]
            if word in ("-i", "-", "--ignore-environment"):
                env = {}
            elif word in ("-u", "--unset") and len(words) > 1:
                env.pop(words[1], None)
                words = words[1:]
            elif word.startswith("-"):
                pass
            elif "=" in word and not word.startswith("/"):
                name, _, value = word.partition("=")
                env[name] = value
            else:
                break
            words = words[1:]
    if words and os.path.basename(words[0]) == "git":
        return words, env
    return None


def _subcommand(
    words: Sequence[str], env: Mapping[str, str], cwd: str
) -> tuple[str, list[tuple[str, str]], str, str | None]:
    """git's subcommand in *words*, the settings its command line gives before it, the folder it
    runs in (its ``-C`` options applied to *cwd*), and the ``--git-dir`` it names."""
    settings: list[tuple[str, str]] = []
    where, git_dir = cwd, None
    i = 1
    while i < len(words):
        word = words[i]
        if word == "-c" and i + 1 < len(words):
            key, eq, value = words[i + 1].partition("=")
            settings.append((key, value if eq else "true"))
            i += 2
        elif (word == "--config-env" and i + 1 < len(words)) or word.startswith("--config-env="):
            spec = words[i + 1] if word == "--config-env" else word.split("=", 1)[1]
            key, _, variable = spec.partition("=")
            settings.append((key, env.get(variable, "")))
            i += 2 if word == "--config-env" else 1
        elif word == "-C" and i + 1 < len(words):
            where = os.path.join(where, words[i + 1])
            i += 2
        elif word == "--git-dir" and i + 1 < len(words):
            git_dir = os.path.join(where, words[i + 1])
            i += 2
        elif word.startswith("--git-dir="):
            git_dir = os.path.join(where, word.split("=", 1)[1])
            i += 1
        elif word in _VALUE_OPTIONS:
            i += 2
        elif word.startswith("-"):
            i += 1
        else:
            return word, settings, where, git_dir
    return "", settings, where, git_dir


def _command_line_helpers(env: Mapping[str, str], settings: Iterable[tuple[str, str]]) -> list[str]:
    """The ``credential.helper`` values git's command line gives, in git's order:
    ``GIT_CONFIG_COUNT``'s, then ``GIT_CONFIG_PARAMETERS``', then the ``-c`` ones. ``""`` is one
    that clears every helper before it. A URL's own helper (``credential.<url>.helper``) is
    counted whatever URL it is for."""
    sequence: list[tuple[str, str]] = []
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        count = 0
    for n in range(count):
        sequence.append((env.get(f"GIT_CONFIG_KEY_{n}", ""), env.get(f"GIT_CONFIG_VALUE_{n}", "")))
    try:
        parameters = shlex.split(env.get("GIT_CONFIG_PARAMETERS", ""))
    except ValueError:
        parameters = []
    for parameter in parameters:
        key, eq, value = parameter.partition("=")
        sequence.append((key, value if eq else "true"))
    sequence += list(settings)
    values: list[str] = []
    for key, value in sequence:
        key = key.strip().lower()
        if key == "credential.helper":
            values.append(value)
        elif key.startswith("credential.") and key.endswith(".helper") and value:
            values.append(value)
    return values


def _effective(values: Iterable[str]) -> list[str]:
    """The helpers git runs from its ``credential.helper`` values: an empty one clears the ones
    before it."""
    helpers: list[str] = []
    for value in values:
        helpers = [*helpers, value] if value else []
    return helpers


#: A section header of a git configuration file: ``[credential]``, ``[credential "<url>"]``.
_SECTION = re.compile(r'^\s*\[\s*([A-Za-z0-9.-]+)(?:\s+"(?:[^"\\]|\\.)*")?\s*\]')


def _file_helpers(path: str, depth: int = 0) -> list[str] | None:
    """The ``credential.helper`` values the git configuration file *path* gives, in order, with
    those of every file it includes (``include.path``, ``includeIf.<condition>.path``, all of
    them whatever their condition); ``[]`` for a file that isn't there, and ``None`` for one that
    can't be read, or includes too deep to follow."""
    if depth > 8:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError:
        return None
    values: list[str] = []
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        header = _SECTION.match(line)
        if header:
            section = header.group(1).lower()
            continue
        key, eq, value = line.partition("=")
        key, value = key.strip().lower(), value.strip() if eq else "true"
        if len(value) > 1 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if section == "credential" and key == "helper":
            values.append(value)
        elif section in ("include", "includeif") and key == "path":
            included = os.path.expanduser(value)
            if not os.path.isabs(included):
                included = os.path.join(os.path.dirname(path), included)
            more = _file_helpers(included, depth + 1)
            if more is None:
                return None
            values += more
    return values


def _repository_configs(where: str, git_dir: str | None, env: Mapping[str, str]) -> list[str]:
    """The configuration files of the repository a git run in *where* reads: its own and its
    worktree's. ``[]`` when it runs in none."""
    found = git_dir or env.get("GIT_DIR")
    if found:
        found = os.path.join(where, found)
    else:
        ceilings = {
            os.path.realpath(c)
            for c in env.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep)
            if c
        }
        folder = os.path.realpath(where)
        while found is None:
            dot = os.path.join(folder, ".git")
            if os.path.isdir(dot):
                found = dot
            elif os.path.isfile(dot):
                try:
                    pointer = Path(dot).read_text(encoding="utf-8").strip()
                except OSError:
                    pointer = ""
                named = pointer.split(":", 1)[1].strip() if pointer.startswith("gitdir:") else ""
                found = os.path.join(folder, named) if named else dot
            elif all(os.path.exists(os.path.join(folder, n)) for n in ("HEAD", "objects", "refs")):
                found = folder  # a bare repository
            else:
                parent = os.path.dirname(folder)
                if parent == folder or parent in ceilings:
                    return []
                folder = parent
    common = found
    try:
        common = os.path.join(found, Path(found, "commondir").read_text(encoding="utf-8").strip())
    except OSError:
        pass
    return [os.path.join(common, "config"), os.path.join(found, "config.worktree")]


class GitGuard:
    """Refuses a git that could sign in with a credential helper of the machine's own: one that
    reads the system files or the global file of whoever runs the tests, or would run a helper git
    finds on the machine rather than one the test wrote — named on its command line, or by a
    configuration file it reads (the global one, its repository's, and what they include) when
    nothing on its command line clears the helpers the files name.

    *real_home* is the home of whoever runs the tests, whose ``~/.gitconfig`` (and XDG one) is
    theirs; *own* are the folders a test's own helper may be in: the temporary folders, and
    whatever else the suite names (its repository).

    What it cannot see: a git a shell starts from a command line, and a git another program
    starts. Both inherit :func:`neutral_git_env` unless whatever starts them hands them an
    environment of its own."""

    def __init__(self, *, real_home: str, own: Iterable[str] = ()) -> None:
        self.real_home = os.path.realpath(real_home)
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(real_home, ".config")
        self.real_xdg = os.path.realpath(xdg)
        self.real_global = {
            os.path.realpath(os.path.join(self.real_home, ".gitconfig")),
            os.path.realpath(os.path.join(self.real_xdg, "git", "config")),
        }
        roots = [tempfile.gettempdir(), "/tmp", *own]
        self.own = tuple(sorted({os.path.realpath(root) for root in roots}))
        self._refused: list[str] = []
        self._lock = threading.Lock()
        self._undo: list = []

    @staticmethod
    def _global_files(env: Mapping[str, str]) -> list[str]:
        """The global git files *env* has git read, in git's order."""
        if "GIT_CONFIG_GLOBAL" in env:
            named = env["GIT_CONFIG_GLOBAL"]
            return [os.path.expanduser(named)] if named else []
        home = env.get("HOME")
        xdg = env.get("XDG_CONFIG_HOME") or (os.path.join(home, ".config") if home else "")
        files = [os.path.join(xdg, "git", "config")] if xdg else []
        return files + ([os.path.join(home, ".gitconfig")] if home else [])

    def _own_global(self, env: Mapping[str, str]) -> str:
        """The owner's own global git file *env* would have git read, or ``""``."""
        if "GIT_CONFIG_GLOBAL" in env:
            named = env["GIT_CONFIG_GLOBAL"]
            real = os.path.realpath(os.path.expanduser(named)) if named else ""
            return named if real in self.real_global else ""
        home = env.get("HOME")
        if home and os.path.realpath(home) == self.real_home:
            return os.path.join(home, ".gitconfig")
        xdg = env.get("XDG_CONFIG_HOME") or (os.path.join(home, ".config") if home else "")
        if xdg and os.path.realpath(xdg) == self.real_xdg:
            return os.path.join(xdg, "git", "config")
        return ""

    def _machines(self, helper: str) -> bool:
        """Whether *helper* is one git finds on the machine, not one a test wrote: a helper named
        by a bare name (git runs ``git-credential-<name>`` from its own programs, or ``PATH``),
        or a path outside the folders a test's own helper may be in. A ``!`` helper is a command
        its caller wrote (core's token sign-in is one)."""
        text = helper.strip()
        if not text or text.startswith("!"):
            return False
        try:
            first = shlex.split(text)[0]
        except (ValueError, IndexError):
            first = text.split()[0]
        if "/" not in first and not first.startswith("~"):
            return True
        path = os.path.realpath(os.path.expanduser(first))
        return not any(path == root or path.startswith(root + os.sep) for root in self.own)

    def refusal(
        self,
        args: object,
        env: Mapping[str, str],
        executable: object = None,
        cwd: "os.PathLike[str] | str | None" = None,
    ) -> str:
        """Why the command *args*, run with *env* in *cwd* (this process's own when ``None``), is a
        git that could sign in with a helper of the machine's own; ``""`` when it isn't."""
        found = _git_words(args, executable, env)
        if found is None:
            return ""
        words, env = found
        folder = os.fspath(cwd) if cwd is not None else os.getcwd()
        subcommand, settings, where, git_dir = _subcommand(words, env, folder)
        if subcommand.startswith("credential-"):
            return f"runs a credential helper itself (git {subcommand})"
        if subcommand not in _SIGNS_IN and not subcommand.startswith("remote-"):
            return ""
        if not _true(env.get("GIT_CONFIG_NOSYSTEM")):
            return (
                "reads the machine's system git configuration, which can name its keychain "
                "helper (GIT_CONFIG_NOSYSTEM isn't set)"
            )
        own = self._own_global(env)
        if own:
            return f"reads the global git configuration of whoever runs the tests ({own})"
        values = _command_line_helpers(env, settings)
        if "" not in values:  # nothing on the command line clears what the files name
            read: list[str] = []
            for config in [*self._global_files(env), *_repository_configs(where, git_dir, env)]:
                named = _file_helpers(config)
                if named is None:
                    return f"can sign in with a helper {config} names, which can't be read"
                read += named
            values = read + values
        for helper in _effective(values):
            if self._machines(helper):
                return f"signs in with a credential helper of the machine's own ({helper})"
        return ""

    def take(self) -> list[str]:
        """The refusals since the last take (``"<test> -> <why>"``), cleared."""
        with self._lock:
            refused, self._refused = self._refused, []
        return refused

    def install(self) -> None:
        """Check every child this process starts, until :meth:`undo`: a refused git raises
        ``PermissionError`` in place of starting, and is noted for :meth:`take`."""
        real = subprocess.Popen._execute_child  # type: ignore[attr-defined]
        signature = inspect.signature(real)
        guard = self

        # Carries `__wrapped__`, so a guard installed after this one reads the signature of
        # `subprocess`'s own method through it, and binds a launch's arguments by their names.
        @functools.wraps(real)
        def execute_child(popen, *args, **kwargs):
            try:
                call = signature.bind(popen, *args, **kwargs).arguments
            except TypeError:
                return real(popen, *args, **kwargs)
            env = call.get("env")
            why = guard.refusal(
                call.get("args"),
                os.environ if env is None else env,
                call.get("executable"),
                call.get("cwd"),
            )
            if why:
                who = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0] or "(no test)"
                with guard._lock:
                    guard._refused.append(f"{who} -> {why}")
                raise PermissionError(
                    errno.EPERM,
                    f"refused by the test suite: this git {why}, and a test's git must never "
                    "reach the machine's own credential helper, which can be the owner's real "
                    "keychain. Run it with the environment the tests inherit, and name only a "
                    "helper the test wrote itself.",
                )
            return real(popen, *args, **kwargs)

        subprocess.Popen._execute_child = execute_child  # type: ignore[attr-defined]
        self._undo.append(real)

    def undo(self) -> None:
        """Put ``subprocess`` back as :meth:`install` found it."""
        while self._undo:
            subprocess.Popen._execute_child = self._undo.pop()  # type: ignore[attr-defined]


def refuse_git_helpers(*, real_home: str, own: Iterable[str] = ()) -> GitGuard:
    """Refuse every git this process starts that could sign in with a credential helper of the
    machine's own, and return the installed guard (see :class:`GitGuard` for *real_home* and
    *own*). A ``conftest.py`` sets :func:`neutral_git_env` and calls it before anything is
    collected, asks :meth:`GitGuard.take` after each test, and calls :meth:`GitGuard.undo` when
    the run is done."""
    guard = GitGuard(real_home=real_home, own=own)
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
