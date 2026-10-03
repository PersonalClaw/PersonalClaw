"""Whether work may read your memory: the one check every memory read passes.

Your memory is what PersonalClaw has learned about you across conversations: the facts it keeps,
the lessons you taught it, the episodes of earlier conversations, its notes on itself and on how you
work, a chat's working memory, and the standing instructions you brought over from your other agent
tools. Your knowledge library is not memory: it is your own content, read as your files are.

Two kinds of work read none of your memory, by any path:

* **A Temporary chat's.** The chat starts blank, and so does everything working for it: its own
  turns, the subagents it starts and theirs, the steps of a workflow it runs, and every tool call
  any of them makes.
* **An app's, unless the app holds the memory permission.** A conversation an app started, an agent
  run the app asked for, and every agent working for either read your memory only when the app's
  manifest declares ``memory`` and you allowed it when you installed the app: its install consent
  says "Read and change your memory". An app that is not installed, is turned off, or whose
  permissions cannot be read holds nothing.

An Incognito chat reads memory as any chat does, and writes nothing back
(:mod:`personalclaw.memory_writes`).

:func:`reach_of` is the answer for the work a session key names. It follows a subagent to the
session it works for, an app's agent run to its app, and a workflow step to the chat that started
its run, up to the chat at the top, and reads each one's mode as the one reader of a session's mode
reads it (``memory_writes.session_mode``): the live chat first, then the in-process registry a
channel, a run or a subagent's start marks (:mod:`session_restrictions`), then the mode its
transcript records, and for a step its run's. A mode nothing can say reads nothing, as it writes
nothing: a record that cannot be read, and a chat the gateway does not hold that nothing records,
as for a Temporary chat that has ended. Every reader asks it: the context a chat's turn and a
subagent's first prompt are assembled with (memory, lessons, standing instructions, episodes,
active recall and the push reflex), the memory tools' routes (``memory_recall``, ``memory_list``),
``get_context``'s memory tier and the Learning page's facts. ``chat_search`` asks it too: a
Temporary chat's work searches no chat, and an app's searches only that app's conversations.

A refusal is said in words (:attr:`Reach.refusal`), so a tool asked for a memory answers why there
is none rather than "nothing found".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The key the dashboard's own pages send: the owner, not a session.
_DASHBOARD_UI = "dashboard:ui"
#: A subagent's key (``subagent.agent_work_id``) and an app's agent run's parent key
#: (``handlers.apps``: ``app:<name>``).
_SUBAGENT = "subagent:"
_APP = "app:"

#: Why a Temporary chat's work reads no memory, as the agent is told it.
TEMPORARY = (
    "This is a Temporary chat, which starts blank: nothing is read from your memory for it or for "
    "any work it starts — no saved facts, lessons or earlier conversations."
)
#: Why work whose chat's mode nothing can say reads no memory.
UNREADABLE = (
    "The memory setting of the chat this work is for cannot be read, so nothing is read from your "
    "memory or your other chats for it."
)


@dataclass(frozen=True)
class Reach:
    """Whose work a call is, and whether it may read your memory.

    ``keys`` are the session the call is made for, then each one it works for, up to the chat at
    the top; ``app`` is the app whose work it is (``""`` for yours); ``blank`` is why the sessions'
    own records say the work starts blank (:data:`TEMPORARY`, :data:`UNREADABLE`, or ``""``);
    ``refusal`` is why it reads none of your memory (``blank``, or the app's reason), ``""`` when it
    may; ``mode`` is the mode of the session the call is made for, as its own records read
    (``memory_writes.session_mode``), ``None`` for work that is no chat's.
    """

    keys: tuple[str, ...] = ()
    app: str = ""
    blank: str = ""
    refusal: str = ""
    mode: str | None = None

    @property
    def reads(self) -> bool:
        return not self.refusal

    @property
    def temporary(self) -> bool:
        """A Temporary chat's work: the chat, or a subagent or a workflow step working for one."""
        return self.blank == TEMPORARY


def reach_of(state: Any, caller: str, *, app: str = "") -> Reach:
    """Whose work a call made for *caller* (its ``X-Session-Key``) is, and whether it may read your
    memory. *app* is the app whose own token made the call, if one did: the work is then that
    app's whatever session it names. *state* is the gateway's dashboard state, or ``None`` where
    there is none to ask (then only the registry, the transcripts and the runs are read).
    ``dashboard:ui`` and no key are your own pages and tools acting for you, which read your
    memory."""
    keys: list[str] = []
    modes: list[str | None] = []
    key = "" if caller == _DASHBOARD_UI else (caller or "").strip()
    while key and key not in keys:
        keys.append(key)
        mode, owner, key = _step(state, key)
        modes.append(mode)
        app = app or owner
    blank = next((why for why in map(_blank, modes) if why), "")
    return Reach(
        tuple(keys),
        app,
        blank=blank,
        refusal=blank or app_refusal(app),
        mode=modes[0] if modes else None,
    )


def fed(chars: int, reach: Reach) -> dict[str, str]:
    """What a turn says it was fed (its context activity line, and the "Fed this turn" row of its
    details): *chars* of context put before the message, and whether any of it came from your
    memory. A turn that read none says so, and why, as its own kind of line."""
    if reach.reads:
        return {
            "kind": "context",
            "text": f"Injected {chars:,} chars of context (memory, lessons, history, episodic)",
        }
    if reach.temporary:
        why = "this is a Temporary chat"
    elif reach.blank:
        why = "this chat's memory setting cannot be read"
    else:
        why = f"the app {reach.app} cannot read your memory"
    return {
        "kind": "context_without_memory",
        "text": f"Injected {chars:,} chars of context, none of it from your memory: {why}",
    }


def app_refusal(app: str) -> str:
    """Why the app *app*'s work reads none of your memory, ``""`` when it may: the app holds the
    ``memory`` permission and is installed and on. Your own work (no app) may."""
    if not app:
        return ""
    from personalclaw.apps.permissions import app_lifecycle_denial, checker_for

    try:
        gone = app_lifecycle_denial(app)
        checker = None if gone else checker_for(app)
    except Exception:  # noqa: BLE001 - an app whose standing cannot be read holds nothing
        gone, checker = "", None
    if gone or checker is None:
        why = gone or "its permissions cannot be read"
        return f"This work is for the app {app}, so nothing is read from your memory for it: {why}."
    if checker.can_use_memory():
        return ""
    return (
        f"This work is for the app {app}, which was not given your memory, so nothing is read from "
        "it. An app's conversations and agents read your memory only when the app asks for the "
        "memory permission and you allow it when you install it."
    )


def _step(state: Any, key: str) -> tuple[str | None, str, str]:
    """For the session *key*: its mode as its own records read (``memory_writes.session_mode``),
    the app its work is for (``""`` for yours), and the key it works for next (``""`` at the
    top)."""
    from personalclaw.memory_writes import NOT_A_STEP, run_of_step, session_mode

    mode = session_mode(key, state=state)
    if key.startswith(_SUBAGENT):
        subagents = getattr(state, "subagents", None)
        info = subagents.get(key[len(_SUBAGENT) :]) if subagents else None
        app = str(getattr(info, "app", "") or "")
        return mode, app, str(getattr(info, "parent_session_key", "") or "")
    if key.startswith(_APP):
        return mode, key[len(_APP) :], ""
    run = run_of_step(key)
    if run is not NOT_A_STEP:
        return mode, "", str(getattr(getattr(run, "origin", None), "session_key", "") or "")
    app = ""
    if state is not None:
        name = key.split(":", 1)[-1]
        app = state.session_creating_app(name) or state.session_creating_app(key)
    return mode, app, ""


def _blank(mode: str | None) -> str:
    """Why work under *mode* reads no memory by its own records: :data:`TEMPORARY` for a
    Temporary chat's, :data:`UNREADABLE` for work whose chat's mode nothing can say, else ``""``."""
    from personalclaw.memory_writes import UNREADABLE as NOTHING_CAN_SAY

    if mode == "temporary":
        return TEMPORARY
    return UNREADABLE if mode == NOTHING_CAN_SAY else ""
