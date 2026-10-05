"""Text from outside, on its way into a prompt: the one door it passes.

Text that crossed the owner's trust boundary reaches a model only through :func:`admit`. The
injection screen reads it first (``triggers.screen.screen``), and what the screen does not refuse
is fenced as data with its source (``security.fence_untrusted``), its words kept as they arrived.
What the screen refuses is kept as nothing, and the door that refused it says so in a sentence
naming the pattern class the screen matched, never the words (:func:`withheld`).

The doors that come in here: a stored trigger's fire (its payload's words, its context line and
what started it, ``triggers.fire_facts.hand_on``), a lifecycle trigger's event and what its action
prints (``hooks.hand_on``, ``hooks.take_in``), a pasted prompt card, the context a callback saved
for its turn, a scheduled run's result opened as a chat, a group channel's recent messages and the
post that started a thread, a refiner's evidence, and a helper's report, to the chat it reports to
and through ``subagent_status`` (``subagent_report.handed_on``). The census in
``tests/test_outside_text_doors_census.py`` lists every place in core that fences text for a model,
and fails a new one that does it without coming through here.

Text PersonalClaw fenced where it arrived (a watched page's items, an event's value, a webhook's
body) keeps that fence when the fence holds all of it (:func:`is_whole_fence`), so the richer
source written there survives; the screen reads it all the same. Text straight from outside (a
program's output, a message as it was sent) has no fence of PersonalClaw's on it, so a fence in it
is only more of its own text, and it is wrapped like any other.

The owner's instruction for such a text (an Inbox message to an address she gave a prompt) is
handed over beside it, never in it, and goes before what the door let through, outside any fence
(:func:`instructed`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from personalclaw.security import UNTRUSTED_CLOSE, fence_untrusted, outside_fences


@dataclass(frozen=True)
class Admitted:
    """What :func:`admit` lets through of one text.

    ``text`` is the words fenced with their source, or ``""`` when there were none or the screen
    refused them; ``refused`` the groups the screen refused them for; ``flagged`` the groups it
    matched without refusing, whose words go on fenced as every word from outside does.
    """

    text: str = ""
    refused: tuple[str, ...] = ()
    flagged: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """Whether the screen matched nothing at all in the words."""
        return not self.refused and not self.flagged


def admit(
    text: str,
    *,
    source: str,
    source_type: str = "",
    source_id: str = "",
    transformation_path: str = "",
    fenced_where_it_arrived: bool = False,
) -> Admitted:
    """*text* as a model may be handed it: read by the injection screen, then fenced as data.

    *source*, *source_type*, *source_id* and *transformation_path* label the fence
    (``security.fence_untrusted``). *fenced_where_it_arrived* is True for text PersonalClaw may
    already have fenced on its way in (a payload's prose, an event's value, a webhook's body): a
    fence that holds all of it is kept as it is. Text the screen refuses comes back as nothing,
    with the groups it was refused for. Never raises: the screen does not, and an empty text is
    handed back as it is.
    """
    if not text or not text.strip():
        return Admitted(text=text or "")
    from personalclaw.triggers.screen import screen

    verdict = screen(text)
    if verdict.blocked:
        # The groups name the refusal; a verdict that named none still refuses.
        return Admitted(refused=verdict.groups or ("injection",))
    flagged = () if verdict.clean else (verdict.groups or (verdict.matched_group or "injection",))
    if fenced_where_it_arrived and is_whole_fence(text):
        return Admitted(text=text, flagged=flagged)
    return Admitted(
        text=fence_untrusted(
            text,
            source=source,
            source_type=source_type,
            source_id=source_id,
            transformation_path=transformation_path,
        ),
        flagged=flagged,
    )


def is_whole_fence(text: str) -> bool:
    """Whether every word of *text* is inside a fence, as text fenced where it arrived is: what
    :func:`admit` keeps rather than wraps again for a door that takes such text. Text that only
    quotes a marker, which ``security.is_fenced`` finds, is not, and is fenced like any other from
    outside.
    """
    body = (text or "").strip()
    return body.endswith(UNTRUSTED_CLOSE) and not outside_fences(body).strip()


def ends_inside_a_fence(text: str) -> bool:
    """Whether *text* stops inside a fence: an open marker with no close after it, so whatever
    follows it would read as fenced. A cut that took a fence's close away leaves such text.

    Read as ``security.outside_fences`` reads spans, where an open marker that is never closed
    takes the rest of the text with it: a character put after *text* is kept only when *text*
    ends outside every fence.
    """
    end = "\x00"
    return not outside_fences(f"{text or ''}{end}").endswith(end)


def instructed(instruction: str, admitted: str) -> str:
    """The owner's *instruction* and what :func:`admit` let through of the text from outside it is
    about (*admitted*), as a model is handed the two: her instruction first, outside any fence,
    then the text, fenced with its source.

    Her words are neither screened nor fenced, as a task an automation's owner writes is not: they
    are hers. Only text from outside is. Whose words an instruction is, is settled before it gets
    here (``inbox_service.admitted_instruction``): the instruction a source hands over beside a
    message is hers only when her settings for the source's app hold it.
    """
    instruction = (instruction or "").strip()
    if not instruction:
        return admitted
    if not (admitted or "").strip():
        return instruction
    return f"{instruction}\n\n{admitted}"


def withheld(what: str, refused: Iterable[str]) -> str:
    """The sentence a door puts where it kept *what* as nothing: what it was, and the pattern class
    the screen refused it for, never its words."""
    groups = ", ".join(refused) or "injection"
    return f"[{what} was withheld: the injection screen refused it ({groups}).]"


@dataclass(frozen=True)
class AdmittedPayload:
    """What :func:`admit_payload` lets through of a fire's payload: the payload with its words
    fenced, or nothing, with ``refused``, the groups the screen refused any of them for."""

    payload: dict[str, Any] = field(default_factory=dict)
    refused: tuple[str, ...] = ()


def admit_payload(
    payload: dict[str, Any] | None, *, kind: str = "", trigger_id: str = ""
) -> AdmittedPayload:
    """*payload* as a fire hands it to its action: every string under a key that carries words for
    its *kind* (``triggers.screen.prose_keys``), however deeply it nests, through :func:`admit`,
    labelled with the trigger as its source. Ids, counts, flags and the keys no kind's words ride
    in go on as they are, so the payload keeps its shape under the action.

    A payload the screen refuses any word of is refused whole: a fire never runs on the part of
    what it was handed that the screen let through.
    """
    if not isinstance(payload, dict):
        return AdmittedPayload()
    from personalclaw.triggers.screen import prose_keys

    refused: set[str] = set()

    def _admit(value: Any) -> Any:
        if isinstance(value, str):
            if not value.strip():
                return value
            admitted = admit(
                value,
                source=f"trigger:{trigger_id}" if trigger_id else "trigger-payload",
                source_type=kind or "trigger",
                source_id=trigger_id,
                transformation_path="fire:payload",
                fenced_where_it_arrived=True,
            )
            refused.update(admitted.refused)
            return admitted.text
        if isinstance(value, list):
            return [_admit(item) for item in value]
        if isinstance(value, tuple):
            return tuple(_admit(item) for item in value)
        if isinstance(value, dict):
            return {key: _admit(item) for key, item in value.items()}
        return value

    out = dict(payload)
    for key in sorted(prose_keys(kind)):
        if key in out:
            out[key] = _admit(out[key])
    if refused:
        return AdmittedPayload(refused=tuple(sorted(refused)))
    return AdmittedPayload(payload=out)
