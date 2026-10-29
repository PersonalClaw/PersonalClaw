"""No test's git reaches a remote repository over the network.

``tests/test_app_catalog.py::test_local_source_apps_surface_in_catalog`` timed out at 30 s on
#5654's run. ``available_catalog()`` scans the app sources, and with ``apps.bundled_source_enabled``
on by default that is a real ``git clone --depth 1`` of github.com/PersonalClaw/PersonalClawApps
(``apps.catalog._scan_git_sources``), which hung in ``communicate``: a unit test depended on GitHub.

So the suite has the shipped sources off in every test (``conftest``'s
``_no_test_lists_the_shipped_app_sources``, which a test that needs them turns back on with the
``shipped_app_sources`` fixture), and two layers keep any git from making the call:

* every git this process starts is checked before it starts (``subprocess.Popen._execute_child``,
  as ``git_helper_guard`` does). One that talks to a remote (``net.git.REMOTE_SUBCOMMANDS``) off
  this machine, by a URL on its command line or a remote its repository's configuration names
  with one (:func:`refusal`), is refused with ``PermissionError`` in place of starting, and
  charged to the test, which ``conftest`` fails by name. A host is judged by its name as written,
  never resolved (:func:`network`): loopback addresses, ``localhost``, and the names reserved never
  to resolve (``example.invalid``, ``listing.test``), which the suite's ssh stand-ins and servers
  of a test's own use, are on this machine, and every other name is off it;
* each test's own global git file (``GIT_CONFIG_GLOBAL``) sends github.com, every way git spells
  it, to a folder that is not there (:func:`seal`), so a git another program starts with the tests'
  environment (a gateway a test runs as a child, and the git that gateway runs) fails at once, with
  no connection made. GitHub is what a new home reaches: the shipped sources, the registry, and the
  checkout's own origin.

A test that needs a remote plants one on disk (``git init --bare``) and names it by its path.

``PCLAW_REMOTE_GIT=report`` notes a remote git and lets it through, and seals no file: the census
of the tests that reach a remote today, without changing what they do (``conftest`` writes it to
``reports/remote-git-<worker>.txt``).

What it cannot see: a git that reads no global file of the test's (one started with an environment
of its own that names another), when this process did not start it; a git another program starts
that reaches a host other than GitHub; a remote command git has outside ``REMOTE_SUBCOMMANDS``
(``archive --remote``); and what an ``ext::`` remote's own command reaches, which git runs only
where ``protocol.ext.allow`` lets it.
"""

from __future__ import annotations

import errno
import functools
import inspect
import ipaddress
import os
import re
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from personalclaw.net.git import _GLOBAL_OPTIONS_WITH_VALUE, REMOTE_SUBCOMMANDS
from personalclaw.sdk.testing import _git_words, _repository_configs, _subcommand

#: ``report``: note and let through (the census); anything else, or unset: refuse.
MODE_ENV = "PCLAW_REMOTE_GIT"

#: Where a test's global git file sends GitHub, every way git spells it: a folder that is not
#: there, so a clone or fetch of it fails at once, with no connection made.
_NOWHERE = "file:///nonexistent/no-test-reaches-github/"

#: What a test's global git file starts with (:func:`seal`).
SEALED = "".join(
    f'[url "{_NOWHERE}"]\n\tinsteadOf = {spelling}\n'
    for spelling in (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
        "github.com:",
        "git://github.com/",
    )
)

#: A URL's host: after a scheme's ``//`` (and any ``user@``), or ssh's ``user@host:path``.
_SCHEME_HOST = re.compile(r"^(?!file://)[a-z][a-z0-9+.-]*://(?:[^/@]*@)?(\[[^\]]*\]|[^/:]*)", re.I)
_SCP_HOST = re.compile(r"^[\w.-]+@(\[[^\]]*\]|[\w.-]+):")
#: ssh's ``host:path`` with no user, as ``clone`` and ``ls-remote`` read it: a word with a colon
#: before any slash. Not for ``fetch``, ``pull`` and ``push``, whose refspecs (``v1.0:v1.0``) have
#: the same shape after the repository.
_SCP_BARE = re.compile(r"^(?:[\w.-]+@)?(\[[^\]]*\]|[\w.-]+):(?!//)")
#: A remote helper's ``<transport>::<address>`` (``ext::``, ``https::``): git hands the address to
#: ``git-remote-<transport>``, so the address is what is judged.
_HELPER = re.compile(r"^[a-z][a-z0-9+.-]*::", re.I)

#: Names that stay on this machine: ``localhost``, and the names reserved never to resolve
#: (RFC 2606 and 6761), which the suite's stand-ins use. ``.localhost`` names are loopback's own.
_NEVER_RESOLVES = (".invalid", ".test", ".example", ".localhost")

#: The ``url`` lines of a git configuration file, by the section they are in.
_SECTION = re.compile(r'^\s*\[\s*(remote|submodule)\s+"([^"]*)"\s*\]')
_URL = re.compile(r"^\s*url\s*=\s*(.+?)\s*$", re.IGNORECASE)


def reporting() -> bool:
    """Whether this run is the census (:data:`MODE_ENV`), which notes and refuses nothing."""
    return os.environ.get(MODE_ENV, "") == "report"


def seal(path: str) -> str:
    """Start the global git file *path* with :data:`SEALED` (the census leaves it unmade), and
    return it."""
    if not reporting():
        Path(path).write_text(SEALED, encoding="utf-8")
    return path


def network(word: str, bare: bool = False) -> bool:
    """Whether *word* is a URL whose host is off this machine.

    Judged by the host as written, never by what it resolves to: resolving a name is itself a call
    to the network, and its answer can change before git connects. On this machine are a loopback
    address (``127.0.0.0/8``, ``::1``), ``localhost``, and a name reserved never to resolve
    (``example.invalid``, ``listing.test``, ``rebind.example``, ``app.localhost``). Any other name
    is off it, one that resolves to loopback too. *bare*: read ``host:path`` with no user as ssh
    too (:data:`_SCP_BARE`). A remote helper's address is judged as its own word: ``ext::`` names
    a command on this machine, which git refuses unless ``protocol.ext.allow`` lets it run."""
    if helper := _HELPER.match(word):
        word = word[helper.end() :]
    found = _SCHEME_HOST.match(word) or (_SCP_BARE if bare else _SCP_HOST).match(word)
    if not found:
        return False
    host = found.group(1).lower().rstrip(".")
    try:
        return not ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        pass
    if host == "localhost":
        return False
    return not any(host == end[1:] or host.endswith(end) for end in _NEVER_RESOLVES)


def _configured_urls(configs: list[str]) -> dict[tuple[str, str], str]:
    """``{(remote or submodule, name): url}`` from the git configuration files *configs*."""
    urls: dict[tuple[str, str], str] = {}
    for path in configs:
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        section: tuple[str, str] | None = None
        for line in lines:
            if head := _SECTION.match(line):
                section = (head.group(1), head.group(2))
            elif line.lstrip().startswith("["):
                section = None
            elif section and (url := _URL.match(line)):
                urls[section] = url.group(1).strip('"')
    return urls


def refusal(args: Any, env: Mapping[str, str], executable: Any = None, cwd: Any = None) -> str:
    """Why the command *args*, run with *env* in *cwd*, reaches a remote over the network, as a
    clause; ``""`` when it does not (not git, a local subcommand, or a local remote).

    A remote is judged by its URL (:func:`network`), found where the command names it: a URL on
    its command line; else each remote it names, by the URL its repository's configuration gives
    it; else, when it names neither a remote nor a repository by path, every remote the
    configuration gives (git picks one of them, and ``remote update`` takes them all); and for
    ``submodule update`` every submodule URL ``.gitmodules`` and the configuration give. A
    ``remote`` that does not connect (``add``, ``get-url``) and a ``submodule`` that does not
    (``status``, ``init``) are not judged, and neither is a git that cannot start (no such
    program, or no folder to run in)."""
    found = _git_words(args, executable, env)
    if found is None:
        return ""
    words, env = found
    programs = [words[0], os.fsdecode(executable) if executable is not None else ""]
    if any(
        os.sep in p and os.path.basename(p) == "git" and not os.path.exists(p) for p in programs
    ):
        return ""  # no such git: it cannot start, so it reaches nothing
    where = os.fspath(cwd) if cwd is not None else os.getcwd()
    sub, _settings, where, git_dir = _subcommand(words, env, where)
    if sub not in REMOTE_SUBCOMMANDS:
        return ""
    if not os.path.isdir(where):
        return ""  # a folder that is not there: git cannot change to it, and stops
    at = next(
        k
        for k, w in enumerate(words)
        if k and w == sub and words[k - 1] not in _GLOBAL_OPTIONS_WITH_VALUE
    )
    rest = words[at + 1 :]
    positional = [w for w in rest if not w.startswith("-")]
    action = positional[0] if positional else ""
    if sub == "remote" and action not in ("update", "show", "prune"):
        return ""  # add, remove, rename, set-url, get-url: none of them connects
    if sub == "submodule" and action not in ("update", "add"):
        return ""  # status, init, sync, foreach, absorbgitdirs: none of them connects
    bare = sub in ("clone", "ls-remote") or (sub == "submodule" and action == "add")
    if urls := [w for w in rest if network(w, bare)]:
        return f"git {sub} {urls[0]}"
    if sub == "clone" or (sub == "submodule" and action == "add"):
        return ""  # it names its source, and that one is on this machine
    configs = list(_repository_configs(where, git_dir, env))
    if sub == "submodule":
        configs.append(os.path.join(where, ".gitmodules"))
        reached = [u for (kind, _), u in _configured_urls(configs).items() if kind == "submodule"]
    else:
        remotes = {n: u for (kind, n), u in _configured_urls(configs).items() if kind == "remote"}
        if sub == "remote":  # update takes every remote; show and prune, the ones they name
            names = remotes if action == "update" else [w for w in positional[1:] if w in remotes]
            reached = [remotes[w] for w in names]
        elif named := [w for w in positional if w in remotes]:
            reached = [remotes[w] for w in named]
        elif any("/" in w or ":" in w or w in (".", "..") for w in positional):
            return ""  # it names its repository by a path, or by a URL on this machine
        else:
            reached = list(remotes.values())  # it names none: git picks one of them
    for url in reached:
        if network(url, bare=True):  # a configured URL is never a refspec
            return f"git {sub} reaches {url}"
    return ""


class RemoteGitGuard:
    """Refuses, or notes in the census, a git that reaches a remote over the network
    (:func:`refusal`), charged to the test running when it starts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._charged: list[str] = []
        self._undo: list = []

    def take(self) -> list[str]:
        """What was charged since the last take (``"<test> -> <why>"``), cleared."""
        with self._lock:
            charged, self._charged = self._charged, []
        return charged

    def install(self) -> None:
        """Check every child this process starts, until :meth:`undo`."""
        real = subprocess.Popen._execute_child  # type: ignore[attr-defined]
        signature = inspect.signature(real)
        guard = self

        @functools.wraps(real)
        def execute_child(popen, *args, **kwargs):
            try:
                call = signature.bind(popen, *args, **kwargs).arguments
            except TypeError:
                return real(popen, *args, **kwargs)
            env = call.get("env")
            why = refusal(
                call.get("args"),
                os.environ if env is None else env,
                call.get("executable"),
                call.get("cwd"),
            )
            if why:
                who = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0] or "(no test)"
                with guard._lock:
                    guard._charged.append(f"{who} -> {why}")
                if not reporting():
                    raise PermissionError(
                        errno.EPERM,
                        f"refused by the test suite: {why}, and a test reaches no remote over the "
                        "network. Plant one on disk (git init --bare) and name it by its path "
                        "(tests/remote_git_guard.py).",
                    )
            return real(popen, *args, **kwargs)

        subprocess.Popen._execute_child = execute_child  # type: ignore[attr-defined]
        self._undo.append(real)

    def undo(self) -> None:
        """Put ``subprocess`` back as :meth:`install` found it."""
        while self._undo:
            subprocess.Popen._execute_child = self._undo.pop()  # type: ignore[attr-defined]


def failure(charged: list[str]) -> str:
    """The failure a test that started a remote git is given."""
    return (
        "this test started a git that would reach a remote over the network, and it was refused: "
        + "; ".join(charged)
        + ". Give it a local stand-in: a repository planted on disk, or the scan monkeypatched "
        "(tests/remote_git_guard.py)."
    )


#: The suite's guard, installed as this module is imported, which ``conftest`` does before anything
#: is collected.
GUARD = RemoteGitGuard()
GUARD.install()
