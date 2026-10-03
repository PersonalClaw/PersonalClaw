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
  run the app asked for, an agent its scheduled job started, and every agent working for any of
  them read your memory only when the app's manifest declares ``memory`` and you allowed it when you
  installed the app: its install consent says "Read and change your memory". An app that is not
  installed, is turned off, or whose permissions cannot be read holds nothing. The same grant is
  what lets such work change your memory (:mod:`personalclaw.memory_writes`).

An Incognito chat reads memory as any chat does, and writes nothing back
(:mod:`personalclaw.memory_writes`).

:func:`reach_of` is the answer for the work a session key names. It follows a subagent to the
session it works for, an app's agent run to its app, an agent a scheduled job started (and the
job's own session) to the app whose job it is, by the job's id the work carries, and a workflow
step to the chat or the app's job that started its run, up to the chat at the top. A run whose
record says it is an app's work (``apps.app_work``: a batch, or any run, the app's agent started)
is that app's from its record, after the agent that started it is gone too. It reads each
one's mode as the one reader of a session's mode reads it (``memory_writes.session_mode``): the
live chat first, then the in-process registry a channel, a run or a subagent's start marks
(:mod:`session_restrictions`), then the mode its transcript records, and for a step its run's. A
mode nothing can say reads nothing, as it writes nothing: a record that cannot be read, and a chat
the gateway does not hold that nothing records, as for a Temporary chat that has ended. Every
reader asks it: the context a chat's turn, a subagent's first prompt and a scheduled job's announce
turn are assembled with (memory, lessons, standing instructions, episodes, active recall and the
push reflex), the memory tools' routes (``memory_recall``, ``memory_list``, ``triage_rules_list``),
``get_context``'s memory tier and the Learning page's facts. ``chat_search`` asks it too: a
Temporary chat's work searches no chat, and an app's searches only that app's conversations. The
write scope asks it for the app whose work a request is and for the chat at the top that what the
request writes is filed under (:mod:`personalclaw.memory_writes`), and the routes' guard of a
change to memory asks it whether the work keeps anything (:attr:`Reach.restricted_mode`): work for
a chat that keeps nothing keeps nothing, whatever its own key is marked, so a subagent's lesson is
saved exactly where the chat it works for may keep one.

A refusal is said in words (:attr:`Reach.refusal`), so a tool asked for a memory answers why there
is none rather than "nothing found".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The key the dashboard's own pages send: the owner, not a session.
_DASHBOARD_UI = "dashboard:ui"
#: A subagent's key (``subagent.agent_work_id``), an app's agent run's parent key
#: (``handlers.apps``: ``app:<name>``) and a trigger's own session (``triggers.wakeup``:
#: ``cron:<trigger id>``).
_SUBAGENT = "subagent:"
_APP = "app:"
_TRIGGER = "cron:"

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
    (``memory_writes.session_mode``), ``None`` for work that is no chat's; ``restricted_mode`` is
    the strictest mode, as each one's own records read, of any session along ``keys`` that keeps
    nothing (``"temporary"``, ``memory_writes.UNREADABLE``, ``"incognito"``), ``""`` when each keeps
    memory or is no chat's: the work then keeps nothing either; ``job`` is the trigger id of that
    app's scheduled job when the job's fire started the work (``app_crons.job_id``), ``""`` else.
    """

    keys: tuple[str, ...] = ()
    app: str = ""
    blank: str = ""
    refusal: str = ""
    mode: str | None = None
    restricted_mode: str = ""
    job: str = ""

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
    job = ""
    key = "" if caller == _DASHBOARD_UI else (caller or "").strip()
    while key and key not in keys:
        keys.append(key)
        mode, owner, owners_job, key = _step(state, key)
        modes.append(mode)
        app = app or owner
        # The app's own job, found wherever along the chain it is named: a task of a batch the
        # job's agent started carries the app's name, and its run names the job.
        job = job or (owners_job if owner == app else "")
    blank = next((why for why in map(_blank, modes) if why), "")
    return Reach(
        tuple(keys),
        app,
        blank=blank,
        refusal=blank or app_refusal(app),
        mode=modes[0] if modes else None,
        restricted_mode=_strictest_keeping_nothing(modes),
        job=job,
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


def app_refusal(app: str, *, changing: bool = False) -> str:
    """Why the app *app*'s work reads none of your memory (*changing*: may change none of it),
    ``""`` when it may: the app holds the ``memory`` permission and is installed and on. One grant
    for both, as its install consent says ("Read and change your memory"), so both answers are
    this one check, said for what was asked. Your own work (no app) may."""
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
        if changing:
            return f"This work is for the app {app}, so it may not change your memory: {why}."
        return f"This work is for the app {app}, so nothing is read from your memory for it: {why}."
    if checker.can_use_memory():
        return ""
    if changing:
        return (
            f"This work is for the app {app}, which was not given your memory, so nothing is saved "
            "to it or removed from it. An app's conversations and agents change your memory only "
            "when the app asks for the memory permission and you allow it when you install it."
        )
    return (
        f"This work is for the app {app}, which was not given your memory, so nothing is read from "
        "it. An app's conversations and agents read your memory only when the app asks for the "
        "memory permission and you allow it when you install it."
    )


def _step(state: Any, key: str) -> tuple[str | None, str, str, str]:
    """For the session *key*: its mode as its own records read (``memory_writes.session_mode``),
    the app its work is for (``""`` for yours), that app's scheduled job when the job's fire
    started it (its trigger id, ``""`` for none), and the key it works for next (``""`` at the
    top)."""
    from personalclaw.memory_writes import NOT_A_STEP, run_of_step, session_mode

    mode = session_mode(key, state=state)
    if key.startswith(_SUBAGENT):
        subagents = getattr(state, "subagents", None)
        info = subagents.get(key[len(_SUBAGENT) :]) if subagents else None
        # The app whose agent permission started it, or whose scheduled job's fire did.
        trigger = str(getattr(info, "trigger_id", "") or "")
        app = str(getattr(info, "app", "") or "") or job_app(trigger)
        job = trigger if app and job_app(trigger) == app else ""
        return mode, app, job, str(getattr(info, "parent_session_key", "") or "")
    if key.startswith(_APP):
        return mode, key[len(_APP) :], "", ""
    run = run_of_step(key)
    if run is not NOT_A_STEP:
        from personalclaw.apps.app_work import of_run

        # Whose work its record says the run is: started for the app's work (its batch, a run its
        # agent started), or by its scheduled job. Read from the record, which outlives the agent
        # that started the run.
        work = of_run(run)
        origin = getattr(run, "origin", None)
        if work is not None and not work.app:
            # Its record says it is an app's work and cannot say whose: nothing can say what it
            # may read, so it reads and keeps nothing.
            from personalclaw.memory_writes import UNREADABLE as NOTHING_CAN_SAY

            mode = NOTHING_CAN_SAY
        app, job = (work.app, work.job) if work is not None else ("", "")
        return mode, app, job, str(getattr(origin, "session_key", "") or "")
    trigger = key[len(_TRIGGER) :] if key.startswith(_TRIGGER) else ""
    app = job_app(trigger)
    job = trigger if app else ""
    creating_app = getattr(state, "session_creating_app", None)
    if not app and callable(creating_app):
        name = key.split(":", 1)[-1]
        app = creating_app(name) or creating_app(key)
    return mode, app, job, ""


def job_app(trigger_id: object) -> str:
    """The app whose scheduled job the trigger *trigger_id* is (``app_crons.app_of``), or ``""``:
    the work the job's fire started is that app's, wherever it carries the job's id."""
    if not isinstance(trigger_id, str) or not trigger_id:
        return ""
    from personalclaw.apps.app_crons import app_of

    return app_of(trigger_id)


def _blank(mode: str | None) -> str:
    """Why work under *mode* reads no memory by its own records: :data:`TEMPORARY` for a
    Temporary chat's, :data:`UNREADABLE` for work whose chat's mode nothing can say, else ``""``."""
    from personalclaw.memory_writes import UNREADABLE as NOTHING_CAN_SAY

    if mode == "temporary":
        return TEMPORARY
    return UNREADABLE if mode == NOTHING_CAN_SAY else ""


def _strictest_keeping_nothing(modes: list[str | None]) -> str:
    """The strictest of *modes* that keeps nothing (:attr:`Reach.restricted_mode`), ``""`` when
    none does."""
    from personalclaw.memory_writes import UNREADABLE as NOTHING_CAN_SAY

    return next((m for m in ("temporary", NOTHING_CAN_SAY, "incognito") if m in modes), "")
