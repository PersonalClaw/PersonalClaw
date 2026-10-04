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
* :func:`chat_grant` says, at each call, who answers it without asking, by the decision the chat's
  own runner makes for a call put to its gate: an operator's hook pattern, what the call declares,
  the chat's Trust, Trust reads and YOLO, each grant held to the allowed hosts and the operator
  ceiling. The channel approves no call on an answer of its own: one this does not answer is
  asked. Before either, the channel refuses a call the deny-list refuses, as the chat's runner
  does, through the same screen (``acp.permission_authority.screen_tool_call``): that call is
  never approved and never asked about.

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
    """Who approves *event*, a call the channel's own turn asks about in the conversation
    *session_key*, without asking anyone; ``""`` when nobody does and the call is asked.

    The decision PersonalClaw's own chat makes for a call put to its gate, in its order:

    * ``hook_pattern``: an operator's pattern in the hook settings (``hooks.auto_approve_tools``)
      names the call. A grant at the "a hook decides" level, which a ``hook_based`` ceiling lets
      stand and an ``ask`` one does not.
    * ``declared_read`` or ``work_asks``: what the call's tool declares, that it only reads, or
      that its work asks the owner itself (the call that starts a subagent, whose start is asked,
      or started by the spawn setting under the ceiling where the start is decided). Neither is a
      grant (``approval_grants.declared_answer``).
    * ``trust_reads``, ``trust`` or ``yolo``: the chat's Trust reads, for a read-only shell
      command, its Trust, and YOLO. None of them for a conversation an app started, which
      approves nothing on its own, nor in a turn someone other than the owner asked for (the
      sender the channel names for it, ``memory_writes.turn_asked_by``): they are hers, for what
      she asks, so that call is asked, its prompt naming who asked.

    Each grant is held to the chat runner's rules (``approval_grants.stands_for_call``, which
    audits a refusal): none answers a call that reaches a host off the allowed hosts, none of hers
    answers a call someone else asked for, and the operator ceiling bounds every one. Nothing
    answers a call the hook chain refuses, read on the command that would run as well as on the
    call's title, which need not carry it; the channel
    refuses that call before it asks this (``screen_tool_call``), so ``""`` here means asked. The
    settings and the chat are read at every call, so a pattern the owner removes, or the chat's
    Trust switched off in the dashboard, makes the next call ask.
    """
    from personalclaw import approval_grants, trust_mode
    from personalclaw.hooks import TOOL_AUTO_APPROVE, TOOL_DENY
    from personalclaw.task_modes import resolve_effective_risk

    verdict = _hook_verdict(event)
    if verdict == TOOL_DENY:
        return ""
    if verdict == TOOL_AUTO_APPROVE and approval_grants.stands_for_call(
        approval_grants.HOOK_PATTERN,
        session_key=session_key,
        event=event,
        level=approval_grants.LEVEL_HOOK,
    ):
        return approval_grants.HOOK_PATTERN
    declared = approval_grants.declared_answer(event)
    if declared:
        return declared
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


def _hook_verdict(event: Any) -> str:
    """The hook chain's verdict on *event* (a ``hooks`` action), from the hook settings as they
    read now, made on the command that would run as well as on its title
    (``acp.permission_authority.screen_tool_call``, the chat runner's own reading). A chain that
    cannot be read is a refusal here: no grant answers a call that may be one it refuses, so the
    call is asked."""
    from personalclaw.acp.permission_authority import screen_tool_call
    from personalclaw.hooks import TOOL_DENY

    try:
        verdict = screen_tool_call(
            None, str(getattr(event, "title", "") or ""), getattr(event, "tool_input", "")
        )
    except Exception:  # noqa: BLE001 - see the docstring: an unread chain approves nothing
        logger.warning("could not read the hook chain for a channel's call; asking", exc_info=True)
        return TOOL_DENY
    return str(verdict.action)
