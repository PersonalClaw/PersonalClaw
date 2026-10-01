"""The sentences a chat turn ends on when its agent or its owner, not a fault, ended it — and how a
refused call is answered: what the model is told, and the record of what a Deny answered the agent
with.

Product copy the chat runner (`chat_runner.run_chat`) puts where the conversation is. An agent
CLI is named by its runtime (``acp:<cli>``), as the image-input sentence names it
(`providers.image_input.agent_label`); PersonalClaw's own runtime is "The agent".
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any

from personalclaw.security import redact_credentials, redact_exfiltration_urls

#: What the model is told of an approval that ended with no answer: nobody declined it.
UNANSWERED = {"expired": "no one answered in time", "cancelled": "the turn was stopped"}
#: ...of a call refused because the run is unattended, and because its pre-tool hook failed.
UNATTENDED = "the run is unattended: no one to approve"
HOOK_FAILED = "its pre-tool hook failed to run"


async def refuse(
    client: Any, request_id: object, ended_as: str = "rejected", *, why: str = "", kind: str = ""
) -> None:
    """Refuse a pending call without running it. A refusal nobody answered (*why*: a screen, of
    ``security.DENY_KIND_*`` *kind*) and an approval that ended unanswered (*ended_as*
    ``expired``/``cancelled``) are told to the model as what they are, never as her decline, by a
    runtime that carries a reason (``AgentProvider.carries_refusal_reasons``); any other runtime's
    answer is a reject, in its own words."""
    if not why and ended_as in UNANSWERED:
        why, kind = UNANSWERED[ended_as], "user"
    if why and getattr(client, "carries_refusal_reasons", False) is True:
        await client.refuse_tool(request_id, why, kind=kind or "policy")
    else:
        await client.reject_tool(request_id)


def blocked_reason(hook_results: Iterable[str]) -> str:
    """Why a pre-tool hook blocked a call: the text of its ``BLOCKED:`` line, else "policy hook"."""
    line = next((r for r in hook_results if r.startswith("BLOCKED:")), "")
    return line.removeprefix("BLOCKED:").strip() or "policy hook"


def _sentence_case(agent: str) -> str:
    who = agent.strip() or "The agent"
    return f"{who[:1].upper()}{who[1:]}"


def stopped_after_deny_notice(agent: str, tool: str) -> str:
    """Said when the agent ended the turn without an answer after she denied one of its calls.
    Her Deny ended it — no fault did — so it is not the cut-short notice or "did not write an
    answer", and the turn is never resent: that would ask her again."""
    return f"{_sentence_case(agent)} stopped after you denied {tool.strip() or 'a step'}."


def refused_turn_notice(agent: str) -> str:
    """Said when the agent refused to continue the turn (``stopReason: refusal``) and wrote
    nothing. Never resent either: it would be asked the same thing again."""
    return f"{_sentence_case(agent)} refused to continue and wrote no answer."


def moved_turn_notice(to: str) -> str:
    """Said when she changed what answers the chat while it was answering (`running_turn`): the
    turn ended stopped and *to* is answering her message again. Not a fault, so not the cut-short
    notice, and not an error."""
    return f"Moved to {to.strip() or 'the new agent'} — it is answering your message."


def moved_after_answer_notice(to: str) -> str:
    """Said when the change landed after the turn had given its answer: the answer stands and
    *to* answers from her next message."""
    return f"Switched to {to.strip() or 'the new agent'}. It answers your next message."


def serving_agent_name(client: object) -> str:
    """A person's name for the agent CLI serving the turn, or "" for PersonalClaw's own runtime."""
    runtime = getattr(client, "provider_id", "")
    if not isinstance(runtime, str) or not runtime.startswith("acp:"):
        return ""
    from personalclaw.providers.image_input import agent_label

    return agent_label(runtime)


def refusal_answered(client: object, request_id: object) -> dict[str, str]:
    """What PersonalClaw answered the agent with when it refused a call: the agent's own option,
    read off the session that sent it (``refusal_answer``), as ``{"detail", "answered"}`` for the
    refused step's row and its audit row. ``{}`` when no option was sent (a cancelled turn's
    ``cancelled``) or the runtime keeps no such record."""
    read = getattr(client, "refusal_answer", None)
    if not callable(read) or inspect.iscoroutinefunction(read):
        return {}
    option = read(request_id)
    if not isinstance(option, dict):
        return {}
    name = str(option.get("label") or option.get("id") or "")
    name = redact_credentials(redact_exfiltration_urls(name)[0])[0].strip()[:160]
    if not name:
        return {}
    return {"detail": f"Answered “{name}”", "answered": str(option.get("id") or "")}
