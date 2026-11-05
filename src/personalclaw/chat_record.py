"""Each chat's record: its settings, kept beside its transcript in ``<safe_key>.chat.json``.

A chat's title, pin, folder, tags, archive, memory mode, runtime binding and consolidation mark
were the first line of its transcript, ``{"_type": "metadata", …}``, and every change to one of
them rewrote the whole transcript. The record holds them now (shared contracts §3.7), and a change
to one writes only the record (:func:`update`), under the record's lock (``record_files``).

**The first line stays, until the cut.** A home must still read as it did under v0.3.6 if its
owner goes back to it, and v0.3.6 reads only that line, as every release before it does. So the
record is a copy of the line, never a move of it (:func:`copy_first_lines`, run at every start
before anything reads a chat):

- a save that rewrites the transcript anyway writes the line too, as the record now says it
  (:func:`saved`), and so does a transcript's creation;
- a clean stop writes the line of every chat whose record changed since its line was written
  (:func:`sync_lines`), so a downgrade after a clean stop loses nothing. A crash before that keeps
  the line as of the chat's last save: a rename made since reads as the old title under v0.3.6;
- the record keeps the digest of the line it last matched (``line_digest``), and how the
  transcript stood then (``line_seen``: its inode, size and modification time). A line written by
  something else since, v0.3.6 after a downgrade or a hand edit among them, wins for each key it
  holds, and what only the record holds is kept, its memory mode too where the line names none
  (:func:`over`): a read of the chat says the line at once
  (:func:`trusted`, :func:`matches`), the next chat-level change takes it back into the record
  before it writes (:func:`update`), and so do the start's copy and the clean stop.

Removing the line is the cut's, beside the other compatibility cuts.

**What only the record holds** (:data:`RECORD_ONLY`): ``v``; the line's digest and length in
bytes, so the chat list reads a chat's count from its record without opening its transcript;
``kind`` (``chat``), ``work`` and ``parent``; and ``created_by_client``, the External access client
that started a delegated chat, written once (:func:`update` refuses to change it).

**A record that cannot be read** is never written over (``record_files``): its chat is opened
read-only, its memory mode reads as unreadable, so it keeps nothing, and a backup leaves it out,
since it may be Temporary. A transcript the backfill could not copy is left as it was and named
by the Doctor (``durability.chat_records``).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw import record_files
from personalclaw.atomic_write import atomic_write
from personalclaw.ledger.hashing import canonical_digest

logger = logging.getLogger(__name__)

#: A record's file, beside its transcript ``<safe_key>.jsonl``.
SUFFIX = ".chat.json"
VERSION = 1
#: What the record holds that the transcript's first line does not.
RECORD_ONLY = frozenset(
    {"v", "line_digest", "line_bytes", "line_seen", "kind", "work", "parent", "created_by_client"}
)
#: The record's own fields a change never sets: its bookkeeping, and what kind of record it is,
#: which no caller writes. ``created_by_client`` is the one a change may set, once (:func:`update`).
NEVER_SET = RECORD_ONLY - {"created_by_client"}
#: What a record is given when it is first made, beside what the first line holds.
_MADE = {"kind": "chat", "work": "", "parent": "", "created_by_client": None}
#: What a line written since keeps of the record when it names none: the memory mode, so such a
#: line never loosens what the chat keeps.
KEPT_UNNAMED = ("memory_mode",)
#: What marks a transcript's first line as its metadata.
_METADATA = {"_type": "metadata"}


class ChatRecordUnreadable(OSError):
    """A chat's record is there and cannot be read: nothing writes it until it can be."""


def held_child(where: Path, name: str) -> str:
    """*name*, held to the folder *where* before anything is read or written for it: the name of
    the folder's direct child it names, joined and normalised, when that child is named exactly as
    given. Raises ``ValueError`` for anything else (``..``, ``a/b``, an absolute path), which is no
    transcript of the folder's. ``ConversationLog._path`` takes every transcript's name from it."""
    # Shaped line for line as dashboard/chat_turns.py's _in_the_sessions_folder (#4009).
    folder = os.path.normpath(os.path.abspath(where))
    held = os.path.normpath(os.path.join(folder, name))
    if not held.startswith(folder + os.sep) or os.path.dirname(held) != folder:
        raise ValueError(f"a chat's transcript is kept in {folder}, never at {held}")
    child = os.path.basename(held)
    if child != name:
        raise ValueError(f"a chat's transcript is kept in {folder}, never at {held}")
    return child


def path_of(transcript: Path) -> Path:
    """The record of the transcript at *transcript* (``<safe_key>.jsonl``)."""
    return transcript.with_name(transcript.name.removesuffix(".jsonl") + SUFFIX)


def read(transcript: Path) -> dict | None:
    """*transcript*'s record, or ``None`` when it has none. Raises :class:`ChatRecordUnreadable`
    for one that is there and cannot be read, which ``record_files`` keeps a copy of and names
    for the Doctor."""
    try:
        return record_files.read(path_of(transcript), dict)
    except record_files.Unreadable as exc:
        raise ChatRecordUnreadable(str(exc)) from exc


def fields(record: Mapping[str, Any]) -> dict:
    """What *record* holds that the transcript's first line holds too: the chat's metadata as
    every reader of it knows it."""
    return {k: v for k, v in record.items() if k not in RECORD_ONLY and k != "_type"}


def line_of(record: Mapping[str, Any]) -> dict:
    """The transcript's first line as *record* says it."""
    return {**_METADATA, **fields(record)}


def _digest(line: Mapping[str, Any]) -> str:
    return canonical_digest({k: v for k, v in line.items() if k != "_type"})


def first_line(transcript: Path) -> tuple[dict, int] | None:
    """*transcript*'s first line and its length in bytes, when it is the metadata line; else
    ``None``. Raises ``OSError`` when the transcript cannot be opened."""
    with open(transcript, "rb") as f:
        raw = f.readline()
    try:
        line = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(line, dict) or line.get("_type") != "metadata":
        return None
    return line, len(raw)


def _seen(transcript: Path) -> list[int] | None:
    """How *transcript* stands: its inode, size and modification time, ``None`` when it is gone."""
    try:
        st = os.stat(transcript)
    except OSError:
        return None
    return [st.st_ino, st.st_size, st.st_mtime_ns]


def trusted(record: Mapping[str, Any], stat: os.stat_result) -> bool:
    """Whether *record* says its chat without its transcript being read: the transcript stands as
    it did when the record last matched its first line (``stat``, the transcript's)."""
    return record.get("line_seen") == [stat.st_ino, stat.st_size, stat.st_mtime_ns]


def matches(record: Mapping[str, Any], first: bytes) -> bool:
    """Whether *first*, the transcript's first line as read, is the line *record* last matched;
    when it is not, something else wrote it since, and the line says the chat."""
    try:
        line = json.loads(first)
    except ValueError:
        return False
    return (
        isinstance(line, dict)
        and line.get("_type") == "metadata"
        and _digest(line) == record.get("line_digest")
    )


def over(line: Mapping[str, Any], record: Mapping[str, Any] | None) -> dict:
    """What *line*, written since *record* matched the transcript's first line, says of the chat:
    the line wins for each key it holds, and the record's memory mode stands where it names none.
    A mode it holds that is no string stays, so it is read as one that cannot be read."""
    said = dict(line)
    for key in KEPT_UNNAMED:
        if record is not None and key not in said and key in record:
            said[key] = record[key]
    return said


def _made(
    line: Mapping[str, Any],
    raw_bytes: int,
    base: Mapping[str, Any] | None,
    seen: list[int] | None = None,
) -> dict:
    """The record that matches *line*, as the transcript stands (*seen*): its fields, over what
    only *base* holds."""
    kept = {k: v for k, v in (base or _MADE).items() if k in RECORD_ONLY}
    made = {**_MADE, **kept, **fields(over(line, base)), "v": VERSION}
    made["line_digest"], made["line_bytes"], made["line_seen"] = _digest(line), raw_bytes, seen
    return made


def saved(transcript: Path, line: Mapping[str, Any]) -> None:
    """*line* was just written as *transcript*'s first line, as ``json.dumps(line) + "\\n"``: the
    record says what it says, and keeps what only the record holds. A record that cannot be read
    is left as it is, and logged."""
    raw = len((json.dumps(dict(line)) + "\n").encode("utf-8"))
    seen = _seen(transcript)
    try:
        record_files.rewrite(path_of(transcript), lambda base: _made(line, raw, base, seen))
    except record_files.Unreadable:
        logger.warning("the record of %s could not be read: left as it is", transcript.name)


def write(transcript: Path, line: Mapping[str, Any], body: str) -> None:
    """Write *transcript* whole, *line* as its first line and then *body*, and its record as the
    line says it (:func:`saved`): the first line is kept for a home that goes back to v0.3.6."""
    atomic_write(transcript, json.dumps(dict(line)) + "\n" + body, fsync=True)
    saved(transcript, line)


def update(transcript: Path, changes: Mapping[str, Any], *, drop: Iterable[str] = ()) -> bool:
    """Change the chat-level fields *changes* (and remove each of *drop*) in *transcript*'s record,
    the one write of a chat-level field: the transcript is not touched. A chat with no record yet
    has one made from its first line; one with neither has nothing written. A first line written
    since the record matched it is taken back first, so the change lands over it. Returns whether it
    wrote. Raises :class:`ChatRecordUnreadable` for a record that cannot be read; ``ValueError``,
    before anything is written, for a change naming the record's own fields (:data:`NEVER_SET`), a
    change to ``created_by_client`` once it is set, or a *drop* naming the record's own fields
    (:data:`RECORD_ONLY`) or the memory mode; and ``TypeError`` for a *drop* that is one string.
    A stale ``line_digest`` would take an old first line back over every change since, and
    clearing ``created_by_client`` would let another be written, while a chat's mode is only ever
    set on purpose."""
    if isinstance(drop, str):
        raise TypeError("drop names fields in a tuple, not one string")
    dropped = tuple(drop)
    refused = sorted(set(dropped) & (RECORD_ONLY | set(KEPT_UNNAMED)))
    if refused:
        raise ValueError(f"a drop removes chat-level fields, never {', '.join(refused)}")
    own = sorted(set(changes) & NEVER_SET)
    if own:
        raise ValueError(f"a change writes chat-level fields, never {', '.join(own)}")

    def change(base: dict | None) -> dict | None:
        found = first_line(transcript) if transcript.exists() else None
        if base is None and found is None:
            return None
        if found is not None and (base is None or _digest(found[0]) != base.get("line_digest")):
            base = _made(found[0], found[1], base, _seen(transcript))
        elif found is not None and base is not None:
            base = {**base, "line_seen": _seen(transcript)}
        assert base is not None
        held = base.get("created_by_client")
        if "created_by_client" in changes and held not in (None, changes["created_by_client"]):
            raise ValueError("a chat's created_by_client is written once")
        merged = {**base, **dict(changes)}
        for key in dropped:
            merged.pop(key, None)
        return merged

    try:
        return record_files.rewrite(path_of(transcript), change)
    except record_files.Unreadable as exc:
        raise ChatRecordUnreadable(str(exc)) from exc


def lock_of(transcript: Path) -> Path:
    """The lock *transcript*'s record is written under (``record_files.locked``)."""
    return record_files.lock_path(path_of(transcript))


def forget(transcript: Path) -> None:
    """Delete *transcript*'s record and its lock, with the chat."""
    for path in (path_of(transcript), lock_of(transcript)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def stamp(transcript: Path) -> tuple[int, int, int]:
    """What says *transcript*'s record changed (``record_files.stamp``); zeros for none."""
    return record_files.stamp(path_of(transcript)) or (0, 0, 0)


# ── The start's copy, and the clean stop's lines ─────────────────────────────────────────────


@dataclass
class Copied:
    """What one pass over a home's transcripts did, by transcript name."""

    copied: list[str] = field(default_factory=list)
    taken_back: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


#: The transcripts the last start's copy left as they were, for the Doctor.
_FAILED: list[str] = []


def failed() -> list[str]:
    """The transcripts the last :func:`copy_first_lines` could not copy, by name."""
    return list(_FAILED)


def _transcripts(sessions_dir: Path) -> list[Path]:
    try:
        with os.scandir(sessions_dir) as found:
            return sorted(
                Path(item.path)
                for item in found
                if item.name.endswith(".jsonl") and not item.is_symlink() and item.is_file()
            )
    except OSError:
        return []


def _copy_one(transcript: Path, done: Copied) -> None:
    found = first_line(transcript)
    if found is None:
        return
    line, raw = found
    made: list[dict] = []

    def change(base: dict | None) -> dict | None:
        if base is not None and base.get("line_digest") == _digest(line):
            return None
        made[:] = [_made(line, raw, base, _seen(transcript)), base or {}]
        return made[0]

    try:
        wrote = record_files.rewrite(path_of(transcript), change)
    except record_files.Unreadable as exc:
        raise ChatRecordUnreadable(str(exc)) from exc
    if not wrote:
        return
    if read(transcript) != made[0]:
        raise OSError(f"{path_of(transcript).name} did not read back as written")
    (done.taken_back if made[1] else done.copied).append(transcript.name)


def copy_first_lines(sessions_dir: Path) -> Copied:
    """Give each transcript in *sessions_dir* whose first line is its metadata a record of it,
    read back to verify; take back into its record a line written since the record matched it
    (the line wins for each key it holds). A second pass changes nothing. A transcript that fails
    is left as it was, logged, and named for the Doctor (:func:`failed`)."""
    done = Copied()
    for transcript in _transcripts(sessions_dir):
        try:
            _copy_one(transcript, done)
        except (OSError, ValueError):
            logger.warning("chat record: could not copy %s", transcript.name, exc_info=True)
            done.failed.append(transcript.name)
    _FAILED[:] = done.failed
    if done.copied or done.taken_back or done.failed:
        logger.info(
            "chat records: %d made from their transcripts, %d taken back from a line written "
            "since, %d left as they were",
            len(done.copied),
            len(done.taken_back),
            len(done.failed),
        )
    return done


def _write_line(transcript: Path, record: dict) -> None:
    """Write *record*'s line over *transcript*'s first line, its metadata line."""
    lines = transcript.read_text(encoding="utf-8").splitlines(keepends=True)
    write(transcript, line_of(record), "".join(lines[1:]))


def sync_lines(sessions_dir: Path) -> int:
    """At a clean stop: write the first line of each transcript in *sessions_dir* whose record
    changed since its line was written, so v0.3.6 reads every chat as this version left it. A
    line something else wrote since is not written over: it is taken back into the record, as a
    read takes it. Nor is a first line that cannot be read: the chat keeps nothing in memory until
    it can be, never the record's mode. Returns how many it wrote; one that fails is left as it
    was."""
    written = 0
    for transcript in _transcripts(sessions_dir):
        try:
            record = read(transcript)
            if record is None or record.get("line_digest") == _digest(line_of(record)):
                continue
            found = first_line(transcript)
            if found is None:
                continue
            if _digest(found[0]) != record.get("line_digest"):
                line, raw = found
                seen = _seen(transcript)
                record_files.rewrite(path_of(transcript), lambda base: _made(line, raw, base, seen))
                continue
            _write_line(transcript, record)
            written += 1
        except (OSError, ValueError):
            logger.warning("chat record: could not write the line of %s", transcript.name)
    return written
