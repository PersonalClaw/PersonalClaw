"""A project's secret file, opened by the file tools for one chat you allow.

The file tools never reach a credential or secret file (``file_scope``). One kind is let through on
your answer: a project's own secret file, by its name and nothing more (``.env``, ``secrets.json``
and the rest of ``project_secrets.PROJECT_SECRET_FILE_PATTERNS``), never PersonalClaw's own
credential store, a sign-in file where it is one, or a file inside a credential folder
(:func:`grantable`). ``read_file`` asks to read one, and ``write_file`` and ``edit_file`` to change
it. The native runtime puts the ask to you on the chat's card, in a chat of yours you are in, and
only your two answers decide it: Allow for this session, or Deny. Nothing answers it on its own: not
Trust, YOLO, an approval mode, a standing grant, a hook's auto-approve pattern or an agent's Always
allow (``run_bounds.put_to_a_person``, ``approval_grants.declared_answer``).

What you allow is that file and that operation, for that chat: kept in memory only, ended when the
chat is deleted, and gone at a restart. A search or a listing still leaves secret files out, and
the shell still refuses them.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)

READ = "read"
WRITE = "write"

#: The file tools that ask for a project secret file, and what each asks to do with it. Every
#: other tool, a search or a listing among them, asks nothing and is refused as before.
ASKING_TOOLS: dict[str, str] = {"read_file": READ, "write_file": WRITE, "edit_file": WRITE}

#: In a call's metadata (the runtime's sink, never a tool card): the file a call was refused that
#: it may ask for, and the file a grant let it reach, each ``{"path": <real path>, "op": <op>}``.
ASKS_META_KEY = "asks_for_secret_file"
USED_META_KEY = "secret_file_grant_used"

#: What :func:`grantable` probes a path with: a name no rule refuses, one level below the path, so
#: the answer is where the file sits and never what it is called.
_PROBE = "_"

_lock = threading.Lock()
_grants: set[tuple[str, str, str]] = set()


@dataclass
class _Call:
    """One file tool call, as :func:`call` binds it: whose chat it is made in, what its tool asks
    to do with a secret file (``""`` for a tool that asks nothing), and what it met."""

    session: str
    op: str
    uses: bool
    asks: dict[str, str] | None = None
    used: dict[str, str] | None = None


_CALL: ContextVar[_Call | None] = ContextVar("secret_file_call", default=None)


def grantable(real: str) -> bool:
    """Whether the file at the real path *real* is one a chat may be allowed to open: a project's
    secret file by its name (``project_secrets.is_project_secret_name``), and by nothing else.

    Not PersonalClaw's own credential store or auth material, not another tool's sign-in by its name
    (``security.SIGN_IN_FILE_BASENAMES``), not one inside or at a protected location (``~/.ssh``,
    ``~/.aws``, the home's ``~/.netrc``, the keychain: a probe below the path is protected too), not
    one a suffix marks as key material (``*.key``, ``*.pem``, ``*.secret``), and not one with
    another name on the disk (a hard link), whose other name could be any of those.
    """
    from personalclaw.file_roots import _BLOCKED_SUFFIXES
    from personalclaw.project_secrets import is_project_secret_name
    from personalclaw.security import OWN_SECRET_BASENAMES, SIGN_IN_FILE_BASENAMES, SensitivePaths

    name = os.path.basename(real)
    folded = name.casefold()
    if not is_project_secret_name(name) or folded.endswith(_BLOCKED_SUFFIXES):
        return False
    if folded in {n.casefold() for n in OWN_SECRET_BASENAMES | SIGN_IN_FILE_BASENAMES}:
        return False
    if SensitivePaths().kind(os.path.join(real, _PROBE)):
        return False
    try:
        return os.stat(real).st_nlink <= 1
    except OSError:
        return True  # a file write_file is about to make has no other name


def arm(client: object, *, unattended: bool, app_chat: str) -> None:
    """Say to a chat's runtime whether this turn asks the owner for a project secret file its file
    tools are refused: in a chat of the owner's they are in, and never on an unattended turn or in
    a conversation an app started (*app_chat*), where the refusal stands. A runtime that cannot ask
    (an agent CLI's) is told nothing."""
    asks = getattr(client, "set_secret_file_asks", None)
    if callable(asks):
        asks(not unattended and not app_chat)


def asked_of(event: object) -> str:
    """What *event*, a permission request, asks to do with a project secret file: :data:`READ` or
    :data:`WRITE` (``AgentEvent.secret_file``), and ``""`` for any other ask, and for anything else
    that field holds, which is no ask this build knows."""
    op = getattr(event, "secret_file", "")
    return op if isinstance(op, str) and op in (READ, WRITE) else ""


def card_meta(event: object) -> dict[str, str]:
    """What the chat's transcript row for *event*'s ask keeps of a project secret file: the
    operation it asks for, so a reloaded card offers only its two answers; ``{}`` for any other
    ask."""
    op = asked_of(event)
    return {"secret_file": op} if op else {}


def ask_note(op: str) -> str:
    """The line an ask for a project secret file shows on every surface it is asked on: what an
    Allow does, and that nothing but the owner's answer gives it."""
    verb = "change" if op == WRITE else "read"
    return (
        f"This is a project's secret file. Allow lets this chat {verb} it until the chat is "
        "deleted or PersonalClaw restarts. Only your answer opens it: Trust, YOLO and saved grants "
        "never do."
    )


def allow(session: str, real: str, op: str) -> bool:
    """Let the chat *session* reach the file at *real* for *op* until the chat ends: your Allow on
    its ask. False, and nothing kept, for a file no grant may open (:func:`grantable`)."""
    if not session or op not in (READ, WRITE) or not grantable(real):
        return False
    with _lock:
        _grants.add((session, real, op))
    return True


def holds(session: str, real: str, op: str) -> bool:
    """Whether the chat *session* was allowed to reach the file at *real* for *op*."""
    with _lock:
        return (session, real, op) in _grants


def end(session: str) -> int:
    """End every grant the chat *session* holds, as its deletion does; how many there were."""
    with _lock:
        ended = {grant for grant in _grants if grant[0] == session}
        _grants.difference_update(ended)
    return len(ended)


def clear() -> None:
    """End every grant: what a restart does, since none is ever written down."""
    with _lock:
        _grants.clear()


@contextlib.contextmanager
def call(session: str, tool: str, meta: dict, *, uses: bool = True) -> Iterator[None]:
    """Bind one call of *tool* in the chat *session* for the file scope's check (:func:`opens`).

    On the way out, *meta* (the call's sink) gets the secret file the call was refused, which it may
    ask for (:data:`ASKS_META_KEY`), and the one a grant let it reach (:data:`USED_META_KEY`) when
    *uses* (a pre-flight check reaches nothing, so it passes ``False``)."""
    bound = _Call(session=session, op=ASKING_TOOLS.get(tool, ""), uses=uses)
    token = _CALL.set(bound)
    try:
        yield
    finally:
        _CALL.reset(token)
        if bound.asks is not None:
            meta[ASKS_META_KEY] = bound.asks
        if bound.used is not None:
            meta[USED_META_KEY] = bound.used


def opens(real: str, change: bool) -> bool:
    """Whether the call being made (:func:`call`) may reach the secret file at *real*, which the
    file scope refuses it otherwise (with *change*, to change it): its chat was allowed that file
    for what its tool does with it. A call of a tool that may ask, refused, notes what it may ask
    for; a call outside :func:`call`, or of another tool, reaches none."""
    bound = _CALL.get()
    op = WRITE if change else READ
    if bound is None or not bound.session or bound.op != op or not grantable(real):
        return False
    if holds(bound.session, real, op):
        if bound.uses:
            bound.used = {"path": real, "op": op}
        return True
    bound.asks = {"path": real, "op": op}
    return False


async def log_grant(session: str, asked: dict[str, str]) -> None:
    """The security log's row for your Allow: the chat, the file's real path and the operation."""
    await _log("secret_file_granted", "granted", session, asked)


async def log_use(session: str, tool: str, used: dict[str, str]) -> None:
    """The security log's row for a call a grant let reach its file: what *tool* did with it. The
    call has run by then, so a row the log cannot take is said in this process's log instead."""
    try:
        await _log("secret_file_grant_used", "allowed", session, used, tool=tool)
    except Exception:  # noqa: BLE001 - the call already ran; only its record failed
        logger.warning("the use of a secret file grant could not be recorded", exc_info=True)


async def _log(
    operation: str, outcome: str, session: str, what: dict[str, str], **more: str
) -> None:
    from personalclaw.sel import sel, sel_log_async

    await sel_log_async(
        sel().log_api_access,
        caller=session or "native",
        operation=operation,
        outcome=outcome,
        source="native",
        resources=f"path={what['path']} op={what['op']}",
        metadata={"session": session, "op": what["op"], **more},
    )
