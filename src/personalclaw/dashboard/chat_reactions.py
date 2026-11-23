"""A reaction to an agent's answer, and what the agent is told of it.

The page sends a reaction as a chat message with no words of its own: its ``meta.reaction`` names
the answer by its row's ``ts`` and the reaction picked (one of :data:`REACTIONS`, or ``None`` to
take one back). The gateway writes what the agent reads from a fixed template (:func:`entry_for`),
never from text the page sent, and the message then joins the chat as a typed one does
(``chat_handlers.api_chat``): a steer the running turn takes at its next step, queued behind a turn
whose runtime takes no steers, else a turn of its own. So a thumbs up to "should I go ahead?"
reads as a yes, where a typed yes would, and nowhere else: it answers no approval card and no
question, which keep their own controls.

Its row keeps the reaction (:data:`REACTION` in the row's ``meta``), and an answer's reaction is
the last such row for it (:func:`reactions_of`). No earlier row is rewritten, so a branch made
after a reaction has it, one made before has neither, and an export reads both from the rows.
A reaction is the owner's input to the agent, never feedback: it writes no feedback record, and
none of its words are hers (``own_words.OWN_WORDS`` is ``""``), so memory learns nothing from it
by itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from personalclaw.instants import as_instant
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.turn_source import TURN_STARTS

#: The ``meta`` key of the row a reaction sent: ``{to, pick, previous}``, ``pick`` and
#: ``previous`` each a key of :data:`REACTIONS` or ``None``.
REACTION = "reaction"

#: The five reactions, by key: the emoji and the name the agent is told.
REACTIONS: Mapping[str, tuple[str, str]] = {
    "thumbs_up": ("👍", "thumbs up"),
    "thumbs_down": ("👎", "thumbs down"),
    "heart": ("❤️", "heart"),
    "celebrate": ("🎉", "celebrate"),
    "thinking": ("🤔", "thinking"),
}

#: How much of the answer the agent is shown, so it can tell which message the reaction is to.
EXCERPT_CHARS = 160


@dataclass(frozen=True)
class Refusal:
    """A reaction the chat did not take, and why: nothing of it was kept or sent."""

    code: str
    message: str
    status: int = 400
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Entry:
    """What a reaction sends: the words the agent reads, and the record its row keeps."""

    text: str
    record: Mapping[str, Any]


def requested(meta: object) -> tuple[str, str | None] | Refusal | None:
    """The reaction a send's *meta* carries, as ``(answer ts, pick)``; ``None`` when it carries
    none; a :class:`Refusal` for one of any other shape."""
    if not isinstance(meta, Mapping) or REACTION not in meta:
        return None
    raw = meta.get(REACTION)
    if not isinstance(raw, Mapping):
        return Refusal("invalid_reaction", "A reaction names the answer and the reaction picked.")
    to, pick = raw.get("to"), raw.get("pick")
    if not isinstance(to, str) or not to.strip():
        return Refusal("invalid_reaction", "A reaction names the answer it is to by its time.")
    if pick is not None and pick not in REACTIONS:
        return Refusal(
            "invalid_reaction",
            "That reaction isn't one of the five: thumbs up, thumbs down, heart, celebrate or "
            "thinking.",
        )
    return to.strip(), pick


def reactions_of(rows: Sequence[object]) -> dict[str, str]:
    """Each answer's reaction now, by the answer's ``ts``: the last reaction row for it, taken in
    row order. An answer whose last row took its reaction back has none."""
    picks: dict[str, str] = {}
    for row in rows:
        record = _record_of(row)
        if record is None:
            continue
        to, pick = record.get("to"), record.get("pick")
        if not isinstance(to, str):
            continue
        if isinstance(pick, str) and pick in REACTIONS:
            picks[to] = pick
        else:
            picks.pop(to, None)
    return picks


def record_of(row: object) -> Mapping[str, Any] | None:
    """The reaction *row* sent (its ``meta.reaction``), or ``None`` for any other row."""
    return _record_of(row)


def entry_for(
    rows: Sequence[object], to: str, pick: str | None, *, running: bool, zone: Any = None
) -> Entry | Refusal:
    """What a reaction of *pick* to the answer at *to* sends, given the chat's *rows*: the added,
    changed or removed template, naming the answer by its time and its first words.

    Refused when *to* names no row, or a row that is no turn's final answer (a tool's row, the
    owner's own message, a line the platform wrote), or the answer of a turn still *running*; and
    when the answer has that reaction already, or none to take back.
    """
    at = next((i for i, r in enumerate(rows) if isinstance(r, Mapping) and r.get("ts") == to), None)
    if at is None:
        return Refusal(
            "reaction_not_an_answer", "No message in this chat has that time.", status=404
        )
    if not _is_final_answer(rows, at):
        return Refusal(
            "reaction_not_an_answer",
            "A reaction goes on an agent's answer: not on a tool's step, your own message or a "
            "notice.",
            status=409,
        )
    if running and not any(_starts_a_turn(r) for r in rows[at + 1 :]):
        return Refusal(
            "reaction_not_an_answer",
            "That answer is still being written; react once it is done.",
            status=409,
        )
    previous = reactions_of(rows).get(to)
    if pick == previous:
        return Refusal(
            "reaction_unchanged",
            "That answer has that reaction already." if pick else "That answer has no reaction.",
            status=409,
        )
    answer = rows[at]
    assert isinstance(answer, Mapping)
    when = _clock(str(answer.get("ts") or ""), zone)
    quote = _excerpt(str(answer.get("content") or ""))
    text = _sentence(previous, pick, when, quote)
    return Entry(text=text, record={"to": to, "pick": pick, "previous": previous})


def carried_by(items: Sequence[object]) -> dict[str, Any]:
    """What the row queued *items* run as keeps of a reaction among them: the last one's record,
    under :data:`REACTION`; ``{}`` when none of them is a reaction. A queued message keeps only
    what its row is read for (``chat_intake.what_they_carry``), and a reaction's row is read for
    the reaction."""
    for item in reversed(items):
        if (record := record_in(item)) is not None:
            return {REACTION: dict(record)}
    return {}


def record_in(item: object) -> Mapping[str, Any] | None:
    """The reaction record a queued message (its own field) or a send's or steer's ``meta``
    carries, or ``None``."""
    if not isinstance(item, Mapping):
        return None
    record = item.get(REACTION)
    if not isinstance(record, Mapping):
        meta = item.get("meta")
        record = meta.get(REACTION) if isinstance(meta, Mapping) else None
    return record if isinstance(record, Mapping) else None


def shown(pick: str | None) -> str:
    """A reaction as a person reads it: ``👍 (thumbs up)``; ``""`` for none."""
    if pick is None or pick not in REACTIONS:
        return ""
    emoji, name = REACTIONS[pick]
    return f"{emoji} ({name})"


def _sentence(previous: str | None, pick: str | None, when: str, quote: str) -> str:
    """The template the agent reads, never the page's words."""
    of = f"your message from {when}" if when else "your message"
    if previous is None and pick is not None:
        return f'The user reacted {shown(pick)} to {of}: "{quote}"'
    if previous is not None and pick is not None:
        return (
            f"The user changed their reaction to {of} from {shown(previous)} to "
            f'{shown(pick)}: "{quote}"'
        )
    return f'The user removed their {shown(previous)} from {of}: "{quote}"'


def _record_of(row: object) -> Mapping[str, Any] | None:
    if not isinstance(row, Mapping) or row.get("role") != "user":
        return None
    meta = row.get("meta")
    record = meta.get(REACTION) if isinstance(meta, Mapping) else None
    return record if isinstance(record, Mapping) else None


def _starts_a_turn(row: object) -> bool:
    """Whether *row* starts a turn: a message that is not a steer taken into a running one."""
    from personalclaw.turn_source import STEERED

    if not isinstance(row, Mapping) or row.get("role") not in TURN_STARTS:
        return False
    meta = row.get("meta")
    return not (isinstance(meta, Mapping) and meta.get(STEERED))


def _is_answer_text(row: object) -> bool:
    if not isinstance(row, Mapping) or row.get("role") != "assistant":
        return False
    meta = row.get("meta")
    if isinstance(meta, Mapping) and meta.get("tool_call_id"):
        return False
    return bool(str(row.get("content") or "").strip())


def _is_final_answer(rows: Sequence[object], at: int) -> bool:
    """Whether ``rows[at]`` is its turn's final answer: the agent's last text in the turn."""
    if not _is_answer_text(rows[at]):
        return False
    for row in rows[at + 1 :]:
        if _starts_a_turn(row):
            return True
        if _is_answer_text(row):
            return False
    return True


def _clock(ts: str, zone: Any) -> str:
    """*ts* as the owner's clock reads it, ``2:31 PM``; ``""`` for no readable time."""
    try:
        stamped = datetime.fromisoformat(str(as_instant(ts)))
    except ValueError:
        return ""
    if zone is None:
        from personalclaw.timezones import resolve_zone

        zone = resolve_zone("")
    local = stamped.astimezone(zone)
    return f"{local:%I:%M %p}".lstrip("0")


def _excerpt(content: str) -> str:
    """The answer's first :data:`EXCERPT_CHARS` characters on one line, masked as text shown on
    a page is."""
    text, _ = redact_exfiltration_urls(content)
    text, _ = redact_credentials(text)
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1].rstrip() + "…"
