"""The security-log rows a process that holds no key reports, signed by the gateway.

The tool server an agent CLI runs (``personalclaw mcp-core``) runs in the CLI's sandbox, which
keeps the security log's key from it and its log and archive unchangeable by it
(``owner_only.AUDIT_FENCED``): a process that could do either could write rows that pass the
log's check, or remove its own. It still records the calls it makes. It hands each row to the
gateway (``POST /api/security-log/reported``), and when the gateway does not answer it leaves the
row in the home's spool (``sel.spool_row``), which the gateway takes in when it starts and at each
row reported after.

Here the gateway takes each row:

* only the event's own fields, each the text it must be and bounded; never a hash, and never the
  caller scope the sender set, which is its claim and is kept as such;
* stamped with who reported it (:data:`REPORTED_BY`, in the row's metadata): the process the
  sender names and its pid, the chat its request names, and whether it came through the spool.
  The sender's word, each of them, kept so that a reported row is never taken for one the
  gateway wrote itself;
* signed and appended through the gateway's own log (``sel.SecurityEventLog.log``), so it passes
  the log's check like any other, and taken once however often it is reported (by its id).
"""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import stat
import threading
from dataclasses import fields
from pathlib import Path
from typing import Any

from personalclaw.owner_only import AUDIT_SPOOL
from personalclaw.sel import SecurityEvent, sel

logger = logging.getLogger(__name__)

#: The metadata key a reported row's stamp is kept under.
REPORTED_BY = "reported_by"

#: The fields a sender may not set: the chain's, which only the gateway's log computes, and the
#: caller scope, which the gateway's log stamps from its own pass (the sender's is kept in the
#: stamp, as its claim).
_NOT_THE_SENDERS = frozenset({"prev_hash", "entry_hash", "caller_scope", "metadata"})
#: The fields a row is nothing without.
_REQUIRED = ("event_id", "timestamp", "event_type", "operation")
#: An event id as the log makes them (``uuid4().hex``) or as a test names one.
_EVENT_ID = re.compile(r"[0-9A-Za-z_.:-]{1,64}")
#: The most of one field kept, and of a row's metadata as JSON. A row the log writes is far
#: smaller (``audit_subject`` bounds its subject); these bound what a sender can make the log keep.
_FIELD_CAP = 8192
_METADATA_CAP = 16384
#: The most of one spooled file read, and of files taken in at one time.
_SPOOLED_CAP = 65536
_SPOOL_BATCH = 1000
#: The ids of the rows taken lately, so a row reported twice (its answer lost on the way back,
#: then spooled) is kept once.
_TAKEN_IDS: collections.OrderedDict[str, None] = collections.OrderedDict()
_TAKEN_CAP = 4096
_TAKEN_LOCK = threading.Lock()
#: A spooled file this gateway has claimed: renamed under this prefix first, so two gateways (or a
#: start and a report at once) never take one row twice.
_CLAIMED = ".taken-"


def event_of(
    row: Any, *, process: Any, pid: Any, session: str, spooled: bool
) -> SecurityEvent | None:
    """The event a reported *row* records, stamped as reported (:data:`REPORTED_BY`), or
    ``None`` for a row that is not one: not an object, a field that is not text, a required field
    missing, an id the log would not make."""
    if not isinstance(row, dict):
        return None
    kept: dict[str, str] = {}
    for field in fields(SecurityEvent):
        if field.name in _NOT_THE_SENDERS:
            continue
        value = row.get(field.name, "")
        if not isinstance(value, str):
            return None
        kept[field.name] = value[:_FIELD_CAP]
    if not all(kept[name] for name in _REQUIRED) or not _EVENT_ID.fullmatch(kept["event_id"]):
        return None
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        return None
    if len(json.dumps(metadata, default=str)) > _METADATA_CAP:
        metadata = {"dropped": f"the reported metadata was over {_METADATA_CAP} characters"}
    stamp = {
        "process": str(process or "")[:64],
        "pid": pid if isinstance(pid, int) and not isinstance(pid, bool) else 0,
        "session": session[:256],
        "spooled": spooled,
        "caller_scope": str(row.get("caller_scope") or "")[:64],
    }
    return SecurityEvent(**kept, metadata={**metadata, REPORTED_BY: stamp})


def take(event: SecurityEvent) -> bool:
    """Sign and append *event* through the gateway's log, once: ``False`` when a row with its id
    was taken lately (:data:`_TAKEN_IDS`), and nothing is written again. A write that fails
    raises, and the row may be reported again."""
    with _TAKEN_LOCK:
        if event.event_id in _TAKEN_IDS:
            return False
        _TAKEN_IDS[event.event_id] = None
        while len(_TAKEN_IDS) > _TAKEN_CAP:
            _TAKEN_IDS.popitem(last=False)
    try:
        sel().log(event)
    except BaseException:
        with _TAKEN_LOCK:
            _TAKEN_IDS.pop(event.event_id, None)
        raise
    return True


def spool_dir() -> Path:
    """The spool of the home this gateway serves."""
    from personalclaw.config.loader import config_dir

    return config_dir() / AUDIT_SPOOL


def take_in_spool() -> int:
    """Take in every row the spool holds (:func:`take`, each stamped as spooled), and answer how
    many were written. Each file is claimed first, by a rename no other taker can also win, then
    read, taken and removed; a file that is not a reported row is said in the gateway's log and
    removed, and one the log could not take is put back for the next. A file a gateway claimed
    and never finished (it stopped) is taken here once that process is gone. Never raises.

    The spool is written from inside an agent CLI's sandbox, so nothing in it is taken on its
    word. The spool itself is opened as it is, never through a link, and must be a folder of this
    gateway's own that no one else can write; a spool that is anything else is said, and nothing
    in it is read or removed. Every name in it is then claimed, read and removed relative to that
    one open folder, and only a regular file is read."""
    spool = spool_dir()
    try:
        folder = os.open(spool, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return 0
    except OSError as exc:
        logger.warning(
            "did not take in the security log's spool %s (%s): it is not a folder this gateway "
            "can open as it is, and nothing in it was read or removed",
            spool,
            exc.strerror or type(exc).__name__,
        )
        return 0
    try:
        refusal = _not_a_spool(os.fstat(folder))
        if refusal:
            logger.warning(
                "did not take in the security log's spool %s: it is %s, and nothing in it was "
                "read or removed",
                spool,
                refusal,
            )
            return 0
        try:
            names = sorted(os.listdir(folder))
        except OSError as exc:
            logger.warning("could not read the security log's spool %s: %s", spool, exc)
            return 0
        written = 0
        for name in names[:_SPOOL_BATCH]:
            claim = _claim(folder, name)
            if claim is not None:
                written += _take_claimed(folder, *claim)
        return written
    finally:
        os.close(folder)


def _not_a_spool(info: os.stat_result) -> str:
    """What the opened spool is when it is not one the gateway takes rows from, or ``""``: it must
    be a folder, this process's user's, and writable by no one else."""
    if not stat.S_ISDIR(info.st_mode):
        return "not a folder"
    if info.st_uid != os.getuid():
        return f"owned by user {info.st_uid}, not this gateway's"
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"writable by others (mode {stat.S_IMODE(info.st_mode):o})"
    return ""


def _claim(folder: int, name: str) -> tuple[str, str] | None:
    """``(claimed, original)`` for the spooled file *name* in the open spool *folder*, renamed to
    its claimed name by this process; ``None`` when it is not a row's file (a temp file being
    written), or another taker holds it."""
    original = name
    if name.startswith(_CLAIMED):
        owner, _, original = name[len(_CLAIMED) :].partition("-")
        if not owner.isdigit() or _alive(int(owner)):
            return None
    if not original or original.startswith(".") or not original.endswith(".json"):
        return None
    claimed = f"{_CLAIMED}{os.getpid()}-{original}"
    try:
        os.rename(name, claimed, src_dir_fd=folder, dst_dir_fd=folder)
    except OSError:
        return None
    return claimed, original


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _read_spooled(folder: int, claimed: str) -> bytes:
    """The bytes of the claimed file, at most one past :data:`_SPOOLED_CAP`: opened in *folder*
    as it is (never through a link, and never waiting on a pipe), and read only when it is a
    regular file. Raises ``OSError`` or ``ValueError`` for anything else."""
    fd = os.open(
        claimed,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        dir_fd=folder,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        chunks: list[bytes] = []
        left = _SPOOLED_CAP + 1
        while left > 0:
            chunk = os.read(fd, left)
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _take_claimed(folder: int, claimed: str, original: str) -> int:
    try:
        raw = _read_spooled(folder, claimed)
        if len(raw) > _SPOOLED_CAP:
            raise ValueError(f"over {_SPOOLED_CAP} bytes")
        spooled = json.loads(raw)
        if not isinstance(spooled, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        logger.warning("dropped the security log's spooled file %s: %s", original, exc)
        _remove(folder, claimed)
        return 0
    event = event_of(
        spooled.get("row"),
        process=spooled.get("process"),
        pid=spooled.get("pid"),
        session="",
        spooled=True,
    )
    if event is None:
        logger.warning("dropped the security log's spooled file %s: not a reported row", original)
        _remove(folder, claimed)
        return 0
    try:
        written = take(event)
    except OSError as exc:
        logger.warning(
            "the security log could not take the spooled row %s (%s); it stays for the next",
            original,
            exc.strerror or type(exc).__name__,
        )
        try:
            os.rename(claimed, original, src_dir_fd=folder, dst_dir_fd=folder)
        except OSError:
            pass
        return 0
    _remove(folder, claimed)
    return 1 if written else 0


def _remove(folder: int, name: str) -> None:
    """Remove the entry *name* from the open spool *folder*: the entry itself, which for a link is
    the link and never what it names."""
    try:
        os.unlink(name, dir_fd=folder)
    except OSError:
        pass
