"""Which Inbox items a call reads: its own chat's, those about no chat, and every one for you.

The Inbox holds what reached you. Most of it is about no chat: a message from your channels or your
mail, a proposal, a notice, a note you wrote, what a run of yours waits on, and an agent's post to
you (``post_to_inbox``, which the work of a Temporary or Incognito chat never makes:
``lasting_work``). Every agent reads those: a scheduled briefing reads what is waiting
(``inbox_list``).

An item raised for a chat's work is that chat's own: the approval its agent or one of its subagents
waits on, a question its agent put to you, the note one of its calls left when nobody answered, a
proposal drawn from it, and what one of its own runs waits on. It is made of what was said there (a
batch's request names each task in that chat's words), so it is read as the chat's subagents are
(``subagent_reach``) and its own runs are (``workflows.chat_runs``):

* **An item is about the work it names:** the session it was raised for (``refs.session``, and the
  chat ``refs.chat`` names), and the run it is about (``refs.workflow``, ``refs.loop``).
* **A session's item is the chat's at the top of that work** (``subagent_reach.Reader.reads``): a
  subagent's is its chat's, and a workflow step's the chat that started its run. The Inbox names a
  dashboard chat by its bare name, which is the chat its ``dashboard:`` key names.
* **A run's item is the chat's whose own run it is** (``chat_runs.reads_id``): a batch, or a run a
  Temporary or Incognito chat started. An item about a run of yours is about no chat.
* **You read every item:** your Inbox page, and a tool you run from your own pages.
* **Any other caller reads its own chat's items and those about no chat.** Another chat's item is
  not there for it: not listed, and not counted.
* **An item whose chat cannot be told is yours alone.**

Every read of the Inbox for anyone but you asks :func:`reads`: ``inbox_list``, wherever it runs (a
chat's agent, its subagents and workflow steps, a scheduled run, and ``POST /api/tools/invoke``,
which a scheduled script, an app and an agent CLI's tool server reach), and the Morning triage
digest (``proactive.collect``), which is no chat's work: a chat's item reaches neither its model
nor its run's record, which is yours and which every agent's workflow tools read.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, TypeVar

from personalclaw import session_keys

if TYPE_CHECKING:
    from personalclaw.subagent_reach import Reader

logger = logging.getLogger(__name__)

#: The refs an item names the session it was raised for under.
_SESSION_REFS = ("session", "chat")
#: The refs an item names the run it is about under: a workflow run's id, or a loop's (a loop that
#: is no workflow run names no run, and is yours).
_RUN_REFS = ("workflow", "loop")

_Item = TypeVar("_Item")


def _named(refs: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    """The values *refs* names under *keys*. One that is there and is no name says nothing of
    whose the item is, so it raises."""
    named = []
    for key in keys:
        value = refs.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise ValueError(f"refs.{key} is a {type(value).__name__}, not a name")
        named.append(value.strip())
    return [value for value in named if value]


def _spellings(session: str) -> tuple[str, ...]:
    """The keys the work *session* names is known by: a dashboard chat by its bare name, which the
    Inbox records, and by its ``dashboard:`` key, which its agent's tools name it by; any other work
    by its one key. None for your own pages, which are no chat."""
    if session == session_keys.DASHBOARD_UI:
        return ()
    bare = session.removeprefix(session_keys.DASHBOARD.prefix)
    if ":" in bare:
        return (session,)
    return (bare, session_keys.DASHBOARD.key(bare))


def reads(reader: Reader, item: Any) -> bool:
    """Whether *reader* reads the Inbox item *item*: every one for you; for anyone else, one about
    no chat and one of its own chat's. One whose chat cannot be told is read by you alone.

    Walks to the chat at the top of the item's work (``subagent_reach.Reader.reads``), which reads
    its records, so call it off the event loop."""
    if reader.everyone:
        return True
    try:
        refs = getattr(item, "refs", None) or {}
        if not isinstance(refs, dict):
            raise ValueError(f"refs is a {type(refs).__name__}")
        for session in _named(refs, _SESSION_REFS):
            spellings = _spellings(session)
            if spellings and not any(reader.reads(key) for key in spellings):
                return False
        from personalclaw.workflows import chat_runs

        return all(chat_runs.reads_id(reader, run_id) for run_id in _named(refs, _RUN_REFS))
    except Exception:  # noqa: BLE001 - security surface: an item no one can place is yours (closed)
        logger.warning(
            "could not tell whose chat the Inbox item %s is",
            getattr(item, "id", "?"),
            exc_info=True,
        )
        return False


def readable(reader: Reader, items: Iterable[_Item]) -> list[_Item]:
    """The items of *items* that *reader* reads (:func:`reads`), in their order."""
    return [item for item in items if reads(reader, item)]
