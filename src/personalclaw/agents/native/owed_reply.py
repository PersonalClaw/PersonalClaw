"""The reply a native turn still owes, and the one time the loop asks its model for it.

A model can run tools for a request and then stop without writing a word back. A local model was
measured doing exactly that after fifteen shell calls: its sixteenth answer was empty, the turn
ended there, and the person got no reply at all. Resending their message would run every step
again, so the turn goes on instead, with the results it already has, and asks for the reply once
(:data:`ANSWER_OWED_NOTE`). A second silence ends the turn, and the surface says the turn has no
answer.

A model can also spend its whole output cap before it writes or calls anything: measured, a review
subagent on a hosted model stopped at exactly 8,192 output tokens on every call, with no text and
no tool call, and asking again in the same words met the same cap. That ask changes the request
(:func:`out_of_room_note`): the note asks for a brief answer, and the request goes out without
extended reasoning (:meth:`OwedReply.effort`), because reasoning is what spends the cap unseen on
the providers whose cap counts it. More room is not what changes, though it would be the other
lever: the cap is the model instance's own setting, a hosted model's ceiling above it is not
declared anywhere core can read, and a request over that ceiling is refused by the provider. A
capped silence after that ask ends the turn at the cap, and the turn's ending names it
(``llm.events.out_of_room``).

:class:`OwedReply` is one turn's account of that: whether the turn wrote anything, whether it has
asked, and whether the request about to go out carries the note. The loop keeps it; the request
the loop assembles lays the note on its tail as a volatile message, like the correction note, so it
rides every attempt of that one inference and never enters the history.
"""

from __future__ import annotations

from personalclaw.agents.native.tools import ARGUMENTS_UNREADABLE, read_tool_arguments
from personalclaw.guardrails.failure import FailureMode, answered_mode
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


def out_of_room_note(output_cap: int) -> str:
    """What the model is told, once, when it stopped at its output cap (*output_cap* tokens, 0 when
    unknown) before it wrote or called anything. It is the same ask on every surface: a loop's
    worker answers it with its next call, a chat or a subagent with its reply."""
    room = f" ({output_cap:,} tokens)" if output_cap > 0 else ""
    return (
        f"Your last response ran out of output room{room} before you wrote anything or called a "
        "tool. Go on now briefly, without extended reasoning: give your answer in a few sentences, "
        "or make the one tool call you need next. If you could not do what was asked, say what "
        "stopped you."
    )


def capped_at(usage: AgentEvent | None) -> int:
    """The output tokens an inference stopped at when it ran into its cap (a length stop), else 0:
    what the turn's terminal event names (``AgentEvent.output_cap``)."""
    if usage is None or not is_length_stop(usage.stop_reason):
        return 0
    return max(0, int(usage.output_tokens or 0))


def answered_row(
    usage: AgentEvent | None, text: str, tool_calls: list[AgentEvent]
) -> tuple[FailureMode, bool, bool]:
    """How the model-call log records an inference that came back, as ``(failure_mode, passed,
    unrecorded)`` (``answered_mode``): one cut at its cap passed only when it wrote *text* or made a
    call whose arguments read whole (a call cut mid-way carries a prefix of them, which runs
    nothing), and is ``unrecorded`` when no guard recorded it (its usage names no guarded call)."""
    stop = usage.stop_reason if usage is not None else ""
    produced = is_length_stop(stop) and (
        bool(text.strip())
        or any(read_tool_arguments(c.tool_input) is not ARGUMENTS_UNREADABLE for c in tool_calls)
    )
    mode, passed = answered_mode(stop, produced=produced)
    unrecorded = mode is FailureMode.OUTPUT_CAP and not getattr(usage, "audit_ids", ())
    return mode, passed, unrecorded


class OwedReply:
    """One turn's account of the reply it owes. A fresh one starts every turn."""

    __slots__ = ("asked", "brief", "pending", "room", "silent", "wrote")

    def __init__(self) -> None:
        #: Whether any inference of the turn wrote more than whitespace: a turn that answered and
        #: then made one closing call owes nothing.
        self.wrote = False
        #: Whether the inference that just came back wrote nothing (whitespace is nothing).
        self.silent = False
        #: Whether the turn has asked. It asks ONE time, in whichever form.
        self.asked = False
        #: Whether the request about to go out carries the note (:meth:`note_message`).
        self.pending = False
        #: Whether that ask is the out-of-room one: a brief answer, no extended reasoning.
        self.brief = False
        #: The output cap the model stopped at before that ask, in tokens (0 when unknown).
        self.room = 0

    def answered(self, text: str) -> None:
        """An inference came back: the note rode that request and no other, and its text counts."""
        self.pending = False
        self.silent = not text.strip()
        self.wrote = self.wrote or not self.silent

    def ask(
        self, *, tool_calls_run: int, usage: AgentEvent | None, cancelled: bool, surface: str
    ) -> bool:
        """Whether a turn its model just ended (with no tool call) asks again now, marking the ask
        if so. A turn asks once, in one of two forms.

        It asks briefly (:func:`out_of_room_note`) when that inference stopped at its output cap
        having written nothing, on every surface and whether or not tools ran, because nothing
        else it could be sent would end differently. Otherwise it asks for the reply when the
        model ran tools this turn and wrote nothing at all. Not when it wrote something, ran
        nothing (a blank turn is its surface's to retry: nothing ran, so resending the message
        costs nothing), or was stopped; and never for a loop's worker, whose deliverable is a
        file. A reply cut mid-sentence is not asked again: it is an answer, and its surface says
        it was cut.
        """
        if self.asked or cancelled:
            return False
        if usage is not None and is_length_stop(usage.stop_reason):
            if not self.silent:
                return False
            self.brief = True
            self.room = max(0, int(usage.output_tokens or 0))
        elif tool_calls_run == 0 or self.wrote or surface == LOOP_SURFACE:
            return False
        self.asked = True
        self.pending = True
        return True

    def note_message(self) -> dict:
        """The request's tail message that asks: volatile, so no history keeps it."""
        note = out_of_room_note(self.room) if self.brief else ANSWER_OWED_NOTE
        return {"role": "user", "content": note, VOLATILE_KEY: True}

    def effort(self, turn_effort: str) -> str:
        """The reasoning effort the request about to go out carries: the turn's, except on the
        out-of-room ask, which goes without it, because on the providers whose output cap counts
        reasoning that is what spent the room the answer needed."""
        return "" if self.pending and self.brief else turn_effort

    def asking(self, tool_calls_run: int) -> str:
        """How the log says the turn is asking, now that it has."""
        if self.brief:
            return (
                f"the model ran out of output room ({self.room} tokens) before it wrote anything "
                "— asking it once more, briefly and without extended reasoning"
            )
        return (
            f"the model stopped after {tool_calls_run} tool call(s) without a reply — asking it "
            "once for the answer"
        )
