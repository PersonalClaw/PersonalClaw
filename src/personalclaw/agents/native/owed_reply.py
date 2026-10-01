"""The reply a native turn still owes, and the one time the loop asks its model for it.

A model can run tools for a request and then stop without writing a word back. A local model was
measured doing exactly that after fifteen shell calls: its sixteenth answer was empty, the turn
ended there, and the person got no reply at all. Resending their message would run every step
again, so the turn goes on instead, with the results it already has, and asks for the reply once
(:data:`ANSWER_OWED_NOTE`). A second silence ends the turn, and the surface says the turn has no
answer.

:class:`OwedReply` is one turn's account of that: whether the turn wrote anything, whether it has
asked, and whether the request about to go out carries the note. The loop keeps it; the request
the loop assembles lays the note on its tail as a volatile message, like the correction note, so it
rides every attempt of that one inference and never enters the history.
"""

from __future__ import annotations

from personalclaw.llm.events import AgentEvent, is_length_stop
from personalclaw.llm.prompt_cache import VOLATILE_KEY

#: What the model is told, once, when it ran tools for a turn and then stopped without writing a
#: word back. "What stopped you" is there because a model that went quiet was often stuck, and
#: saying so is an answer too.
ANSWER_OWED_NOTE = (
    "You ran tools for this request and have not written your reply yet. Write it now from what "
    "those steps found. If you could not do what was asked, say what stopped you."
)

#: The runtime surface of a loop's worker and planner (the `loops` model axis). Their deliverable
#: is a file, not a reply, and the loop re-prompts a cycle that wrote none, so a turn there that
#: ends on a tool call owes no answer.
LOOP_SURFACE = "loops"


def owed_note_message() -> dict:
    """The request's tail message that asks for the reply: volatile, so no history keeps it."""
    return {"role": "user", "content": ANSWER_OWED_NOTE, VOLATILE_KEY: True}


class OwedReply:
    """One turn's account of the reply it owes. A fresh one starts every turn."""

    __slots__ = ("asked", "pending", "wrote")

    def __init__(self) -> None:
        #: Whether any inference of the turn wrote more than whitespace: a turn that answered and
        #: then made one closing call owes nothing.
        self.wrote = False
        #: Whether the turn has asked. It asks ONE time.
        self.asked = False
        #: Whether the request about to go out carries the note (:func:`owed_note_message`).
        self.pending = False

    def answered(self, text: str) -> None:
        """An inference came back: the note rode that request and no other, and its text counts."""
        self.pending = False
        self.wrote = self.wrote or bool(text.strip())

    def ask(
        self, *, tool_calls_run: int, usage: AgentEvent | None, cancelled: bool, surface: str
    ) -> bool:
        """Whether a turn its model just ended asks for the reply now, marking the ask if so.

        It does when the model ran tools this turn, wrote nothing at all, and has not been asked
        yet. Not when it wrote something, ran nothing (a blank turn is its surface's to retry:
        nothing ran, so resending the message costs nothing), was stopped, or hit its output cap
        (asking again meets the same cap); and never for a loop's worker, whose deliverable is a
        file.
        """
        if self.asked or cancelled or tool_calls_run == 0 or self.wrote:
            return False
        if usage is not None and is_length_stop(usage.stop_reason):
            return False
        if surface == LOOP_SURFACE:
            return False
        self.asked = True
        self.pending = True
        return True
