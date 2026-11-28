"""Where a run's calls reach without a person saying yes: the allowed hosts, its own folders,
never the owner's protected folders, and commits only under the identity git is configured with.

Four bounds, read from one reading of each call (:mod:`personalclaw.command_effects` for a shell
command, the path a native file tool is given):

* **The network.** A shell command that reaches the network is held to the egress allow-list
  (Allowed hosts in Settings → Security → Network egress, ``security.egress.allow_hosts``): every
  host it names must be on that list and not on Denied hosts, and the run's safety profile can
  only narrow that further (an egress tier of ``off`` lists nothing). A command that reaches a host
  it does not name (a remote by name, a URL built from a variable) cannot be checked, so it is
  held the same way as an unlisted one. An **unattended** run is refused such a command; an
  **attended** one asks a person, with the host named on the card, whatever would otherwise have
  answered for it (Trust, YOLO, a standing grant, a hook's auto-approve). Only the listed host
  goes ahead unasked, because the list is the owner's own answer for it, and, under YOLO in a
  chat of hers she is watching, a command that only fetches from hosts it names, none on Denied
  (:func:`fetched_from`, ``approval_grants.yolo_fetch_hosts``): a network command that sends asks.
* **Writes.** An unattended run changes files only inside its own folders: the folder it works in
  (a workspace, or a code loop's worktree), the folders it was given (a loop's own folder, a
  project's context folder, the files an automation may change) and its own temporary folder
  (:func:`scratch_dir`, which its shell gets as ``TMPDIR``). A native file write elsewhere is
  refused, and so is a shell command whose write facet names a path elsewhere, or a path this
  reading cannot name. An attended run is asked about each of its writes as before.
* **The protected folders.** A shell command that would delete the owner's home folder, the
  filesystem root or the folder the call runs in (a folder holding one, or everything inside one,
  included), or that deletes a path this reading cannot name, is put to a person whatever would
  otherwise have answered for it, as a call past the allowed hosts is; an unattended run is
  refused it (:mod:`personalclaw.protected_folders`).
* **Who a commit is by.** A shell command this reading sees giving git a commit's author or
  committer itself, or changing where git reads them from (``CommandEffects.identity``), is put
  to a person the same way, and an unattended run is refused it and told to commit with a plain
  ``git commit``. A commit and its sign-off are given in the owner's name.

What a program does that its command line does not say (a script's own ``open``, a tool's own
configuration naming another registry) is outside any reading of the text: the OS sandbox and
the credential screens are the fence, and this is defence in depth in front of them.
"""

from __future__ import annotations

import contextvars
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from personalclaw import protected_folders
from personalclaw.command_effects import DEVICES, CommandEffects, command_effects, url_host
from personalclaw.file_scope import PATH_TOOLS
from personalclaw.periodic_sweep import PeriodicSweep
from personalclaw.persistence_paths import TOO_MANY_TO_READ, Planted, planted
from personalclaw.protected_folders import NOTHING, ProtectedDelete
from personalclaw.shell_syntax import LEADING_VARIABLES

logger = logging.getLogger(__name__)

#: The native file tools that change a file, and the argument naming it (``file_scope``'s table).
FILE_WRITES = {tool: arg for tool, (arg, changes, _) in PATH_TOOLS.items() if changes}


# ── The network ──────────────────────────────────────────────────────────────────────────────────


def shell_egress_policy(session_key: str = "") -> Any:
    """The hosts a shell command may reach unasked, as an exclusive :class:`EgressPolicy`: the
    operator's Allowed hosts (and their Denied hosts), narrowed by the session's safety-profile
    egress tier (the one reader of it, ``net.policy.egress_policy_for_run``). ``None`` when
    that tier allows no egress at all. A ``registry`` tier adds no host to the list: the run may
    reach a registry, and a command that does is still put to a person (refused, in an
    unattended run) as any command reaching a host off the list is.

    A profile that cannot be read leaves the operator's list as it is: the list is already the
    narrowest reach there is short of none, and an unreadable profile narrows nothing it names."""
    from personalclaw.net.policy import LISTED, egress_policy_for, egress_policy_for_run

    base = egress_policy_for(LISTED)
    try:
        return egress_policy_for_run(base, session_key)
    except Exception:  # noqa: BLE001 - the operator's own list still holds
        logger.warning("run bounds: the session's egress tier is unreadable", exc_info=True)
        return base


def unlisted(hosts: Iterable[str], policy: Any, *, declared: Sequence[str] = ()) -> tuple[str, ...]:
    """The hosts in *hosts* that may not be reached unasked under *policy*: a denied one always,
    and one neither the allow-list nor *declared* (hosts the caller's own consent covers) names.
    Every host, under a policy of ``None``."""
    from personalclaw.net.guard import host_matches

    out = []
    for host in sorted(set(hosts)):
        if policy is None or host_matches(host, tuple(policy.deny_hosts)):
            out.append(host)
        elif not (
            host_matches(host, tuple(policy.allow_hosts)) or host_matches(host, tuple(declared))
        ):
            out.append(host)
    return tuple(out)


#: A reach whose host is not read.
_UNREAD: tuple[frozenset[str], bool] = (frozenset(), True)
#: A value that begins with a scheme, slashes or none: ``https:host`` names a host to a WHATWG
#: reader (a browser, Node).
_SCHEME_START = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
#: The schemes a WHATWG reader treats as special: ``\\`` is ``/``, and ``https:host``,
#: ``https:/host`` and ``https:\\host`` name a host.
_SPECIAL_SCHEMES = frozenset({"http", "https", "ws", "wss", "ftp"})
#: A character no URL a reader takes the same way holds: whitespace, a backslash, a control.
_AMBIGUOUS_VALUE = re.compile(r"[\\\s\x00-\x1f\x7f]")


def _names_a_host(host: str) -> bool:
    """Whether a bare value's *host* is one: a name with a dot, ``localhost`` or an IP address."""
    if "." in host or host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def argument_reach(value: str, *, bare: bool = False) -> tuple[frozenset[str], bool] | None:
    """Where a tool's argument *value* reaches when it is a URL, read as the tool that receives it
    would read it: the hosts it reaches and whether it reaches one it does not name (readers could
    disagree on it). ``None`` for a value that is no URL, or one that reaches no host (``file:``).

    A value that begins with two slashes (``//host/x``, or with a backslash, which is not read) is
    one, as a tool resolving it against a base reads it. A value that begins with a scheme followed
    by ``//`` is one, whatever the scheme
    (``redis://``, ``s3://``); one of the special schemes (``https:``…) is one with no slashes, one
    slash or a backslash too. Such a form, and any URL holding whitespace (around it too), a
    backslash or a control character, is not read: its host is unread whichever reading is right,
    so it is never a listed host or a given link. *bare*: the value's key names a place (``url``,
    ``host``…), so a value with no scheme is read as ``curl`` reads a bare host, and counts when it
    names a host with a dot, an IP address or ``localhost``."""
    text = value.strip()
    if not text:
        return None
    lead = len(text) - len(text.lstrip("/\\"))
    if lead >= 2 and (text[lead : lead + 1].isalnum() or text[lead : lead + 1] == "["):
        # Scheme-relative: a tool that resolves it against a base (a client's, a page's) reaches
        # it, and a WHATWG reader takes any run of slashes and backslashes there as two slashes.
        # Under a key that names no place it is one only as one word: `//go:build linux\n…` and
        # `// TODO` open a file's text.
        if not bare and re.search(r"\s", value.strip()):
            return None
        if text[:lead] != "//" or _AMBIGUOUS_VALUE.search(value):
            return _UNREAD
        host = url_host("http:" + text)
        return _UNREAD if host is None else (frozenset({host}), False) if host else None
    match = _SCHEME_START.match(text)
    if (
        match is not None
        and bare
        and match.group(1).lower() not in _SPECIAL_SCHEMES
        and text[match.end() : match.end() + 1].isdigit()
    ):
        match = None  # `localhost:8080`, `host.example:8443/x`: a host and its port
    if match is not None:
        scheme = match.group(1).lower()
        transport = scheme.rsplit("+", 1)[-1]
        rest = text[match.end() :]
        if rest.startswith("//"):
            host = None if _AMBIGUOUS_VALUE.search(value) else url_host(text)
        elif transport in _SPECIAL_SCHEMES:
            return _UNREAD  # `https:host`, `https:/host`, `https:\\host`: readers differ
        else:
            return None  # `mailto:`, `data:`, prose with a colon
        if host is None:
            return _UNREAD
        return None if host == "" else (frozenset({host}), False)
    if not bare or not (text[0].isalnum() or text[0] == "["):
        return None
    host = url_host("http://" + text.split()[0])
    if host is None:
        return _UNREAD
    if not _names_a_host(host):
        return None  # a word (`prod`), a number, a street address
    return _UNREAD if _AMBIGUOUS_VALUE.search(value) else (frozenset({host}), False)


#: The words a key ends in when its value names a place even with no scheme (``{"host": "x"}``,
#: ``serverAddress``, ``hosts``): read as ``curl`` reads a bare host (``argument_reach``).
_PLACE_WORDS = frozenset(
    {
        "url",
        "uri",
        "href",
        "link",
        "endpoint",
        "host",
        "hostname",
        "domain",
        "server",
        "address",
        "proxy",
        "ip",
    }
)
#: Those a key may end in run together with the word before it (``imageurl``); ``ip`` only as a
#: word of its own (``serverIp``, ``ip``), as ``zip`` and ``clip`` end in it too.
_PLACE_ENDINGS = tuple(sorted(_PLACE_WORDS - {"ip"}))
#: Which strings a link's consent may come from: the first this many of a call's arguments.
_ARGUMENT_STRINGS = 64
#: How much of a call's arguments is read at all (values, of every depth): past it, the call
#: reaches a host it does not name.
_ARGUMENT_LIMIT = 4096
#: The built-in agent's tools whose ``url`` the conversation's own links consent to
#: (``web.fetch``'s provenance): the exact link, given by the owner or a tool's result.
_CONSENTED_TOOLS = frozenset({"web_fetch", "web_extract"})


def _may_hold_a_url(value: object) -> bool:
    try:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return ":/" in text or ":\\" in text or "//" in text or "\\\\" in text


def _singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("es") and word[:-2] in _PLACE_WORDS:
        return word[:-2]
    return word[:-1] if word.endswith("s") else word


def _names_a_place(key: str) -> bool:
    """Whether *key* ends in a word that names a place, one or many (``hosts``, ``proxies``)."""
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", key)
    last = words[-1].lower() if words else ""
    if last in _PLACE_WORDS or _singular(last) in _PLACE_WORDS:
        return True
    lowered = key.lower()
    return lowered.endswith(_PLACE_ENDINGS) or _singular(lowered).endswith(_PLACE_ENDINGS)


def _argument_strings(value: object) -> tuple[list[tuple[str, str, int]], bool]:
    """Each string in a call's arguments in the order they are written, with its key and depth (a
    top-level value is at 2); and whether the arguments run past what is read at all
    (:data:`_ARGUMENT_LIMIT`), or hold a value of no JSON kind that may hold a URL."""
    found: list[tuple[str, str, int]] = []
    queue: deque[tuple[str, object, int]] = deque([("", value, 1)])
    read = 0
    while queue:
        key, item, depth = queue.popleft()
        read += 1
        if read > _ARGUMENT_LIMIT:
            return found, True
        if isinstance(item, str):
            found.append((key, item, depth))
        elif isinstance(item, Mapping):
            queue.extend((str(k), v, depth + 1) for k, v in item.items())
        elif isinstance(item, (list, tuple)):
            queue.extend((key, v, depth + 1) for v in item)
        elif not isinstance(item, (int, float, bool, type(None))) and _may_hold_a_url(item):
            return found, True
    return found, False


def _consented(
    title: str, tool_kind: str, key: str, depth: int, url: str, session_key: str
) -> bool:
    """Whether the conversation was given *url* itself, for the built-in agent's web fetch."""
    if tool_kind or title not in _CONSENTED_TOOLS or key != "url" or depth != 2 or not session_key:
        return False
    try:
        from personalclaw.web.fetch import url_provenance

        return bool(url_provenance(session_key, url))
    except Exception:  # noqa: BLE001 - security surface: a reader that fails consents to nothing
        logger.warning("run bounds: a link's provenance is unreadable", exc_info=True)
        return False


def argument_network(
    title: str, tool_kind: str, tool_input: object, *, session_key: str
) -> tuple[tuple[str, ...], bool]:
    """The hosts past the allowed hosts that a call names in its arguments, and whether it
    reaches one it does not name: every URL argument (:func:`argument_reach`) and every
    bare host under a key that names a place, read against the shell's policy
    (:func:`shell_egress_policy`), so a host a ``curl`` may reach unasked is one a tool may too.

    Every string is read, whatever its depth. A host readers could disagree on, an agent CLI's
    input that is no JSON and may hold a URL, and arguments past what is read at all
    (:data:`_ARGUMENT_LIMIT`) are hosts it does not name. The built-in agent's ``web_fetch`` and
    ``web_extract`` reach the exact link the conversation was given unasked
    (``web.fetch.url_provenance``), from their top-level ``url`` among the first
    :data:`_ARGUMENT_STRINGS` strings; no other tool, and no other link, does."""
    if not tool_kind and title in PATH_TOOLS:
        return (), False  # a file tool's arguments name files
    args = tool_input
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return (), _may_hold_a_url(args)
    strings, unread = _argument_strings(args)
    hosts: set[str] = set()
    for at, (key, text, depth) in enumerate(strings):
        reached = argument_reach(text, bare=_names_a_place(key))
        if reached is None:
            continue
        named, unnamed = reached
        if (
            not unnamed
            and at < _ARGUMENT_STRINGS
            and _consented(title, tool_kind, key, depth, text, session_key)
        ):
            continue  # a link the conversation was given, read the same by every reader
        hosts |= named
        unread = unread or unnamed
    return (unlisted(hosts, shell_egress_policy(session_key)) if hosts else ()), unread


# ── Writes ───────────────────────────────────────────────────────────────────────────────────────

#: Where every run's own temporary folder lives, under this user's temporary folder.
_SCRATCH_PARENT = "personalclaw-scratch"


#: A scratch folder no run has been handed for this long is removed (:func:`sweep_scratch`).
SCRATCH_IDLE_SECS = 2 * 24 * 3600

#: How often the gateway sweeps (:func:`start_scratch_sweep`).
SCRATCH_SWEEP_SECS = 6 * 3600

_DIGEST_RE = re.compile(r"[0-9a-f]{16}")


def _scratch_parent() -> Path:
    return Path(tempfile.gettempdir()) / f"{_SCRATCH_PARENT}-{os.getuid()}"


def _scratch_name(session_key: str) -> str:
    return hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]


def _own_folder(path: Path) -> bool:
    """Whether *path* is a folder (not a link) this user owns."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()


def scratch_dir(session_key: str) -> str:
    """The run's own temporary folder (created, ``0700``), or ``""`` when one cannot be made safely.

    It sits in this user's temporary folder, named by a digest of the session (a session's key is
    not written into a path), under a folder only this user owns: a parent that is a link or
    someone else's is not used, so a run is never handed a folder another account controls. Each
    hand-out marks the folder used, which is what keeps it from :func:`sweep_scratch`.
    """
    if not session_key:
        return ""
    parent = _scratch_parent()
    folder = parent / _scratch_name(session_key)
    try:
        parent.mkdir(mode=0o700, exist_ok=True)
        info = os.lstat(parent)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            logger.warning(
                "run bounds: %s is not this user's own folder; no scratch folder", parent
            )
            return ""
        os.chmod(parent, 0o700)
        folder.mkdir(mode=0o700, exist_ok=True)
        if not _own_folder(folder):
            return ""
        os.utime(folder)
    except OSError:
        logger.warning("run bounds: the scratch folder could not be made", exc_info=True)
        return ""
    return os.path.realpath(folder)


def _unlock(root: Path) -> None:
    """Give the owner back write and search permission on *root* and every folder inside it that
    its run took them from (a test that made a folder read-only, a module cache kept so), so the
    folder can be removed. Folders only, this user's own, never through a link."""
    for top, dirs, _files in os.walk(root, topdown=True, followlinks=False):
        for path in (top, *(os.path.join(top, name) for name in dirs)):
            try:
                info = os.lstat(path)
                if (
                    stat.S_ISDIR(info.st_mode)
                    and info.st_uid == os.getuid()
                    and info.st_mode & stat.S_IRWXU != stat.S_IRWXU
                ):
                    os.chmod(path, stat.S_IMODE(info.st_mode) | stat.S_IRWXU)
            except OSError:
                continue


def _remove_scratch_folder(folder: Path) -> bool:
    if not _own_folder(folder):
        return False
    shutil.rmtree(folder, ignore_errors=True)
    if os.path.lexists(folder) and _own_folder(folder):
        _unlock(folder)
        shutil.rmtree(folder, ignore_errors=True)
    if os.path.lexists(folder):
        logger.warning("run bounds: the scratch folder %s could not all be removed", folder)
        return False
    return True


def remove_scratch(session_keys: Iterable[str]) -> int:
    """Remove the scratch folder of each session *session_keys* names whose work is over for good,
    and say how many there were. Blocking (a run's folder can hold a test suite's temporary
    trees): call it off the event loop."""
    parent = _scratch_parent()
    if not _own_folder(parent):
        return 0
    names = {_scratch_name(key) for key in session_keys if key}
    return sum(_remove_scratch_folder(parent / name) for name in sorted(names))


def sweep_scratch(*, now: float | None = None, idle: float = SCRATCH_IDLE_SECS) -> int:
    """Remove every scratch folder no run has been handed or written into for *idle* seconds, and
    say how many. A folder's time is when :func:`scratch_dir` last handed it out or something was
    added to or taken from it, so a session still at work keeps its own; one whose session is
    over, or idle for days, goes, with whatever its commands left in it. A folder a runtime this
    process runs still holds (:func:`runtime_scratch`) is kept however long since its last call,
    so a run that is waiting or paused keeps its own. Only folders named as :func:`scratch_dir`
    names them, under the parent only this user owns. Blocking."""
    parent = _scratch_parent()
    if not _own_folder(parent):
        return 0
    cutoff = (time.time() if now is None else now) - idle
    try:
        entries = [e for e in os.scandir(parent) if _DIGEST_RE.fullmatch(e.name)]
    except OSError:
        logger.warning("run bounds: the scratch folders could not be listed", exc_info=True)
        return 0
    held = _held_scratch()
    removed = 0
    for entry in entries:
        try:
            if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
        except OSError:
            continue
        if os.path.realpath(entry.path) in held:
            continue
        removed += _remove_scratch_folder(Path(entry.path))
    if removed:
        logger.info("run bounds: removed %d scratch folder(s) idle for %ds", removed, idle)
    return removed


def _sweep_pass() -> None:
    """One pass of the gateway's sweep: a pass that fails is said at warning, and the next one
    runs as planned."""
    try:
        sweep_scratch()
    except Exception:  # noqa: BLE001 - one failed pass must not end the sweep
        logger.warning("run bounds: a sweep of the idle scratch folders failed", exc_info=True)


#: The gateway's sweep of idle scratch folders: once as it starts, then every few hours.
_SWEEP = PeriodicSweep("scratch-sweep", SCRATCH_SWEEP_SECS, _sweep_pass, first_after=0.0)


def start_scratch_sweep() -> None:
    """Start the gateway's sweep of idle scratch folders (:func:`sweep_scratch`), its first pass
    now, off the caller's thread. Ended by :func:`stop_scratch_sweep`."""
    _SWEEP.start()


def stop_scratch_sweep() -> None:
    """Stop the sweep :func:`start_scratch_sweep` started. Idempotent."""
    _SWEEP.stop()


#: The run's own temporary folder for the call being dispatched ("" = none: the shell keeps the
#: owner's), bound with the native tools' per-turn context (``builtin_tools.bind_tool_context``).
CURRENT_SCRATCH: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_native_scratch", default=""
)


def shell_env(*, site: str, run: str = "") -> dict[str, str]:
    """The environment a run's shell starts with: the child allowlist
    (``sandbox.build_child_env``) and, when the run has its own temporary folder, that folder as
    ``TMPDIR``, so the scratch files a command makes (``mktemp -d``, a program's temporary files)
    land where it may write. *run* is the run's marker (``run_processes``), which everything the
    command starts inherits, so what it leaves running ends with the run."""
    from personalclaw.run_processes import RUN_VARIABLE
    from personalclaw.sandbox import build_child_env

    env = build_child_env(site=site)
    scratch = CURRENT_SCRATCH.get()
    if scratch:
        env["TMPDIR"] = scratch
    if run:
        env[RUN_VARIABLE] = run
    return env


def places(roots: Iterable[str | os.PathLike[str] | None], scratch: str = "") -> tuple[str, ...]:
    """The real paths of the folders a run changes files in: *roots* (where it works, and the
    folders it was given) and its *scratch* folder."""
    out = [os.path.realpath(str(r)) for r in roots if r and str(r)]
    if scratch:
        out.append(os.path.realpath(scratch))
    return tuple(dict.fromkeys(out))


def _inside(real: str, root: str) -> bool:
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def _expand_leading(path: str, scratch: str) -> str | None:
    """*path* with the variable it begins with (``$TMPDIR``, ``$HOME``) written out: the run's
    shell gets its own temporary folder as ``TMPDIR``. ``None`` for one with no value here."""
    for name in LEADING_VARIABLES:
        for spelled in (f"${{{name}}}", f"${name}"):
            if path == spelled or path.startswith(spelled + "/"):
                value = scratch if name == "TMPDIR" else os.path.expanduser("~")
                return value + path[len(spelled) :] if value else None
    return path


def _real(path: str, cwd: str, scratch: str = "") -> str | None:
    """*path* as the real path a write would reach, or ``None`` when it is relative to a folder
    nobody knows (or begins with a variable that has no value here)."""
    if path.startswith("$"):
        expanded = _expand_leading(path, scratch)
        if expanded is None or expanded.startswith("$"):
            return None
        path = expanded
    named = os.path.expanduser(path) if path.startswith("~") else path
    if not os.path.isabs(named):
        if not cwd:
            return None
        named = os.path.join(cwd, named)
    return os.path.realpath(named)


def outside(
    targets: Iterable[str], *, cwd: str, within: Sequence[str], scratch: str = ""
) -> tuple[str, ...]:
    """The paths in *targets* (as a command names them, from *cwd*; ``$TMPDIR`` is *scratch*) that
    lie outside every folder in *within*. A path relative to an unknown folder lies outside."""
    out = []
    for target in sorted(set(targets)):
        if target in DEVICES or target.startswith("/dev/fd/"):
            continue
        real = _real(target, cwd, scratch)
        if real is None or not any(_inside(real, root) for root in within):
            out.append(target)
    return tuple(out)


# ── One call ─────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reach:
    """Where one call reaches past a run's bounds. Empty (false) when it stays inside them."""

    #: Hosts it names that may not be reached unasked.
    hosts: tuple[str, ...] = ()
    #: It reaches the network at a host it does not name.
    unnamed_host: bool = False
    #: Paths it writes outside the run's folders, as it names them.
    paths: tuple[str, ...] = ()
    #: It writes a path it does not name.
    unnamed_path: bool = False
    #: What it would delete of the owner's protected folders.
    deletes: ProtectedDelete = NOTHING
    #: Each way it gives git a commit's author or committer itself (``CommandEffects.identity``).
    identity: tuple[str, ...] = ()
    #: It only fetches from every host it reaches: a shell command whose reading says it sends
    #: nothing of this machine's (``CommandEffects.sends``). Never so of any other call's reach.
    only_fetches: bool = False
    #: Each file it writes that your own tools run later (``persistence_paths``).
    plants: tuple[Planted, ...] = ()

    @property
    def network(self) -> bool:
        return bool(self.hosts) or self.unnamed_host

    @property
    def writes(self) -> bool:
        return bool(self.paths) or self.unnamed_path

    @property
    def asks(self) -> bool:
        """It is put to a person whatever grant stands: past the allowed hosts, a delete of a
        protected folder, a commit under an identity it gives git itself, or a write to a file
        your own tools run later."""
        return self.network or bool(self.deletes) or bool(self.identity) or bool(self.plants)

    def __bool__(self) -> bool:
        return self.asks or self.writes


NOWHERE = Reach()


def effects_reach(
    effects: CommandEffects,
    *,
    policy: Any,
    cwd: str = "",
    within: Sequence[str] | None = None,
    scratch: str = "",
    declared: Sequence[str] = (),
) -> Reach:
    """Where a command whose reading is *effects* reaches past the bounds: hosts *policy* (and
    *declared*) does not cover, and, when *within* is given, writes outside those folders
    (``$TMPDIR`` being the run's *scratch* folder)."""
    hosts: tuple[str, ...] = ()
    unnamed_host = False
    only_fetches = False
    if effects.network:
        hosts = unlisted(effects.hosts, policy, declared=declared)
        unnamed_host = effects.host_unread
        only_fetches = not effects.sends
    paths: tuple[str, ...] = ()
    unnamed_path = False
    if within is not None and effects.writes:
        paths = outside(effects.targets, cwd=cwd, within=within, scratch=scratch)
        unnamed_path = effects.target_unread
    return Reach(hosts, unnamed_host, paths, unnamed_path, only_fetches=only_fetches)


def call_reach(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    *,
    session_key: str,
    cwd: str,
    within: Sequence[str] | None = None,
    scratch: str = "",
    tmpdir: str | None = None,
    named: Sequence[str] = (),
) -> Reach:
    """Where one tool call reaches past its run's bounds, run in the folder *cwd*.

    What it writes of the files your own tools run later (``persistence_paths``) is read of every
    call that writes, attended or not: the targets of a shell command's write facet, a native file
    write's path, and the files an agent CLI's edit or move names (*named*, the call's
    ``named_files``).

    The network half is asked of every shell call (:func:`personalclaw.task_modes.shell_command`
    says whether a call is one), and so is the protected folders' half, of the command the call
    runs (:func:`~personalclaw.task_modes.shell_call_command`, which reads a command that arrived
    only in the call's title too), whose shell has *tmpdir* as ``TMPDIR`` (``None``: this
    process's). The writes half only when *within* names the run's folders, which is how a caller
    says the run is unattended: of a shell command's write facet, and of a native file write's
    path."""
    from personalclaw.task_modes import declared_level, shell_call_command, shell_command

    runs = shell_call_command(title, tool_kind, tool_input, declared)
    deletes = protected_folders.protected_delete(runs, cwd=cwd, tmpdir=tmpdir) if runs else NOTHING
    identity = tuple(sorted(command_effects(runs).identity)) if runs else ()
    command = shell_command(title, tool_kind, tool_input, declared)
    if command:
        effects = command_effects(command)
        reach = effects_reach(
            effects,
            policy=shell_egress_policy(session_key),
            cwd=cwd,
            within=within,
            scratch=scratch,
        )
        landed, unread = _landed(effects.into, cwd, effects.targets) if effects.writes else ([], [])
        written = [*effects.targets, *landed] if effects.writes else []
        plants = planted(written, cwd=cwd)
        plants += tuple(Planted(path, TOO_MANY_TO_READ) for path in dict.fromkeys(unread))
        return replace(reach, deletes=deletes, identity=identity, plants=plants)
    hosts, unnamed = argument_network(title, tool_kind, tool_input, session_key=session_key)
    plants = planted(_written(title, tool_kind, tool_input, declared, named), cwd=cwd)
    if deletes or identity or hosts or unnamed:
        return Reach(hosts, unnamed, deletes=deletes, identity=identity, plants=plants)
    arg = FILE_WRITES.get(title)
    if within is None or arg is None or not declared_level(declared):
        return Reach(plants=plants) if plants else NOWHERE
    raw = tool_input.get(arg) if isinstance(tool_input, dict) else None
    if not isinstance(raw, str) or not raw:
        return NOWHERE  # the tool says what is missing
    return Reach(paths=outside([raw], cwd=cwd, within=within, scratch=scratch), plants=plants)


#: The most of a copied folder's entries read for where they land.
_LANDED_ENTRIES = 10_000


def _landed(
    into: Iterable[tuple[str, str]], cwd: str, written: Iterable[str] = ()
) -> tuple[list[str], list[str]]:
    """Where each copy, move or link lands (``CommandEffects.into``), read from *cwd*; and each
    destination whose source holds more than :data:`_LANDED_ENTRIES` entries, too many to read.

    Into a folder (a destination spelled as one, or one that is a folder on disk), the folder's
    entry of the source's name. A source that is a folder lands its entries too, below where it
    lands: those on disk, and those the same command writes inside it (*written*, the command's
    own write targets). It lands at the destination itself when the destination is not spelled
    as a folder: a new name (``mv evil .vscode``), and a folder on disk that the same command may
    take away or replace first (``rmdir .vscode && mv evil .vscode``), so such a folder is landed
    both ways, the safe side. It lands there too when what is copied is the folder's contents:
    ``src/.`` (whose entry in the folder is the folder), and ``src/``, which a BSD ``cp -R`` reads
    so."""
    targets = [_absolute(path, cwd) for path in written]
    landed: list[str] = []
    unread: list[str] = []
    for destination, source in into:
        spelled = os.path.expanduser(destination)
        named_a_folder = destination.endswith("/")
        roots = [] if named_a_folder else [destination]
        if named_a_folder or os.path.isdir(os.path.join(cwd or ".", spelled)):
            # The folder's entry of the source's name: the folder itself for ``src/.``.
            roots.append(os.path.join(destination, os.path.basename(source.rstrip("/"))))
            landed.append(roots[-1])
            if source.endswith("/"):
                roots.append(destination)
        entries, whole = _entries(source, cwd, targets)
        if not whole:
            unread.append(destination)
        landed += [os.path.join(root, entry) for root in dict.fromkeys(roots) for entry in entries]
    return landed, unread


def _absolute(path: str, cwd: str) -> str:
    expanded = os.path.expanduser(path)
    return os.path.normpath(os.path.join(cwd or os.getcwd(), expanded))


def _entries(source: str, cwd: str, targets: Sequence[str]) -> tuple[list[str], bool]:
    """What a folder *source* holds, each relative to it: its entries on disk, at most
    :data:`_LANDED_ENTRIES`, and each of *targets* (absolute) the same command writes inside it;
    and whether that is all of them. Nothing for a source that is no folder."""
    folder = _absolute(source, cwd)
    inside = folder.rstrip(os.sep) + os.sep
    found = [os.path.relpath(target, folder) for target in targets if target.startswith(inside)]
    if not os.path.isdir(folder):
        return found, True
    read = 0
    for at, folders, files in os.walk(folder):
        for entry in (*folders, *files):
            if read >= _LANDED_ENTRIES:
                return found, False
            found.append(os.path.relpath(os.path.join(at, entry), folder))
            read += 1
    return found, True


#: The kinds of an agent CLI's call that write the files they name (``acp.translate``).
_WRITING_KINDS = frozenset({"edit", "move"})


def _written(
    title: str, tool_kind: str, tool_input: object, declared: object, named: Sequence[str]
) -> tuple[str, ...]:
    """The paths a call that is no shell command writes, as it names them: a native file write's
    path argument, and the files an agent CLI's edit or move names."""
    from personalclaw.task_modes import declared_level

    found: list[str] = []
    arg = FILE_WRITES.get(title)
    if arg is not None and declared_level(declared) and isinstance(tool_input, dict):
        raw = tool_input.get(arg)
        if isinstance(raw, str) and raw:
            found.append(raw)
    if tool_kind in _WRITING_KINDS:
        found.extend(p for p in named if isinstance(p, str))
    return tuple(found)


def session_roots(tool_index: Any, cwd: Any) -> list[str]:
    """The folders a native session's file tools work in and change: its folder and the folders
    it was given (``file_scope``'s workspace places), never the owner's other allowed folders.
    Its folder alone when the tools' scope cannot be read."""
    from personalclaw.file_scope import WORKSPACE

    roots = [str(cwd)] if cwd else []
    provider = tool_index.get("write_file") or tool_index.get("bash")
    file_scope = getattr(provider, "file_scope", None)
    if callable(file_scope):
        try:
            roots = [place.root for place in file_scope().places if place.kind == WORKSPACE]
        except Exception:  # noqa: BLE001 - the run keeps its own folder alone
            logger.warning("run bounds: the session's folders are unreadable", exc_info=True)
    return roots


def tool_call_reach(
    declared: object,
    tool_name: str,
    tool_input: object,
    *,
    session_key: str,
    cwd: str,
    scratch: str = "",
    roots: Callable[[], Iterable[str]] | None = None,
) -> tuple[Reach, tuple[str, ...]]:
    """:func:`call_reach` for a native runtime's call, and the folders an unattended run's writes
    are held to. *roots* (the run's folders, read only for a call that can write) is given for an
    unattended run and ``None`` for an attended one, whose writes are asked about. The run's
    shell has *scratch* as ``TMPDIR`` when it has one (``shell_env``)."""
    from personalclaw.task_modes import shell_command

    if (
        tool_name not in FILE_WRITES
        and not shell_command(tool_name, "", tool_input, declared)
        and argument_network(tool_name, "", tool_input, session_key=session_key) == ((), False)
    ):
        return NOWHERE, ()
    within = places(roots(), scratch) if roots is not None else None
    reach = call_reach(
        declared,
        tool_name,
        "",
        tool_input,
        session_key=session_key,
        cwd=cwd,
        within=within,
        scratch=scratch,
        tmpdir=scratch or None,
    )
    return reach, within or ()


def asked_call_reach(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    *,
    session_key: str,
    workspace: str,
    extra_roots: Iterable[str] = (),
    unattended: bool,
    named: Sequence[str] = (),
) -> tuple[Reach, tuple[str, ...], str]:
    """:func:`call_reach` for a call put to a chat session's approval gate (an agent CLI's, or a
    native runtime's ask): the reach, the folders an unattended session's writes are held to (its
    workspace, the folders it was given, its own temporary folder), and that temporary folder."""
    scratch = scratch_dir(session_key) if unattended else ""
    within = places([workspace, *extra_roots], scratch) if unattended else None
    reach = call_reach(
        declared,
        title,
        tool_kind,
        tool_input,
        session_key=session_key,
        cwd=workspace,
        within=within,
        scratch=scratch,
        named=named,
    )
    return reach, within or (), scratch


# ── What it says ─────────────────────────────────────────────────────────────────────────────────


def _listed(names: Sequence[str], limit: int = 4) -> str:
    shown = list(names[:limit])
    more = len(names) - len(shown)
    text = ", ".join(shown)
    return f"{text} and {more} more" if more > 0 else text


def network_sentence(reach: Reach) -> str:
    """What the call reaches on the network, in a clause (``it reaches pypi.org, which …``)."""
    from personalclaw.net.guard import ALLOWED_HOSTS

    parts = []
    if reach.hosts:
        which = "which is" if len(reach.hosts) == 1 else "which are"
        parts.append(f"it reaches {_listed(reach.hosts)}, {which} not on {ALLOWED_HOSTS}")
    if reach.unnamed_host:
        parts.append(
            "it reaches the network at a host its command does not name, so that host cannot be "
            "checked against your allowed hosts"
        )
    return "; ".join(parts)


def writes_sentence(reach: Reach, within: Sequence[str]) -> str:
    """What the call writes outside the run's folders, in a clause."""
    where = _listed(tuple(within), limit=3) or "none"
    parts = []
    if reach.paths:
        parts.append(
            f"it writes {_listed(reach.paths)}, outside the folders this run works in ({where})"
        )
    if reach.unnamed_path:
        parts.append(
            f"it writes a path its command does not name, which cannot be checked against the "
            f"folders this run works in ({where})"
        )
    return "; ".join(parts)


def past_bounds(reach: Reach, within: Sequence[str] = ()) -> str:
    """Why an unattended run is refused the call, in a clause: what it reaches past its bounds.

    What the call's card says; :func:`refusal` tells the model the same and what it can do."""
    reasons = [
        network_sentence(reach),
        writes_sentence(reach, within),
        protected_folders.clause(reach.deletes),
        identity_sentence(reach),
        plants_sentence(reach),
    ]
    return (
        "this run is unattended, so nobody is here to allow a call past its bounds: "
        + "; ".join(r for r in reasons if r)
    )


def refusal(
    reach: Reach, within: Sequence[str] = (), *, scratch: str = "", shell_tmpdir: bool = False
) -> str:
    """Why an unattended run is refused the call, for the model, with what it can do instead.

    *shell_tmpdir*: the run's shell has its *scratch* folder as ``TMPDIR`` (the built-in agent's
    does; an agent CLI's shell keeps its own), so ``mktemp -d`` makes a folder inside it."""
    steps = []
    if reach.network:
        steps.append(
            "reach only hosts on that list, and name the host in the command (a remote by name "
            "or a URL in a variable cannot be checked)"
        )
    if reach.writes:
        steps.append(
            "write only inside those folders, naming each path in the command"
            + (f"; scratch files go in {scratch}" if scratch else "")
            + (", the folder `mktemp -d` makes" if scratch and shell_tmpdir else "")
        )
    if reach.deletes:
        steps.append(
            f"leave {protected_folders.ALWAYS_ASKS} alone, and name in the command each path a "
            "delete removes"
        )
    if reach.identity:
        steps.append(_IDENTITY_STEP)
    if reach.plants:
        steps.append(
            f"leave {_listed([p.path for p in reach.plants])} alone, which only the user changes, "
            "and write the step's other files"
        )
    return (
        past_bounds(reach, within)
        + ". To go on, "
        + "; and ".join(steps)
        + ". Otherwise leave the step for the user, and say what it needs"
    )


def ask_note(reach: Reach) -> str:
    """The line an attended run's card shows for a call put to a person past every grant (one
    that reaches past the allowed hosts, that deletes a protected folder, that gives git its own
    author or committer, or that writes a file your own tools run later): why it asks, though a
    standing grant would have answered any other call."""
    notes = []
    sentence = network_sentence(reach)
    if sentence:
        notes.append(
            sentence[0].upper()
            + sentence[1:]
            + ". A command that reaches a host off that list, or one it does not name, is asked "
            "about whatever this chat or its agent allows, except a download YOLO runs in a chat "
            "of your own in the dashboard."
        )
    if reach.deletes:
        notes.append(
            protected_folders.sentence(reach.deletes)
            + f" A command that deletes {protected_folders.ALWAYS_ASKS} is always asked about, "
            "whatever this chat or its agent allows."
        )
    sentence = identity_sentence(reach)
    if sentence:
        notes.append(
            sentence[0].upper()
            + sentence[1:]
            + ". A command that gives git its own author or committer is always asked about, "
            "whatever this chat or its agent allows."
        )
    notes.extend(plant.sentence() for plant in reach.plants)
    return " ".join(notes)


def plants_sentence(reach: Reach) -> str:
    """What the call writes of the files your own tools run later, in a clause (``it writes …``)."""
    if not reach.plants:
        return ""
    return "it writes " + "; and ".join(
        f"{plant.path}, which {plant.row.what}" for plant in reach.plants
    )


#: What a run refused a commit under its own identity can do instead.
_IDENTITY_STEP = (
    "commit with a plain `git commit` (`-s` for a sign-off), and change no git setting or "
    "settings file; if git has no identity for the user, leave the commit for the user"
)


def identity_sentence(reach: Reach) -> str:
    """How the call gives git a commit's author or committer, in a clause (``it gives git …``)."""
    if not reach.identity:
        return ""
    return (
        f"it gives git the author or committer of a commit itself ({_listed(reach.identity)}), "
        "where a commit is made under the identity git is configured with, and its sign-off "
        "is given in that name"
    )


def reach_note(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    session_key: str,
    *,
    cwd: str,
    named: Sequence[str] = (),
) -> str:
    """The card's line (:func:`ask_note`) for a call put to a person, run in the folder *cwd*
    (*named*: the files an agent CLI's call names); ``""`` when nothing puts it past every grant.
    A display line, so a reading that fails says nothing."""
    try:
        reach = call_reach(
            declared, title, tool_kind, tool_input, session_key=session_key, cwd=cwd, named=named
        )
    except Exception:  # noqa: BLE001 - the card still shows the call and its risk
        logger.warning("run bounds: could not read where an asked call reaches", exc_info=True)
        return ""
    return ask_note(reach)


def _event_reach(event: Any, session_key: str, cwd: str) -> Reach:
    return call_reach(
        getattr(event, "risk_level", "") or "",
        str(getattr(event, "title", "") or ""),
        str(getattr(event, "tool_kind", "") or ""),
        getattr(event, "tool_input", ""),
        session_key=session_key,
        cwd=cwd,
        named=named_files(event),
    )


def named_files(event: Any) -> tuple[str, ...]:
    """The files an agent CLI's call names (``AgentEvent.named_files``), ``()`` for any other."""
    return tuple(p for p in (getattr(event, "named_files", ()) or ()) if isinstance(p, str))


def put_to_a_person(event: Any, *, session_key: str, cwd: str) -> bool:
    """Whether a call put to an approval gate (a permission request), run in the folder *cwd*,
    must be answered by a person whatever grant stands: it asks for a project secret file
    (``secret_file_grants``), it reaches a host off the allowed hosts, or one its command does not
    name, or it deletes one of the owner's protected folders, or a path its command does not name
    (:mod:`personalclaw.protected_folders`), or it gives git a commit's author or committer
    itself. No grant answers it, but YOLO a fetch from hosts its command names, in a chat of hers
    she is watching (:func:`fetched_from`, ``approval_grants.stands_for_call``)."""
    from personalclaw.secret_file_grants import asked_of

    if asked_of(event):
        return True
    return _event_reach(event, session_key, cwd).asks


def fetched_from(event: Any, *, session_key: str, cwd: str) -> tuple[str, ...]:
    """The hosts off the allowed hosts a call put to an approval gate, run in the folder *cwd*,
    only fetches from, when they are all that put it to a person (:func:`put_to_a_person`): each
    one its command names, none on Denied hosts, the command sending nothing of this machine's to
    any host it reaches (``CommandEffects.sends``), and no URL in it carrying a secret or a long
    query out (``security.scan_exfiltration_urls``), nor any key or token the credential screen
    knows anywhere in it (``security.redact_credentials``). ``()`` for every other call, and for a
    run whose egress tier reaches nothing. What YOLO may answer past the list
    (``approval_grants.yolo_fetch_hosts``)."""
    from personalclaw.net.guard import host_matches
    from personalclaw.secret_file_grants import asked_of
    from personalclaw.security import redact_credentials, scan_exfiltration_urls
    from personalclaw.task_modes import shell_call_command

    if asked_of(event):
        return ()
    reach = _event_reach(event, session_key, cwd)
    if not reach.hosts or not reach.only_fetches or replace(reach, hosts=()).asks:
        return ()
    policy = shell_egress_policy(session_key)
    if policy is None or any(host_matches(h, tuple(policy.deny_hosts)) for h in reach.hosts):
        return ()
    title = str(getattr(event, "title", "") or "")
    runs = shell_call_command(
        title,
        str(getattr(event, "tool_kind", "") or ""),
        getattr(event, "tool_input", ""),
        getattr(event, "risk_level", "") or "",
    )
    text = f"{title}\n{runs}"
    return () if scan_exfiltration_urls(text) or redact_credentials(text)[1] else reach.hosts


def screen(hooks: Any, event: Any, session_key: str, cwd: str) -> tuple[Any, bool]:
    """The hook chain's verdict on a call put to an approval gate, run in the folder *cwd*
    (:func:`~personalclaw.acp.permission_authority.screen_tool_call`), and whether the call is put
    to a person past every grant (:func:`put_to_a_person`). A hook's auto-approve does not answer
    such a call (it stays an allow); a refusal does."""
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_AUTO_APPROVE

    verdict = screen_tool_call(
        hooks,
        str(event.title or ""),
        event.tool_input,
        cwd,
        tool_kind=str(getattr(event, "tool_kind", "") or ""),
        declared=getattr(event, "risk_level", "") or "",
    )
    asks = put_to_a_person(event, session_key=session_key, cwd=cwd)
    if asks and verdict.action == TOOL_AUTO_APPROVE:
        verdict = type(verdict).allow()
    return verdict, asks


def event_note(event: Any, session_key: str, *, cwd: str) -> str:
    """:func:`reach_note` for a permission request, run in the folder *cwd*; for an ask for a
    project secret file, the line saying only the owner's answer opens it."""
    from personalclaw.secret_file_grants import ask_note, asked_of

    if op := asked_of(event):
        return ask_note(op)
    return reach_note(
        getattr(event, "risk_level", "") or "",
        str(getattr(event, "title", "") or ""),
        str(getattr(event, "tool_kind", "") or ""),
        getattr(event, "tool_input", ""),
        session_key,
        cwd=cwd,
        named=named_files(event),
    )


def attended(sessions: Any, parent_session_key: str) -> bool:
    """Whether a spawned agent's parent session is one a person answers: it exists, and its
    runtime is not an unattended run's (an Unattended loop's worker). The agents of an
    unattended parent have nobody to ask either, so they run unattended, inside its bounds."""
    if not parent_session_key or not sessions.has_session(parent_session_key):
        return False
    return getattr(sessions.get_provider(parent_session_key), "_unattended", False) is not True


@dataclass(frozen=True)
class NativeCheck:
    """Where one native runtime call reaches past its run's bounds (:func:`native_check`). False
    when it stays inside them; true for a call a person answers whatever the run's approval policy
    says (an attended run's call past the allowed hosts) or one an unattended run is refused."""

    reach: Reach = NOWHERE
    unattended: bool = False
    tool_name: str = ""
    session_key: str = ""
    within: tuple[str, ...] = ()
    scratch: str = ""

    def __bool__(self) -> bool:
        return bool(self.reach)

    def refuse(self, meta: dict) -> str | None:
        """The refusal an unattended run gets for the call, with *meta* marked refused and an egress
        row for each host it reaches; ``None`` for a call inside the bounds or one a person answers.
        Asked once the call's tool has had its say: a call the tool refuses anyway is told the
        tool's reason, never that nobody was here to allow it."""
        from personalclaw import security
        from personalclaw.llm.events import TOOL_META_REFUSED_BY

        if not (self.reach and self.unattended):
            return None
        if self.reach.network:
            audit(
                f"session:{self.session_key}",
                self.reach.hosts,
                outcome="denied",
                what=self.tool_name,
                reason="unattended run, not on the allowed hosts",
                unnamed=self.reach.unnamed_host,
            )
        _, observation = security.classify_denial(
            security.DENY_KIND_POLICY,
            refusal(self.reach, self.within, scratch=self.scratch, shell_tmpdir=True),
            self.tool_name,
        )
        meta.update({"ok": False, TOOL_META_REFUSED_BY: "run_bounds"})
        return observation


def native_check(runtime: Any, tool_name: str, args: Any) -> NativeCheck:
    """The native runtime's bounds check for one call (``_guard_and_invoke``), read before its
    approval policy. An attended run's writes are asked about as before, so only its network half
    is read; an unattended run's writes are held to its own folders."""
    unattended = bool(getattr(runtime, "_unattended", False))
    session_key = str(getattr(runtime, "_session_key", "") or "")
    cwd = getattr(runtime, "_cwd", None)
    scratch = runtime_scratch(runtime)
    reach, within = tool_call_reach(
        runtime.declared(tool_name),
        tool_name,
        args,
        session_key=session_key,
        cwd=str(cwd or ""),
        scratch=scratch,
        roots=(lambda: session_roots(runtime._tool_index, cwd)) if unattended else None,
    )
    return NativeCheck(reach, unattended, tool_name, session_key, tuple(within), scratch)


def runtime_folder(provider: Any) -> str:
    """The folder *provider*'s turns run in, its commands' folder (``AgentProvider.workspace``),
    else the workspace, where a session given no folder of its own runs."""
    folder = getattr(provider, "workspace", "")
    if isinstance(folder, str) and folder:
        return folder
    from personalclaw.config.loader import workspace_root

    return str(workspace_root())


def session_folder(session_key: str) -> str:
    """The folder the turns of the session *session_key* run in: the folder its runtime was started
    in, as the gateway's sessions hold it (:func:`runtime_folder`), else the workspace. For a gate
    that holds the session's key and not its runtime, such as a conversation a channel runs
    itself."""
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    state = get_dashboard_state()
    sessions = getattr(state, "sessions", None) if state is not None else None
    getter = getattr(sessions, "get_provider", None)
    provider = getter(session_key) if callable(getter) and session_key else None
    return runtime_folder(provider)


def runtime_scratch(runtime: Any) -> str:
    """An unattended native runtime's own temporary folder (:func:`scratch_dir`, made once and
    kept on the runtime); ``""`` for an attended one, whose shell keeps the owner's."""
    if not getattr(runtime, "_unattended", False):
        return ""
    made = getattr(runtime, "_run_scratch", None)
    if made is None or (made and not _mark_used(made)):
        made = scratch_dir(str(getattr(runtime, "_session_key", "") or ""))
        runtime._run_scratch = made
        if made:
            _hold(runtime, made)
    return made


#: The scratch folder each live runtime holds (:func:`runtime_scratch`), which a sweep never
#: removes: a runtime that is waiting or paused makes no call for as long as it waits.
_HELD: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
_HELD_LOCK = threading.Lock()


def _hold(runtime: Any, folder: str) -> None:
    try:
        with _HELD_LOCK:
            _HELD[runtime] = folder
    except TypeError:  # a runtime no weak reference can name (a stand-in) is held by nothing
        pass


def _held_scratch() -> frozenset[str]:
    with _HELD_LOCK:
        return frozenset(os.path.realpath(folder) for folder in _HELD.values())


def _mark_used(folder: str) -> bool:
    """Mark the scratch folder a runtime holds used; false when it is gone (swept) or no longer
    this user's own folder, so the runtime is handed a new one."""
    if not _own_folder(Path(folder)):
        return False
    try:
        os.utime(folder)
    except OSError:
        return False
    return True


async def end_runtime_scratch(runtime: Any) -> None:
    """Remove the scratch folder of a runtime whose session is never resumed (a scheduled run's, a
    helper's, an Inbox or side run's: ``session_keys.is_stateless``) as the runtime stops: its
    work is over. Another runtime's folder stays for its session's next turn, held by no runtime
    from here on, so a sweep removes it once it has been idle for days."""
    import asyncio

    from personalclaw import session_keys

    key = str(getattr(runtime, "_session_key", "") or "")
    try:
        with _HELD_LOCK:
            _HELD.pop(runtime, None)
    except TypeError:
        pass
    if getattr(runtime, "_run_scratch", None) and session_keys.is_stateless(key):
        runtime._run_scratch = None
        await asyncio.to_thread(remove_scratch, [key])


def audit(
    caller: str,
    hosts: Iterable[str],
    *,
    outcome: str,
    what: str,
    reason: str = "",
    unnamed: bool = False,
) -> None:
    """One egress row per host a launch or a command reaches (``unnamed``: one for the host it
    does not name), allowed or denied — the audit log's record of where it went. Best-effort,
    like every egress row."""
    try:
        from personalclaw.sel import sel

        named = [*sorted(set(hosts)), *(["(a host its command does not name)"] if unnamed else [])]
        for host in named:
            sel().log_api_access(
                caller=caller,
                operation="egress_launch",
                outcome=outcome,
                source="net",
                resources=f"{host} ({what})"[:200],
                error=reason[:200] if outcome != "allowed" else "",
            )
    except Exception:
        logger.debug("egress SEL audit failed", exc_info=True)


__all__ = [
    "CURRENT_SCRATCH",
    "FILE_WRITES",
    "asked_call_reach",
    "NOWHERE",
    "NativeCheck",
    "Reach",
    "ask_note",
    "audit",
    "call_reach",
    "effects_reach",
    "event_note",
    "native_check",
    "put_to_a_person",
    "fetched_from",
    "attended",
    "network_sentence",
    "outside",
    "past_bounds",
    "places",
    "reach_note",
    "refusal",
    "runtime_folder",
    "runtime_scratch",
    "session_folder",
    "scratch_dir",
    "screen",
    "shell_env",
    "session_roots",
    "shell_egress_policy",
    "tool_call_reach",
    "unlisted",
    "writes_sentence",
]
