"""How a subagent's time-limit stop reads: the limit that ran out, and the ask for its owner's
answer it ended, when it ended one.

The stop is a sentence a person reads (the background agents list, a workflow step's ending), and
the readers that tell one kind of stop from another (a workflow step's settlement) read it through
:func:`ran_out_of_time` and :func:`ended_a_wait_for_the_owner` alone, so the words and the readers
live together. ``SubagentManager`` words its run's own stop (:func:`time_limit_stop`) and its
reaper's kill past the limit (:data:`REAPED`, :func:`waiting_note`).
"""

from __future__ import annotations

from personalclaw.auth.lifetimes import duration_words
from personalclaw.security import redact_credentials, redact_exfiltration_urls

#: How a time-limit stop the run itself reached opens (:func:`time_limit_stop`).
_TIME_LIMIT = "Its time limit of "
#: How the reaper's own kill of an agent past its limit opens (``SubagentManager._force_reap``).
REAPED = "Reaped after"
#: The words a time-limit stop ends with when it ended a wait for the owner's answer
#: (:func:`waiting_note`), which :func:`ended_a_wait_for_the_owner` reads.
_FOR_YOUR_ANSWER = " for your answer"


def waiting_note(asking: tuple[str, float] | None, now: float) -> str:
    """The clause a time-limit stop adds when the agent was waiting for its owner to answer a
    call: which call, and how long THAT ask had been open (*asking* is the call and when it was
    asked, on the ``time.monotonic`` clock *now* reads).

    The limit counts the agent's whole run, so naming only the limit blamed the owner for every
    minute the agent spent working before it asked: a step that worked eighteen minutes and then
    waited twelve read as thirty minutes of waiting for her."""
    if not asking:
        return ""
    tool, asked_at = asking
    tool, _ = redact_exfiltration_urls(tool)
    tool, _ = redact_credentials(tool)
    waited = duration_words(max(0.0, now - asked_at))
    return f" while its {tool[:80]} call had been waiting {waited}{_FOR_YOUR_ANSWER}"


def time_limit_stop(limit_secs: float, asking: tuple[str, float] | None, now: float) -> str:
    """The error an agent ends with when its time limit runs out: the limit, and the ask it ended
    when it ended one (:func:`waiting_note`). Worded to read whole on its own (the background
    agents list) and inside a step's ending ("“check” stopped: its time limit of …")."""
    return f"{_TIME_LIMIT}{duration_words(limit_secs)} ran out{waiting_note(asking, now)}"


def ran_out_of_time(error: str) -> bool:
    """Whether *error* is a time-limit stop: the run's own limit (:func:`time_limit_stop`) or the
    reaper's kill past it."""
    return str(error or "").startswith((_TIME_LIMIT, REAPED))


def ended_a_wait_for_the_owner(error: str) -> bool:
    """Whether *error* is a time-limit stop that ended a wait for the owner's answer. What a reader
    of how the agent ended (a workflow step's settlement) tells apart from a step that ran out of
    time working, because the remedy differs: answer the ask next time, not raise the limit."""
    return ran_out_of_time(error) and str(error).endswith(_FOR_YOUR_ANSWER)
