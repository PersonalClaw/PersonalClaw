"""The chat's Trust, for a conversation a chat channel runs itself.

A channel app either hands the owner's message to PersonalClaw, which runs the turn in a chat of
its own (``GatewayServices.deliver_channel_inbound``), or runs the turn itself, in a conversation
it keeps, as Slack does with its threads. For the first, a call that needs approval is core's to
ask, and the Allow for this chat its prompt offers trusts that chat
(``DashboardApprovalState.answer_on_channel``). For the second, the channel asks on a prompt of
its own, and a trust given there used to stay inside the app: the chat the dashboard shows for the
conversation neither said so nor could switch it off, and the security log had no row for it.

So such a channel keeps no trust of its own. The trust is the Trust of PersonalClaw's chat for the
conversation, the chat the dashboard lists it as under its channel: the Trust its approval card,
its Permission mode and every other channel's Allow for this chat set, and the one the owner
switches off there.

* ``approval_brief_for(event, chat=<conversation>)`` (``approval_brief``) offers on the prompt what
  the chat's card offers for the call, Allow for this chat among them where the card would
  (``channel_delivery.chat_answers``), and only where PersonalClaw can hold that Trust in a chat
  the owner sees (:func:`can_trust`).
* :func:`answer_in_chat` takes the answer pressed there: Allow for this chat trusts the chat,
  as the card's This chat does, and writes the audit row another channel's Allow for this chat
  writes.
* :func:`chat_grant` says, at each call, which of the chat's grants answers it without asking
  (YOLO, its Trust, Trust reads), held to the rules the chat's own runner holds them to.

This is core code below the HTTP surface, so the chat is reached through the gateway's dashboard
state (``inbox_providers.native_source``). A gateway with no dashboard has no chat to show a trust
in: nothing standing is offered, and every call is asked.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _state() -> Any:
    from personalclaw.inbox_providers.native_source import get_dashboard_state

    return get_dashboard_state()


def can_trust(session_key: str) -> bool:
    """Whether the Trust of the conversation *session_key* can be held in a chat the owner sees:
    a dashboard runs, and lists the conversation's chat under the channel that runs it (a
    conversation the channel linked to one of its threads, that the owner started and that keeps
    its transcript: an Incognito or Temporary chat is never listed)."""
    state = _state()
    return bool(session_key) and state is not None and state.holds_channel_chat(session_key)


def answer_in_chat(session_key: str, answer: str, *, channel: str, request_id: str = "") -> bool:
    """Take the answer the owner pressed on a channel's own approval prompt, asked in the
    conversation *session_key* that the channel runs itself, as the chat's card takes it.

    *answer* is the pressed answer's ``key``, one the prompt's brief offered
    (``approval_brief_for(event, chat=session_key)``). Allow for this chat trusts that chat: its
    Permission mode shows Trust, its next calls run without asking (:func:`chat_grant`), the owner
    switches it off there, and the security log records it as decided by the owner on *channel*.
    An answer for this call alone remembers nothing beyond it. *request_id* is the call it was
    given on, for the audit row.

    Returns whether the answer was taken. False for an answer no prompt offers, and for a Trust
    PersonalClaw cannot hold (no dashboard, a conversation it does not list under a channel, such
    as a chat of yours no channel runs, the operator ceiling):
    the press then decides nothing, and the call is the channel's to keep asking about. The call
    itself is the channel's to run or refuse either way.
    """
    from personalclaw import approval_answer
    from personalclaw.channel_delivery import ALLOW_FOR_THIS_CHAT, ONE_CALL_ANSWERS

    if answer in {one.key for one in ONE_CALL_ANSWERS}:
        return True
    if answer != ALLOW_FOR_THIS_CHAT.key:
        logger.warning("%s answered %r, which no prompt offers; nothing was taken", channel, answer)
        return False
    state = _state()
    if state is None or not session_key:
        return False
    return bool(
        state.trust_channel_chat(
            session_key, by=approval_answer.on_channel(channel), request_id=request_id
        )
    )


def chat_grant(session_key: str, event: Any) -> str:
    """The grant that approves *event*, a call the channel's own turn asks about in the
    conversation *session_key*, without asking anyone: ``yolo`` while YOLO is on, ``trust`` while
    the conversation's chat is trusted, ``trust_reads`` for a read-only shell command while it
    trusts reads, or ``""`` when none does and the call is asked.

    What the chat's own runner decides for one of its calls, by the same rules: a conversation an
    app started approves nothing on its own; no grant answers a call that reaches a host off the
    allowed hosts, and the operator ceiling bounds each one (``approval_grants.stands_for_call``,
    which audits a refusal). Read at every call, so the owner switching the chat's Trust off in the
    dashboard makes the next call ask.
    """
    from personalclaw import approval_grants, trust_mode
    from personalclaw.task_modes import resolve_effective_risk

    state = _state()
    posture = state.channel_chat_posture(session_key) if state is not None and session_key else ""
    if posture is None:
        return ""
    yolo = trust_mode.is_yolo_active()
    if (
        posture == approval_grants.TRUST_READS
        and not yolo
        and resolve_effective_risk(
            getattr(event, "risk_level", "") or "",
            str(getattr(event, "title", "") or ""),
            str(getattr(event, "tool_kind", "") or ""),
            getattr(event, "tool_input", ""),
        )
        == "safe"
    ):
        grant = approval_grants.TRUST_READS
    elif yolo or posture == approval_grants.TRUST:
        grant = approval_grants.YOLO if yolo else approval_grants.TRUST
    else:
        return ""
    if approval_grants.stands_for_call(grant, session_key=session_key, event=event):
        return grant
    return ""
