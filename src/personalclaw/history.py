"""Persistent conversation history — JSONL per session + LLM consolidation.

Session files: ~/.personalclaw/sessions/{safe_key}.jsonl
Each entry tracks provenance (source_thread, source_user) for citation.

**A session file is the user's record, and nothing here shortens it.** No size cap, no
rotation, no background rewrite: what bounds cost is what a READER takes — ``recent`` and
:meth:`ConversationLog.history_for_model` return a window, the consolidator reads past its
offset. Background compression (``bg_compress``) shortens only what the MODEL reads, through
a derived record beside the transcript (``{safe_key}.summary.json``, see :func:`model_view`)
that deleting loses nothing from.

``sessions/archive/{safe_key}__{stamp}.jsonl`` holds lines that EARLIER versions trimmed out
of a session (size rotation and background compression both used to). Nothing writes there
any more and nothing prunes it: for a chat trimmed before this, a batch may be the only copy
of the lines it holds. A batch goes when the chat it came from is deleted. Any surface that
lists these files owes the reader both facts: that a row is a slice of one session rather
than the session, and that it is a leftover of an earlier version (#464).
"""

import asyncio
import hashlib
import itertools
import json
import logging
import math
import os
import re
import threading
import time as _time
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple

from personalclaw.atomic_write import atomic_write
from personalclaw.concurrency import single_flight
from personalclaw.config import loader as config_loader
from personalclaw.guardrails.incident import incident_active
from personalclaw.instants import as_instant, utc_iso, utc_now_iso
from personalclaw.security import (
    MaskConflict,
    keep_masked_lines,
    redact_credentials,
    redact_exfiltration_urls,
)
from personalclaw.sel import sel
from personalclaw.skills import AutoSkillProvenance


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from personalclaw.memory import MemoryStore
    from personalclaw.memory_service import MemoryService
    from personalclaw.skills import SkillsLoader
    from personalclaw.vector_memory import VectorMemoryStore

logger = logging.getLogger(__name__)

SESSIONS_DIR_NAME = "sessions"
ARCHIVE_DIR_NAME = "archive"
#: The metadata-line key a conversation an app started records its app under
#: (``_ChatSession.created_by_app``): written by ``chat_persistence.save_session_to_history``, read
#: back by ``DashboardState.session_creating_app``, the session-creation chokepoint and the
#: consolidator (``HistoryConsolidator._as_its_work``).
CREATED_BY_APP_META_KEY = "created_by_app"
#: The background-compression record beside a transcript: ``{safe_key}.summary.json``.
SUMMARY_SUFFIX = ".summary.json"
_CONSOLIDATION_THRESHOLD = 30  # preferences/projects update threshold (messages)
SEARCH_MIN_CHARS = 2  # shortest query string that triggers backend search
_TITLE_BOOST = 10  # field-boost multiplier for title matches in search_sessions
_SEARCH_SCAN_WINDOW = 500  # cap files scanned per search to bound I/O
#: How much of a message a direct read's snippet shows around the match, before and after it —
#: short before, so the match is on screen in the chat list's one truncated line.
_SNIPPET_BEFORE = 48
_SNIPPET_AFTER = 96


def match_snippet(texts: list[str], needle: str) -> str:
    """The passage around the first place *needle* (already casefolded) is said in *texts*, with
    the match marked ``<<…>>`` as the search index marks its own; ``""`` when no text says it.

    What a chat a direct read found shows of why it matched, the way an index hit does. Matched by
    full case folding, as the read counts matches, so the passage is cut from the text as it was
    written: where folding keeps a text's length every character folds to one and the offsets
    agree; otherwise each folded character is mapped back to the one it came from.
    """
    for text in texts:
        folded = text.casefold()
        at = folded.find(needle)
        if at < 0:
            continue
        if len(folded) == len(text):
            start, end = at, at + len(needle)
        else:
            origin = [i for i, ch in enumerate(text) for _ in ch.casefold()]
            start, end = origin[at], origin[at + len(needle) - 1] + 1
        lead = re.sub(r"\s+", " ", text[max(0, start - _SNIPPET_BEFORE) : start])
        tail = re.sub(r"\s+", " ", text[end : end + _SNIPPET_AFTER])
        # A word the cut went through is dropped rather than shown in part.
        cut_lead, cut_tail = start > _SNIPPET_BEFORE, end + _SNIPPET_AFTER < len(text)
        if cut_lead and " " in lead:
            lead = lead.split(" ", 1)[1]
        if cut_tail and " " in tail:
            tail = tail.rsplit(" ", 1)[0]
        before, after = ("…" if cut_lead else ""), ("…" if cut_tail else "")
        return f"{before}{lead}<<{text[start:end]}>>{tail}{after}"
    return ""


def _live_restricted(session_key: str) -> bool:
    """Whether the in-process registry currently marks this session restricted.

    Complements the persisted `memory_mode`: a session marked incognito after some
    of its lines were written still reads as "persistent" on disk, so checking only
    the metadata would let content search surface it.
    """
    if not session_key:
        return False
    try:
        from personalclaw import session_restrictions

        return bool(session_restrictions.is_restricted(session_key))
    except Exception:  # noqa: BLE001 — an unavailable registry must not open the gate
        return False


def _marked_mode(session_key: str) -> str:
    """The restricted mode the in-process registry marks ``session_key`` with, or ``""``."""
    from personalclaw import session_restrictions

    if session_restrictions.is_temporary(session_key):
        return "temporary"
    if session_restrictions.is_incognito(session_key):
        return "incognito"
    return ""


def _sessions_dir() -> Path:
    return config_dir() / SESSIONS_DIR_NAME


def _archive_dir(base: Path | None = None) -> Path:
    return (base or _sessions_dir()) / ARCHIVE_DIR_NAME


def _is_archive_batch_of(name: str, safe: str) -> bool:
    """Whether *name* is an archive batch an earlier version wrote for the safe key *safe*.

    Batches are named ``{safe_key}__{YYYYMMDD-HHMMSS}[-n].jsonl``. A key may itself contain
    ``__``, so a bare prefix match would also claim another chat's batches; the stamp after
    the delimiter is what ties the name to exactly this key.
    """
    return re.fullmatch(re.escape(safe) + r"__\d{8}-\d{6}(?:-\d+)?\.jsonl", name) is not None


# ── What the MODEL reads: background compression's derived record ──

#: The rows a conversation hands the model: its turns. Everything else in a transcript (tool
#: rows, approvals, stop cards, errors) is shown to the user and never restored as history.
TURN_ROLES = ("user", "assistant")
#: The entries of a model view (:func:`model_view`): a background summary, then turns.
MODEL_VIEW_ROLES = ("summary", *TURN_ROLES)


def _turns(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m.get("role") in TURN_ROLES]


def span_digest(turns: list[dict]) -> str:
    """A digest of what a summary record stands in for: each turn's role and text, in order."""
    h = hashlib.sha256()
    for m in turns:
        h.update(json.dumps([m.get("role", ""), str(m.get("content", ""))]).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def summary_record(
    messages: list[dict],
    stamp: tuple[int, int],
    *,
    summary: str,
    summarized: int,
    reduced: int,
    reduced_cap: int,
) -> dict:
    """The record background compression keeps beside a transcript (``{key}.summary.json``).

    ``summarized`` and ``reduced`` count the conversation's TURNS (user/assistant rows),
    not the file's lines: the model reads ``summary`` in place of the first ``summarized``
    turns and turns ``summarized..reduced`` capped to ``reduced_cap`` characters, for as
    long as ``digest`` still matches the first ``reduced`` turns. Counting turns is what
    lets one record apply to every source of them — the file, or a resident session's
    buffer. ``stamp`` is the transcript's ``(mtime_ns, size)`` when it was read, so a pass
    can tell it already summarized this exact file without reading it again.
    """
    return {
        "stamp": [int(stamp[0]), int(stamp[1])],
        "turns": len(_turns(messages)),
        "summary": summary,
        "summarized": summarized,
        "reduced": reduced,
        "reduced_cap": reduced_cap,
        "digest": span_digest(_turns(messages)[:reduced]),
        "created_at": utc_now_iso(),
    }


def summary_holds(record: dict, messages: list[dict]) -> bool:
    """Whether *record*'s summary still describes *messages*: the span it covers is unchanged.

    An edit, an undo, a regenerate or a switched variant inside the span changes its digest,
    and a shorter conversation no longer has the span at all. Either way the summary is
    stale and the model reads the turns themselves.
    """
    if not record.get("summary"):
        return False
    try:
        summarized = int(record["summarized"])
        reduced = int(record["reduced"])
        cap = int(record["reduced_cap"])
    except (KeyError, TypeError, ValueError):
        return False
    turns = _turns(messages)
    if not (0 < summarized <= reduced <= len(turns)) or cap <= 0:
        return False
    return record.get("digest") == span_digest(turns[:reduced])


def model_view(messages: list[dict], record: dict | None) -> list[dict]:
    """The conversation as the MODEL reads it: ``[{role, content}]`` of its turns, in order.

    While *record* holds (:func:`summary_holds`), one ``{"role": "summary"}`` entry stands in
    for the turns it summarized and the turns after them are read capped to
    ``reduced_cap`` characters. Every later turn, including every one written since the
    record was made, is read as written. *messages* is only ever READ here: the file, or a
    session's buffer — any list whose turns are the conversation's.
    """
    turns = _turns(messages)
    view: list[dict] = []
    start = 0
    if record is not None and summary_holds(record, turns):
        summarized, start = int(record["summarized"]), int(record["reduced"])
        cap = int(record["reduced_cap"])
        view.append({"role": "summary", "content": str(record["summary"])})
        for m in turns[summarized:start]:
            content = str(m.get("content", ""))
            if len(content) > cap:
                content = content[:cap] + " …"
            view.append({"role": m["role"], "content": content})
    view.extend({"role": m["role"], "content": m.get("content", "")} for m in turns[start:])
    return view


def model_window(view: list[dict], max_messages: int) -> list[dict]:
    """The last *max_messages* entries of a model view, for a reader with a message budget.

    A summary the window would cut stays as the first entry: it is the only thing left
    that tells the model how the conversation started.
    """
    if len(view) <= max_messages:
        return view
    if max_messages > 1 and view[0].get("role") == "summary":
        return [view[0], *view[len(view) - max_messages + 1 :]]
    return view[-max_messages:] if max_messages > 0 else []


def _safe_key(key: str) -> str:
    """Convert a session key (e.g. a channel thread_ts) to a safe filename."""
    return re.sub(r"[^\w\-.]", "_", key)


def session_path(key: str) -> Path:
    """Where session ``key``'s transcript lives in the active home."""
    return _sessions_dir() / f"{_safe_key(key)}.jsonl"


def import_conversation(
    key: str, *, metadata: dict, messages: list[dict], modified: float | None = None
) -> Path:
    """Create the transcript of a conversation brought over from another tool.

    Written once and whole, in the shape every chat's transcript has — the metadata line, then a
    line per message — and never over an existing one (``FileExistsError``): an import adds to
    the history and replaces nothing in it. ``modified`` (seconds since the epoch) dates the file
    as the conversation was last held, which is where the history lists it.
    """
    path = session_path(key)
    if path.exists():
        raise FileExistsError(f"a transcript for session {key!r} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(message) for message in messages]
    # The count the chat list shows, with the bytes of the lines it counts, as the dashboard's
    # save records them (`_recorded_count`): without them, listing a history of imported chats
    # read every line of every one.
    head = {
        "_type": "metadata",
        **metadata,
        "message_count": len(lines),
        "message_bytes": sum(len(line.encode("utf-8")) + 1 for line in lines),
    }
    atomic_write(path, "\n".join([json.dumps(head), *lines]) + "\n")
    if modified is not None:
        os.utime(path, (modified, modified))
    return path


# ── What the chat list reads of each transcript ──

#: How many of a transcript's first lines the title fallback looks through for a first prompt.
_TITLE_SCAN_LINES = 21


@dataclass(frozen=True)
class _Head:
    """What the chat list reads of one transcript: its metadata line, how many messages it holds,
    and — when the metadata names no title — its first prompt.

    ``meta`` is the metadata line (empty when the first line is not one) and ``head_bytes`` that
    line's length. A head read for its metadata alone (:meth:`ConversationLog.get_metadata`) is
    not ``complete``: ``messages`` and ``first_prompt`` are ``None`` until a listing reads them.
    """

    meta: Mapping[str, Any]
    head_bytes: int
    messages: int | None
    first_prompt: str | None
    complete: bool


def _with_instants(record: dict) -> dict:
    """*record*, a parsed transcript line, with each time it holds read as an instant.

    A message's ``ts`` and the metadata's ``*_at`` times were written as this machine's local
    time with no offset until stamps carried one, and a reader in another zone read them hours
    off. Each is read here as the instant it was (:func:`personalclaw.instants.as_instant`), so
    every reader of a transcript sees the same one; a stamp with an offset keeps its text.
    """
    for key, value in record.items():
        if (key == "ts" or key.endswith("_at")) and isinstance(value, str):
            record[key] = as_instant(value)
    return record


def _metadata_line(first: bytes) -> dict:
    """The metadata a transcript's first line holds, or ``{}`` when it is not a metadata line."""
    if not first.strip():
        return {}
    try:
        data = json.loads(first)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("_type") != "metadata":
        return {}
    return _with_instants(data)


def read_memory_mode(path: Path) -> str | None:
    """The memory mode the transcript at ``path`` records, read from its first line.

    ``None`` when it records none: there is no transcript, or its first line is a message rather
    than metadata, or its metadata names no mode (a transcript written before modes existed, or by
    a channel that has none). :data:`~personalclaw.memory_writes.UNREADABLE` when the transcript
    is there and its first line cannot be read or parsed, which keeps nothing: a mode that cannot
    be read is not taken for ``persistent``. Read fresh on every call; it is one line.
    """
    from personalclaw.memory_writes import UNREADABLE

    try:
        with open(path, "rb") as f:
            first = f.readline()
    except FileNotFoundError:
        return None
    except OSError:
        return UNREADABLE
    if not first.strip():
        return None
    try:
        data = json.loads(first)
    except ValueError:
        return UNREADABLE
    if not isinstance(data, dict):
        return UNREADABLE
    if data.get("_type") != "metadata":
        return None
    mode = data.get("memory_mode")
    if mode is None:
        return None
    return mode if isinstance(mode, str) else UNREADABLE


def _prompt_of(data: object) -> str:
    """A message line's text when it is the user's, as the title fallback takes it."""
    if isinstance(data, dict) and data.get("role") == "user":
        content = data.get("content")
        if isinstance(content, str) and content:
            return content[:80]
    return ""


def _recorded_count(meta: Mapping[str, Any], message_bytes: int) -> int | None:
    """The message count the metadata records, while the file still holds exactly the message
    bytes it was recorded with — the dashboard's save and the import both record them. A writer
    that appended since (a channel app's ``append``) changed those bytes: ``None``, count them."""
    recorded, recorded_bytes = meta.get("message_count"), meta.get("message_bytes")
    if type(recorded) is int and type(recorded_bytes) is int and recorded_bytes == message_bytes:
        return recorded
    return None


def _read_head(path: Path, size: int, *, listing: bool) -> _Head | None:
    """Read what the chat list needs of one transcript in ONE open: the metadata line and, for a
    ``listing``, the message count and title fallback too. ``None`` when it cannot be read.

    The count is read from the metadata when it records one (:func:`_recorded_count`), and
    counted otherwise; the title fallback is the first user message in the first
    :data:`_TITLE_SCAN_LINES` lines, for a transcript whose metadata names no title.
    """
    try:
        with open(path, "rb") as f:
            first = f.readline()
            meta = _metadata_line(first)
            if not listing:
                return _Head(MappingProxyType(meta), len(first), None, None, False)
            recorded = _recorded_count(meta, size - len(first))
            need_title = not meta.get("title")
            counted, prompt = 0, ""
            if recorded is None or need_title:
                for number, raw in enumerate(itertools.chain([first], f)):
                    if number == 0 and meta:
                        continue  # the metadata line itself
                    if recorded is not None and (prompt or number >= _TITLE_SCAN_LINES):
                        break
                    message = _message_line(raw)
                    if message is None:
                        continue
                    counted += 1
                    if need_title and not prompt and number < _TITLE_SCAN_LINES:
                        prompt = _prompt_of(message)
    except OSError:
        return None
    messages = recorded if recorded is not None else counted
    return _Head(MappingProxyType(meta), len(first), messages, prompt, True)


def _message_line(raw: bytes) -> dict | None:
    """A transcript line as :meth:`ConversationLog._read_messages` reads it: a message, or
    ``None`` for a blank, unparsable or metadata line."""
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("_type") == "metadata":
        return None
    return data


def _signature(stat: os.stat_result) -> tuple[int, int, int]:
    """What changes when a transcript does: every write replaces it (a new inode) or grows it."""
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns)


class _TranscriptHeads:
    """Each transcript's :class:`_Head`, kept for as long as the file is unchanged.

    ONE store for every :class:`ConversationLog` in the process — the dashboard's, the search
    index's, a script's — so a log made after another lists without opening a transcript again.
    Two logs each keeping their own had every new one read all of them: 24,000 opens to list
    12,005 chats, which the search index's pass paid every five minutes (measured). Keyed by the
    file's path and valid for its :func:`_signature`; it holds metadata lines, never a message.
    """

    #: A bound far past any real history: cleared whole when crossed.
    LIMIT = 200_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kept: dict[str, tuple[tuple[int, int, int], _Head]] = {}

    def get(self, path: str, signature: tuple[int, int, int]) -> _Head | None:
        with self._lock:
            kept = self._kept.get(path)
        return kept[1] if kept is not None and kept[0] == signature else None

    def keep(self, path: str, signature: tuple[int, int, int], head: _Head) -> None:
        with self._lock:
            if len(self._kept) >= self.LIMIT:
                self._kept.clear()
            self._kept[path] = (signature, head)

    def drop(self, path: str) -> None:
        with self._lock:
            self._kept.pop(path, None)

    def clear(self) -> None:
        with self._lock:
            self._kept.clear()


#: The process's transcript heads, shared by every conversation log.
_HEADS = _TranscriptHeads()

#: The home's chat list, as last listed: beside the ``sessions`` directory it describes.
LISTING_FILE = "session_listing.json"
_LISTING_VERSION = 1
#: A listing that had to open this many transcripts writes the listing file back. A handful
#: changed since (one chat's turn) costs the next process that many opens, not a rewrite of
#: the whole file each time; a restart after a bulk change pays once and records it.
_LISTING_REWRITE_OPENS = 32
_LISTING_LOADED: set[str] = set()
_LISTING_LOCK = threading.Lock()


def _load_listing(sessions_dir: Path) -> None:
    """Seed :data:`_HEADS` from the home's listing file, once per process.

    A gateway that has just started has read no transcript yet, and listing 12,005 chats then
    opened every one of them — 1.8 s on a machine whose security agent makes each open cost
    85 µs (measured). Each entry is the head a listing read, with the signature of the file it
    read it from, so it counts only while that file is unchanged; the file is derived and
    disposable, and one that is missing, unreadable or of another version is ignored.
    """
    key = str(sessions_dir)
    with _LISTING_LOCK:
        if key in _LISTING_LOADED:
            return
        _LISTING_LOADED.add(key)
    try:
        payload = json.loads((sessions_dir.parent / LISTING_FILE).read_bytes())
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict) or payload.get("version") != _LISTING_VERSION:
        return
    heads = payload.get("heads")
    if not isinstance(heads, dict):
        return
    for name, row in heads.items():
        try:
            ino, size, mtime_ns, head_bytes, messages, first_prompt, meta = row
        except (TypeError, ValueError):
            continue
        if not (
            isinstance(name, str)
            and all(type(v) is int for v in (ino, size, mtime_ns, head_bytes))
            and (messages is None or type(messages) is int)
            and isinstance(first_prompt, str)
            and isinstance(meta, dict)
        ):
            continue
        path = os.path.join(key, name)
        if _HEADS.get(path, (ino, size, mtime_ns)) is None:
            meta = _with_instants(meta)  # a listing written before stamps carried offsets
            head = _Head(MappingProxyType(meta), head_bytes, messages, first_prompt, True)
            _HEADS.keep(path, (ino, size, mtime_ns), head)


def _save_listing(sessions_dir: Path, heads: list[tuple[str, tuple[int, int, int], _Head]]) -> None:
    """Write the home's listing file: each listed transcript's head, with its signature.

    A restricted chat's head is left out — its metadata stays in its own transcript and nowhere
    else — and costs the next process one open.
    """
    rows = {
        name: [*signature, head.head_bytes, head.messages, head.first_prompt, dict(head.meta)]
        for name, signature, head in heads
        if head.complete and head.meta.get("memory_mode") not in ("incognito", "temporary")
    }
    payload = {"version": _LISTING_VERSION, "heads": rows}
    try:
        atomic_write(
            sessions_dir.parent / LISTING_FILE,
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        )
    except OSError:
        logger.debug("could not write the chat listing file", exc_info=True)


def speaker_of(msg: dict) -> str:
    """The per-message author, or ``""`` for the human and for every pre-``speaker`` line.

    The tolerant half of :meth:`ConversationLog.append`'s ``speaker`` argument. Every
    message line written before AGENT-ROOMS, and every line a non-room caller writes,
    carries no ``speaker`` key at all — so the absent field must read as the human
    rather than as a fault. Defined here, beside the writer, so the two ends of the
    field cannot drift apart.
    """
    value = msg.get("speaker", "")
    return value if isinstance(value, str) else ""


class ConversationLog:
    """Append-only JSONL conversation store with provenance. It never shortens a transcript."""

    def __init__(self, base_dir: Path | None = None):
        self._dir = base_dir or _sessions_dir()
        # mtime-based message cache: key → (mtime, messages). What a listing reads of each
        # transcript is process-wide instead (`_HEADS`), so a new log lists for free.
        self._msg_cache: dict[str, tuple[float, list[dict]]] = {}

    def init(self) -> None:
        """Create sessions directory if missing."""
        self._dir.mkdir(parents=True, exist_ok=True)

    def is_home_log(self) -> bool:
        """Whether this is the active home's own transcript store (``<home>/sessions``), as
        opposed to one rooted elsewhere by an explicit ``base_dir`` — a room, an eval cell, a
        test's or a script's scratch directory. Home-wide derived state (the session-search
        index) describes only the home's own store."""
        try:
            return self._dir.resolve() == _sessions_dir().resolve()
        except OSError:
            return False

    def _path(self, key: str) -> Path:
        return self._dir / f"{_safe_key(key)}.jsonl"

    def has_log(self, key: str) -> bool:
        """Return True if a conversation log file exists for *key*."""
        return self._path(key).exists()

    def recorded_memory_mode(self, key: str) -> str | None:
        """The memory mode *key*'s transcript records (see :func:`read_memory_mode`)."""
        return read_memory_mode(self._path(key))

    def append(
        self,
        key: str,
        role: str,
        content: str,
        tools: list[str] | None = None,
        source_thread: str | None = None,
        source_user: str | None = None,
        agent: str | None = None,
        tab_id: str | None = None,
        speaker: str = "",
    ) -> None:
        """Append a message with optional provenance to the session log.

        If the session file does not yet exist, it will be created with an
        initial metadata line.  When *agent* is supplied, the agent name is
        recorded in that metadata so the session can be resumed under the
        correct agent later.  (Has no effect if the file already exists;
        use :meth:`update_metadata` to change the agent after creation.)

        *speaker* names the PER-MESSAGE author and is the one field a shared
        transcript needs that a per-participant session does not (AGENT-ROOMS C1).
        It is distinct from *agent*, which lands in the file's metadata line on
        creation only and therefore cannot vary line to line, and from
        *source_user*, which means a human participant on a channel. Written only
        when non-empty, so every existing session file and every non-room caller
        produces byte-identical lines; read back through :func:`speaker_of`, which
        answers ``""`` for the absent field.

        A session a channel marked Incognito or Temporary in the in-process registry has that
        mode recorded in its metadata line here, so it outlives the process: after a restart the
        registry is empty and the transcript is what says the session keeps nothing.
        """
        path = self._path(key)
        marked = _marked_mode(key)
        if not path.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            meta: dict = {
                "_type": "metadata",
                "created_at": utc_now_iso(),
                "last_consolidated": 0,
            }
            if agent:
                meta["agent"] = agent
            if tab_id:
                meta["tab_id"] = tab_id
            if marked:
                meta["memory_mode"] = marked
            atomic_write(path, json.dumps(meta) + "\n")
        elif marked and read_memory_mode(path) != marked:
            self.update_metadata(key, {"memory_mode": marked})

        msg: dict = {
            "role": role,
            "content": content,
            "ts": utc_now_iso(),
        }
        if tools:
            msg["tools"] = tools
        if source_thread:
            msg["source_thread"] = source_thread
        if source_user:
            msg["source_user"] = source_user
        if speaker:
            msg["speaker"] = speaker

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg) + "\n")

        # Invalidate cache since file changed
        self._invalidate_cache(key)

    def recent(
        self,
        key: str,
        max_messages: int = 20,
        roles: set[str] | None = None,
    ) -> list[dict]:
        """Return last *max_messages* entries as ``[{role, content}]``.

        When *roles* is provided, only messages with matching roles are
        counted toward the limit.  This filters out low-signal entries
        (e.g. tool display titles) so the budget is spent on user and
        assistant content.
        """
        messages = self._read_messages(key)
        if roles:
            messages = [m for m in messages if m["role"] in roles]
        return [{"role": m["role"], "content": m["content"]} for m in messages[-max_messages:]]

    def get_unconsolidated(self, key: str) -> tuple[list[dict], int]:
        """Return (messages_after_last_consolidated, total_message_count)."""
        messages = self._read_messages(key)
        offset = self._read_metadata(key).get("last_consolidated", 0)
        return messages[offset:], len(messages)

    def mark_consolidated(self, key: str, offset: int) -> None:
        """Rewrite metadata line with updated last_consolidated offset."""
        path = self._path(key)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if not lines:
            return
        meta = json.loads(lines[0])
        meta["last_consolidated"] = offset
        meta["updated_at"] = utc_now_iso()
        lines[0] = json.dumps(meta) + "\n"
        atomic_write(path, "".join(lines))
        self._invalidate_cache(key)

    def unconsolidated_count(self, key: str) -> int:
        """Count messages not yet processed by the consolidator."""
        messages = self._read_messages(key)
        offset = self._read_metadata(key).get("last_consolidated", 0)
        return max(0, len(messages) - offset)

    def load_transcript(self, key: str) -> str:
        """Load full session as formatted text for LLM summarization."""
        messages = self._read_messages(key)
        if not messages:
            return ""
        lines: list[str] = []
        for m in messages:
            role = m["role"].title()
            lines.append(f"{role}: {m['content']}")
        return "\n\n".join(lines)

    @staticmethod
    def _canonical_key(key: str) -> str:
        """Collapse stacked ``dashboard_`` prefixes to a single one.

        Files like ``dashboard_dashboard_chat-1-123`` are duplicates of
        ``dashboard_chat-1-123`` caused by resume round-trips.  Return
        the canonical (single-prefix) form so callers can deduplicate.
        """
        if not key.startswith("dashboard_"):
            return key
        stripped = key
        while stripped.startswith("dashboard_"):
            stripped = stripped[len("dashboard_") :]
        return f"dashboard_{stripped}" if stripped else key

    def list_sessions(self) -> list[dict]:
        """Return metadata for all session files, newest first.

        Deduplicates stacked ``dashboard_`` prefix files, keeping the most recently modified
        version. See :meth:`list_sessions_with_metadata`, which this is without the metadata.
        """
        return [entry for entry, _meta in self.list_sessions_with_metadata()]

    def list_sessions_with_metadata(self) -> list[tuple[dict, Mapping[str, Any]]]:
        """Each session, newest first, with its metadata line (read-only): ``(entry, meta)``.

        What it costs is one ``stat`` per transcript, and one open of each transcript that
        changed since this PROCESS last read it (:data:`_HEADS`): the metadata line, which also
        says how many messages the chat holds. Measured at 12,005 chats: listing opened every
        transcript twice (24,110 opens, 2.1 s), and so did every new log — the search index's
        pass paid it every five minutes — while ``get_metadata`` read each file whole.
        """
        if not self._dir.exists():
            return []
        home = self.is_home_log()
        if home:
            _load_listing(self._dir)
        by_canon: dict[str, tuple[dict, Mapping[str, Any]]] = {}
        try:
            with os.scandir(self._dir) as found:
                entries = [item for item in found if item.name.endswith(".jsonl")]
        except OSError:
            return []
        heads: list[tuple[str, tuple[int, int, int], _Head]] = []
        opened = 0
        for item in entries:
            # Skip symlinks — these are handoff aliases pointing to the real session
            if item.is_symlink():
                continue
            try:
                if not item.is_file():
                    continue
                stat = item.stat()
            except OSError:
                continue
            signature = _signature(stat)
            kept = _HEADS.get(item.path, signature)
            head = self._head(item.path, stat, listing=True)
            if head is not None:
                heads.append((item.name, signature, head))
                opened += kept is not head
            meta: Mapping[str, Any] = head.meta if head is not None else MappingProxyType({})
            key = item.name[: -len(".jsonl")]
            entry: dict = {
                "key": key,
                "messages": head.messages if head is not None else None,
                "modified": stat.st_mtime,
                "created": meta.get("created_at") or utc_iso(stat.st_mtime),
                "memory_mode": meta.get("memory_mode", "persistent"),
                "title": meta.get("title")
                or (head.first_prompt if head is not None else "")
                or key,
            }
            if meta.get("agent"):
                entry["agent"] = meta["agent"]
            # Deduplicate: keep newer entry per canonical key
            canon = self._canonical_key(key)
            existing = by_canon.get(canon)
            if existing is None or stat.st_mtime >= existing[0]["modified"]:
                by_canon[canon] = (entry, meta)
        if home and opened >= _LISTING_REWRITE_OPENS:
            _save_listing(self._dir, heads)
        listed = list(by_canon.values())
        listed.sort(key=lambda pair: pair[0].get("modified", 0), reverse=True)
        return listed

    @staticmethod
    def _head(path: str, stat: os.stat_result, *, listing: bool) -> _Head | None:
        """*path*'s :class:`_Head`: the one this process kept while the file is unchanged, or read
        now — for a ``listing``, completed with the message count and title fallback."""
        signature = _signature(stat)
        kept = _HEADS.get(path, signature)
        if kept is not None:
            if kept.complete or not listing:
                return kept
            # Read for its metadata alone: a recorded count and a title complete it unread.
            recorded = _recorded_count(kept.meta, stat.st_size - kept.head_bytes)
            if recorded is not None and kept.meta.get("title"):
                completed = _Head(kept.meta, kept.head_bytes, recorded, "", True)
                _HEADS.keep(path, signature, completed)
                return completed
        head = _read_head(Path(path), stat.st_size, listing=listing)
        if head is not None:
            _HEADS.keep(path, signature, head)
        return head

    def search_sessions(
        self, query: str, limit: int = 50, *, keys: "list[str] | None" = None
    ) -> list[dict]:
        """Return session metadata for files whose message content matches *query*, each with
        the ``snippet`` of where it was said (:func:`match_snippet`) when that is what matched.

        Case-insensitive substring match over each message's ``content``
        field using full Unicode case folding via :meth:`str.casefold`
        (so e.g. German ``ß`` folds to ``ss``).  Matching only on parsed
        ``content`` avoids false positives from JSON structural elements
        (e.g. the word ``"user"`` matching every ``"role": "user"`` line).

        Ranking (higher is better)::

            score = (title_matches * _TITLE_BOOST)
                  + (content_matches / sqrt(1 + doc_chars / 1024))

        Title matches get a strong field boost - titles are short and
        intentional, so a hit there is strong evidence.  Content matches
        are normalized by a sqrt length factor so a long session with a
        casual mention doesn't outrank a short, focused one.  (Simpler
        than BM25's ``(1-b) + b*(dl/avgdl)`` because we avoid the
        two-pass scan needed for corpus stats.)  Sessions with zero
        matches are dropped.  Ties break by recency (existing
        ``list_sessions`` order - newest first).  Caps results at *limit*.
        Only the ``_SEARCH_SCAN_WINDOW`` most recent files are scored, so
        I/O stays bounded even with hundreds of sessions — unless *keys* names the sessions
        to read, which reads exactly those (the chats a search index has not caught up with).
        """
        if not query or limit <= 0 or not self._dir.exists():
            return []
        needle = query.casefold()
        scored: list[tuple[float, int, dict]] = []  # (score, -rank, meta)
        listed = self.list_sessions()
        if keys is None:
            listed = listed[:_SEARCH_SCAN_WINDOW]
        else:
            wanted = set(keys)
            listed = [meta for meta in listed if meta["key"] in wanted]
        for rank, meta in enumerate(listed):
            # Restricted (incognito/temporary) sessions promise to stay out of
            # history — they must not be discoverable through content search.
            # Both sources are consulted: the persisted mode survives a restart,
            # while the live registry knows about a session marked restricted AFTER
            # its transcript lines were already written (the metadata still says
            # "persistent" in that window, so the mode alone would leak it).
            if meta.get("memory_mode") in ("incognito", "temporary"):
                continue
            if _live_restricted(meta.get("key", "")):
                continue
            path = self._path(meta["key"])
            content_hits = 0
            doc_chars = 0
            texts: list[str] = []
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        # Always parse - a raw-line fast path would miss
                        # queries containing JSON-escapable chars
                        # (backslash, quote, newline) because the on-disk
                        # line has escaped forms while the parsed content
                        # has literal chars.  Skip unparseable lines to
                        # avoid matching on structural JSON keys.
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        raw = obj.get("content") if isinstance(obj, dict) else None
                        text = raw if isinstance(raw, str) else ""
                        if text:
                            # Accumulate length only for content text so
                            # the length normalizer's denominator matches
                            # the hit counter's numerator (no penalty for
                            # verbose metadata / tool-call lines).
                            doc_chars += len(text)
                            texts.append(text)
            except OSError:
                continue
            # Casefold + count once per file instead of per line: a 200-line
            # session produces one temporary casefolded string instead of 200,
            # bounding GC pressure under rapid-fire search keystrokes.  The
            # ``\x00`` separator can't appear in user queries, so cross-line
            # false matches are impossible.
            if texts:
                content_hits = "\x00".join(texts).casefold().count(needle)
            title_hits = (meta.get("title") or "").casefold().count(needle)
            if not title_hits and not content_hits:
                continue
            length_norm = math.sqrt(1 + doc_chars / 1024)
            score = title_hits * _TITLE_BOOST + content_hits / length_norm
            # Why it matched, as an index hit says it: the passage, marked. A title match is shown
            # by the title itself, so only a match in what was said carries one. A copy, so the
            # listing's own row is never changed by a search.
            if content_hits:
                meta = {**meta, "snippet": match_snippet(texts, needle)}
            # Negate rank so a smaller (newer) rank wins ties after score desc sort.
            scored.append((score, -rank, meta))
        scored.sort(reverse=True)
        return [meta for _, _, meta in scored[:limit]]

    def read_messages(self, key: str) -> list[dict]:
        """Public access to session messages."""
        return self._read_messages(key)

    def has_session(self, key: str) -> bool:
        """Whether a session file exists for *key*.

        A pure existence probe (no read, no cache): the wire layer needs to
        distinguish "no such session" from "a session with no messages yet",
        and ``read_messages`` alone answers ``[]`` for both.
        """
        return self._path(key).exists()

    def read_messages_chained(self, key: str) -> list[dict]:
        """Read messages from all session files sharing the same ``tab_id``.

        Returns messages from the current file only if no ``tab_id`` is set
        (legacy sessions).  Otherwise finds all sibling files with the same
        ``tab_id``, sorts chronologically, and concatenates their messages.

        Uses a ``_tab_id_index`` cache (built lazily, invalidated on save)
        to avoid scanning every file on each call.
        """
        meta = self.get_metadata(key)
        tid = meta.get("tab_id")
        if not tid:
            return self._read_messages(key)
        if not hasattr(self, "_tab_id_index"):
            self._tab_id_index: dict[str, list[str]] = {}
        if tid not in self._tab_id_index:
            self._rebuild_tab_id_index()
            if tid not in self._tab_id_index:
                self._tab_id_index[tid] = []  # sentinel: prevent repeated rebuilds
        keys = self._tab_id_index.get(tid, [])
        if not keys:
            return self._read_messages(key)
        all_msgs: list[dict] = []
        for k in keys:
            all_msgs.extend(self._read_messages(k))
        return all_msgs or self._read_messages(key)

    def _rebuild_tab_id_index(self) -> None:
        """Scan all dashboard session files and build tab_id → [keys] mapping."""
        index: dict[str, list[str]] = {}
        for path in sorted(self._dir.glob("dashboard_chat-*.jsonl")):
            try:
                with path.open(encoding="utf-8") as f:
                    first_line = f.readline()
                m = json.loads(first_line)
                tid = m.get("tab_id")
                if tid:
                    index.setdefault(tid, []).append(path.stem.replace("_", ":", 1))
            except Exception:
                continue
        self._tab_id_index = index

    def invalidate_tab_id_cache(self) -> None:
        """Clear the tab_id index so it's rebuilt on next chained read."""
        if hasattr(self, "_tab_id_index"):
            self._tab_id_index.clear()

    def delete_session(self, key: str) -> bool:
        """Delete a session file. Returns True if deleted.

        Also drops the session's full-text-search rows (SM-11): the transcript
        and the FTS index are separate stores, so without this a deleted chat's
        messages stayed searchable — a privacy hole. Best-effort: search-index
        failure must never block the deletion itself (the periodic
        ``session_search.purge_orphans`` sweep is the compensator).

        The same holds for the two other places a chat's words live: its background
        summary, and any batch of lines an earlier version trimmed out of it into
        ``archive/``. Nothing prunes that directory any more, so a deleted chat would
        otherwise leave its trimmed lines behind for good.
        """
        path = self._path(key)
        if path.exists():
            path.unlink()
            self._invalidate_cache(key)
            self.invalidate_tab_id_cache()
            self._forget_search_rows(key)
            self.delete_summary(key)
            self._forget_archive_batches(key)
            return True
        return False

    def _forget_archive_batches(self, key: str) -> None:
        """Unlink the archive batches earlier versions trimmed out of *key*'s transcript."""
        adir = _archive_dir(self._dir)
        safe = _safe_key(key)
        try:
            batches = [
                p for p in adir.glob(f"{safe}__*.jsonl") if _is_archive_batch_of(p.name, safe)
            ]
        except OSError:
            return
        for p in batches:
            try:
                p.unlink()
            except OSError:
                logger.warning("delete_session: could not remove archive batch %s", p.name)

    # ── The background summary: derived data beside the transcript ──

    def transcript_stamp(self, key: str) -> tuple[int, int] | None:
        """``(mtime_ns, size)`` of *key*'s transcript, or ``None`` when there is none."""
        try:
            st = self._path(key).stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def summary_path(self, key: str) -> Path:
        return self._dir / f"{_safe_key(key)}{SUMMARY_SUFFIX}"

    def read_summary(self, key: str) -> dict | None:
        """*key*'s background-summary record, or ``None``.

        Fails OPEN to "no summary": the record is derived, so a missing or unreadable one
        only means the model reads the transcript itself, as it would for a chat that was
        never summarized.
        """
        try:
            data = json.loads(self.summary_path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def write_summary(self, key: str, record: dict) -> None:
        atomic_write(self.summary_path(key), json.dumps(record))

    def delete_summary(self, key: str) -> None:
        try:
            self.summary_path(key).unlink(missing_ok=True)
        except OSError:
            logger.warning("could not remove the background summary for %s", key)

    def prune_orphan_summaries(self) -> int:
        """Unlink every summary whose transcript is gone, whatever removed the transcript."""
        removed = 0
        try:
            records = list(self._dir.glob(f"*{SUMMARY_SUFFIX}"))
        except OSError:
            return 0
        for p in records:
            transcript = self._dir / f"{p.name[: -len(SUMMARY_SUFFIX)]}.jsonl"
            if transcript.exists():
                continue
            try:
                p.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def history_for_model(self, key: str, max_messages: int) -> list[dict]:
        """What the model reads of *key*'s persisted transcript: :func:`model_view` through
        :func:`model_window`. For a reader with no live session to take the turns from."""
        return model_window(
            model_view(self._read_messages(key), self.read_summary(key)), max_messages
        )

    @staticmethod
    def _forget_search_rows(key: str) -> None:
        """Drop *key*'s FTS rows. Callers pass either ``dashboard:chat-X`` or
        ``dashboard_chat-X``; the index keeps a chat under its file's name alone and forgets it
        by either (``session_search.forget_session``)."""
        try:
            from personalclaw import session_search

            session_search.forget_session(key)
        except Exception:  # noqa: BLE001 — search cleanup must never block deletion
            logger.debug("delete_session: FTS forget failed for %s", key, exc_info=True)

    def set_title(self, key: str, title: str) -> None:
        """Persist a title into the session's metadata line."""
        self.update_metadata(key, {"title": title})

    def update_metadata(self, key: str, fields: dict) -> None:
        """Merge *fields* into the session's metadata line and persist."""
        path = self._path(key)
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if not lines:
            return
        try:
            meta = json.loads(lines[0])
            if meta.get("_type") != "metadata":
                return
        except json.JSONDecodeError:
            return
        meta.update(fields)
        lines[0] = json.dumps(meta) + "\n"
        atomic_write(path, "".join(lines), fsync=True)
        self._invalidate_cache(key)

    def _read_messages(self, key: str) -> list[dict]:
        """Read all non-metadata entries from a session JSONL file.

        Uses mtime-based caching to avoid re-parsing unchanged files.
        """
        path = self._path(key)
        if not path.exists():
            self._msg_cache.pop(key, None)
            return []
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []
        cached = self._msg_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        messages: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("_type") == "metadata":
                continue
            messages.append(_with_instants(data))
        self._msg_cache[key] = (mtime, messages)
        return messages

    def _invalidate_cache(self, key: str) -> None:
        """Invalidate caches for a key after a write operation — and, for the home's own
        transcripts, tell the search index to read it again."""
        self._msg_cache.pop(key, None)
        _HEADS.drop(str(self._path(key)))
        if self.is_home_log():
            from personalclaw import session_search

            session_search.INDEXER.note_changed(_safe_key(key))

    def get_metadata(self, key: str) -> dict:
        """Return session metadata for *key*."""
        return self._read_metadata(key)

    def _read_metadata(self, key: str) -> dict:
        """The metadata line (first line) of a session JSONL file, as a dict of the caller's own.

        Only the first line is read, once while the file is unchanged (:data:`_HEADS`). It read
        the whole file to take its first line: 352 MB to answer this for 12,005 chats (measured).
        """
        path = str(self._path(key))
        try:
            stat = os.stat(path)
        except OSError:
            return {}
        head = self._head(path, stat, listing=False)
        return dict(head.meta) if head is not None else {}


# ── Module-level helpers for auto skill eligibility ──
#
# Kept at module level so they're trivially unit-testable without
# instantiating HistoryConsolidator.

# Canonical tool titles that indicate a read targeting a sensitive path.
# Supplements is_sensitive_path() and is_sensitive_bash_command() which
# handle the actual runtime blocking — this is a second-layer defense
# that refuses to extract a skill if the session tried to access a
# sensitive path, even when the attempt was denied at hook time.
_SENSITIVE_TOOL_PATTERNS: tuple[str, ...] = (
    ".aws/",
    ".ssh/",
    ".gnupg/",
    ".gpg/",
    ".docker/config",
    ".kube/config",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    ".personalclaw/.env",
    "169.254.169.254",  # IMDS
)


def consolidation_line(m: dict) -> str:
    """One transcript row as consolidation reads it.

    Consolidation learns a person's corrections and preferences from these lines, so a person's row
    gives the words they typed as theirs (``own_words``) and what else it holds as material that is
    not: each block they pasted, and the whole text of a row sent in their turn with nothing of
    theirs in it (an automation's result, plan mode carrying an approved plan back). What else the
    platform put into their row (the dictation note, a merge's header, plan mode's wrapper around
    their feedback) is left out, and a saved prompt they ran is named, not quoted."""
    from personalclaw.own_words import RAN_PROMPT, own_words, pasted_blocks

    stamp = f"[{str(m.get('ts', '?'))[:16]}]"
    tools = f" [tools: {', '.join(m['tools'])}]" if m.get("tools") else ""
    if m.get("role") != "user":
        return f"{stamp} {str(m.get('role', '')).upper()}{tools}: {m.get('content', '')}"
    raw_meta = m.get("meta")
    meta: dict = raw_meta if isinstance(raw_meta, dict) else {}
    typed = own_words(m)
    ran = meta.get(RAN_PROMPT) if isinstance(meta.get(RAN_PROMPT), dict) else None
    lines: list[str] = []
    if ran:
        said = f": {typed}" if typed else ""
        lines.append(f"{stamp} USER{tools} ran the saved prompt @{ran.get('name', '')}{said}")
    elif typed:
        lines.append(f"{stamp} USER{tools}: {typed}")
    lines += [
        f"{stamp} PASTED BY THE USER (material, not their words): {block}"
        for block in pasted_blocks(meta)
    ]
    if not lines:
        lines.append(f"{stamp} SENT IN THE USER'S TURN, NOT TYPED BY THEM: {m.get('content', '')}")
    return "\n".join(lines)


_TOOL_ROLES: frozenset[str] = frozenset({"tool", "tool_call", "tool_result"})


def _count_tool_call_messages(messages: list[dict]) -> int:
    """Count messages that represent tool invocations under either schema.

    Two recording formats exist:
    - Channel-pipeline schema: assistant messages carry a ``tools`` list field.
    - Dashboard pipeline: separate messages with ``role`` in {"tool", "tool_call",
      "tool_result"} and the tool name embedded in ``content``.

    A message matching EITHER condition counts once (no double-counting).
    """
    count = 0
    for msg in messages:
        tools = msg.get("tools")
        if isinstance(tools, list) and tools:
            count += 1
        elif msg.get("role") in _TOOL_ROLES:
            count += 1
    return count


def _session_touched_sensitive(messages: list[dict]) -> bool:
    """Return True if any tool call in the session referenced a sensitive path.

    Checks both recording schemas:
    - Channel-pipeline schema: substring match over each entry in ``msg["tools"]`` list.
    - Dashboard: substring match over ``content`` when ``role`` indicates a tool event.

    Designed to be conservative — a false positive just means we skip
    auto-creation for this session.
    """
    for msg in messages:
        # Channel-pipeline schema: tools list on assistant messages
        tools = msg.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, str):
                    continue
                lower = tool.lower()
                for pattern in _SENSITIVE_TOOL_PATTERNS:
                    if pattern in lower:
                        return True
        # Dashboard schema: role="tool" with tool info in content
        if msg.get("role") in _TOOL_ROLES:
            content = msg.get("content", "")
            if isinstance(content, str):
                lower = content.lower()
                for pattern in _SENSITIVE_TOOL_PATTERNS:
                    if pattern in lower:
                        return True
    return False


def _kept_lines(rewrite: object, stored: str) -> str:
    """*rewrite* of the *stored* markdown with each hidden value kept, or "" to leave it.

    "" when there is no rewrite, and when it would move, copy or rewrite a line holding a value
    the model was shown as a marker (``keep_masked_lines``): the file stays as stored.
    """
    if not isinstance(rewrite, str) or not rewrite:
        return ""
    try:
        return keep_masked_lines(rewrite, stored)
    except MaskConflict:
        logger.info("Consolidation rewrite would move a hidden value; left as stored")
        return ""


class _KeptMemory(NamedTuple):
    """The memory a chat keeps, as consolidation writes it (:meth:`HistoryConsolidator._kept_in`):
    its markdown store, the service over its record store, and that record store."""

    markdown: "MemoryStore"
    service: "MemoryService"
    records: "VectorMemoryStore | None"


class HistoryConsolidator:
    """Summarize old messages into structured memory via LLM.

    Two consolidation paths:
    - Preferences/projects: triggered by message count (30 messages)
    - Daily history: triggered by idle time (3h default) or end of day

    Both are model calls nobody typed, so neither runs while incident mode is on. A skipped pass
    advances no offset: the messages are still unconsolidated, and the first pass after the switch
    is off takes them.
    """

    def __init__(
        self,
        log: ConversationLog,
        memory: "MemoryStore",
        *,
        history_idle_secs: float = 3 * 3600,
        vector_store: "VectorMemoryStore | None" = None,
        migrated: bool = False,
        # ── Auto skill creation ──
        # All-default so callers unaware of this feature continue to work.
        skills_loader: "SkillsLoader | None" = None,
        auto_skills_enabled: bool = False,
        auto_refine_enabled: bool = False,
        auto_min_tool_calls: int = 5,
        auto_similarity_threshold: float = 0.85,
    ) -> None:
        self._log = log
        self._memory = memory
        self._history_idle_secs = history_idle_secs
        self._vector_store = vector_store
        self._memory_service: "MemoryService | None" = None  # lazily built over the store
        self._migrated = migrated
        self._skills_loader = skills_loader
        self._auto_skills_enabled = auto_skills_enabled
        self._auto_refine_enabled = auto_refine_enabled
        self._auto_min_tool_calls = auto_min_tool_calls
        self._auto_similarity_threshold = auto_similarity_threshold
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]
        # Track last activity per session for idle-based history consolidation
        self._last_activity: dict[str, float] = {}
        self._history_consolidated: dict[str, float] = {}  # key → last history consolidation time
        # Consolidations no model answered (``owed_chores``): the failure of each key's last call,
        # and the ending sessions whose seal waits for the consolidation that runs before it.
        self._unanswered: dict[str, BaseException] = {}
        self._owed_seal: set[str] = set()
        # Separate offset for prefs-only consolidation (doesn't advance main offset)
        self._prefs_offset: dict[str, int] = {}
        # Autonomous episodic→semantic promotion: count history consolidations to
        # fire promotion every Nth, with a min-interval floor (monotonic time).
        self._consolidation_count: int = 0
        self._last_promote_monotonic: float = 0.0

    @property
    def _svc(self):
        """The MemoryService over this consolidator's record/vector store (L3).

        The consolidator is an L3 write path — it operates directly on the
        record store, so the service wraps the vector store it was handed rather
        than a markdown projection. Cached; no-op when no store is wired."""
        if self._memory_service is None:
            from personalclaw.memory_service import MemoryService

            self._memory_service = MemoryService.over_vector_store(self._vector_store)
        return self._memory_service

    @property
    def _proactive_commitments(self) -> bool:
        """Whether proactive commitment extraction is opted in (M5e). Read fresh
        from config each call so the Settings toggle takes effect live, like the
        auto-promote gate — the feature is OFF by default (creepy when wrong)."""
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().memory.proactive_commitments)

    @property
    def _proactive_commitments_max(self) -> int:
        """Hard per-day cap on active proactive commitments per agent (M5e)."""
        from personalclaw.config.loader import AppConfig

        return max(1, int(AppConfig.load().memory.proactive_commitments_max_per_day))

    @property
    def _holder_attribution(self) -> bool:
        """Whether the holder axis is opted in (MEMORY-GRAPH-AND-VAULT §4.2 — MGAV-5).

        Read fresh per call for the same reason as the commitments gate: the toggle is a
        Settings switch, not a boot-time decision. OFF means the extraction fragment is
        not composed AND no holder is persisted — the axis is absent, not merely hidden.
        """
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().memory.holder_attribution)

    def maybe_consolidate(self, key: str) -> None:
        """Fire preferences/projects consolidation if message threshold exceeded."""
        self._last_activity[key] = _time.time()
        if key in self._running or incident_active():
            return
        total = len(self._log._read_messages(key))
        prefs_off = self._prefs_offset.get(key, 0)
        if total - prefs_off < _CONSOLIDATION_THRESHOLD:
            return
        self._running.add(key)
        t = asyncio.create_task(self._consolidate(key, include_history=False))
        self._tasks.add(t)

        def _on_done(fut: asyncio.Task, k: str = key, off: int = total) -> None:  # type: ignore[type-arg]  # noqa: E501
            self._tasks.discard(fut)
            # A preferences pass is not owed: the history consolidation reads the same messages.
            self._unanswered.pop(k, None)
            if not fut.cancelled() and fut.exception() is None:
                self._prefs_offset[k] = off

        t.add_done_callback(_on_done)

    async def consolidate_now(self, key: str) -> bool:
        """Run history consolidation + auto-skill extraction for one session NOW.

        The synchronous-await public entry point for explicit triggers (idle-
        expiry callback, a channel "End session", `personalclaw consolidate`) — as
        opposed to the fire-and-forget idle poll. Always uses the **history**
        path (``include_history=True``), since auto-skill extraction is gated on
        it (see ``_consolidate`` ``auto_skills_eligible``). Respects the running
        guard so it never double-runs against the idle poll; ``_consolidate``
        clears the guard in its ``finally``. Returns True if it ran, False if a
        consolidation was already in flight for this key, incident mode is on, or
        the session keeps nothing (:meth:`keeps_nothing_from`).
        """
        if key in self._running or incident_active() or self.keeps_nothing_from(key):
            return False
        self._running.add(key)
        await self._consolidate(key, include_history=True)
        return True

    def keeps_nothing_from(self, key: str) -> bool:
        """Whether session ``key`` (or the work asking) must leave nothing in long-term memory
        (:meth:`why_nothing_is_kept`). A pass over one never runs: not its model call, not its
        writes, not its seal."""
        return bool(self.why_nothing_is_kept(key))

    def why_nothing_is_kept(self, key: str) -> str:
        """Why nothing of session ``key`` is kept in long-term memory, ``""`` when it may be.

        Incognito and Temporary sessions, and any whose mode cannot be read
        (:func:`~personalclaw.memory_writes.blocks_memory_writes`), answer
        :data:`~personalclaw.memory_writes.REFUSAL`. A conversation an app started answers, in the
        app's words, when the app was not given your memory (:meth:`_as_its_work`).
        """
        from personalclaw import memory_writes

        with self._as_its_work(key):
            if memory_writes.writes_refused():
                return memory_writes.REFUSAL
            return memory_writes.app_change_refusal()

    def _as_its_work(self, key: str) -> "AbstractContextManager[None]":
        """The scope a pass over session ``key`` runs in (``memory_writes.derived_from``): the
        mode its transcript records, and the app that started it (its metadata line), whose work
        the pass is: what it writes names the app, and an app not given your memory has nothing
        kept. A metadata line that cannot be read names no app, and its mode then reads as
        unreadable, which keeps nothing."""
        from personalclaw import memory_writes

        app = self._log.get_metadata(key).get(CREATED_BY_APP_META_KEY, "")
        return memory_writes.derived_from(
            key,
            memory_mode=self._log.recorded_memory_mode(key),
            app=app if isinstance(app, str) else "",
        )

    # The explicit session-end seam (E11): an idle-expire / channel-end / CLI
    # trigger calls this. Distinct from the fire-and-forget poll so call sites
    # read intentionally ("consolidate this ending session") AND so SEALING only
    # fires at real session end (M5c) — never on a mid-session idle consolidation.
    async def consolidate_session(self, key: str) -> bool:
        """Consolidate an ENDING session, then SEAL it: distill the session's
        working memory into a durable in-scope record and sweep unpromoted
        session-scoped records (memory-architecture.md §3.5). Sealing deepens
        tier (working→episodic) at scope=session — it does NOT write to global;
        the heat gate (run on the maintenance cadence) is the only path to global.

        A consolidation no model answered is owed (``owed_chores``), and the seal waits for it:
        the session is sealed once its messages are consolidated, never before.

        An Incognito or Temporary session ends with nothing kept: no pass, no seal. So does a
        conversation an app started when the app was not given your memory."""
        nothing = self.why_nothing_is_kept(key)
        if nothing:
            logger.info("Session %s ended and nothing from it is kept: %s", key, nothing)
            return False
        ran = await self.consolidate_now(key)
        if self._owe_if_unanswered(key, ending=True):
            return ran
        self._seal(key)
        return ran

    def _seal(self, key: str) -> None:
        """Seal the ended session *key* in the memory it keeps (:meth:`_kept_in`) and mirror
        memory to the vault (:meth:`consolidate_session`). The seal is the session's own work
        (:meth:`_as_its_work`)."""
        from personalclaw import memory_locality

        kept = self._kept_in(memory_locality.chat_folder(self._log.get_metadata(key)))
        with self._as_its_work(key):
            try:
                swept = kept.service.seal_session(key) if kept is not None else 0
                if swept:
                    logger.info("Sealed session %s — swept %d unpromoted record(s)", key, swept)
            except Exception:
                logger.debug("session seal failed for %s", key, exc_info=True)
        # Mirror memory → markdown vault at the natural post-seal boundary (the
        # mem-fs-mirror freshness trigger). No-op when the vault is disabled;
        # never raises (best-effort, guarded internally).
        try:
            from personalclaw.memory_vault import mirror_after_consolidation

            mirror_after_consolidation(self._svc)
        except Exception:
            logger.debug("memory vault mirror failed for %s", key, exc_info=True)

    def _owe_if_unanswered(self, key: str, *, ending: bool) -> bool:
        """Owe *key*'s consolidation when no model answered its call and messages are left to
        consolidate (``owed_chores``); True when it is owed. *ending* also owes the seal of the
        session it ended (:meth:`consolidate_session`)."""
        failure = self._unanswered.pop(key, None)
        if failure is None or self._log.unconsolidated_count(key) < 1:
            return False
        if ending:
            self._owed_seal.add(key)
        from personalclaw import owed_chores

        owed_chores.owe(
            f"consolidation:{key}",
            f"the consolidation of {key}",
            partial(self._owed_consolidation, key),
        )
        return True

    async def _owed_consolidation(self, key: str) -> bool:
        """Consolidate *key* again, and seal it if it had ended (``owed_chores``). False while no
        model answers, or while another consolidation of it is running."""
        if key in self._running or incident_active():
            return False
        if self._log.unconsolidated_count(key) >= 1:
            await self.consolidate_now(key)
            if self._unanswered.pop(key, None) is not None:
                return False
        if key in self._owed_seal:
            self._owed_seal.discard(key)
            self._seal(key)
        return True

    def check_idle_sessions(self) -> None:
        """Check all tracked sessions for idle-based history consolidation."""
        if incident_active():
            return
        now = _time.time()
        for key, last in list(self._last_activity.items()):
            if (
                now - last < self._history_idle_secs
                or self._log.unconsolidated_count(key) < 1
                or now - self._history_consolidated.get(key, 0) < self._history_idle_secs
                or key in self._running
            ):
                continue
            self._running.add(key)
            captured_now = now
            t = asyncio.create_task(self._consolidate(key, include_history=True))
            self._tasks.add(t)

            def _on_idle_done(
                fut: asyncio.Task,  # type: ignore[type-arg]
                k: str = key,
                ts: float = captured_now,
            ) -> None:
                self._tasks.discard(fut)
                if not fut.cancelled() and fut.exception() is None:
                    self._history_consolidated[k] = ts
                    self._owe_if_unanswered(k, ending=False)

            t.add_done_callback(_on_idle_done)

    async def _consolidate(self, key: str, include_history: bool = True) -> None:
        """Run LLM consolidation for a session, single-flight across processes.

        The in-memory ``self._running`` guard prevents double-runs within this
        process; the :func:`single_flight` lock prevents a second process (the
        ``personalclaw consolidate`` CLI, the eval runner) from consolidating the
        same key concurrently — which would race on the history metadata offset,
        the vector store, and the lesson store. If another process holds the
        lock we skip (clearing the in-memory guard the caller set), since a
        concurrent consolidation of the same key is redundant, not queued work.

        Every trigger of a pass ends here, and the pass runs as deriving from ``key``, as the
        work of the app that started it if one did (:meth:`_as_its_work`): a session that keeps
        nothing (Incognito, Temporary, or a mode that cannot be read), and an app's conversation
        when the app was not given your memory, is skipped before its transcript is read or a
        model is called, and the stores refuse every write made in its name
        (:mod:`personalclaw.memory_writes`). What an app's pass writes names the app.
        """
        from personalclaw import memory_writes

        with self._as_its_work(key):
            if memory_writes.writes_refused():
                logger.info("Not consolidating %s: it keeps no memory", key)
                self._running.discard(key)
                return
            app_refused = memory_writes.app_change_refusal()
            if app_refused:
                logger.info("Not consolidating %s: %s", key, app_refused)
                self._running.discard(key)
                return
            with single_flight(f"consolidate:{key}") as acquired:
                if not acquired:
                    logger.info(
                        "Consolidation for %s already running in another process — skipping",
                        key,
                    )
                    self._running.discard(key)
                    return
                await self._consolidate_locked(key, include_history=include_history)

    def _kept_in(self, folder: str) -> _KeptMemory | None:
        """The memory a chat working in *folder* keeps (``memory_locality.chat_folder``): the
        folder's own partition, as every turn of the chat reads it, or the global memory, this
        consolidator's own, for no folder or the gateway's workspace. None when the folder was one
        of PersonalClaw's own and is gone: its partition went with it, so nothing more is kept."""
        from personalclaw import memory_locality

        if not memory_locality.is_local_partition(folder):
            return _KeptMemory(self._memory, self._svc, self._vector_store)
        if memory_locality.folder_is_gone(folder):
            return None
        from personalclaw.context import ContextBuilder
        from personalclaw.memory_service import service_for

        memory = ContextBuilder.get_memory_for(folder, writes=True)
        return _KeptMemory(memory, service_for(memory), memory.vector_store)

    async def _consolidate_locked(self, key: str, include_history: bool = True) -> None:
        """Run LLM consolidation for a session (holding the single-flight lock).

        Everything the pass keeps goes to the memory the chat keeps (:meth:`_kept_in`), which
        the chat's own turns read, except a lesson, which joins the global lesson list with the
        chat's reach (:meth:`_save_lessons`), and a proactive check-in, which the heartbeat
        delivers from the global memory (:meth:`_write_commitments`)."""
        from personalclaw import memory_locality

        try:
            unconsolidated, total = self._log.get_unconsolidated(key)
            if not unconsolidated:
                return

            meta = self._log.get_metadata(key)
            folder = memory_locality.chat_folder(meta)
            kept = self._kept_in(folder)
            if kept is None:
                logger.info("Not consolidating %s: the folder it worked in is gone", key)
                return
            memory, svc, vs = kept

            conversation = "\n".join(consolidation_line(m) for m in unconsolidated)

            current_prefs = memory.read_preferences()
            current_projects = memory.read_projects()

            # Build prompt keys dynamically based on consolidation type. Each key's
            # instruction prose is a bundled ``consolidation-key-*`` snippet; the
            # selection logic (which keys apply) stays here.
            from personalclaw.prompt_providers.runtime import render_snippet_block

            keys: list[str] = []
            if include_history:
                keys.append(render_snippet_block("consolidation-key-history"))

            # Structured memory extraction (when the record store is available)
            has_vector = svc.has_vector
            if has_vector:
                from personalclaw.vector_memory import is_fact_key

                # The facts, and only the facts: this list is what the model is asked to keep
                # current, and a row another writer owns (a procedural prior, the self-model's
                # evidence, a lesson, a slot) is not its to rewrite.
                current_semantic = [
                    e for e in svc.get_all_semantic() if is_fact_key(str(e.get("key") or ""))
                ]
                semantic_json = (
                    json.dumps(
                        [
                            {k: e[k] for k in ("key", "value_json", "confidence")}
                            for e in current_semantic
                        ],
                        indent=1,
                    )
                    if current_semantic
                    else "[]"
                )
                keys.append(render_snippet_block("consolidation-key-semantic"))
                keys.append(render_snippet_block("consolidation-key-episodic"))
                # Holder attribution: opt-in, so
                # the fragment is only composed when the axis is on. With it off the
                # extraction prompt is byte-identical to before and nothing downstream
                # persists a holder — the flag gates the whole axis, not just its display.
                if self._holder_attribution:
                    keys.append(render_snippet_block("consolidation-key-claims"))

            # Markdown memory (used when not migrated to structured memory)
            if not self._migrated:
                keys.append(render_snippet_block("consolidation-key-preferences"))
                keys.append(render_snippet_block("consolidation-key-projects"))

            if include_history:
                keys.append(render_snippet_block("consolidation-key-lessons"))

            # Agent self-persona (M5e): the agent's own positive growth notes —
            # always available on the history path. Distinct from lessons (which
            # record what NOT to do); this records who the agent is becoming.
            if include_history and has_vector:
                keys.append(render_snippet_block("consolidation-key-self-persona"))

            # Commitments (M5e — O-A4): inferred proactive check-ins. GUARDRAILED —
            # only extracted when the user opted in (off by default). The 'creepy
            # when wrong' class, so the prompt demands high-confidence + genuinely
            # useful time-bound follow-ups the user did NOT ask to be reminded of.
            if include_history and has_vector and self._proactive_commitments:
                keys.append(
                    render_snippet_block(
                        "consolidation-key-commitments",
                        {"max_commitments": self._proactive_commitments_max},
                    )
                )

            # ── Auto skill creation ──
            # Only eligible when the feature is enabled, we have a loader to
            # write to, we're on the history path (so prefs-only doesn't retrigger
            # extraction), and the session has enough tool calls to be non-trivial.
            auto_skills_eligible = (
                include_history
                and self._auto_skills_enabled
                and self._skills_loader is not None
                and _count_tool_call_messages(unconsolidated) >= self._auto_min_tool_calls
                and not _session_touched_sensitive(unconsolidated)
            )
            if auto_skills_eligible:
                keys.append(render_snippet_block("consolidation-key-new-skill"))
                if self._auto_refine_enabled:
                    keys.append(render_snippet_block("consolidation-key-refined-skill"))

            numbered = "\n\n".join(f"{i+1}. {k}" for i, k in enumerate(keys))
            # The envelope (intro + section ordering + closing instruction) is the
            # bundled ``task-memory-consolidation`` prompt; the optional context
            # sections are assembled here in the same order as before.
            semantic_block = (
                f"\n\n## Current Semantic Memory\n{semantic_json}" if has_vector else ""
            )
            markdown_blocks = ""
            if not self._migrated:
                markdown_blocks = (
                    f"\n\n## Current Preferences\n{current_prefs or '(empty)'}"
                    f"\n\n## Current Projects\n{current_projects or '(empty)'}"
                )
            from personalclaw.prompt_providers.runtime import render_use_case_prompt

            prompt = (
                render_use_case_prompt(
                    "memory_consolidation",
                    {
                        "numbered_keys": numbered,
                        "semantic_block": semantic_block,
                        "markdown_blocks": markdown_blocks,
                        "conversation": conversation,
                    },
                )
                or ""
            )

            result = await self._call_llm(prompt, key)
            if not result:
                return

            if entry := result.get("history_entry"):
                memory.append_history(entry)
                logger.info("Consolidated %d messages for %s", len(unconsolidated), key)
                # Session working memory (M5c): reuse this distilled summary as
                # the always-injected rolling session memory — one distillation
                # pass, not a second summarizer (decision #5). scope=session, so
                # it's injected every turn for THIS session and swept on seal.
                try:
                    svc.write_working_memory(key, entry)
                except Exception:
                    logger.debug("working-memory write failed for %s", key, exc_info=True)

            # Structured memory writes — the Extract→Gather→Decide restructure.
            # `result` above IS the Extract phase; the semantic half now runs through
            # formation (deterministic Gather, then ONE structured Decide call) instead of
            # being written straight in, so a contradiction becomes a supersession chain
            # with an undo rather than a second row nobody reconciles. Episodic writes are
            # unchanged — an episodic fragment is an event, and two accounts of the same
            # event do not contradict each other.
            if svc.has_vector:
                await self._form_semantic_memory(result, key, vs)
                self._write_episodic_memory(result, key, svc)

            # Markdown writes (skipped when migrated to structured memory). The model read both
            # files masked (a chore's prompt is masked, ``chores.run_chore``), so a rewrite keeps
            # each hidden value on the line it kept, and one that moves or rewrites one is not
            # applied.
            if not self._migrated:
                if prefs := _kept_lines(result.get("preferences_update"), current_prefs):
                    if prefs.strip() != current_prefs.strip():
                        memory.write_preferences(prefs)

                if projects := _kept_lines(result.get("projects_update"), current_projects):
                    if projects.strip() != current_projects.strip():
                        memory.write_projects(projects)

            if self._svc.has_vector and (raw_lessons := result.get("lessons")):
                self._save_lessons(raw_lessons, folder)

            # Agent self-persona + commitments (M5e) — agent-scoped. The agent
            # name is normalized to the canonical default when the session didn't
            # pin one (the common dashboard case), so capture keys on the SAME
            # string the context read path uses — otherwise writes and reads
            # disagree and nothing is ever surfaced. Best-effort; never blocks the
            # rest of consolidation. A check-in is delivered by the heartbeat from the global
            # memory and never recalled into a prompt, so it is kept there.
            if include_history:
                from personalclaw.agents.defaults import normalize_agent_name

                agent = normalize_agent_name(meta.get("agent"))
                if svc.has_vector:
                    self._write_self_persona(result, agent, svc)
                if self._svc.has_vector and self._proactive_commitments:
                    self._write_commitments(result, agent, key)

            # Auto skill creation / refinement.
            # Guarded by flag + eligibility — failures are logged, never fatal.
            if auto_skills_eligible:
                try:
                    self._process_auto_skills(result, key)
                except Exception:
                    logger.warning("Auto-skill processing failed for %s", key, exc_info=True)

            # Only advance the consolidated offset for history consolidation.
            # Prefs-only consolidation uses a separate in-memory offset.
            if include_history:
                self._log.mark_consolidated(key, total)
                # Autonomous self-learning: periodically promote repeated episodic
                # memories to durable semantic facts. Piggybacks on consolidation
                # (no new scheduler), guarded so a flood/stack can't happen.
                try:
                    self._maybe_promote_episodic(memory)
                except Exception:
                    logger.warning("Episodic promotion failed for %s", key, exc_info=True)
                # The maintenance below runs over the memory this pass kept: the records it
                # ages, promotes, collapses and digests are the ones the chat's turns read.
                # Category-TTL sweep: age out short-lived categorized memories
                # (debug/event/decision) on the same maintenance cadence. Durable
                # facts/prefs + user_explicit globals are never touched.
                try:
                    expired = svc.expire_by_category()
                    if expired:
                        logger.info("Category-TTL expired %d memory record(s)", expired)
                except Exception:
                    logger.debug("Category-TTL sweep failed for %s", key, exc_info=True)
                # Heat-gated promotion (M5c): the conservative GLOBAL gate — promote
                # in-scope records that earned cross-session heat to scope=global.
                # Runs HERE (maintenance cadence), never at session-end, so global
                # never fills with one-off session noise.
                try:
                    promoted_scope = svc.promote_by_heat()
                    if promoted_scope:
                        logger.info("Heat-promoted %d record(s) to global scope", promoted_scope)
                except Exception:
                    logger.debug("Heat promotion failed for %s", key, exc_info=True)
                # Failure-pattern synthesis (M5d): collapse clusters of same-root-
                # cause procedural failures into one prior so the class never
                # bloats into a tool-call log. The anti-noise mechanism.
                try:
                    synth = svc.synthesize_failures()
                    if synth:
                        logger.info("Synthesized %d procedural failure prior(s)", synth)
                except Exception:
                    logger.debug("Failure synthesis failed for %s", key, exc_info=True)
                # Daily-digest nodes (mem-tree, descoped): roll up each completed
                # day's episodic activity into one 'what happened on day D' record.
                # Idempotent (keyed by date) + extractive by default, so it adds no
                # LLM cost to the maintenance cadence.
                try:
                    digested = svc.build_daily_digest()
                    if digested:
                        logger.info("Built %d daily-digest node(s)", digested)
                except Exception:
                    logger.debug("Daily-digest build failed for %s", key, exc_info=True)
                # Push-reflex volunteer log: 90-day
                # retention on the same cadence. The log exists to compute a precision
                # ratio, not to be a permanent record of every turn's entity matches.
                try:
                    pruned_vol = svc.prune_volunteer_events(keep_days=90)
                    if pruned_vol:
                        logger.info("Pruned %d volunteer event(s)", pruned_vol)
                except Exception:
                    logger.debug("Volunteer-log prune failed for %s", key, exc_info=True)
                # External-agent capture retention: `capture/*.jsonl`
                # age out at `external_access.capture.retention_days` on THIS tick — the
                # "curator tick" `capture_store.prune`'s own docstring already named, while
                # nothing called it, so a shipped and round-tripped retention control
                # governed a function no schedule reached. Beside the volunteer prune
                # because both are retention sweeps, and BEFORE the curator below so a
                # later replay-mining pass sees an already-aged capture dir. Deliberately
                # NOT inside `_run_learning_curator`: retention is a data-hygiene
                # obligation the operator configured, not a learning feature, and gating it
                # on `learning.enabled` would make "I turned learning off" silently mean
                # "keep every captured transcript forever".
                try:
                    from personalclaw.inbound import capture_store

                    pruned_captures = capture_store.prune()
                    if pruned_captures:
                        logger.info("Pruned %d expired capture file(s)", pruned_captures)
                except Exception:
                    logger.debug("Capture prune failed for %s", key, exc_info=True)
                # Community topology: deterministic seeded
                # Louvain over mem_links, writing `community` into mem_link_stats. HERE
                # rather than in a loop of its own, and after the write paths above, so it
                # sees this consolidation's new links. Runs regardless of the injection
                # toggle: the column also feeds the graph visualization, and computing it
                # only when a display flag is on is how a "topology is empty" bug gets
                # blamed on Louvain instead of on the flag.
                try:
                    communities = svc.refresh_topology()
                    if communities:
                        logger.info("Topology: assigned %d entity communit(ies)", communities)
                except Exception:
                    logger.debug("Topology refresh failed for %s", key, exc_info=True)
                # Learning curator: age the learned library
                # on this same verified cadence. Deliberately NOT a new scheduler —
                # `skills/curator.run_aging` had no scheduled caller at all, which is
                # how a whole grooming pass came to exist and never run. Bounded batch,
                # reversible, refuses a mass cut; pattern analysis runs LAST so it sees
                # an already-cleaned set.
                try:
                    curated = self._run_learning_curator()
                    if curated:
                        logger.info("Learning curator: %s", curated)
                except Exception:
                    logger.debug("Learning curator failed for %s", key, exc_info=True)
                # Local A/B replay evidence: mine a few real turns
                # from the captured sessions and replay each one twice — baseline vs candidate
                # — for the pending skill/template proposals, attaching the pair to the card
                # the user decides from. AFTER the curator on purpose: the curator FILES
                # proposals, so running first would replay a queue missing this tick's own
                # additions and they would wait a whole cadence for evidence. `await`ed
                # directly rather than fired as a task because this consolidation is already
                # the bounded background pass, and a detached task would outlive the
                # `_running` guard that stops two passes overlapping. Off unless the operator
                # set BOTH `learning.replay_enabled` and a positive
                # `learning.replay_max_dollars` — LLM spend on a maintenance tick is opt-in.
                try:
                    from personalclaw.learning import replay as replay_mod

                    replay_note = replay_mod.summarize_pass(await replay_mod.run_pass())
                    if replay_note:
                        logger.info("Learning replay: %s", replay_note)
                except Exception:
                    logger.debug("Learning replay pass failed for %s", key, exc_info=True)

        except Exception:
            logger.exception("Consolidation failed for %s", key)
            raise
        finally:
            self._running.discard(key)

    def _run_learning_curator(self) -> str:
        """One bounded curator tick over the learned library. Returns a summary or "".

        Builds candidates from the usage store rather than from the skills loader: the
        curator judges anything with recorded usage, and coupling it to one entity
        type is what made the previous version un-generalizable.

        Aging DECIDES; the entity's owner applies. So this reports and files review
        proposals, and does not itself rewrite skill frontmatter — that write belongs
        to the skills loader, which is the only thing that knows the file format.
        """
        from personalclaw.config.loader import AppConfig
        from personalclaw.learning import curator as curator_mod
        from personalclaw.learning.usage import UsageStore

        cfg = AppConfig.load().learning
        if not getattr(cfg, "enabled", True) or not getattr(cfg, "curator_enabled", True):
            return ""

        # LEARN-R18: grade any decision whose horizon has elapsed BEFORE the aging pass, so a
        # freshly-measured outcome is in the library the same tick it resolves. Inert unless a
        # vector store is wired (`_svc` degrades to null), and best-effort — a resolver failure
        # never blocks curation.
        outcomes_note = ""
        try:
            from personalclaw.learning import outcome_resolver

            rep = outcome_resolver.resolve(self._svc)
            if rep.get("resolved") or rep.get("unscored") or rep.get("inconclusive"):
                outcomes_note = (
                    f"outcomes resolved={rep['resolved']} unscored={rep['unscored']} "
                    f"inconclusive={rep['inconclusive']}"
                )
        except Exception:
            logger.debug("Outcome resolver failed", exc_info=True)

        # With the publish bets just graded, ask which work units nobody reads. Runs AFTER
        # the resolver on purpose — the sweep reads resolutions, so grading first means a cycle that
        # matured this tick is in the window rather than a tick late. Reports and PROPOSES only:
        # nothing here can pause or retire a work unit, because "nobody looked yet" and "nobody will
        # ever look" are different facts and only the user knows which.
        liveness_note = ""
        try:
            from personalclaw.learning import consumer_liveness

            rep = consumer_liveness.sweep()
            if rep.get("dormant") or rep.get("proposed"):
                liveness_note = (
                    f"consumer liveness dormant={rep['dormant']} proposed={rep['proposed']}"
                )
        except Exception:
            logger.debug("Consumer-liveness sweep failed", exc_info=True)

        # LEARN-R16 / criterion 9: grade every accepted change whose post-acceptance horizon has
        # elapsed. Reads the Run Ledger (not semantic memory) so it runs on every box regardless of
        # embedder; inert-by-data when nothing has been accepted, gated on `learning.attribution_*`
        # internally, best-effort — a grading failure never blocks curation. A HARMFUL verdict files
        # a revert PROPOSAL through the shared queue; nothing is ever applied here.
        attribution_note = ""
        try:
            from personalclaw.learning import attribution

            rep = attribution.grade_accepted_changes()
            if rep.get("graded") or rep.get("reverts"):
                attribution_note = f"attribution graded={rep['graded']} reverts={rep['reverts']}"
        except Exception:
            logger.debug("Attribution grading failed", exc_info=True)

        # LEARN-R4 / §2.5: "Events prune at 90d on the curator tick." Here rather than on its own
        # timer because the surfacing log is exactly the kind of high-volume, low-value,
        # independently-prunable data the curator tick already exists to age — a second cadence
        # would be a daemon to own for one DELETE.
        try:
            from personalclaw.learning.surfacing_events import SurfacingEventStore

            _events = SurfacingEventStore()
            try:
                _pruned = _events.prune()
            finally:
                _events.close()
            if _pruned:
                logger.debug("pruned %d surfacing events past retention", _pruned)
        except Exception:
            logger.debug("Surfacing-event prune failed", exc_info=True)

        store = UsageStore()
        try:
            records = [rec for kind in ("skill", "template") for rec in store.list_kind(kind)]
            candidates = [
                curator_mod.Candidate(
                    kind=rec.kind,
                    entity=rec.entity,
                    last_used_at=rec.last_used_at,
                    created_at=rec.first_seen_at,
                    stability=min(1.0, rec.used / 10.0),
                    pinned=rec.pinned,
                    source_type=rec.source_type,
                )
                for rec in records
            ]
            if not candidates:
                return "; ".join(p for p in (outcomes_note, liveness_note, attribution_note) if p)
            active_dates = store.active_days()
            report = curator_mod.run_aging(candidates, active_dates=active_dates, mode="")
            curator_mod.file_review_proposals(report)
            # LEARN-R6f: heat-earned promotion. The multi-gate (`usage.promotion_ready`) had no
            # caller anywhere in the tree — a gate nothing runs is a gate that never refuses
            # anything, and the bare "surfaced ≥2×" it replaced was still what the ladder
            # effectively used. This is its live cadence: the same verified tick as the aging
            # pass, filing SUGGESTIONS into the shared queue and promoting nothing itself.
            promo_note = ""
            suggestions = curator_mod.promotion_suggestions(records, active_dates=active_dates)
            filed = curator_mod.file_promotion_suggestions(suggestions)
            if filed:
                promo_note = f"promotion suggestions filed={filed}"
            summary = report.summary() if (report.changed or report.review_proposals) else ""
            return "; ".join(
                p
                for p in (summary, promo_note, outcomes_note, liveness_note, attribution_note)
                if p
            )
        finally:
            store.close()

    def _maybe_promote_episodic(self, memory) -> None:
        """Run autonomous episodic→semantic promotion every Nth consolidation.

        Anti-runaway: gated on the every-N counter AND a 30-min min-interval AND a
        cross-process single-flight lock (so the gateway + CLI can't both promote
        at once) AND a per-run cap. Each run is SEL-audited (no silent caps).
        """
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load().memory
        if not getattr(cfg, "auto_promote_enabled", True):
            return
        from personalclaw.memory_service import service_for

        svc = service_for(memory)
        if not svc.can_vector_search:
            return
        self._consolidation_count += 1
        if self._consolidation_count % max(1, cfg.auto_promote_every_n) != 0:
            return
        now = _time.monotonic()
        if self._last_promote_monotonic and now - self._last_promote_monotonic < 1800:
            return  # min-interval floor (30 min) regardless of consolidation rate

        from personalclaw.concurrency import single_flight

        with single_flight("mem-promote-episodic") as acquired:
            if not acquired:
                return  # another process is already promoting
            self._last_promote_monotonic = now
            promoted = svc.promote_episodic_patterns(max_promotions=cfg.auto_promote_max_per_run)
        if promoted:
            logger.info("Autonomous promotion: %d episodic→semantic", promoted)
            try:
                sel().log_api_access(
                    caller="consolidator:auto_promote",
                    operation="memory.promote_episodic",
                    outcome="allowed",
                    resources=f"promoted={promoted}",
                )
            except Exception:
                logger.debug("SEL audit failed for auto-promotion", exc_info=True)

    def _save_lessons(self, raw: object, folder: str) -> None:
        """Save extracted lessons from consolidation into memory.db ``lesson.*``.

        The global lesson list, where Settings → Memory → Lessons and ``memory_remember`` keep
        lessons too, with the reach of the chat working in *folder*: one in a folder of its own
        teaches that folder (``scope=workspace``), where its chats follow it, and any other chat
        teaches every chat. The record store is the sole lesson store (dedup-aware; a store with
        no embedder still persists). Nothing to do when no store is wired."""
        if not isinstance(raw, list) or not self._svc.has_vector:
            return
        from personalclaw import memory_locality

        reach: dict[str, Any] = {}
        if memory_locality.is_local_partition(folder):
            from personalclaw.memory_record import MemoryScope
            from personalclaw.memory_service import normalize_workspace_ref

            ref = normalize_workspace_ref(folder)
            if not ref:
                return  # a folder no absolute path names is matched by no reader
            reach = {"scope": MemoryScope.WORKSPACE, "scope_ref": ref}
        count = 0
        for item in raw:
            if isinstance(item, dict) and item.get("rule"):
                ok = self._svc.write_lesson(
                    rule=item["rule"],
                    category=item.get("category", "knowledge"),
                    negative=item.get("negative"),
                    source="consolidation",
                    **reach,
                )
                if ok:
                    count += 1
        if count:
            logger.info("Extracted %d lesson(s) from chat (record store)", count)

    async def _form_semantic_memory(
        self, result: dict, key: str, vs: "VectorMemoryStore | None"
    ) -> None:
        """The Gather → Decide → apply half of memory formation (§4.1 — MGAV-5), over the
        record store *vs* of the memory chat *key* keeps (:meth:`_kept_in`).

        Extract already happened: ``result["semantic"]`` is its output. This method adds
        the deterministic Gather pass, ONE structured Decide call for the whole batch, and
        the verdict application (``ADD``/``UPDATE``/``SUPERSEDE``/``NOOP``, with unsure
        contradictions kept as two flagged rows).

        Every failure path degrades to the pre-MGAV-5 behavior — write every candidate as
        an ADD — because a formation failure must cost adjudication, never the memories:

        * no candidates, or none with an overlap → no Decide call at all (the common
          case; this is what keeps "one extra cheap call" honest rather than "one extra
          call per consolidation");
        * no Decide prompt (snippet unresolvable) or no model → ADD everything;
        * unparseable/garbled verdicts → ADD everything, per candidate.
        """
        if vs is None:
            return
        from personalclaw import memory_formation
        from personalclaw.vector_memory import _MAX_SEMANTIC_PER_CONSOLIDATION

        semantic_items = result.get("semantic")
        if not isinstance(semantic_items, list) or not semantic_items:
            return
        source = f"consolidation:{key}"
        candidates = memory_formation.candidates_from_extract(
            semantic_items,
            holder_attribution=self._holder_attribution,
            limit=_MAX_SEMANTIC_PER_CONSOLIDATION,
        )
        if not candidates:
            return

        decisions: dict[int, memory_formation.Decision] = {}
        degraded = False
        try:
            memory_formation.gather(vs, candidates)
            prompt = memory_formation.build_decide_prompt(candidates)
            if prompt:
                decide_result = await self._call_llm(prompt, key)
                decisions = memory_formation.parse_decisions(decide_result, candidates)
                degraded = not decisions
        except Exception:
            # Gather reads the store and Decide talks to a model; either can fail on a
            # box where the other half is fine. Losing the session's facts because we
            # could not decide about them would be strictly worse than not deciding.
            logger.warning("Memory formation degraded for %s — writing as ADD", key, exc_info=True)
            decisions, degraded = {}, True

        report = memory_formation.apply_decisions(
            vs,
            candidates,
            decisions,
            source=source,
            holder_attribution=self._holder_attribution,
        )
        report.degraded = report.degraded or degraded
        logger.info("Memory formation for %s: %s", key, report.summary())

    def _write_episodic_memory(self, result: dict, key: str, svc: "MemoryService") -> None:
        """Write episodic entries from a consolidation result (unchanged by MGAV-5) through
        *svc*, the memory chat *key* keeps (:meth:`_kept_in`)."""
        if not svc.has_vector:
            return
        from personalclaw.vector_memory import _MAX_EPISODIC_PER_CONSOLIDATION

        source = f"consolidation:{key}"
        episodic_items = result.get("episodic")
        if isinstance(episodic_items, list):
            written = 0
            for item in episodic_items[:_MAX_EPISODIC_PER_CONSOLIDATION]:
                if not isinstance(item, dict) or "text" not in item:
                    continue
                ep_ok = svc.write_episodic(
                    item["text"],
                    conversation_id=key,
                    tags=item.get("tags", []),
                    importance=float(item.get("importance", 0.5)),
                    source=source,
                )
                if ep_ok:
                    written += 1
            if written:
                logger.info("Wrote %d episodic entries from consolidation", written)

    def _write_self_persona(self, result: dict, agent: str, svc: "MemoryService") -> None:
        """Write extracted agent self-persona traits (M5e), scoped to ``agent``, through *svc*:
        the memory the chat keeps, whose turns read them (:meth:`_kept_in`).

        Best-effort: a positive self-model injected always-on for this agent.
        Each trait is redacted + bounded; recurrence reinforces heat via the
        service's per-trait key."""
        traits = result.get("self_persona")
        if not isinstance(traits, list):
            return
        written = 0
        for trait in traits[:4]:
            if not isinstance(trait, str):
                continue
            safe, _ = redact_exfiltration_urls(trait.strip()[:120])
            safe, _ = redact_credentials(safe)
            if not safe:
                continue
            try:
                if svc.record_persona(agent=agent, trait=safe):
                    written += 1
            except Exception:
                logger.debug("self_persona write failed", exc_info=True)
        if written:
            logger.info("Wrote %d self-persona trait(s) for agent %s", written, agent)

    def _write_commitments(self, result: dict, agent: str, key: str) -> None:
        """Write extracted proactive commitments (M5e — O-A4), GUARDRAILED.

        Only reached when the user opted in (``_proactive_commitments``). The
        service enforces the real guardrails (enabled + confidence>=0.8 + per-day
        cap); this path supplies them and redacts the check-in text. The channel
        is the heartbeat deliver-target (``dashboard:<bare-session-name>``) so the
        check-in lands back in the originating conversation; the consolidation key
        is the FULL session key (``dashboard:chat-…``), so strip its prefix before
        re-prefixing or the deliver-target doubles (``dashboard:dashboard:…``) and
        the session never resolves."""
        commitments = result.get("commitments")
        if not isinstance(commitments, list):
            return
        channel = f"dashboard:{key.removeprefix('dashboard:').removeprefix('dashboard_')}"
        max_per_day = self._proactive_commitments_max
        written = 0
        for item in commitments[:max_per_day]:
            if not isinstance(item, dict):
                continue
            text = item.get("text", "")
            due = item.get("due_window", "")
            if not isinstance(text, str) or not isinstance(due, str):
                continue
            try:
                conf = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            safe, _ = redact_exfiltration_urls(text.strip()[:300])
            safe, _ = redact_credentials(safe)
            if not safe or not due:
                continue
            try:
                if self._svc.record_commitment(
                    agent=agent,
                    channel=channel,
                    text=safe,
                    due_window=due,
                    confidence=conf,
                    enabled=True,
                    max_per_day=max_per_day,
                ):
                    written += 1
            except Exception:
                logger.debug("commitment write failed", exc_info=True)
        if written:
            logger.info("Recorded %d proactive commitment(s) for agent %s", written, agent)

    def _process_auto_skills(self, result: dict, key: str) -> None:
        """Extract + write auto-generated skills from the consolidation result.

        Handles both ``new_skill`` and ``refined_skill`` result keys.  Each
        is validated, redacted via ``security.redact_*``, then deduped
        against existing skills (for new creation) before being written
        through ``SkillsLoader``.  Every successful write emits a SEL audit
        event via ``sel().log_tool_invocation``.
        """
        if self._skills_loader is None:
            return

        def _redact(text: object) -> str:
            """Run the same two-pass redaction used for channel/dashboard output."""
            if not isinstance(text, str):
                return ""
            safe, _ = redact_exfiltration_urls(text)
            safe, _ = redact_credentials(safe)
            return safe

        # Create path
        new_skill = result.get("new_skill")
        if isinstance(new_skill, dict):
            slug = str(new_skill.get("slug", "")).strip()
            description = _redact(new_skill.get("description", ""))
            triggers = _redact(new_skill.get("triggers", ""))
            procedure_md = _redact(new_skill.get("procedure_md", ""))
            if not (slug and description and procedure_md):
                # Required fields missing (or stripped empty by redaction).
                # Audit the rejection so operators can see that a create
                # attempt happened but lacked the minimum inputs.
                logger.info(
                    "Auto-skill create skipped: empty slug/description/procedure "
                    "after redaction (slug=%r)",
                    slug,
                )
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_create",
                    tool_kind="skills",
                    outcome="rejected",
                    metadata={
                        "slug": slug or "(empty)",
                        "reason": "empty_after_redaction",
                    },
                )
            else:
                similar = self._skills_loader.find_similar(
                    description, threshold=self._auto_similarity_threshold
                )
                if similar:
                    logger.info(
                        "Auto-skill synthesis skipped: '%s' overlaps existing skill '%s'",
                        slug,
                        similar,
                    )
                    sel().log_tool_invocation(
                        session_key=key,
                        tool_name="auto_skill_create",
                        tool_kind="skills",
                        outcome="rejected",
                        metadata={
                            "slug": slug,
                            "reason": "similar_exists",
                            "existing": similar,
                        },
                    )
                else:
                    # Propose-only (skill-evolution-proposal-only): autonomous
                    # synthesis NEVER writes live — it enqueues a human-reviewable
                    # proposal. A person accepts (→ live auto/ skill) or rejects it
                    # from the Skill-proposals inbox. No auto-install path exists.
                    from personalclaw.skills import proposals as _proposals

                    # 🔴 A proposal for a slug that ALREADY EXISTS is a REFINEMENT, and it has to
                    # say so. `find_similar` above compares DESCRIPTIONS, so a differently-worded
                    # synthesis for an installed skill passes that guard — and then went out as
                    # `kind="new"` (the `enqueue` default), which accept() could not apply because
                    # `create_auto_skill` refuses an existing slug. Measured on a live instance:
                    # 26 of 30 pending proposals named an already-installed slug, 20 of them the
                    # same one, and every accept answered 409 permanently.
                    #
                    # accept() now infers this too (a proposal labelled `new` whose slug exists is
                    # overlaid), which is what recovers a queue the bug already filled. Labelling
                    # it HERE is what stops the queue filling again — and it makes the inbox row
                    # say "Refine a skill" instead of "New skill proposed", which is the truth.
                    from personalclaw.skills.loader import AUTO_SKILL_NAMESPACE

                    _existing = f"{AUTO_SKILL_NAMESPACE}/{slug}"
                    _is_refine = self._skills_loader.load_skill(_existing) is not None

                    prop = _proposals.enqueue(
                        slug=slug,
                        kind="refine" if _is_refine else "new",
                        refine_target=_existing if _is_refine else "",
                        description=description,
                        triggers=triggers,
                        procedure_md=procedure_md,
                        session_key=key,
                        created_at=AutoSkillProvenance.now_iso(),
                        source_excerpt=procedure_md,
                    )
                    if prop is not None:
                        logger.info("Queued skill proposal %s from session %s", prop.id, key)
                        sel().log_tool_invocation(
                            session_key=key,
                            tool_name="auto_skill_propose",
                            tool_kind="skills",
                            outcome="invoked",
                            metadata={"proposal_id": prop.id, "slug": slug},
                        )
                    else:
                        logger.info(
                            "Auto-skill proposal rejected for slug '%s' (queue full/invalid)",
                            slug,
                        )
                        sel().log_tool_invocation(
                            session_key=key,
                            tool_name="auto_skill_propose",
                            tool_kind="skills",
                            outcome="rejected",
                            metadata={"slug": slug, "reason": "queue_full_or_invalid"},
                        )

        # Refine path (only if explicitly enabled)
        if not self._auto_refine_enabled:
            return
        refined = result.get("refined_skill")
        if isinstance(refined, dict):
            name = str(refined.get("name", "")).strip()
            if not self._skills_loader.is_auto_generated(name):
                logger.info("Auto-skill refine rejected for %s: not in auto namespace", name)
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_refine",
                    tool_kind="skills",
                    outcome="rejected",
                    metadata={"name": name, "reason": "not_auto_namespace"},
                )
                return
            description = _redact(refined.get("description", ""))
            triggers = _redact(refined.get("triggers", ""))
            procedure_md = _redact(refined.get("procedure_md", ""))
            if not description or not procedure_md:
                logger.info(
                    "Auto-skill refine skipped for %s: empty description/procedure "
                    "after redaction",
                    name,
                )
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_refine",
                    tool_kind="skills",
                    outcome="rejected",
                    metadata={"name": name, "reason": "empty_after_redaction"},
                )
                return
            # Refresh the human-facing reuse_count snapshot from the sidecar
            # usage counter (the live source of truth is skills/usage.py; the
            # frontmatter just mirrors it at the already-rewriting refine seam).
            try:
                from personalclaw.skills.usage import SkillUsageStore

                _reuse = SkillUsageStore().get(name).count
            except Exception:
                _reuse = 0
            provenance = AutoSkillProvenance(
                session_key=key,
                created_at=AutoSkillProvenance.now_iso(),
                refined_at=AutoSkillProvenance.now_iso(),
                reuse_count=_reuse,
            )
            ok = self._skills_loader.update_auto_skill(
                name,
                description=description,
                triggers=triggers,
                procedure_md=procedure_md,
                provenance=provenance,
            )
            if ok:
                logger.info("Auto-refined skill %s from session %s", name, key)
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_refine",
                    tool_kind="skills",
                    outcome="invoked",
                    metadata={"name": name},
                )
            else:
                # update_auto_skill returned False: oversized procedure,
                # file missing, or other internal rejection.  Audit it so
                # operators can trace why a refine was proposed but not
                # applied.
                logger.info("Auto-skill refine rejected for %s (update_failed)", name)
                sel().log_tool_invocation(
                    session_key=key,
                    tool_name="auto_skill_refine",
                    tool_kind="skills",
                    outcome="rejected",
                    metadata={"name": name, "reason": "update_failed"},
                )

    async def _call_llm(self, prompt: str, chat_key: str) -> dict | None:
        """The consolidation's answer as a JSON object, None on failure.

        Asked as a chore of its own (``chores.run_chore``): a call that is sent this chat's
        consolidation prompt and nothing else, so what it keeps is read from this chat alone. A
        first model of the Background chain that fails, is paused or answers no JSON object hands
        the call to the next one. The call's usage row is the consolidated chat's (*chat_key*), as
        its compression's is. A failure no model answered is kept for *chat_key*
        (``_unanswered``), so the consolidation it was for is owed rather than lost; any other
        outcome settles it.
        """
        from personalclaw import chores, owed_chores
        from personalclaw.llm_helpers import (
            failure_clause,
            is_model_call_failure,
            json_object_problem,
            parse_llm_json,
        )

        try:
            text = await chores.run_chore(
                prompt, usage=chores.chore_usage(chat_key), validate=json_object_problem
            )
        except Exception as exc:
            # A model that did not answer is said in one line, with what happened; its
            # traceback holds only the HTTP client's frames. A defect keeps its traceback.
            if is_model_call_failure(exc):
                logger.warning("LLM consolidation call failed: %s", failure_clause(exc))
                logger.debug("LLM consolidation failure", exc_info=exc)
            else:
                logger.warning("LLM consolidation call failed", exc_info=True)
            if owed_chores.no_model_answered(exc):
                self._unanswered[chat_key] = exc
            else:
                self._unanswered.pop(chat_key, None)
            return None
        self._unanswered.pop(chat_key, None)
        return parse_llm_json(text)
