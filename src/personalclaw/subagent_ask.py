"""The permission request a subagent's start asks with, and how a start it did not get reads.

A start asks the way a tool call asks, so every surface that shows an approval (the Inbox row and
its notification, the decision under it, the chat card, the phone, a channel's prompt) shows this
one the same way: named as the tool that starts it, ``subagent_run``, with a sentence saying what
allowing it does, the WHOLE task it would work on as its input, and the risk ``subagent_run``
carries. It starts work that can change things, so ``caution``, as a tool that declares no
read-only effect is.

The start used to ask as ``subagent_run(<task cut at 80 characters>)`` with no input and no risk,
so the person approving it read a prompt that stopped mid-word, on every surface that showed it.
"""

from __future__ import annotations

from personalclaw import approval_grants
from personalclaw.approval_grants import ToolDecision
from personalclaw.llm.base import EVENT_PERMISSION_REQUEST, LLMEvent
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.tool_providers.base import RiskLevel


def _redact(text: str) -> str:
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


def spawn_ask(request_id: str, task: str, agent: str = "") -> LLMEvent:
    """The ask for starting a subagent on ``task`` (as ``agent``, when one is named)."""
    named = _redact(agent or "")
    return LLMEvent(
        kind=EVENT_PERMISSION_REQUEST,
        request_id=request_id,
        title="subagent_run",
        tool_purpose=(
            f"Starts the “{named}” agent on this task."
            if named
            else "Starts a subagent on this task."
        ),
        tool_input=_redact(task or ""),
        risk_level=RiskLevel.CAUTION.value,
    )


#: How every sentence :func:`spawn_refusal` writes ends: the spawn never started.
_NEVER_STARTED = "so it never started"


def spawn_refusal(decision: ToolDecision) -> str:
    """Why a spawn that asked its owner never started, in the words its failure is read in: a
    workflow step's cause, the loop's Inbox note, the background-agents list. "spawn rejected" said
    it of every ending, and so read as the owner refusing work that nobody had answered."""
    if decision.outcome == "expired":
        minutes = round(approval_grants.approval_window_secs() / 60)
        return (
            f"spawn not approved in time: nobody answered within {minutes} minutes "
            f"(Settings → Agent defaults → Approval wait), {_NEVER_STARTED}"
        )
    if decision.outcome == "cancelled":
        return f"spawn not approved: its approval ended before anyone answered, {_NEVER_STARTED}"
    if decision.decided_by == "approval_failed":
        return f"spawn not approved: asking for the approval failed, {_NEVER_STARTED}"
    return f"spawn declined, {_NEVER_STARTED}"


def never_started(error: str) -> bool:
    """Whether *error* is :func:`spawn_refusal`'s: the spawn was not approved, so the subagent
    never ran a turn and left no transcript."""
    return str(error or "").endswith(_NEVER_STARTED)
