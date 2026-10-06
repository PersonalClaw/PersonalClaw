"""What of a message the owner wrote: the one answer every reader of her words asks.

A turn's message is rarely only what the person typed. The composer expands each pasted block
into it, the turn builder runs a saved prompt in place of its ``@name`` and puts an attached
file's text, a referenced note, a theme's persona and natural voice's instructions around it, and
the platform sends rows of its own into a chat (an automation's result, a subagent's report, a
loop's nudge, a heartbeat's delivery) that nobody typed at all. The model is right to read all of
it. None of it is the person's own words, and a reader that takes it for theirs learns a prompt's
body as "a correction" because it says "what I said", or a theme's rules as a standing veto.

So the message that started a turn says which of its words were typed, where the turn is built:
the dispatcher that composes a row records them in its ``meta`` (:data:`OWN_WORDS`), and the
turn builder records them on the row when it runs a saved prompt in the row's place, beside the
prompt and the text the agent was sent for it (:func:`record_prompt_run`), which the chat shows
under her message as the prompt's. Every reader of her words then asks :func:`own_words`, which
also leaves out what the person pasted and anything a sender the owner does not trust wrote.

A conversation can also have other people in it: a channel's door lets in everyone the owner
trusts to talk to the agent, in a group, a shared thread, a mailbox or an open direct message, and
a program can send through the OpenAI-compatible door. What they write is theirs, so
:func:`own_words` reads a row only when the owner sent it (``turn_source.sent_by_owner``, from the
source the row records); :func:`sender_words` is what a row's sender typed, whoever that was.
What the agent did and said in a turn someone else asked for is theirs as well, so a pass over the
whole conversation (memory consolidation) is shown only the turns she asked for (:func:`her_turns`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from personalclaw.turn_source import Row, sent_by_owner

#: The ``meta`` key of a message whose text holds more than its sender typed: the words they did
#: type, ``""`` when none. Written only by the code that composed the row; a send's own meta never
#: carries it (``dashboard.chat_handlers`` drops it), because a key a client could set would let a
#: message teach something other than what it says.
OWN_WORDS = "own_words"

#: The ``meta`` key of a message a saved prompt ran in place of: ``{"name", "text"}``, the prompt
#: and the text the agent was sent for it. The chat shows that text under the message, folded and
#: labelled as the prompt's, and it is never part of :func:`own_words`.
RAN_PROMPT = "ran_prompt"

#: The ``meta`` key of the blocks a person pasted into a message: ``[{"seq", "lines", "content"}]``.
#: The composer sends each block in the message's text where its ``[Paste #N]`` marker stood in the
#: draft, and the blocks beside it, whichever way the message is sent: a send, a steer, a queued
#: send, an edit or a rewind. The message's row keeps them, so the chat shows each block as its
#: chip again, and learning reads them as material, never as her words.
PASTES = "pastes"


def paste_marker(seq: int) -> str:
    """The marker the composer shows in a draft where block *seq* was pasted."""
    return f"[Paste #{seq}]"


def pastes_of(meta: object) -> list[dict[str, Any]]:
    """The blocks a send pasted, as the composer sent them (:data:`PASTES`): each with its text,
    and its number and line count when it gives them. An entry with no text is no block."""
    pastes = meta.get(PASTES) if isinstance(meta, Mapping) else None
    if not isinstance(pastes, list):
        return []
    return [
        {
            "content": p["content"],
            **{
                k: p[k]
                for k in ("seq", "lines")
                if isinstance(p.get(k), int) and not isinstance(p.get(k), bool)
            },
        }
        for p in pastes
        if isinstance(p, Mapping) and isinstance(p.get("content"), str)
    ]


def pasted_blocks(meta: object) -> list[str]:
    """The text of each block a send pasted (:func:`pastes_of`)."""
    return [p["content"] for p in pastes_of(meta)]


def left_as_marker(message: str, pastes: Sequence[Mapping[str, Any]]) -> int | None:
    """The number of a block in *pastes* whose marker *message* holds in its place, or ``None``.

    The composer sends each block in place of its marker, so a message that still holds one would
    reach the model as ``[Paste #N]`` alone and never as what was pasted there. A marker with no
    block beside it is words someone typed, and is not asked about."""
    for p in pastes:
        seq = p.get("seq")
        if isinstance(seq, int) and paste_marker(seq) in message and p["content"] not in message:
            return seq
    return None


def typed_text(message: str, pasted: Sequence[str] = ()) -> str:
    """*message* without the blocks that were pasted into it: the words the person typed.

    The composer expands each ``[Paste #N]`` marker to its block before sending, so each block is
    taken out once, longest first: a block that contains another is removed whole."""
    for block in sorted((b for b in pasted if b), key=len, reverse=True):
        at = message.find(block)
        if at != -1:
            message = f"{message[:at]} {message[at + len(block):]}"
    return message


def record_prompt_run(row: object, *, name: str, text: str, words: int) -> dict[str, str]:
    """Record on *row*, the message that started a turn, that the saved prompt *name* ran in place
    of its first *words* words (``@name`` is one, ``/prompts get name`` three) and that the agent
    was sent *text* for it. Only what follows those words is the sender's own. Returns the record
    the chat shows under the message."""
    ran = {"name": name, "text": text}
    if not isinstance(row, dict):
        return ran
    rest = sender_words(row).split(None, words)
    meta = row.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        row["meta"] = meta
    meta[OWN_WORDS] = rest[words] if len(rest) > words else ""
    meta[RAN_PROMPT] = ran
    return ran


def queued_words(items: Sequence[Mapping[str, Any]]) -> str | None:
    """The words the owner typed in *items*, messages taken off a chat's queue to run as one turn,
    for the row that turn starts with (:data:`OWN_WORDS`). ``None`` when that row is one message
    that is all its sender's. A merge heads the messages with a line of its own, and a queued
    message may hold more than its sender typed, so each one gives what it recorded; one someone
    else sent (``turn_source.sent_by_owner``, from the source each records) gives nothing, since a
    row made of messages from different places records no source of its own. Redacted as the
    queued message is."""
    if len(items) <= 1 and not any(OWN_WORDS in item for item in items):
        return None
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    words, _ = redact_exfiltration_urls(
        "\n\n".join(
            str(item.get(OWN_WORDS, item.get("content") or ""))
            for item in items
            if sent_by_owner(item)
        )
    )
    words, _ = redact_credentials(words)
    return words


def own_words(row: object) -> str:
    """The words the owner typed in *row*, the message that started a turn: what every reader of
    her words asks, learning and memory among them.

    Its sender's words (:func:`sender_words`) when the owner sent it, and ``""`` when someone else
    did (``turn_source.sent_by_owner``): another person in a channel's group, thread, mailbox or
    open direct message, or a program through the OpenAI-compatible door. Their words are never
    the owner's, whatever they say about her.
    """
    return sender_words(row) if sent_by_owner(row) else ""


def sender_words(row: object) -> str:
    """The words the sender of *row*, the message that started a turn, typed in it, whoever sent
    it: what a reply weighs as the request (a word limit it asked for), never what memory learns.

    ``""`` when nobody typed it: a row an automation, a subagent's report or a loop's nudge
    started the turn with (any role but ``user``), or no row at all. A person's row is read less
    what they did not write: the text the platform composed around their words, when it recorded
    which were theirs (:data:`OWN_WORDS`); the blocks they pasted, which are material for the
    answer; and a fenced sender's text (``learning.hygiene.strip_untrusted``), since the words of
    someone the owner does not trust are never the owner's.
    """
    if not isinstance(row, Mapping) or row.get("role") != "user":
        return ""
    meta: Any = row.get("meta")
    meta = meta if isinstance(meta, Mapping) else {}
    recorded = meta.get(OWN_WORDS)
    text = recorded if isinstance(recorded, str) else str(row.get("content") or "")
    from personalclaw.learning.hygiene import strip_untrusted

    text, _ = strip_untrusted(typed_text(text, pasted_blocks(meta)))
    return text.strip()


def her_turns(rows: Sequence[Row], start: int = 0) -> list[Row]:
    """The rows of *rows*, a conversation in order, from *start* on, that memory may take anything
    from: what a pass over the whole conversation is shown (``history.consolidation_line``).

    Every row of each turn the owner asked for, by the row that started it (``turn_source.by_turn``,
    ``turn_source.asked_by``). A turn someone else asked for gives only what she sent in it, read
    as her words (:func:`own_words`): her part of a row her queued message and theirs run as, a
    message she sent into it while it ran. Their message, and what the agent did and said for
    them, are never given. A turn in which she refused a change to her memory gives nothing
    (``declined_calls``). So a model shown these is never shown anyone else's words for her to be
    credited with, nor what she would not keep, whatever it would have made of a label on them.
    """
    from personalclaw.declined_calls import declined_a_memory_change
    from personalclaw.turn_source import asked_by, by_turn, starts_a_turn

    kept: list[tuple[int, Row]] = []
    first = 0
    for turn in by_turn(rows):
        head, end = turn[0], first + len(turn)
        if end <= start or declined_a_memory_change(turn):
            pass  # a turn wholly before *start* is not read at all
        elif not (starts_a_turn(head) and asked_by(head)):
            kept += enumerate(turn, first)
        else:
            kept += [
                (at, row)
                for at, row in enumerate(turn, first)
                if row.get("role") == "user" and own_words(row)
            ]
        first = end
    return [row for at, row in kept if at >= start]
