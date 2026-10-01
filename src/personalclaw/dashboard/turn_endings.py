"""The sentences a chat turn ends on when its agent, not a fault, ended it — and the record of
what a Deny answered the agent with.

Product copy the chat runner (`chat_runner.run_chat`) puts where the conversation is. An agent
CLI is named by its runtime (``acp:<cli>``), as the image-input sentence names it
(`providers.image_input.agent_label`); PersonalClaw's own runtime is "The agent".
"""

from __future__ import annotations

import inspect

from personalclaw.security import redact_credentials, redact_exfiltration_urls


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
