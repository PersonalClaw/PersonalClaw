"""What was said in your earlier chats, found for your agent: the search its ``chat_search`` runs.

The chat list and the command palette search your chats by what was said in them
(:func:`personalclaw.session_search.search`), and the agent could not: asked for a handoff, a
standup or a weekly review, it read your notes, trackers and code, and left out what only your
chats held. :func:`search` is that search, for the agent, and it keeps three promises:

* **An Incognito or Temporary chat is never in what it finds.** The search it runs leaves them out
  at the index and again when it reads (:mod:`personalclaw.session_search`). A Temporary chat's own
  call finds nothing at all, because it starts blank: the gateway's route answers it with
  :data:`TEMPORARY`.
* **The chat it is asked from is not in it either** (:func:`chats_of`): that chat is already in
  front of the agent, and handed back it would read as earlier work.
* **It searches her chats only for her.** A search made for work someone other than the owner
  asked for (a colleague's turn in a shared thread, a correspondent's, a program's, and the work
  such a turn starts: ``memory_writes.asker``) searches nothing, and is answered with
  :func:`not_searched_for`. The gateway's route and the inbound door's search both say it.
* **It is bounded**: a few chats (:data:`MAX_CHATS`), a few turns of each (:data:`TURNS_PER_CHAT`),
  and a window of each turn (:data:`TURN_CHARS`) around where the words were said.

An answer says how much of the history it searched (``session_search.Answer.shortfall``), so "no
chat says it" is never read as "not there" while the index is still being built. What it hands on
was said in a chat; it is not a request made now, so :func:`render` quotes each chat as data
(``security.fence_untrusted``), with its credentials masked before anything is cut.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Any
from urllib.parse import quote

from personalclaw import session_search
from personalclaw.constants import dashboard_history_key, dashboard_key_from_file_form
from personalclaw.history import TURN_ROLES, ConversationLog, _safe_key
from personalclaw.instants import as_instant
from personalclaw.security import fence_untrusted, redact_for_model

#: The most chats one search returns, and how many it returns when not asked for a number.
MAX_CHATS = 10
DEFAULT_CHATS = 5
#: The most turns shown of one chat: those that say the most of what was asked.
TURNS_PER_CHAT = 3
#: The most of one turn shown: a window around where the words were said.
TURN_CHARS = 600
#: How much of that window comes before the words.
_LEAD_CHARS = 150

#: What a Temporary chat's call is answered with: it starts blank, so it reads no other chat.
TEMPORARY = (
    "This is a Temporary chat: it starts blank and reads nothing from your other chats, so none "
    "were searched."
)


def not_searched_for(source: Mapping[str, str]) -> str:
    """What a search of her chats made for work *source* asked for, someone other than the owner,
    is answered with: who asked, and that her chats are searched only for her own requests. The
    agent can pass it on as it is."""
    from personalclaw.turn_source import named

    return (
        f"None of the owner's other chats were searched: {named(source)} asked for this, and "
        "nothing says they are the owner, whose chats are searched only for their own requests."
    )


#: How a chat's start and last activity are written: as the prompt's own ``[CURRENT DATE]`` line
#: writes today (``context.py``), in the zone it uses, so "Tuesday" means the same day in both.
_DAY = "%A, %Y-%m-%d %H:%M %Z"
_TURN_DAY = "%a %Y-%m-%d %H:%M %Z"

_WORD = re.compile(r"\w+")
#: Endings a word may carry in a question and not in the chat that answers it ("outages" said as
#: "outage", "spiked" as "spike"). The index reads every word through a stemmer; a turn is matched
#: with the little of one this needs.
_ENDINGS = ("ing", "ed", "es", "s")
#: Words that say nothing about which turn is meant, so they do not rank one ("what happened with
#: the spike" asks for "happened" and "spike"). Used only to rank the turns of a chat the search
#: already found, and only while the query keeps another word.
_FILLER = frozenset(
    "a an and any are as at be but by did do does for from had has have how i if in is it its me "
    "my of on or our so that the their them then there this to us was we were what when where "
    "which who why will with you your".split()
)


@dataclass(frozen=True)
class Turn:
    """One turn of a chat that says what was asked: who said it, when (an instant with its
    offset, ``""`` when the transcript has none), and the window of it that is shown."""

    role: str
    at: str
    text: str


@dataclass(frozen=True)
class Chat:
    """One chat a search found: its key as the chat list names it, its title, when it started and
    was last active (instants), the turns shown, how many ``more`` of its turns use the words,
    and the passage the search found, for a chat where no turn can be shown."""

    key: str
    title: str
    created: str
    modified: str
    turns: tuple[Turn, ...]
    more: int
    snippet: str


@dataclass(frozen=True)
class Recall:
    """What one search found, and the answer it came from (how much of the history it covered).
    ``left_out`` says the chat it was asked from was not searched."""

    query: str
    chats: tuple[Chat, ...]
    answer: session_search.Answer
    left_out: bool


def chats_of(log: ConversationLog, session_key: str) -> frozenset[str]:
    """The transcripts of the chat *session_key* names, as the chat list names them: its file under
    either spelling of its key (``dashboard:chat-7`` is ``dashboard_chat-7``), and the older files
    of the same chat (one ``tab_id``). Empty for no key."""
    key = (session_key or "").strip()
    if not key:
        return frozenset()
    names = {_one_file(_safe_key(k)) for k in (key, dashboard_history_key(key))}
    listed = log.list_sessions_with_metadata()
    own = [(entry, meta) for entry, meta in listed if _one_file(entry["key"]) in names]
    tabs = {meta.get("tab_id") for _entry, meta in own if meta.get("tab_id")}
    found = names | {entry["key"] for entry, _meta in own}
    if tabs:
        found |= {entry["key"] for entry, meta in listed if meta.get("tab_id") in tabs}
    return frozenset(found)


def search(
    query: str,
    *,
    log: ConversationLog,
    limit: int = DEFAULT_CHATS,
    exclude: frozenset[str] = frozenset(),
    visible: Callable[[str], bool] | None = None,
) -> Recall:
    """The chats that say *query*, best first, at most *limit* (within :data:`MAX_CHATS`), none of
    them in *exclude* (:func:`chats_of`, the chat asking) and each one *visible* admits (all, by
    default). Incognito and Temporary chats are left out by the search itself."""
    text = " ".join((query or "").split())
    most = max(1, min(int(limit), MAX_CHATS))

    def searched(key: str) -> bool:
        return key not in exclude and (visible is None or visible(key))

    answer = session_search.search(text, log=log, limit=most, visible=searched)
    chats = tuple(_found(log, hit, text) for hit in answer.hits[:most])
    return Recall(text, chats, answer, left_out=bool(exclude))


def route(key: str, query: str) -> str:
    """Where the dashboard opens chat *key* at what was asked: ``#/chat/<key>?find=<query>``, the
    way a content search opens one (``web/src/pages/chat/searchDeepLink.ts``)."""
    bare = re.sub(r"^dashboard[_:]", "", key)
    find = f"?find={quote(query, safe='')}" if query else ""
    return f"#/chat/{quote(bare, safe='')}{find}"


def to_dict(recall: Recall, *, link: Callable[[str], str]) -> dict[str, Any]:
    """*recall* as the route serves it, with each chat's ``route`` and ``link`` (*link* of the
    route: the dashboard's absolute address for it, or ``""`` when none is known)."""
    answer = recall.answer
    chats = []
    for chat in recall.chats:
        where = route(chat.key, recall.query)
        chats.append(
            {
                "key": chat.key,
                "title": chat.title,
                "created": chat.created,
                "modified": chat.modified,
                "route": where,
                "link": link(where),
                "turns": [{"role": t.role, "at": t.at, "text": t.text} for t in chat.turns],
                "more": chat.more,
                "snippet": chat.snippet,
            }
        )
    return {
        "query": recall.query,
        "chats": chats,
        "matched": answer.matched,
        "searched": {"chats": answer.searched, "of": answer.of},
        "complete": answer.complete,
        "index": answer.index,
    }


def render(recall: Recall, *, zone: tzinfo, link: Callable[[str], str]) -> str:
    """What the agent is handed: how many chats say it and how much was searched, then each chat,
    best first: when it started and was last active (in *zone*, the user's), where to open it
    (*link* of its route when the dashboard's address is known, else the route itself), and its
    title and turns, quoted as data."""
    lines = [_headline(recall)]
    for number, chat in enumerate(recall.chats, start=1):
        where = route(chat.key, recall.query)
        started, active = _when(chat.created, zone, _DAY), _when(chat.modified, zone, _DAY)
        about = [
            f"Started {started}." if started else "",
            f"Last active {active}." if active else "",
        ]
        lines += [
            "",
            " ".join([f"{number}.", *(a for a in about if a), f"Open it: {link(where) or where}"]),
        ]
        quoted = [f"Title: {chat.title}"]
        for turn in chat.turns:
            at = _when(turn.at, zone, _TURN_DAY)
            quoted.append(f"[{at}] {turn.role}: {turn.text}" if at else f"{turn.role}: {turn.text}")
        if not chat.turns and chat.snippet:
            quoted.append(f"Where it was said: {chat.snippet}")
        lines.append(
            fence_untrusted(
                "\n".join(quoted), source="chat-history", source_type="chat", source_id=chat.key
            )
        )
        if chat.more:
            lines.append(f"{chat.more} more of its turns use these words.")
    return "\n".join(lines)


def _headline(recall: Recall) -> str:
    """How many chats say it, of how many matched, and what was not searched."""
    shown = len(recall.chats)
    asked = f'"{recall.query}"'
    if not shown:
        head = f"No earlier chat says {asked}."
    elif recall.answer.matched > shown:
        head = f"{recall.answer.matched:,} earlier chats say {asked}; these are the best {shown}."
    elif shown == 1:
        head = f"1 earlier chat says {asked}."
    else:
        head = f"{shown} earlier chats say {asked}, best first."
    notes = [
        "This chat is not searched." if recall.left_out else "",
        recall.answer.shortfall("chats"),
        # A chat is found by what was said in it, not by when: "Thursday outages" misses a chat
        # from Thursday that said "outages".
        (
            "A chat is found by the words said in it: try fewer of them."
            if not shown and len(recall.query.split()) > 1
            else ""
        ),
    ]
    return " ".join(part for part in (head, *notes) if part)


# ── reading one chat ─────────────────────────────────────────────────────────────────────────


def _one_file(name: str) -> str:
    """A transcript's name as one chat has it, however a dashboard chat's prefix was stacked on it
    (``dashboard_dashboard_chat-7`` is ``dashboard_chat-7``, as the chat list counts them)."""
    if not name.startswith("dashboard_"):
        return name
    return _safe_key(dashboard_key_from_file_form(name))


def _found(log: ConversationLog, hit: Mapping[str, Any], text: str) -> Chat:
    """The chat the search hit *hit* names, with the turns that say the most of *text*."""
    key = str(hit["key"])
    meta = log.get_metadata(key)
    stamp = log.transcript_stamp(key)
    modified = datetime.fromtimestamp(stamp[0] / 1e9, timezone.utc).isoformat() if stamp else ""
    # A transcript past what a search reads whole is not read again here: the passage the search
    # found says where it was said.
    readable = stamp is not None and stamp[1] <= session_search.LONG_READ_BYTES
    turns, more = _turns_saying(log.read_messages(key), text) if readable else ((), 0)
    snippet = str(hit.get("snippet") or "").replace("<<", "").replace(">>", "")
    # A chat with no title of its own is named by its first prompt, which may run over lines.
    title = " ".join(str(hit.get("title") or meta.get("title") or key).split())
    return Chat(
        key=key,
        title=redact_for_model(title),
        created=str(as_instant(hit.get("created") or meta.get("created_at") or "")),
        modified=modified,
        turns=turns,
        more=more,
        snippet=redact_for_model(snippet),
    )


def _words(query: str) -> list[str]:
    """The words of *query*, case-folded, each without an ending a chat may say it without, and
    without :data:`_FILLER` while another word is left."""
    words: list[str] = []
    for word in _WORD.findall(query.casefold()):
        for ending in _ENDINGS:
            if word.endswith(ending) and len(word) - len(ending) >= 3:
                word = word[: -len(ending)]
                break
        if len(word) >= 2 and word not in words:
            words.append(word)
    meant = [w for w in words if w not in _FILLER]
    return meant or words


def _turns_saying(messages: list[dict], text: str) -> tuple[tuple[Turn, ...], int]:
    """The turns of *messages* that say the most of *text*, at most :data:`TURNS_PER_CHAT`, in the
    order they were said, and how many more of its turns use any of its words.

    A turn that says the whole of *text* ranks first, then one that says more of its words; of
    two that rank alike, the earlier. What the user or the agent said is a turn; a tool's output
    is not, and is never shown.
    """
    phrase = text.casefold()
    patterns = [re.compile(rf"(?<!\w){re.escape(w)}") for w in _words(text)]
    ranked: list[tuple[tuple[int, int, int], Turn]] = []
    for position, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") not in TURN_ROLES or not isinstance(content, str):
            continue
        # Masked whole before any of it is cut, so a window never ends inside a credential.
        said = " ".join(redact_for_model(content).split())
        folded = said.casefold()
        whole = bool(phrase) and phrase in folded
        found = [m.start() for m in (p.search(folded) for p in patterns) if m is not None]
        if not whole and not found:
            continue
        # Where the words were said, in the turn as written when folding kept its length.
        at = (folded.find(phrase) if whole else min(found)) if len(folded) == len(said) else 0
        turn = Turn(role=_speaker(message), at=str(message.get("ts") or ""), text=_window(said, at))
        ranked.append(((-int(whole), -len(found), position), turn))
    ranked.sort(key=lambda row: row[0])
    chosen = sorted(ranked[:TURNS_PER_CHAT], key=lambda row: row[0][2])
    return tuple(turn for _rank, turn in chosen), len(ranked) - len(chosen)


def _speaker(message: Mapping[str, Any]) -> str:
    """Who said a turn: its role, and for a channel message from someone named, who."""
    role = str(message.get("role") or "")
    sender = message.get("source_user")
    return f"{role} ({sender})" if role == "user" and isinstance(sender, str) and sender else role


def _window(said: str, at: int) -> str:
    """At most :data:`TURN_CHARS` of *said*, starting a little before *at*, where the words were
    said, cut at whole words and marked where it was cut."""
    if len(said) <= TURN_CHARS:
        return said
    start = max(0, min(at - _LEAD_CHARS, len(said) - TURN_CHARS))
    end = start + TURN_CHARS
    piece = said[start:end]
    if start > 0 and " " in piece:
        piece = piece.split(" ", 1)[1]
    if end < len(said) and " " in piece:
        piece = piece.rsplit(" ", 1)[0]
    return f"{'…' if start > 0 else ''}{piece}{'…' if end < len(said) else ''}"


def _when(stamp: str, zone: tzinfo, form: str) -> str:
    """The instant *stamp* in *zone*, written in *form*; ``""`` for no stamp, or one that does not
    parse."""
    if not stamp:
        return ""
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(zone).strftime(form)
