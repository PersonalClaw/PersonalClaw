"""A chat turn waiting on a tool approval, kept on disk while it waits, so a restart neither loses
the turn nor runs a call nobody allowed.

A native turn that asks to run a call waits on its approval inside its runtime, with the turn's
history and the step's calls held nowhere else. A restart used to end it: the approval went from
every surface, and the owner got the turn back only by asking the model again. Now the turn is
recorded the moment it waits (:func:`record`), one file per chat in :data:`PARKED_DIR`, holding:

* where the turn stands (``NativeAgentRuntime.park_point``): its history as its model read it,
  the turn's own message in it, the call it waits on, and the results its step already has. Every
  tool output and every call that already ran is masked as the transcript masks it
  (:func:`_masked`); only the call it waits on and the step's calls not yet run stay as the model
  sent them, since they run as sent;
* the approval's card and the chat's permission row, as the chat showed them (a save drops the
  row of an approval nobody answered, so the start writes it again), and a digest over them and the
  call it waits on (:func:`_digest`), so a record that shows one call and holds another is never
  read (:func:`waiting`);
* who asked for the turn when the owner did not, and when it began to wait, for how long.

It carries no grant, and no setting the turn ran under: everything that decides whether and how the
call runs is read again when the turn carries on.

A chat that keeps no words (Incognito or Temporary) is never recorded (``memory_writes``), and a
record past :data:`RECORD_CAP` is not written: either turn ends at a restart as it did. The record
is forgotten when the wait ends any way but the gateway stopping (:func:`forget`), and taken, once,
when an answer after the restart carries the turn on (:func:`take`), before the call runs, so a
crash while it runs never runs it a second time.

The settle after a restart is the dashboard's (``dashboard.parked_chat_turns``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.ledger.hashing import canonical_digest
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

#: The folder under the PersonalClaw home the records are kept in, one file per chat.
PARKED_DIR = "chat_parked"
#: The largest record written, in bytes of its JSON. A turn whose record would be larger ends at a
#: restart as it did before records were kept.
RECORD_CAP = 4 * 1024 * 1024
#: The record's shape. A record of another shape is not read.
VERSION = 1

#: The approval card's fields a record keeps: what ``DashboardState.hold_session_approval`` is
#: handed for the call, already masked as the card shows it.
CARD_FIELDS = (
    "tool",
    "tool_input",
    "tool_purpose",
    "agent",
    "risk",
    "is_read_only",
    "blast_radius",
    "grant_agent",
    "reach",
    "deny_effect",
    "asked_for",
    "confirms",
    "secret_file",
)


def _home() -> Path:
    from personalclaw.config import loader as config_loader

    return config_loader.config_dir()


def _dir() -> Path:
    return _home() / PARKED_DIR


def path_of(session_key: str) -> Path:
    """The file the chat *session_key*'s waiting turn is kept in, named from the whole key."""
    name = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:32]
    return _dir() / f"{name}.json"


def _mask(text: str) -> str:
    """*text* as the transcript keeps it: credentials and exfiltration URLs masked."""
    return redact_credentials(redact_exfiltration_urls(text)[0])[0]


def _unrun(point: Mapping[str, Any]) -> set[str]:
    """The ids of the calls the step of *point* has not run: the one it waits on and the ones
    after it, which run as the model sent them."""
    messages = list(point.get("messages") or ())
    last = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
    if last < 0:
        return set()
    answered = {str(m.get("tool_call_id") or "") for m in messages[last + 1 :]}
    carried = set((point.get("carried") or {}).keys())
    return {
        str(tc.get("id") or "")
        for tc in messages[last].get("tool_calls") or ()
        if str(tc.get("id") or "") not in answered | carried
    }


def _masked(point: Mapping[str, Any]) -> dict[str, Any]:
    """*point* with every tool output and every call that already ran masked as the transcript
    masks it; the calls not yet run are left as their model sent them."""
    keep = _unrun(point)
    messages: list[dict[str, Any]] = []
    for m in point.get("messages") or ():
        m = dict(m)
        if m.get("role") == "tool" and isinstance(m.get("content"), str):
            m["content"] = _mask(m["content"])
        if m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = dict(tc.get("function") or {})
                if str(tc.get("id") or "") not in keep and isinstance(fn.get("arguments"), str):
                    fn["arguments"] = _mask(fn["arguments"])
                calls.append({**tc, "function": fn})
            m["tool_calls"] = calls
        messages.append(m)
    carried = {
        str(cid): [_mask(str(kept[0])), dict(kept[1])]
        for cid, kept in (point.get("carried") or {}).items()
    }
    return {**point, "messages": messages, "carried": carried}


def parked_call(point: Mapping[str, Any]) -> dict[str, Any] | None:
    """The call the turn of *point* waits on, as its model sent it (``{"id", "name",
    "arguments"}``), or ``None`` when its step holds no such call."""
    call = str(point.get("call") or "")
    for m in reversed(list(point.get("messages") or ())):
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or ():
            if str(tc.get("id") or "") == call:
                fn = tc.get("function") or {}
                return {"id": call, "name": fn.get("name"), "arguments": fn.get("arguments")}
        return None
    return None


def _digest(
    session_key: str, request_id: str, card: Mapping[str, Any], row: Mapping[str, Any], call: Any
) -> str:
    return canonical_digest(
        {
            "session": session_key,
            "request_id": request_id,
            "card": dict(card),
            "row": dict(row),
            "call": call,
        }
    )


def record(
    session_key: str,
    request_id: str,
    *,
    card: Mapping[str, Any],
    row: Mapping[str, Any],
    point: Mapping[str, Any],
    asked_for_by: Mapping[str, str] | None,
    window_secs: float,
    now: float | None = None,
) -> dict[str, Any] | None:
    """The record of the chat *session_key*'s turn, waiting on *request_id*'s answer with *card*
    shown and its permission *row* (``{"title", "meta"}``) in the chat, from where its runtime says
    it stands (*point*). ``None`` when the point names no call of its step."""
    masked = _masked(point)
    call = parked_call(masked)
    if call is None or call["id"] != str(request_id):
        return None
    card = {k: card.get(k) for k in CARD_FIELDS}
    row = {"title": str(row.get("title") or ""), "meta": dict(row.get("meta") or {})}
    return {
        "v": VERSION,
        "session": session_key,
        "request_id": str(request_id),
        "card": card,
        "row": row,
        "point": masked,
        "asked_for_by": dict(asked_for_by) if asked_for_by else None,
        "parked_at": time.time() if now is None else now,
        "window_secs": float(window_secs),
        "digest": _digest(session_key, str(request_id), card, row, call),
    }


def write(rec: Mapping[str, Any]) -> bool:
    """Keep *rec* (:func:`record`). False, and nothing kept, when it is past :data:`RECORD_CAP` or
    cannot be written: the turn then ends at a restart as it did."""
    text = json.dumps(rec, ensure_ascii=False)
    if len(text.encode("utf-8")) > RECORD_CAP:
        logger.info("a waiting turn of %s is too large to keep through a restart", rec["session"])
        return False
    try:
        atomic_write(path_of(str(rec["session"])), text, fsync=True)
    except (OSError, ValueError):
        logger.warning("could not keep the waiting turn of %s", rec["session"], exc_info=True)
        return False
    return True


def _read(path: Path) -> dict[str, Any] | None:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def holds(rec: Mapping[str, Any]) -> bool:
    """Whether *rec* is a record this version reads whose card is the card of the call it holds:
    its digest over both is the one they make now."""
    try:
        if rec.get("v") != VERSION:
            return False
        point, card, row = rec["point"], rec["card"], rec["row"]
        session_key, request_id = str(rec["session"]), str(rec["request_id"])
        call = parked_call(point)
        return (
            call is not None
            and call["id"] == request_id
            and rec.get("digest") == _digest(session_key, request_id, card, row, call)
        )
    except (KeyError, TypeError, AttributeError):
        return False


def forget(session_key: str, request_id: str | None = None) -> None:
    """Drop the record of the chat *session_key*'s waiting turn: the one waiting on *request_id*
    when it is named, else whichever it holds."""
    path = path_of(session_key)
    if request_id is not None:
        rec = _read(path)
        if rec is not None and str(rec.get("request_id")) != str(request_id):
            return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("could not drop the waiting turn of %s", session_key, exc_info=True)


def take(session_key: str, request_id: str) -> dict[str, Any] | None:
    """The record of the chat *session_key*'s turn waiting on *request_id*, removed from disk
    first: a turn is carried on from it once, whatever happens after. ``None`` when there is none
    to take, or it no longer holds (:func:`holds`)."""
    path = path_of(session_key)
    rec = _read(path)
    if rec is None or str(rec.get("request_id")) != str(request_id):
        return None
    try:
        path.unlink()
    except OSError:
        # One that cannot be removed is not carried on: the next start would carry it on again.
        logger.warning("could not take the waiting turn of %s", session_key, exc_info=True)
        return None
    return rec if holds(rec) else None


def waiting() -> list[dict[str, Any]]:
    """Every record a previous run left, oldest first. One that does not hold (:func:`holds`) is
    dropped and not listed."""
    folder = _dir()
    if not folder.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        rec = _read(path)
        if rec is not None and holds(rec) and path == path_of(str(rec["session"])):
            found.append(rec)
            continue
        logger.warning("dropping a waiting-turn record that does not hold: %s", path.name)
        try:
            path.unlink()
        except OSError:
            pass
    return sorted(found, key=lambda r: float(r.get("parked_at") or 0.0))


def window_left(rec: Mapping[str, Any], *, now: float | None = None) -> float:
    """How long *rec*'s approval may still wait for an answer: its window less the time since it
    began to wait, the gateway's time down included. 0 when it has run out."""
    now = time.time() if now is None else now
    waited = max(0.0, now - float(rec.get("parked_at") or 0.0))
    return max(0.0, float(rec.get("window_secs") or 0.0) - waited)


def settle_restored(home: Path) -> list[str]:
    """Drop every record a replace restore wrote into *home*; the chats they were of. A snapshot
    is a moment in the past: a turn it held waiting is not asked again, nor its call run, on the
    home it was restored to (``snapshot._hold_what_was_in_flight``)."""
    dropped: list[str] = []
    folder = home / PARKED_DIR
    if not folder.is_dir():
        return dropped
    for path in sorted(folder.glob("*.json")):
        rec = _read(path)
        try:
            path.unlink()
        except OSError:
            logger.warning("could not drop a restored waiting turn: %s", path.name)
            continue
        if rec is not None and rec.get("session"):
            dropped.append(str(rec["session"]))
    return dropped
