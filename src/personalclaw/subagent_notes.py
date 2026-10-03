"""What a person is told when background agent work ends: a note named by the run, saying what
happened, and a failure told once.

The parent's model is handed the completion event (`gateway._subagent_done`): "[Subagent completion
event]", the agent's id, its task and what it said, or why it failed. That is the model's input, and
it was the note too, titled "Subagent `<id>` failed": an id a person never saw anywhere else, over a
body that reached the reason last. A note is now named by its run, the title it was given (its
trigger's name, say) or else the first line of its task (`triggers.store.run_title`), and its body
is what happened: for one run what it said or why it failed, for several each run's name with its
own.

**A failure is told once** (:class:`FailureNotes`). The same failure again, the same run and the
same reason as `triggers.delivery.failure_hash` reads it, within
`triggers.delivery.FAILURE_REMINDER_SECS` of the note that told it, is not told again: the window
an automation's "Collapse repeat failures" re-alerts on, so one still failing an hour on is told
again, and a run that ended well in between makes the next failure news. An automation's own runs
follow their trigger's setting instead: told on its route (`gateway._report_to_its_trigger`), or
not at all when that route is ``none``, they never reach this note, alone or in a batch.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

#: What a run is called when nothing names it: no title, no task, no agent.
_UNNAMED = "Background agent"


def ended(info: Any) -> str:
    """How a run ended, in one word: ``declined`` (its owner's Deny, which is not a failure),
    ``failed`` or ``completed``."""
    if getattr(info, "declined", False) is True:
        return "declined"
    return "failed" if getattr(info, "error", "") else "completed"


def run_name(info: Any) -> str:
    """What a run is called: its title, else the first line of its task, else its agent's name."""
    from personalclaw.triggers.store import run_title

    title = str(getattr(info, "title", "") or "").strip()
    if title:
        return title
    return (
        run_title("", str(getattr(info, "task", "") or ""))
        or str(getattr(info, "agent", "") or "").strip()
        or _UNNAMED
    )


def _named(info: Any) -> str:
    """The run's name with how it ended, when it did not simply complete."""
    how = ended(info)
    return run_name(info) if how == "completed" else f"{run_name(info)} — {how}"


def note_for(batch: Sequence[Any], details: dict[str, str]) -> tuple[str, str]:
    """``(title, body)`` of the note a person reads about *batch*, the runs that ended together.

    *details* is what each run said, or why it failed, by its id: the text the completion event
    carries for it. One run's note is titled by the run and IS that text; several runs' note says
    how many finished and how many failed, and lists each by name with its own.
    """
    if len(batch) == 1:
        (info,) = batch
        return _named(info), details[info.id]
    failed = sum(1 for info in batch if ended(info) == "failed")
    title = f"{len(batch)} agents finished" + (f" — {failed} failed" if failed else "")
    body = "\n\n".join(f"{_named(info)}\n{details[info.id]}" for info in batch)
    return title, body


class FailureNotes:
    """The failures a person was told about, so that the same failure is told once (see the
    module's docstring). One per gateway: what it holds is what this gateway's notes said."""

    def __init__(self) -> None:
        #: ``(parent session, run name)`` → ``(failure_hash of the reason, when it was told)``.
        self._told: dict[tuple[str, str], tuple[str, float]] = {}

    def already_told(self, parent_key: str, batch: Sequence[Any]) -> bool:
        """Whether every run in *batch* is a failure this gateway told within the window, so a note
        about them would say nothing new. Otherwise the note is going out: each failure it tells
        starts its window, and a run that did not fail clears its own."""
        from personalclaw.triggers.delivery import FAILURE_REMINDER_SECS, suppress_repeat_failure

        now = time.time()
        news = False
        for info in batch:
            key = (parent_key, run_name(info))
            if ended(info) != "failed":
                self._told.pop(key, None)
                news = True
                continue
            last_hash, last_at = self._told.get(key, ("", 0.0))
            repeat, digest = suppress_repeat_failure(
                error=str(info.error), last_hash=last_hash, last_at=last_at, now=now
            )
            if not repeat:
                news = True
                self._told[key] = (digest, now)
        for key in [k for k, (_d, at) in self._told.items() if now - at >= FAILURE_REMINDER_SECS]:
            del self._told[key]
        return not news
