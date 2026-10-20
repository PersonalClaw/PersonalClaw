"""How a helper's ending reaches the chat that asked for it: a turn of its own, or told its agent.

A helper's report starts a turn where its work was asked for (``gateway._subagent_done``), once the
turn that asked for it has ended: the agent reads what the helper found and carries on the work it
asked for. That holds for a helper that ran, whether it finished or failed (its agent says why).

A helper that never ran has no work to carry on: its start was declined, nobody allowed it in time,
the product refused it, or it was stopped before it started (``SubagentInfo.never_ran``). Neither
has one she stopped herself (``SubagentInfo.stopped_by_you``). A turn started with such an ending
told the agent only that its work was not done, and the agent took the work up again by itself with
nobody asking: once she declined a helper's start, her chat went back to work as soon as its turn
ended. So such an ending starts no turn (:func:`starts_a_turn`). Its chat's agent is told it instead
(:func:`owe`): with the next turn that runs in that chat, ahead of its message (``chat_runner``), or
at once by a ``wait`` under way there, whose check-in takes it (:func:`take_for`,
``POST /api/session-keepalive``).

What ended the work that started a helper ended the helper too (``SubagentInfo.starter_ended``: the
turn's Stop, the end of a loop or of a run). Its report starts no turn either, and it is told to
nobody, since what she ended is over (``started_work``).

A report that could not be handed to its chat (``SubagentManager.notify_injection_failed``) is owed
to the chat's agent the same way.

Every ending also reaches the chat's Subagents list live, as one ``subagent_done`` event
(:func:`done_event`), a helper that never ran among them: its report starts no turn, so its card is
where the chat shows how it ended.
"""

from __future__ import annotations

from typing import Any

from personalclaw.security import redact_credentials, redact_exfiltration_urls

#: The ``_ChatSession`` attribute holding what the chat's agent is owed, oldest first.
OWED = "_owed_subagent_endings"
#: The longest result a ``subagent_done`` event carries (its last part), so it does not bloat it.
DONE_RESULT_CAP = 50_000


def _masked(text: str) -> str:
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text


def done_event(info: Any) -> dict[str, Any]:
    """The ``subagent_done`` event of the helper *info*, masked, as its chat's Subagents list shows
    it. One that ran says how long it took, what it returned and what it cost (the figures its
    completion carried); one that never ran says so (``never_ran``, and ``declined`` for her Deny),
    and the list shows it beside the helpers that ran."""
    event: dict[str, Any] = {
        "error": _masked(info.error) if info.error else None,
        "task": _masked(info.task),
        "agent": _masked(info.agent),
    }
    if info.never_ran:
        return {**event, "never_ran": True, "declined": info.declined}
    result = _masked(info.result) if info.result else ""
    if len(result) > DONE_RESULT_CAP:
        result = "…(truncated)\n" + result[-DONE_RESULT_CAP:]
    return {
        **event,
        "elapsed": info.elapsed,
        "result": result,
        "cost_usd": round(info.cost_usd, 6),
        "tokens": info.input_tokens + info.output_tokens,
    }


def ended_with_its_starter(member: Any) -> bool:
    """Whether the work that started *member* ended, and ended it too (``starter_ended``)."""
    why = getattr(member, "starter_ended", "")
    return isinstance(why, str) and bool(why)


def told_instead(member: Any) -> bool:
    """Whether *member*'s ending is told to its chat's agent rather than handed to a turn: it never
    ran, or she stopped it, and the work that started it did not end."""
    return not ended_with_its_starter(member) and (
        getattr(member, "never_ran", False) is True
        or getattr(member, "stopped_by_you", False) is True
    )


def starts_a_turn(member: Any) -> bool:
    """Whether *member*'s report starts a turn where its work was asked for: work that ran, that
    she did not stop, and whose starter did not end."""
    return not (told_instead(member) or ended_with_its_starter(member))


def note(member: Any, *, task: str, reason: str) -> str:
    """What its chat's agent is told of *member*'s ending, under the completion event's header: the
    helper, how it ended, its task, why, and that no report of it will come. *task* and *reason*
    come masked, as every text a model is handed of a helper does."""
    who = f"Agent `{member.id}`" + (f" ({member.agent})" if member.agent else "")
    if getattr(member, "stopped_by_you", False) is True:
        how = "was stopped by the user"
        after = (
            "The user stopped it, so no report of it will come: do not start it again unless "
            "they ask."
        )
    elif getattr(member, "declined", False) is True:
        how = "declined"
        after = (
            "It never ran, so no report of it will come. The user declined it: do not start it "
            "again unless they ask."
        )
    else:
        how = "did not start"
        after = "It never ran, so no report of it will come."
    return f"{who} {how}\nTask: {task}\n\n{reason}\n{after}"


def owe(chat: Any, notes: list[str]) -> None:
    """Owe *notes* to the agent of the chat session *chat*: the next turn that runs there takes
    them, or a ``wait`` under way there does."""
    owed = getattr(chat, OWED, None)
    if isinstance(owed, list):
        owed.extend(n for n in notes if n)


def take(chat: Any) -> list[str]:
    """What the agent of the chat session *chat* is owed, taken from it: the caller hands it on."""
    owed = getattr(chat, OWED, None)
    if not isinstance(owed, list) or not owed:
        return []
    taken = owed[:]
    owed.clear()
    return taken


def take_for(state: Any, session_key: str) -> list[str]:
    """What the agent of the chat *session_key* is owed, taken (:func:`take`); nothing for a
    session that is no chat, or a chat the gateway no longer holds."""
    from personalclaw import session_keys

    if not session_keys.DASHBOARD.names(session_key):
        return []
    get_session = getattr(state, "get_session", None)
    if not callable(get_session):
        return []
    chat = get_session(session_key.removeprefix(session_keys.DASHBOARD.prefix))
    return take(chat) if chat is not None else []
