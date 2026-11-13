"""Which project an artifact an agent saves is stamped with, so it shows on that project's page.

The artifact tools (:mod:`personalclaw.mcp_artifacts`) ask here for every new row: an artifact, a
document or a sheet (:func:`current`), and an image or a video (:func:`for_media`). When the
answer is not known, the row is filed under no project, never a default one, and the reply says so
(:func:`unknown_note`).

The rule for which project a piece of work is in (:mod:`personalclaw.work_project`) answers the
waiting and running lists. The chat read here (:func:`of_open_chat`) answers differently on
purpose, so it is not that rule. It says when it could not tell, where that rule reads any failure
as no project, and the stamp's note and WARNING depend on it. It reads one key on the tool's own
thread, where that rule reads the gateway's memory on its event loop (``work_project.taken``). It
names a ``dashboard:`` chat as the bound-project endpoint does
(``GET /api/chat/sessions/bound-project``), by the name after the prefix, so a save from an agent
CLI's tool server and an image the gateway makes for it stamp the same chat. And it stamps the id
as the chat holds it, where that rule drops an id that names no project: which project a row is in
is decided when the library reads it (:mod:`personalclaw.project_items`), and an id no project
has reads as no project there, as a deleted project's own artifacts do.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from personalclaw.mcp_core import _resolve_session_key

# Logged under the artifact tools' name, so what reads or filters their log sees these lines.
logger = logging.getLogger("personalclaw.mcp_artifacts")


class Project(NamedTuple):
    """The Project a new row is filed under, and why it is unknown when the lookup failed."""

    id: str
    #: Why the gateway gave no answer ("" when it answered, or was never asked).
    why_unknown: str


#: What a new row's reply ends with when the lookup failed, so the agent can tell the user.
NO_PROJECT_NOTE = (
    " Filed under no project: PersonalClaw's gateway didn't say which project this chat belongs to."
)


def current() -> Project:
    """The Project this save scopes under, in both runtimes.

    Two callers, two mechanisms, one answer:

    * the **native** runtime runs in-process and binds a per-turn contextvar, so reading
      it is exact and free;
    * an **ACP** CLI's tools run in a separate ``personalclaw mcp-core`` process, the agent
      CLI's tool server (:func:`~personalclaw.mcp_core.serves_an_agent_cli`). That server
      binds the session key around every call, but nothing there binds the native project
      contextvar: ``provider_bridge`` pops ``project_id`` unconditionally and hands it only
      to the native builder, so nothing about the binding crosses into the ACP branch.
      Every ACP ``artifact_save`` therefore stamped ``project_id=""`` and the artifact never
      appeared on its Project page.

    The ACP half resolves SERVER-SIDE from the session key rather than by threading a new
    argument through the protocol: the key already crosses as ``PERSONALCLAW_SESSION_KEY``
    (or the ``session_pid_<pid>.txt`` ancestor walk), which is exactly what
    ``_resolve_session_key`` reads, so the gateway can be asked what that session is bound
    to (:func:`session_bound`). No protocol change, and the answer is live rather
    than a snapshot taken at spawn.

    The id is "" for an unscoped session, and "" on any failure — never a default project and
    never None. Stamping an unscoped save into "Personal" would file work under a project the
    user never chose, which is worse than an unstamped artifact. *why_unknown* is set only when
    the gateway was asked and gave no usable answer.
    """
    try:
        from personalclaw.agents.native.builtin_tools import current_project_id

        pid = current_project_id() or ""
        if pid:
            return Project(pid, "")
    except Exception:
        pass
    return session_bound()


def for_media() -> Project:
    """The Project a new image or video is filed under (``image_generate``, ``video_generate``).

    :func:`current` first. When that has no Project and no failure to report, and this
    process is the gateway (``serves_an_agent_cli()`` False), it also reads the Project of the
    chat the call is bound to (by ``_as_its_work`` or the native ``_dispatch_scope``) from the
    gateway's open chats (:func:`of_open_chat`), not of the chat ``_resolve_session_key()``
    names, which takes an inherited ``PERSONALCLAW_SESSION_KEY`` first. An agent CLI's call of
    either tool is made by the gateway (``MODEL_WORK``, through ``POST /api/tools/invoke``),
    and that route binds only the session key and no Project, so the CLI's image would otherwise
    be filed under none. Only these two tools read it this way. The project contextvar, and with it
    ``command_secrets.call_project()``, stays unbound for such a call.
    """
    from personalclaw.mcp_core import get_current_session_key, serves_an_agent_cli

    project = current()
    if project.id or project.why_unknown or serves_an_agent_cli():
        return project
    return of_open_chat(get_current_session_key())


def of_open_chat(sk: str) -> Project:
    """The Project of the chat *sk* names (``dashboard:<name>``), read from the gateway's open
    chats, as ``GET /api/chat/sessions/bound-project`` reads a ``dashboard:`` chat.

    Work that is no chat's (your own pages' ``dashboard:ui``, an app's, a scheduled job's ``cron:``,
    no session at all) and a chat the gateway does not hold are ``("", "")``. ``why_unknown`` is set
    only when the open chats could not be read, or the chat's Project is not a str. The id is
    stamped as the chat holds it. Why this is not ``work_project``'s rule: the module docstring.
    """
    from personalclaw import session_keys

    if sk == session_keys.DASHBOARD_UI or not session_keys.DASHBOARD.names(sk):
        return Project("", "")
    try:
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        state = get_dashboard_state()
        if state is None:
            return Project("", "the gateway's open chats could not be read")
        chats = getattr(state, "_sessions", {}) or {}
        chat = chats.get(sk.removeprefix(session_keys.DASHBOARD.prefix))
        pid = (getattr(chat, "project_id", "") if chat is not None else "") or ""
    except Exception as exc:  # noqa: BLE001 - a failed lookup files under no Project
        return Project("", str(exc) or type(exc).__name__)
    if not isinstance(pid, str):
        return Project("", f"project_id was {type(pid).__name__}")
    return Project(pid, "")


def session_bound() -> Project:
    """The calling session's bound Project, read from the gateway.

    The answer counts only as a dict with no ``error`` whose ``project_id`` is a str or absent
    (absent is ""). Anything else (a refused connection, an error status, a body that is not
    JSON, a timeout, an unreadable credential, a non-str id, a raising call) is ``("", why)``,
    logged once as a WARNING that names the session.

    **Only the agent CLI's tool server asks, and the guard is load-bearing.** Which process
    this is decides it, through :func:`~personalclaw.mcp_core.serves_an_agent_cli`:

    * **Inside the gateway** (``serves_an_agent_cli()`` is False) the native project
      contextvar :func:`current` reads first is authoritative, and its emptiness
      means the turn is unscoped (``image_generate`` and ``video_generate`` alone then read the
      chat's Project, :func:`for_media`). No HTTP call is made: asking the gateway
      from inside itself is at best a wasted loopback round trip, and a request the
      gateway can't serve if a tool is ever called on its event loop, where a blocking
      ``urllib`` GET would wait on the very loop that has to answer it.
    * **In the agent CLI's tool server** (True, set by ``run_mcp_core_server`` before it
      serves a call) the session key is bound around every call
      (``mcp_core._call_as_its_session``), but no project contextvar is. So it asks
      ``GET /api/chat/sessions/bound-project`` for the session it serves.

    The guard used to be "the session-key contextvar is bound", taken as the sign of being
    in-process. It is not one: ``_call_as_its_session`` binds that same key in the tool
    server too, so the old guard returned "" there and every ACP save stayed unstamped,
    while a gateway call with no key bound but one that still resolves (an inherited
    ``PERSONALCLAW_SESSION_KEY``) would have asked the gateway from inside itself.
    """
    from personalclaw.mcp_core import _get, serves_an_agent_cli

    if not serves_an_agent_cli():
        # The gateway's own turn is answered by the native project contextvar; the gateway
        # is never asked from inside itself.
        return Project("", "")
    sk = _resolve_session_key()
    if not sk:
        return Project("", "")  # no session identity → nothing to look up
    try:
        got = _get("/api/chat/sessions/bound-project")
    except Exception as exc:  # noqa: BLE001 - a failed lookup files under no Project
        got = {"error": str(exc) or type(exc).__name__}
    if not isinstance(got, dict):
        why = f"the answer was {type(got).__name__}"
    elif got.get("error"):
        why = str(got["error"])
    elif not isinstance(pid := got.get("project_id", ""), str):
        why = f"project_id was {type(pid).__name__}"
    else:
        return Project(pid, "")
    logger.warning("Could not tell which project chat %s belongs to: %s", sk, why)
    return Project("", why)


def unknown_note(slug: str, why: str, sk: str | None) -> str:
    """What the reply for the new row *slug* adds when the lookup failed (*why*): the note, with a
    WARNING that names the row and the chat. "" when the Project is known."""
    if not why:
        return ""
    logger.warning("Filed %s under no project for chat %s: %s", slug, sk, why)
    return NO_PROJECT_NOTE
