"""Where a run's calls reach without a person saying yes: the allowed hosts and its own folders.

Two bounds, read from one reading of each call (:mod:`personalclaw.command_effects` for a shell
command, the path a native file tool is given):

* **The network.** A shell command that reaches the network is held to the egress allow-list
  (Allowed hosts in Settings → Security → Network egress, ``security.egress.allow_hosts``): every
  host it names must be on that list and not on Denied hosts, and the run's safety profile can
  only narrow that further (an egress tier of ``off`` lists nothing). A command that reaches a host
  it does not name (a remote by name, a URL built from a variable) cannot be checked, so it is
  held the same way as an unlisted one. An **unattended** run is refused such a command; an
  **attended** one asks a person, with the host named on the card, whatever would otherwise have
  answered for it (Trust, YOLO, a standing grant, a hook's auto-approve). Only the listed host
  goes ahead unasked, because the list is the owner's own answer for it.
* **Writes.** An unattended run changes files only inside its own folders: the folder it works in
  (a workspace, or a code loop's worktree), the folders it was given (a loop's own folder, a
  project's context folder, the files an automation may change) and its own temporary folder
  (:func:`scratch_dir`, which its shell gets as ``TMPDIR``). A native file write elsewhere is
  refused, and so is a shell command whose write facet names a path elsewhere, or a path this
  reading cannot name. An attended run is asked about each of its writes as before.

What a program does that its command line does not say (a script's own ``open``, a tool's own
configuration naming another registry) is outside any reading of the text: the OS sandbox and
the credential screens are the fence, and this is defence in depth in front of them.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.command_effects import DEVICES, CommandEffects, command_effects
from personalclaw.file_scope import PATH_TOOLS
from personalclaw.shell_syntax import LEADING_VARIABLES

logger = logging.getLogger(__name__)

#: The native file tools that change a file, and the argument naming it (``file_scope``'s table).
FILE_WRITES = {tool: arg for tool, (arg, changes, _) in PATH_TOOLS.items() if changes}


# ── The network ──────────────────────────────────────────────────────────────────────────────────


def shell_egress_policy(session_key: str = "") -> Any:
    """The hosts a shell command may reach unasked, as an exclusive :class:`EgressPolicy`: the
    operator's Allowed hosts (and their Denied hosts), narrowed by the session's safety-profile
    egress tier. ``None`` when that tier allows no egress at all.

    A profile that cannot be read leaves the operator's list as it is: the list is already the
    narrowest reach there is short of none, and an unreadable profile narrows nothing it names."""
    from personalclaw.net.policy import LISTED, egress_policy_for, egress_policy_for_profile

    base = egress_policy_for(LISTED)
    try:
        from personalclaw.guardrails.policy import profile_for_session

        tier = profile_for_session(session_key).egress_tier
    except Exception:  # noqa: BLE001 - the operator's own list still holds
        logger.warning("run bounds: the session's egress tier is unreadable", exc_info=True)
        return base
    return egress_policy_for_profile(base, tier)


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


# ── Writes ───────────────────────────────────────────────────────────────────────────────────────

#: Where every run's own temporary folder lives, under this user's temporary folder.
_SCRATCH_PARENT = "personalclaw-scratch"


def scratch_dir(session_key: str) -> str:
    """The run's own temporary folder (created, ``0700``), or ``""`` when one cannot be made safely.

    It sits in this user's temporary folder, named by a digest of the session (a session's key is
    not written into a path), under a folder only this user owns: a parent that is a link or
    someone else's is not used, so a run is never handed a folder another account controls.
    """
    if not session_key:
        return ""
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]
    parent = Path(tempfile.gettempdir()) / f"{_SCRATCH_PARENT}-{os.getuid()}"
    folder = parent / digest
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
        info = os.lstat(folder)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            return ""
    except OSError:
        logger.warning("run bounds: the scratch folder could not be made", exc_info=True)
        return ""
    return os.path.realpath(folder)


#: The run's own temporary folder for the call being dispatched ("" = none: the shell keeps the
#: owner's), bound with the native tools' per-turn context (``builtin_tools.bind_tool_context``).
CURRENT_SCRATCH: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_native_scratch", default=""
)


def shell_env(*, site: str) -> dict[str, str]:
    """The environment a run's shell starts with: the child allowlist
    (``sandbox.build_child_env``) and, when the run has its own temporary folder, that folder as
    ``TMPDIR``, so the scratch files a command makes (``mktemp -d``, a program's temporary files)
    land where it may write."""
    from personalclaw.sandbox import build_child_env

    env = build_child_env(site=site)
    scratch = CURRENT_SCRATCH.get()
    if scratch:
        env["TMPDIR"] = scratch
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

    @property
    def network(self) -> bool:
        return bool(self.hosts) or self.unnamed_host

    @property
    def writes(self) -> bool:
        return bool(self.paths) or self.unnamed_path

    def __bool__(self) -> bool:
        return self.network or self.writes


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
    if effects.network:
        hosts = unlisted(effects.hosts, policy, declared=declared)
        unnamed_host = effects.host_unread
    paths: tuple[str, ...] = ()
    unnamed_path = False
    if within is not None and effects.writes:
        paths = outside(effects.targets, cwd=cwd, within=within, scratch=scratch)
        unnamed_path = effects.target_unread
    return Reach(hosts, unnamed_host, paths, unnamed_path)


def call_reach(
    declared: object,
    title: str,
    tool_kind: str,
    tool_input: object,
    *,
    session_key: str,
    cwd: str = "",
    within: Sequence[str] | None = None,
    scratch: str = "",
) -> Reach:
    """Where one tool call reaches past its run's bounds.

    The network half is asked of every shell call (:func:`personalclaw.task_modes.shell_command`
    says whether a call is one). The writes half only when *within* names the run's folders,
    which is how a caller says the run is unattended: of a shell command's write facet, and of a
    native file write's path."""
    from personalclaw.task_modes import declared_level, shell_command

    command = shell_command(title, tool_kind, tool_input, declared)
    if command:
        return effects_reach(
            command_effects(command),
            policy=shell_egress_policy(session_key),
            cwd=cwd,
            within=within,
            scratch=scratch,
        )
    arg = FILE_WRITES.get(title)
    if within is None or arg is None or not declared_level(declared):
        return NOWHERE
    raw = tool_input.get(arg) if isinstance(tool_input, dict) else None
    if not isinstance(raw, str) or not raw:
        return NOWHERE  # the tool says what is missing
    return Reach(paths=outside([raw], cwd=cwd, within=within, scratch=scratch))


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
    unattended run and ``None`` for an attended one, whose writes are asked about."""
    from personalclaw.task_modes import shell_command

    if tool_name not in FILE_WRITES and not shell_command(tool_name, "", tool_input, declared):
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
    reasons = [network_sentence(reach), writes_sentence(reach, within)]
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
    return (
        past_bounds(reach, within)
        + ". To go on, "
        + "; and ".join(steps)
        + ". Otherwise leave the step for the user, and say what it needs"
    )


def ask_note(reach: Reach) -> str:
    """The line an attended run's card shows for a call that reaches past the allowed hosts:
    why it asks, though a standing grant would have answered any other call."""
    sentence = network_sentence(reach)
    if not sentence:
        return ""
    return (
        sentence[0].upper()
        + sentence[1:]
        + ". A command that reaches a host off that list, or one it does not name, is always "
        "asked about, whatever this chat or its agent allows."
    )


def reach_note(
    declared: object, title: str, tool_kind: str, tool_input: object, session_key: str = ""
) -> str:
    """The card's line (:func:`ask_note`) for a call put to a person, ``""`` when it reaches no
    host past the allowed hosts. A display line, so a reading that fails says nothing."""
    try:
        reach = call_reach(declared, title, tool_kind, tool_input, session_key=session_key)
    except Exception:  # noqa: BLE001 - the card still shows the call and its risk
        logger.warning("run bounds: could not read where an asked call reaches", exc_info=True)
        return ""
    return ask_note(reach)


def _event_reach(event: Any, session_key: str) -> Reach:
    return call_reach(
        getattr(event, "risk_level", "") or "",
        str(getattr(event, "title", "") or ""),
        str(getattr(event, "tool_kind", "") or ""),
        getattr(event, "tool_input", ""),
        session_key=session_key,
    )


def off_list(event: Any, session_key: str = "") -> bool:
    """Whether a call put to an approval gate (a permission request) reaches a host off the
    allowed hosts, or one its command does not name: no grant answers it, so a person must."""
    return _event_reach(event, session_key).network


def screen(hooks: Any, event: Any, session_key: str, cwd: str | None = None) -> tuple[Any, bool]:
    """The hook chain's verdict on a call put to an approval gate
    (:func:`~personalclaw.acp.permission_authority.screen_tool_call`), and whether the call
    reaches a host off the allowed hosts (:func:`off_list`). Such a call is put to a person past
    every grant, so a hook's auto-approve does not answer it (it stays an allow); a refusal does."""
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_AUTO_APPROVE

    verdict = screen_tool_call(hooks, str(event.title or ""), event.tool_input, cwd)
    reaches = off_list(event, session_key)
    if reaches and verdict.action == TOOL_AUTO_APPROVE:
        verdict = type(verdict).allow()
    return verdict, reaches


def event_note(event: Any, session_key: str = "") -> str:
    """:func:`reach_note` for a permission request."""
    return reach_note(
        getattr(event, "risk_level", "") or "",
        str(getattr(event, "title", "") or ""),
        str(getattr(event, "tool_kind", "") or ""),
        getattr(event, "tool_input", ""),
        session_key,
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
        runtime._declared(tool_name),
        tool_name,
        args,
        session_key=session_key,
        cwd=str(cwd or ""),
        scratch=scratch,
        roots=(lambda: session_roots(runtime._tool_index, cwd)) if unattended else None,
    )
    return NativeCheck(reach, unattended, tool_name, session_key, tuple(within), scratch)


def runtime_scratch(runtime: Any) -> str:
    """An unattended native runtime's own temporary folder (:func:`scratch_dir`, made once and
    kept on the runtime); ``""`` for an attended one, whose shell keeps the owner's."""
    if not getattr(runtime, "_unattended", False):
        return ""
    made = getattr(runtime, "_run_scratch", None)
    if made is None:
        made = scratch_dir(str(getattr(runtime, "_session_key", "") or ""))
        runtime._run_scratch = made
    return made


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
    "off_list",
    "attended",
    "network_sentence",
    "outside",
    "past_bounds",
    "places",
    "reach_note",
    "refusal",
    "runtime_scratch",
    "scratch_dir",
    "screen",
    "shell_env",
    "session_roots",
    "shell_egress_policy",
    "tool_call_reach",
    "unlisted",
    "writes_sentence",
]
