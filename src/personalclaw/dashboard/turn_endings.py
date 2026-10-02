"""The sentences a chat turn ends on when it ends without its answer — its agent or its owner ended
it, it ran past its time limit, its connection closed after it made calls, or it wrote nothing —
and which of those turns end in the error that says so (`unanswered_turn`,
`say_the_turn_has_no_answer`). And how a refused call is answered: what the model is told, what a
Deny will do (said before it is pressed), and the record of what a Deny answered the agent with.

Product copy the chat runner (`chat_runner.run_chat`) puts where the conversation is. An agent
CLI is named by its runtime (``acp:<cli>``), as the image-input sentence names it
(`providers.image_input.agent_label`); PersonalClaw's own runtime is "The agent".
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from personalclaw.acp.types import is_cancelled_stop
from personalclaw.llm.events import is_length_stop, is_refusal_stop, out_of_room_notice
from personalclaw.security import redact_credentials, redact_exfiltration_urls

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState, _ChatSession

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


def past_limit_notice(limit_secs: float, *, asked_by_person: bool) -> str:
    """Said when a turn that runs on its own ran past its time limit and was stopped
    (`turn_deadline`): what ended it, which no fault did and no one pressed. A turn an automation,
    a subagent's report or a loop's nudge started has no message of hers to send again; the chat's
    Retry runs it again."""
    minutes = int(limit_secs // 60)
    limit = f"{minutes}-minute" if minutes else f"{int(limit_secs)}-second"
    retry = "Send your message again to retry." if asked_by_person else "Retry it from the chat."
    return f"This turn ran past its {limit} limit and was stopped. {retry}"


def lost_after_steps_notice(agent: str, steps: int) -> str:
    """Said when the agent's connection closed or its process ended after the turn had made
    *steps* calls nobody refused: her message is not sent again on its own, since that could make
    those calls again. The notice carries Retry, which sends it again because she chose to."""
    who = agent.strip() or "the agent"
    made = f"{steps} step{'' if steps == 1 else 's'}"
    return (
        f"The connection to {who} closed after {made} of this turn, so your message was not "
        "sent again: that could repeat them. Send it again to retry."
    )


#: A completed turn that wrote nothing and ran nothing: resent once, silently, since nothing ran.
UNANSWERED_BLANK = "blank"
#: A completed turn that ran steps and wrote nothing. Never resent — that would run every step
#: again — so the turn ends in the error that says so (`no_answer_notice`), which the chat offers
#: Retry on, as on any notice a turn ends on.
UNANSWERED_AFTER_STEPS = "after_steps"
#: Wrote nothing, and its stop says why: its agent refused to go on, or its model ran out of room.
UNANSWERED_SAYS_WHY = "says_why"


def unanswered_turn(
    *,
    wrote_text: bool,
    stop_reason: str,
    saw_compaction: bool,
    needs_session_reset: bool,
    is_slash: bool,
    tool_call_count: int,
    is_loop: bool,
) -> str:
    """How a completed turn that wrote nothing at all is handled, or "" when it needs nothing.

    ``wrote_text`` is whether any of the turn's text was more than whitespace. Writing nothing is
    not a missing answer when the turn was a user cancel, a compaction / clear / agent-switch
    turn (each emits its own status line), a slash command, or a goal loop worker turn (loops own
    a dedicated deliverable-forcing re-prompt loop, so the chat's handling stands aside).
    Otherwise it is :data:`UNANSWERED_SAYS_WHY` when it refused to go on or ran out of room,
    :data:`UNANSWERED_BLANK` when the turn ran no tool either, and
    :data:`UNANSWERED_AFTER_STEPS` when it did. The second used to count as finished ("the agent
    did real work, just no closing prose"), so a turn of fifteen commands and no answer ended as
    "Response complete." with nothing on screen, in the transcript or in the log.
    """
    if wrote_text:
        return ""
    if (
        is_cancelled_stop(stop_reason)
        or saw_compaction
        or needs_session_reset
        or is_slash
        or is_loop
    ):
        return ""
    if is_refusal_stop(stop_reason) or is_length_stop(stop_reason):
        return UNANSWERED_SAYS_WHY
    return UNANSWERED_AFTER_STEPS if tool_call_count > 0 else UNANSWERED_BLANK


def no_answer_notice(steps: int, *, asked_by_person: bool) -> str:
    """What a turn that wrote nothing says where its answer should have been.

    Product copy, and the same line reaches a linked channel, the OpenAI-compatible endpoint and
    ``personalclaw run``, none of which has a Retry button, so it says how to retry in words.
    A turn an automation, a subagent's report or an auto-nudge started has no message of the
    person's to send again; the chat's Retry runs it again.
    """
    ran = "" if steps <= 0 else f" ran {steps} step{'' if steps == 1 else 's'} but"
    retry = "Send your message again to retry." if asked_by_person else "Retry it from the chat."
    return f"The agent{ran} did not write an answer. {retry}"


def say_the_turn_has_no_answer(state: "DashboardState", session: "_ChatSession", note: str) -> None:
    """End the turn in the error that says why it has no answer (*note*).

    An errored turn, so ``chat_done`` says the turn ended in an error rather than "Response
    complete.", the chat offers Retry on the notice, and a linked channel hears this sentence
    (`chat_runner.say_how_an_unanswered_turn_ended`).
    """
    session.append("error", note, "msg msg-err")
    state.broadcast_ws(
        "chat_message",
        {"session": session.key, "role": "error", "content": note},
    )
    session._last_turn_errored = True


def moved_turn_notice(to: str) -> str:
    """Said when she changed what answers the chat while it was answering (`running_turn`): the
    turn ended stopped and *to* is answering her message again. Not a fault, so not the cut-short
    notice, and not an error."""
    return f"Moved to {to.strip() or 'the new agent'} — it is answering your message."


def moved_after_answer_notice(to: str) -> str:
    """Said when the change landed after the turn had given its answer: the answer stands and
    *to* answers from her next message."""
    return f"Switched to {to.strip() or 'the new agent'}. It answers your next message."


def wrote_nothing(stop_reason: str, agent: str, output_cap: int) -> str:
    """Said when a turn wrote nothing and its stop says why: the agent refused to continue
    (``refusal``, :func:`refused_turn_notice`), or its model ran out of output room before it
    answered (a length stop, ``llm.events.out_of_room_notice``, naming the *output_cap* it ran
    into). Never resent: it would be asked the same thing again, and meet the same cap."""
    if is_length_stop(stop_reason):
        return out_of_room_notice(output_cap)
    return refused_turn_notice(agent)


def serving_agent_name(client: object) -> str:
    """A person's name for the agent CLI serving the turn, or "" for PersonalClaw's own runtime."""
    runtime = getattr(client, "provider_id", "")
    if not isinstance(runtime, str) or not runtime.startswith("acp:"):
        return ""
    from personalclaw.providers.image_input import agent_label

    return agent_label(runtime)


def _option_words(text: object) -> str:
    """An agent's option id or name as a row and an audit row carry it: masked and bounded."""
    return redact_credentials(redact_exfiltration_urls(str(text or ""))[0])[0].strip()[:160]


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
    name = _option_words(option.get("label") or option.get("id"))
    if not name:
        return {}
    return {"detail": f"Answered “{name}”", "answered": str(option.get("id") or "")}


def offered(options: object) -> dict[str, list[dict[str, str]]]:
    """The answers an agent CLI offered for a call (``{id, kind, name}`` each), for the audit row
    of the decision, so which refusal a Deny could send — and whether one let the agent go on — is
    on the record. ``{}`` for a runtime that offers none (PersonalClaw's own)."""
    if not isinstance(options, list) or not options:
        return {}
    return {
        "offered": [
            {
                "id": _option_words(o.get("id")),
                "kind": _option_words(o.get("kind")),
                "name": _option_words(o.get("label")),
            }
            for o in options
            if isinstance(o, dict)
        ]
    }


def deny_effect(client: object, request_id: object, agent: str) -> str:
    """What the approval card says a Deny does, when it does more than decline the call: the
    agent offered only refusals that end its turn (``deny_outcome``), so the Deny ends it, and the
    turn is then carried on without the call while it may be (`acp.session`). ``""`` when a Deny
    declines the call and the agent goes on, which is what Deny means and needs no words."""
    read = getattr(client, "deny_outcome", None)
    if not callable(read) or inspect.iscoroutinefunction(read):
        return ""
    outcome = read(request_id)
    if outcome not in ("carries_on", "ends"):
        return ""
    who = _sentence_case(agent)
    said = f"{who} offers no way to skip only this step: Deny ends its turn"
    if outcome == "ends":
        return f"{said}."
    return f"{said}, and PersonalClaw then asks it to carry on without it."


def carried_on_notice(agent: str, steps: str, *, after_your_deny: bool) -> str:
    """Said where the conversation is when a refusal of *steps* ended the agent's turn and
    PersonalClaw asked it to carry on without them: what it does next is still this turn."""
    who = _sentence_case(agent)
    what = steps.strip() or "a step"
    why = f"when you denied {what}" if after_your_deny else f"when {what} was refused"
    return f"{who} ended its turn {why}, so PersonalClaw asked it to carry on without it."
