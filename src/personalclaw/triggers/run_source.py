"""What started a run of an automation: the word its row in the run history keeps.

Every row a run records says what started it (`ScheduleRun.source`), and the run history and the
Triggers page show it. Two kinds of run, and this word is what tells them apart:

* **Yours** (:data:`YOU`): Run now in PersonalClaw, ``personalclaw cron trigger`` typed at a
  terminal, your answer to the question a run stopped on, and the restart review's Run now. The one
  run the automation's hourly cap and its failure streak pass over
  (`schedule_history.ScheduleRunStore.count_since`, `autopause.consecutive_failures_from`): the cap
  is there to stop the machine running away on its own, and testing a broken automation by hand
  must neither pause it nor clear a real failure streak.
* **A fire**: the automation firing on its own (its schedule, an event, a program's post to its
  webhook, a view it is bound to rendering, another run finishing, a watched file or page changing,
  a chat going quiet), or because something that is not you asked for it by name: an agent, an
  app, another automation's own work, or a program on this machine. Nobody answers what a fire
  runs, so each one is admitted as the clock's fires are and counts against the cap and the streak.

The word is written where the run starts and kept on its row, never worked out later from what the
automation is now: an automation's kind can change, and a row says what started the run it records.
A row written before rows said this reads ``""``, and is not yours.
"""

from __future__ import annotations

from typing import Any

#: You ran it: Run now (in PersonalClaw, or typed at a terminal), your answer, the review's Run now.
YOU = "you"
#: An agent asked for it: a chat's, a subagent's, a loop's or a workflow step's (its tool, or the
#: CLI in its shell).
AGENT = "agent"
#: An app asked for it: its token, or its own work.
APP = "app"
#: Another automation's own work asked for it: its script calling back, or its agent.
AUTOMATION = "automation"
#: A program on this machine asked for it, with no session of its own: the CLI run by a script, a
#: command another run started.
PROGRAM = "program"

#: The automation's clock.
SCHEDULE = "schedule"
#: An event it listens for.
EVENT = "event"
#: A program's post to its webhook's address.
WEBHOOK = "webhook"
#: A view it is bound to rendering.
VIEW = "view"
#: Another run finishing.
CHAIN = "chain"
#: A file it watches changing.
FILE = "file"
#: A page it watches changing.
PAGE = "page"
#: A chat it watches going quiet.
IDLE = "idle"

#: What fires an automation of each kind on its own. A ``manual`` automation never does.
_FIRED_BY_KIND: dict[str, str] = {
    "clock": SCHEDULE,
    "event": EVENT,
    "webhook": WEBHOOK,
    "view": VIEW,
    "run_completed": CHAIN,
    "file": FILE,
    "web_watch": PAGE,
    "idle": IDLE,
}

#: Who can ask for a run by name, rather than fire it by what it watches.
ASKERS: frozenset[str] = frozenset({YOU, AGENT, APP, AUTOMATION, PROGRAM})

#: Every word a row can say.
SOURCES: frozenset[str] = ASKERS | frozenset(_FIRED_BY_KIND.values())


def fired(trigger: Any) -> str:
    """What fires *trigger* on its own, by its kind; ``""`` for a kind nothing fires on its own."""
    return _FIRED_BY_KIND.get(str(getattr(trigger, "kind", "") or ""), "")


def yours(source: str) -> bool:
    """Whether a run *source* started is yours: the one run the cap and the streak pass over."""
    return source == YOU


def of_row(row: Any) -> str:
    """What started the run a history row records (``""`` for a row that does not say)."""
    getter = getattr(row, "get", None)
    return str((getter("source") if getter is not None else "") or "")


def of_request(request: Any) -> str:
    """Who asked for the run an HTTP *request* starts.

    :data:`YOU` for a signed-in session of yours (the dashboard, a ``personalclaw token`` link) and
    for your own command typed at a terminal on this computer, which names its work in the
    ``cli:`` kind (`session_keys.CLI`). Otherwise whoever proved it: an app's token is the app's,
    and a call made with the gateway's internal credential is the work its session names
    (`approval_answer.of_request`): an app's own (:data:`APP`), an automation's own
    (:data:`AUTOMATION`), a dispatch with no session (:data:`PROGRAM`), and every other an agent's
    (:data:`AGENT`). A request that proved none of these is a program's: never yours.
    """
    from personalclaw import approval_answer, session_keys

    by = approval_answer.of_request(request)
    if by == approval_answer.YOU:
        return YOU
    if by.kind == approval_answer.APP:
        return APP
    if by.kind != approval_answer.AGENT:
        return PROGRAM
    kind = session_keys.judged_kind(by.name)
    if kind is session_keys.CLI:
        return YOU
    if kind is session_keys.APP:
        return APP
    if kind is session_keys.TRIGGER:
        return AUTOMATION
    if kind in (session_keys.UNATTENDED, session_keys.TILE):
        return PROGRAM
    return AGENT
