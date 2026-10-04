"""A helper's report on its way to the chat that asked for it: whole, kept, and handed on as text
from outside.

A helper (a subagent) reads pages, files, mail and command output its owner never saw, and its
report is made of what it read. So the report is text from outside: a model is handed it only
through the one door such text takes into a prompt (``outside_text.admit``), read by the injection
screen and fenced as data with the helper as its source, so nothing in it speaks for the chat or
its owner. A report the screen refuses is handed on as the door's sentence and never as its words:
to its chat, through ``subagent_status``, and in the person's note alike.

A report longer than :data:`REPORT_CAP` characters is projected the way a large tool result is
(``projection.project_and_retain``): its chat is shown the part its kind of text keeps, and the
whole report is retained in the chat's tool-result store under the handle ``tool_result_get``
reads. Every report is also kept whole in the chat's own workspace (:func:`keep`), where
``subagent_status`` reads it once the gateway has let the helper go, after a restart too.

Kept where a chat keeps its large tool results, and as long: in the chat's workspace on this
machine (a backup holds it, a sync never carries it), until the chat is deleted or its workspace
has gone a week unused (``context_management.SESSION_MAX_AGE_SECS``).
"""

from __future__ import annotations

import logging

from personalclaw.outside_text import Admitted, admit, withheld

logger = logging.getLogger(__name__)

#: The longest report a chat is shown whole, in characters; a longer one is projected.
REPORT_CAP = 3000


def handed_on(agent_id: str, text: str) -> Admitted:
    """*text*, of the report of the helper *agent_id*, as a model may be handed it: through the
    door every text from outside takes into a prompt, with the helper as its source."""
    return admit(
        text,
        source=f"subagent:{agent_id}",
        source_type="subagent",
        source_id=agent_id,
        transformation_path="report",
    )


def _withheld(agent_id: str, refused: tuple[str, ...]) -> str:
    return withheld(f"The report of agent {agent_id}", refused)


def for_a_model(agent_id: str, report: str) -> str:
    """The whole *report* of the helper *agent_id* as a model is handed it (``subagent_status``):
    fenced with the helper as its source, or the door's sentence when the screen refuses it."""
    admitted = handed_on(agent_id, report)
    return _withheld(agent_id, admitted.refused) if admitted.refused else admitted.text


def for_a_person(agent_id: str, report: str) -> str:
    """*report* as the person's note about the run says it: whole, as the helper wrote it, or the
    door's sentence when the screen refuses it, so a note that travels on (to a channel, say)
    never carries words no model may be handed."""
    refused = handed_on(agent_id, report).refused
    return _withheld(agent_id, refused) if refused else report


def for_its_chat(agent_id: str, report: str, chat_key: str) -> str:
    """What the chat *chat_key* is handed of *report*, the report of the helper *agent_id*, which
    is kept there first (:func:`keep`).

    A report the screen refuses is the door's sentence. One of up to :data:`REPORT_CAP` characters
    is handed whole, fenced. A longer one is projected and retained as a large tool result is: its
    projection is fenced, and after the fence, in PersonalClaw's own words, come the handles that
    read all of it, the projection's ``tool_result_get`` and ``subagent_status``.
    """
    kept_it = keep(chat_key, agent_id, report)
    whole = handed_on(agent_id, report)
    if whole.refused:
        return _withheld(agent_id, whole.refused)
    if len(report) <= REPORT_CAP:
        return whole.text
    from personalclaw.tool_providers.projection import RETAINED_NOTE_START, project_and_retain

    shown, retained = project_and_retain(report, session_key=chat_key, cap=REPORT_CAP)
    handles: list[str] = []
    preview, start, note = shown.rpartition(RETAINED_NOTE_START)
    if retained.get("raw_ref") and start:
        shown = preview
        handles.append((start + note).strip("\n"))
    fenced = handed_on(agent_id, shown)
    if fenced.refused:
        return _withheld(agent_id, fenced.refused)
    if kept_it and agent_id.isalnum():
        handles.append(
            f'[the whole report is kept: subagent_status(agent_id="{agent_id}") returns all '
            f"{len(report):,} characters of it]"
        )
    return fenced.text + ("\n\n" + "\n".join(handles) if handles else "")


def keep(chat_key: str, agent_id: str, report: str) -> bool:
    """Keep *report*, the whole report of the helper *agent_id*, in the workspace of the chat
    *chat_key* it is handed to, where :func:`kept` reads it. Whether it was kept: a report that
    cannot be (an id no workspace takes, a full disk) is still handed on, and ``subagent_status``
    then has nothing to read once the gateway has let the helper go."""
    if not chat_key:
        return False
    from personalclaw import session_workspace

    try:
        session_workspace.write_result(chat_key, agent_id, report)
    except ValueError:
        logger.debug("no workspace takes the report of agent %r in %r", agent_id, chat_key)
        return False
    except OSError:
        logger.warning("could not keep the report of agent %s in %s", agent_id, chat_key)
        return False
    return True


def kept(chat_key: str, agent_id: str) -> str:
    """The report of the helper *agent_id* kept in the chat *chat_key*, or ``""`` when that chat
    keeps none: a chat reads only the reports handed to it."""
    if not chat_key:
        return ""
    from personalclaw import session_workspace

    try:
        return session_workspace.read_result(chat_key, agent_id)
    except (OSError, ValueError):
        return ""
