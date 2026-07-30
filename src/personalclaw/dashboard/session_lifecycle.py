"""Session lifecycle — archive, restore, and the auto-archive rule (SESSION-MANAGEMENT S2).

At 100+ conversations the list becomes a scroll. Archiving is the answer, and it is
deliberately **not** deletion: an archived session keeps its transcript, keeps its
FTS index entry, and can be restored in one action. That is what makes it safe to do
on a timer — the worst case of a wrong auto-archive is one click to undo, not a lost
conversation.

Distinct from two neighbours that already exist and are easy to confuse with it:

* ``history.py``'s 2MB JSONL rotation is **storage**, not a user-facing state.
* ``api_chat_sessions_cleanup`` **evicts** stale sessions from memory (they reload from
  disk on demand). Archiving changes a session's declared lifecycle instead, and never
  drops it from the in-memory map.

The rule is: a session with no user/assistant turn for ``session.auto_archive_days``
days archives, unless it is pinned ``never_archive``. A session whose activity was
never recorded (an old one, or one untouched since the field landed) is treated as
NOT stale — guessing "archive it" from missing data would archive someone's history on
first upgrade.

**Both halves of the session list are swept, not just the resident one.** The chat list
merges in-memory sessions with sessions that exist only as a transcript on disk, and
``dashboard.restore_sessions`` defaults to False — so on a default install a chat that
has been idle for months is precisely the one that is NOT in memory. A sweep over
``state._sessions`` alone therefore skipped exactly the sessions it exists to catch,
while reporting success. Disk-only candidates are read from the conversation log's
metadata and archived by writing the lifecycle back to the transcript's metadata line.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing only
    from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_ARCHIVED = "archived"
VALID_LIFECYCLES = frozenset({LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED})

_DAY_SECS = 86400.0


def is_archived(session: Any) -> bool:
    """True when *session* is archived. Tolerant of a session predating the field."""
    return getattr(session, "lifecycle", LIFECYCLE_ACTIVE) == LIFECYCLE_ARCHIVED


def set_lifecycle(session: Any, lifecycle: str) -> bool:
    """Set *session*'s lifecycle. Returns True when it actually changed.

    Restoring also clears staleness by stamping activity: a session the user just
    pulled out of the archive must not be re-archived by the next sweep.
    """
    if lifecycle not in VALID_LIFECYCLES:
        raise ValueError(
            f"unknown lifecycle {lifecycle!r}; expected one of {sorted(VALID_LIFECYCLES)}"
        )
    if getattr(session, "lifecycle", LIFECYCLE_ACTIVE) == lifecycle:
        return False
    session.lifecycle = lifecycle
    if lifecycle == LIFECYCLE_ACTIVE:
        session.last_activity_at = time.time()
    session._dirty = True
    return True


def _disk_stale_keys(
    state: DashboardState, *, cutoff: float, resident: set[str], active_session: str
) -> list[tuple[float, str]]:
    """``(last_activity, key)`` for stale sessions that exist ONLY on disk.

    See the module docstring: with ``restore_sessions`` off (the default) an idle chat
    is never resident, so skipping this half made the rule inert for its main case.
    Reads metadata only — never a full transcript — so a sweep over hundreds of
    sessions stays cheap.
    """
    log = getattr(state, "conversation_log", None)
    if log is None:
        return []
    out: list[tuple[float, str]] = []
    try:
        listed = log.list_sessions()
    except Exception:  # a listing failure must not break the sweep
        logger.debug("auto-archive: listing disk sessions failed", exc_info=True)
        return []
    for entry in listed:
        raw_key = str(entry.get("key", "") or "")
        # Only dashboard chats: a channel-provider thread's lifecycle is owned by the
        # channel app, not by this rule.
        if raw_key.startswith("dashboard:"):
            name = raw_key[len("dashboard:") :]
        elif raw_key.startswith("dashboard_"):
            name = raw_key[len("dashboard_") :]
        else:
            continue
        if name in resident or name == active_session:
            continue
        try:
            meta = log.get_metadata(raw_key)
        except Exception:
            continue
        if not meta:
            continue
        if meta.get("lifecycle", LIFECYCLE_ACTIVE) == LIFECYCLE_ARCHIVED:
            continue
        if meta.get("never_archive"):
            continue
        if meta.get("app"):
            continue  # worker session, not a user conversation
        if meta.get("memory_mode") in ("incognito", "temporary"):
            continue
        last = float(meta.get("last_activity_at", 0.0) or 0.0)
        if not last or last >= cutoff:
            continue  # never recorded ⇒ not stale (see the module docstring)
        out.append((last, name))
    return out


def stale_session_keys(
    state: DashboardState,
    *,
    days: int,
    now: float | None = None,
    active_session: str = "",
) -> list[str]:
    """Keys of sessions the auto-archive rule would archive, oldest first.

    Pure and side-effect free so the caller can preview (and a test can assert)
    exactly what a sweep would touch. Covers BOTH resident and disk-only sessions —
    see the module docstring for why the disk half is not optional.

    Skipped: already-archived sessions, ``never_archive`` pins, the session the user
    is currently looking at, worker sessions owned by an app (they are not user
    conversations and the chat list already hides them), non-persistent
    (incognito/temporary) sessions, and anything whose activity was never recorded.
    """
    if days <= 0:
        return []  # 0 = off
    cutoff = (now if now is not None else time.time()) - days * _DAY_SECS
    stale: list[tuple[float, str]] = []
    resident: set[str] = set()
    for key in list(state._sessions):
        session = state._sessions.get(key)
        if session is None:
            continue
        resident.add(key)
        if getattr(session, "lifecycle", LIFECYCLE_ACTIVE) == LIFECYCLE_ARCHIVED:
            continue
        if getattr(session, "never_archive", False):
            continue
        if key == active_session:
            continue
        if getattr(session, "_app", ""):
            continue  # loop/campaign worker, not a user conversation
        last = float(getattr(session, "last_activity_at", 0.0) or 0.0)
        if not last:
            continue  # never recorded ⇒ not stale (see the module docstring)
        if last >= cutoff:
            continue
        stale.append((last, key))
    stale.extend(
        _disk_stale_keys(state, cutoff=cutoff, resident=resident, active_session=active_session)
    )
    stale.sort()
    return [key for _, key in stale]


def run_auto_archive(
    state: DashboardState,
    *,
    days: int,
    now: float | None = None,
    active_session: str = "",
) -> list[str]:
    """Archive every stale session. Returns the keys archived.

    Idempotent: a second call with the same clock archives nothing, because the first
    pass moved those sessions out of the candidate set.
    """
    keys = stale_session_keys(state, days=days, now=now, active_session=active_session)
    archived: list[str] = []
    for key in keys:
        session = state._sessions.get(key)
        if session is not None:
            if set_lifecycle(session, LIFECYCLE_ARCHIVED):
                archived.append(key)
            continue
        # Disk-only: no live object to mutate, so the lifecycle is written straight to
        # the transcript's metadata line. The session is NOT loaded into memory to do
        # it — restoring every stale chat just to archive it would undo the reason it
        # wasn't resident.
        if _archive_on_disk(state, key):
            archived.append(key)
    if archived:
        logger.info("auto-archived %d session(s) idle >%dd", len(archived), days)
    return archived


def _archive_on_disk(state: DashboardState, name: str) -> bool:
    """Mark a non-resident session archived by updating its transcript metadata."""
    log = getattr(state, "conversation_log", None)
    if log is None:
        return False
    for key in (f"dashboard:{name}", f"dashboard_{name}"):
        try:
            if not log.get_metadata(key):
                continue
            log.update_metadata(key, {"lifecycle": LIFECYCLE_ARCHIVED})
        except Exception:
            logger.debug("auto-archive: disk write failed for %r", key, exc_info=True)
            return False
        return True
    return False
