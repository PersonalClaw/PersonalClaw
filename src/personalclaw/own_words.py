"""What of a message its sender wrote: the one answer every reader of "what they said" asks.

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
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: The ``meta`` key of a message whose text holds more than its sender typed: the words they did
#: type, ``""`` when none. Written only by the code that composed the row; a send's own meta never
#: carries it (``dashboard.chat_handlers`` drops it), because a key a client could set would let a
#: message teach something other than what it says.
OWN_WORDS = "own_words"

#: The ``meta`` key of a message a saved prompt ran in place of: ``{"name", "text"}``, the prompt
#: and the text the agent was sent for it. The chat shows that text under the message, folded and
#: labelled as the prompt's, and it is never part of :func:`own_words`.
RAN_PROMPT = "ran_prompt"


def pasted_blocks(meta: object) -> list[str]:
    """The blocks a send pasted, as the composer sent them (``meta.pastes[].content``)."""
    pastes = meta.get("pastes") if isinstance(meta, Mapping) else None
    if not isinstance(pastes, list):
        return []
    return [
        p["content"] for p in pastes if isinstance(p, dict) and isinstance(p.get("content"), str)
    ]


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
    rest = own_words(row).split(None, words)
    meta = row.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        row["meta"] = meta
    meta[OWN_WORDS] = rest[words] if len(rest) > words else ""
    meta[RAN_PROMPT] = ran
    return ran


def queued_words(items: Sequence[Mapping[str, Any]]) -> str | None:
    """The words of *items*, messages taken off a chat's queue to run as one turn, that their
    senders typed, for the row that turn starts with (:data:`OWN_WORDS`). ``None`` when that row is
    one message that is all its sender's. A merge heads the messages with a line of its own, and a
    queued message may hold more than its sender typed, so each one gives what it recorded. Redacted
    as the queued message is."""
    if len(items) <= 1 and not any(OWN_WORDS in item for item in items):
        return None
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    words, _ = redact_exfiltration_urls(
        "\n\n".join(str(item.get(OWN_WORDS, item.get("content") or "")) for item in items)
    )
    words, _ = redact_credentials(words)
    return words


def own_words(row: object) -> str:
    """The words the sender of *row*, the message that started a turn, typed in it.

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
