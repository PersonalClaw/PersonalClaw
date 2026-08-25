"""Stage 1 — the live collectors (PROACTIVE-ASSISTANT §1.1).

Three lanes, three functions, one rule each about what "accumulated" means:

* **inbox** — rows still wanting attention (`pending`/`seen`), muted threads and dismissed
  items already excluded by the store's own filters. Read from `InboxStore.items` rather than
  re-polling a source: §1.2's gate deliberately runs at DIGEST time over STORED items, because
  `evaluate_alert` fires once at ingestion and never re-evaluates. Re-polling here would be a
  second ingestion path with a second set of alert semantics.
* **channel** — a `channel:` session whose last turn is not the assistant's. That is the
  cheapest honest reading of "unresolved": the machine has the ball. Sessions the user has
  since answered themselves fall out with no bookkeeping.
* **run** — the Run Ledger's own rows for recent runs (AUTO-R2 materiality), NOT a fresh
  classification. A run that wrote something is `action`, one that failed is `error`, one that
  only produced words is `response`. This plan adds zero run instrumentation, so a materiality
  this module computed itself would be a second dialect for a question the ledger answers.

Every collector is **defensive by construction**: a lane that cannot be read contributes zero
items and a warning, never an exception. A digest is a scheduled unattended run, so one broken
lane must not take the other two down — an empty channel lane is a smaller digest, an
exception is no digest at all, and the second failure is invisible until someone notices they
stopped arriving.

Nothing here numbers anything. Ordinals come from `build_manifest` over the union, because a
collector only sees its own lane and numbering is a property of the set (see `manifest.py`).
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.proactive.manifest import (
    MATERIALITY_ACTION,
    MATERIALITY_ERROR,
    MATERIALITY_NONE,
    MATERIALITY_RESPONSE,
    SOURCE_CHANNEL,
    SOURCE_INBOX,
    SOURCE_RUN,
    CollectedItem,
)

logger = logging.getLogger(__name__)

#: Inbox statuses that still want attention. `handled`, `dismissed`, `sent` and `filtered` are
#: all answers already given — re-collecting them would make the digest a list of things the
#: user already dealt with, which is the fastest way to teach someone to ignore it.
ATTENTION_STATUSES = frozenset({"pending", "seen"})

#: How many recent runs the run lane inspects. A ceiling rather than a window-only filter: the
#: run lane reads one ledger file per run, so an unbounded "since last digest" after a busy
#: night would make the collect stage the most expensive thing in the pipeline.
RUN_SCAN_LIMIT = 25

#: One line of an item's body is enough for a digest line and for the gate. The full message
#: is a click away in the inbox; sending all of it multiplies the fenced payload of the one
#: paid stage by the length of the user's longest email.
DETAIL_CHARS = 160


def _clip(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:DETAIL_CHARS]


def collect_inbox(store: Any, *, since_ts: float = 0.0) -> list[CollectedItem]:
    """Inbox rows still wanting attention, newest-first within the window.

    `since_ts` is an epoch float compared against `created_at`; `0.0` means "everything still
    pending", which is the right default for a FIRST digest — a fresh install with a
    three-week-old backlog should see it once, not never.
    """
    out: list[CollectedItem] = []
    try:
        items = list(getattr(store, "items", {}).values())
    except Exception:  # noqa: BLE001 - a lane that cannot be read contributes nothing
        logger.warning("triage: inbox lane unreadable", exc_info=True)
        return []
    for item in items:
        try:
            if str(getattr(item, "status", "")) not in ATTENTION_STATUSES:
                continue
            created = float(getattr(item, "created_at", 0.0) or 0.0)
            if since_ts and created and created < since_ts:
                continue
            channel_name = str(getattr(item, "channel_name", "") or "")
            out.append(
                CollectedItem(
                    source=SOURCE_INBOX,
                    source_id=str(getattr(item, "id", "")),
                    title=_clip(getattr(item, "message", "")) or f"message in {channel_name}",
                    detail=channel_name,
                    sender=str(getattr(item, "sender_name", "") or ""),
                    # An inbox row is somebody waiting on the user: `response` weight, so it
                    # ranks under a run that already changed something but over noise.
                    materiality=MATERIALITY_RESPONSE,
                    ts=str(getattr(item, "ts", "") or ""),
                )
            )
        except Exception:  # noqa: BLE001 - one bad row must not lose the lane
            logger.warning("triage: skipped an unreadable inbox row", exc_info=True)
    return out


def collect_channels(state: Any, *, since_ts: float = 0.0) -> list[CollectedItem]:
    """`channel:` sessions whose last turn is not the assistant's — the machine has the ball."""
    out: list[CollectedItem] = []
    try:
        sessions = dict(getattr(state, "_sessions", {}) or {})
    except Exception:  # noqa: BLE001
        logger.warning("triage: channel lane unreadable", exc_info=True)
        return []
    for key, session in sessions.items():
        try:
            if not str(key).startswith("channel:"):
                continue
            last_activity = float(getattr(session, "last_activity_at", 0.0) or 0.0)
            if since_ts and last_activity and last_activity < since_ts:
                continue
            messages = list(getattr(session, "messages", []) or [])
            if not messages:
                continue
            last = messages[-1]
            role = str(
                last.get("role", "") if isinstance(last, dict) else getattr(last, "role", "")
            )
            if role == "assistant":
                continue
            title = str(getattr(session, "title", "") or "").strip() or str(key)
            out.append(
                CollectedItem(
                    source=SOURCE_CHANNEL,
                    source_id=str(key),
                    title=f"unanswered in {title}",
                    detail=_clip(last.get("content", "") if isinstance(last, dict) else str(last)),
                    sender=role or "user",
                    materiality=MATERIALITY_RESPONSE,
                    ts=f"{last_activity:.0f}",
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("triage: skipped an unreadable channel session", exc_info=True)
    return out


def _run_materiality(status: str, effects: int) -> str:
    """AUTO-R2's weight for a finished run, read off the ledger rather than re-judged."""
    if status in ("failed", "cancelled"):
        return MATERIALITY_ERROR
    if effects:
        return MATERIALITY_ACTION
    if status == "completed":
        return MATERIALITY_RESPONSE
    return MATERIALITY_NONE


def collect_runs(*, since: str = "", limit: int = RUN_SCAN_LIMIT) -> list[CollectedItem]:
    """Recent background runs, weighted by their own ledger's `effect` rows.

    `since` is an ISO `created_at` string compared lexicographically — which is exact for the
    ISO-8601 stamps the run store writes, and avoids parsing a timestamp only to compare it.
    """
    try:
        from personalclaw.ledger import read_events
        from personalclaw.ledger.kinds import EFFECT
        from personalclaw.workflows import store as run_store
    except Exception:  # noqa: BLE001 - engine absent (a bare library import) → no run lane
        logger.warning("triage: run lane unavailable", exc_info=True)
        return []

    try:
        runs, _total = run_store.list_runs(limit=max(1, limit))
    except Exception:  # noqa: BLE001
        logger.warning("triage: run lane unreadable", exc_info=True)
        return []

    # The MODULE is the `LedgerStore` — the protocol is one `read_jsonl` method and
    # `workflows.store` implements it (the same handle `resume_account` reads a run through).
    # `LedgerStore()` is a Protocol and cannot be instantiated.
    store = run_store
    out: list[CollectedItem] = []
    for run in runs:
        try:
            created = str(getattr(run, "created_at", "") or "")
            if since and created and created < since:
                continue
            status = str(getattr(getattr(run, "status", ""), "value", getattr(run, "status", "")))
            effects = 0
            try:
                effects = len(read_events(store, str(run.id), kinds={EFFECT}))
            except Exception:  # noqa: BLE001 - a run with no ledger file yet is not an error
                effects = 0
            materiality = _run_materiality(status, effects)
            if materiality == MATERIALITY_NONE:
                # An unfinished run is not an outcome. Collecting it would put a running job in
                # the "what your machine did" section, which is a claim about the past.
                continue
            wrote = f" ({effects} effect{'s' if effects != 1 else ''})" if effects else ""
            out.append(
                CollectedItem(
                    source=SOURCE_RUN,
                    source_id=str(run.id),
                    title=f"{run.workflow_name}: {status}{wrote}",
                    detail=str(getattr(run, "error_message", "") or "")[:DETAIL_CHARS],
                    materiality=materiality,
                    permalink=f"/runs/{run.id}",
                    ts=created,
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("triage: skipped an unreadable run row", exc_info=True)
    return out


def collect_all(
    *,
    inbox_store: Any = None,
    state: Any = None,
    since_ts: float = 0.0,
    since_iso: str = "",
    include_runs: bool = True,
) -> list[CollectedItem]:
    """The union of the three lanes. A `None` handle means that lane is simply absent."""
    items: list[CollectedItem] = []
    if inbox_store is not None:
        items.extend(collect_inbox(inbox_store, since_ts=since_ts))
    if state is not None:
        items.extend(collect_channels(state, since_ts=since_ts))
    if include_runs:
        items.extend(collect_runs(since=since_iso))
    return items


__all__ = [
    "ATTENTION_STATUSES",
    "DETAIL_CHARS",
    "RUN_SCAN_LIMIT",
    "collect_all",
    "collect_channels",
    "collect_inbox",
    "collect_runs",
]
