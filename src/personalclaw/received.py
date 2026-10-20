"""What arrived from outside: the record that lets a delivery made again change nothing.

An outside service delivers again whenever it cannot tell that its first delivery landed. A chat
service resends an event whose acknowledgement it did not see, or serves an update again until the
next poll confirms it; a mailbox is read again from the last place saved before a crash; a
webhook's sender retries a call whose answer it lost. Each is the same delivery, and what it
started must not start again: one message from the owner is one turn, and one webhook delivery is
one fire.

This module is that record, one for every door that takes deliveries. :func:`first_arrival`
records a delivery and says whether it is new, for a door that acts on a delivery as soon as it
takes it (the channel door). :func:`seen` and :func:`note` are the two halves, for a door that
takes a delivery only once its own rules let it go ahead (a webhook fire held by its automation's
spacing is not taken, and its sender's retry fires). A delivery is named by its door and by the
identity its sender gave it: a channel message by its channel, the chat it came in and the
channel's own id for it; a webhook delivery by its automation or session, its sender and the key
the sender named it with. Never by what it says: two messages with the same words are two.

**On disk.** One JSON line per delivery, in ``received.jsonl`` under the home: when it arrived (a
UTC instant), its door, and a digest of its identity. Never a body, an address or an id in the
clear: the record has to know a delivery again, and needs nothing else to. A restart reads it
back, so a delivery made again after one is still known. It is machine-local (its durability
entry, ``received``): what this machine's receivers took in, which a backup carries and no sync
merges into another machine's record or takes from one.

**Bounded.** A delivery is remembered for :data:`MAX_AGE`, longer than any sender keeps trying (a
chat service keeps an unconfirmed update a day; a webhook sender retries for a few days at most),
and the file keeps the :data:`KEEP` newest by when each arrived (``bounded_log``) once it holds
twice that. A delivery past either bound counts as new: the one way the record can be wrong, and
the bounds are set so that no sender delivers that late.

**Fail open, and said.** A record that cannot be read counts nothing as seen, and a delivery that
cannot be written is kept in memory only; each says so at WARNING. Either way the delivery goes
ahead: refusing every message while the disk is full would silence every channel to guard against
a repeat, and the warning names the repeat that could follow.

The file is read once and kept parsed, and asked again only when it changed under this process
(``bounded_log.signature``), so asking costs a ``stat``. The gateway is its one writer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from personalclaw import bounded_log
from personalclaw.atomic_write import atomic_write
from personalclaw.instants import utc_iso

logger = logging.getLogger(__name__)

#: The record's file under the home. Its durability entry is ``received``.
FILE_NAME = "received.jsonl"

#: How long a delivery is remembered.
MAX_AGE = timedelta(days=7)

#: How many deliveries the file keeps, newest first by when each arrived, once it holds twice this.
KEEP = 10_000


@dataclass
class _Record:
    """The file as this process last read it: which version of it, when each delivery arrived (by
    its key), and how many lines it holds, the trim's count."""

    signature: tuple[int, int, int] | None
    arrived: dict[str, float] = field(default_factory=dict)
    lines: int = 0


_LOCK = threading.Lock()

#: Each home's record, by the path of its file: a test's home is its own.
_RECORDS: dict[str, _Record] = {}


def path() -> Path:
    """The record's file in this home."""
    from personalclaw.config.loader import config_dir

    return config_dir() / FILE_NAME


def _key(door: str, identity: tuple[str, ...]) -> str:
    """The digest a delivery is kept by: of its door and every part of its identity, each kept
    apart (a JSON list), so no two identities run together into one."""
    named = json.dumps([door, *identity], ensure_ascii=False)
    return hashlib.sha256(named.encode("utf-8")).hexdigest()[:32]


def _parse(line: str) -> tuple[str, float] | None:
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict) or not isinstance(row.get("key"), str):
        return None
    at = bounded_log.instant(row.get("at"))
    return (row["key"], at) if at != bounded_log.UNKNOWN else None


def _load(file: Path) -> _Record:
    """This home's record, read again when the file is not the one last read."""
    signature = bounded_log.signature(file)
    cached = _RECORDS.get(str(file))
    if cached is not None and cached.signature == signature:
        return cached
    record = _Record(signature=signature)
    if signature is not None:
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "the record of deliveries already received (%s) cannot be read: a delivery "
                "made again now runs again",
                file,
                exc_info=True,
            )
            text = ""
        oldest = time.time() - MAX_AGE.total_seconds()
        for line in text.splitlines():
            if not line.strip():
                continue
            record.lines += 1
            parsed = _parse(line)
            if parsed is not None and parsed[1] >= oldest:
                key, at = parsed
                record.arrived[key] = max(at, record.arrived.get(key, at))
    _RECORDS[str(file)] = record
    return record


def _seen(record: _Record, key: str) -> bool:
    at = record.arrived.get(key)
    return at is not None and time.time() - at < MAX_AGE.total_seconds()


def _write(file: Path, record: _Record, door: str, key: str) -> None:
    """Append the delivery *key* names to the file, and trim the file once it is full."""
    now = time.time()
    record.arrived[key] = now
    line = json.dumps({"at": utc_iso(now), "door": door, "key": key}, separators=(",", ":"))
    _append(file, record, line + "\n")


def _append(file: Path, record: _Record, line: str) -> None:
    before = record.signature[1] if record.signature is not None else 0
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        logger.warning(
            "a delivery could not be added to the record of deliveries already received "
            "(%s): it is kept until this gateway stops, and runs again if delivered after that",
            file,
            exc_info=True,
        )
        return
    record.lines += 1
    signature = bounded_log.signature(file)
    if signature is None or signature[1] != before + len(line.encode("utf-8")):
        # Another writer added to the file too: read it whole next time rather than trust this.
        _RECORDS.pop(str(file), None)
        return
    record.signature = signature
    if record.lines > 2 * KEEP:
        _trim(file)


def _trim(file: Path) -> None:
    """Rewrite the file to the deliveries it still remembers: those inside :data:`MAX_AGE`, at
    most the :data:`KEEP` newest by when each arrived."""
    try:
        lines = [line for line in file.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        logger.warning("the record of deliveries already received could not be trimmed")
        return
    oldest = time.time() - MAX_AGE.total_seconds()
    fresh = [line for line in lines if (parsed := _parse(line)) is not None and parsed[1] >= oldest]
    kept = bounded_log.newest(fresh, KEEP, at=lambda line: json.loads(line).get("at"))
    try:
        atomic_write(file, "".join(line + "\n" for line in kept))
    except OSError:
        logger.warning("the record of deliveries already received could not be trimmed")
        return
    _RECORDS.pop(str(file), None)


def seen(door: str, *identity: str) -> bool:
    """Whether the delivery *door* knows by *identity* arrived before, inside the bounds."""
    with _LOCK:
        file = path()
        return _seen(_load(file), _key(door, identity))


def note(door: str, *identity: str) -> None:
    """Record that *door* took the delivery *identity* names (:func:`seen` asks first)."""
    with _LOCK:
        file = path()
        record = _load(file)
        key = _key(door, identity)
        if not _seen(record, key):
            _write(file, record, door, key)


def first_arrival(door: str, *identity: str) -> bool:
    """Record the delivery *door* knows by *identity*: True when it arrives for the first time,
    False when it arrived before. Asked and recorded at once, so of two deliveries of it that
    arrive together exactly one is the first."""
    with _LOCK:
        file = path()
        record = _load(file)
        key = _key(door, identity)
        if _seen(record, key):
            return False
        _write(file, record, door, key)
        return True
