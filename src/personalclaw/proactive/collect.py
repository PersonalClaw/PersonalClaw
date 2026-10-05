"""Stage 1 — the live collectors.

Three lanes, three functions, one rule each about what "accumulated" means:

* **inbox** — rows still wanting attention (`pending`/`seen`), muted threads and dismissed
  items already excluded by the store's own filters. Read from `InboxStore.items` rather than
  re-polling a source: the gate deliberately runs at DIGEST time over STORED items, because
  `evaluate_alert` fires once at ingestion and never re-evaluates. Re-polling here would be a
  second ingestion path with a second set of alert semantics. Only rows about no chat: the digest
  is no chat's work (`inbox_reach`), so a chat's own row reaches neither its model nor its run.
* **channel** — a `channel:` session whose last turn is not the assistant's. That is the
  cheapest honest reading of "unresolved": the machine has the ball. Sessions the user has
  since answered themselves fall out with no bookkeeping.
* **run** — the Run Ledger's own rows for recent runs that have ENDED (the substrate's
  materiality), NOT a fresh classification. A run that failed, was cancelled or was handed to you
  is `error`, one whose effect LANDED is `action`, one that only produced words is `response`. A
  run still going — the digest's own run among them — is not an outcome and is not collected,
  and a run is in the window it ENDED in, whenever it started: one that was still going when the
  last digest ran is in the next. A Morning triage run is the digest itself, never an item in one.
  This plan adds zero run instrumentation, so a materiality this module computed itself would be
  a second dialect for a question the ledger answers.

Each lane reads one item in one place (``_inbox_item``, ``_channel_item``, ``_run_item``), and
:func:`current_item` asks that same reading of one item whatever the window: whether a proposal
an earlier digest made still applies (`carry.recheck`) is the question the lane already answers.

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
from typing import TYPE_CHECKING, Any

from personalclaw.inbox import OPEN_STATUSES
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

if TYPE_CHECKING:
    from personalclaw.subagent_reach import Reader

logger = logging.getLogger(__name__)

#: Inbox statuses that still want attention. `handled`, `dismissed`, `sent` and `filtered` are
#: all answers already given — re-collecting them would make the digest a list of things the
#: user already dealt with, which is the fastest way to teach someone to ignore it.
#:
#: An ALIAS, not a second definition: this used to re-spell the set and would then have drifted
#: from the counts on the inbox surface, so a digest could announce work the inbox no longer shows
#: (or stay silent about work it does). One owner in `inbox` (issue 493); the name stays because
#: this module's own vocabulary calls the lane "attention" and it is re-exported below.
ATTENTION_STATUSES = OPEN_STATUSES

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


def _digest_reads(state: Any) -> Reader:
    """Who the digest reads the Inbox as: no chat's work (``inbox_reach``), so it reads what is
    about no chat. What it collects goes to its model and into its run's record, a run of yours,
    which every agent's workflow tools read (``workflows.chat_runs``), so a chat's own row (its
    approvals, the questions its work put to you, what its own runs wait on), a Temporary or
    Incognito chat's above all, reaches neither."""
    from personalclaw.subagent_reach import reader_of_work

    return reader_of_work(state, "")


def _inbox_item(item: Any, reader: Reader) -> CollectedItem | None:
    """One Inbox row as the lane collects it, or None when it no longer wants attention or is a
    row *reader* does not read (:func:`_digest_reads`)."""
    from personalclaw import inbox_reach

    if str(getattr(item, "status", "")) not in ATTENTION_STATUSES:
        return None
    if not inbox_reach.reads(reader, item):
        return None
    channel_name = str(getattr(item, "channel_name", "") or "")
    return CollectedItem(
        source=SOURCE_INBOX,
        source_id=str(getattr(item, "id", "")),
        title=_clip(getattr(item, "message", "")) or f"message in {channel_name}",
        detail=channel_name,
        sender=str(getattr(item, "sender_name", "") or ""),
        # An inbox row is somebody waiting on the user: `response` weight, so it ranks under a
        # run that already changed something but over noise.
        materiality=MATERIALITY_RESPONSE,
        ts=str(getattr(item, "ts", "") or ""),
        # A reply proposal is offered only for a message that takes one.
        can_reply=bool(getattr(item, "can_reply", False)),
    )


def collect_inbox(store: Any, *, since_ts: float = 0.0, state: Any = None) -> list[CollectedItem]:
    """Inbox rows still wanting attention that are about no chat (:func:`_digest_reads`, over the
    gateway's dashboard *state*), newest-first within the window.

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
    reader = _digest_reads(state)
    for item in items:
        try:
            created = float(getattr(item, "created_at", 0.0) or 0.0)
            if since_ts and created and created < since_ts:
                continue
            collected = _inbox_item(item, reader)
            if collected is not None:
                out.append(collected)
        except Exception:  # noqa: BLE001 - one bad row must not lose the lane
            logger.warning("triage: skipped an unreadable inbox row", exc_info=True)
    return out


def _channel_item(key: str, session: Any) -> CollectedItem | None:
    """One `channel:` session as the lane collects it: None unless the machine has the ball."""
    if not str(key).startswith("channel:"):
        return None
    messages = list(getattr(session, "messages", []) or [])
    if not messages:
        return None
    last = messages[-1]
    role = str(last.get("role", "") if isinstance(last, dict) else getattr(last, "role", ""))
    if role == "assistant":
        return None
    last_activity = float(getattr(session, "last_activity_at", 0.0) or 0.0)
    title = str(getattr(session, "title", "") or "").strip() or str(key)
    return CollectedItem(
        source=SOURCE_CHANNEL,
        source_id=str(key),
        title=f"unanswered in {title}",
        detail=_clip(last.get("content", "") if isinstance(last, dict) else str(last)),
        sender=role or "user",
        materiality=MATERIALITY_RESPONSE,
        ts=f"{last_activity:.0f}",
    )


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
            last_activity = float(getattr(session, "last_activity_at", 0.0) or 0.0)
            if since_ts and last_activity and last_activity < since_ts:
                continue
            collected = _channel_item(str(key), session)
            if collected is not None:
                out.append(collected)
        except Exception:  # noqa: BLE001
            logger.warning("triage: skipped an unreadable channel session", exc_info=True)
    return out


def _run_materiality(status: str, effects: int) -> str:
    """A run's materiality, read off the run store's own status and the ledger.

    Only an ENDED run (``RUN_PHASES``) is an outcome. The digest collects while its own run is
    running, and that run had written an effect, so checking effects first listed it under "What
    your machine did" as "morning-triage: running (1 effect)". The statuses are the run store's
    (``RunStatus``): this read ``"completed"``, which the store never writes, so a finished run that
    only produced words was never collected. A status the store does not know is not collected.
    """
    from personalclaw.workflows.models import RUN_PHASES, LifecyclePhase, RunStatus

    try:
        run_status = RunStatus(status)
    except ValueError:
        return MATERIALITY_NONE
    if RUN_PHASES[run_status] is not LifecyclePhase.ENDED:
        return MATERIALITY_NONE
    # Failed, stopped, or handed to you: each needs a human, which is what `error` means here.
    if run_status in (RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.ESCALATED):
        return MATERIALITY_ERROR
    if effects:
        return MATERIALITY_ACTION
    return MATERIALITY_RESPONSE


def _effects_that_landed(rows: list[dict[str, Any]]) -> int:
    """How many of a run's effects committed and still stand: one per node instance at most.

    The ledger writes an ``attempted`` row before a dispatch and a ``committed`` row when it lands
    (`workflows.effects.EffectStatus`), so counting the rows counted one archive as "2 effects"
    and an attempt that failed as an effect the machine had. The run store's own reading of a
    path's standing effect (`committed_effect`) is the one asked here.
    """
    from personalclaw.workflows.effects import EffectRecord, committed_effect

    by_path: dict[str, list[EffectRecord]] = {}
    for row in rows:
        record = EffectRecord.from_event(row)
        by_path.setdefault(record.instance_path, []).append(record)
    return sum(1 for records in by_path.values() if committed_effect(records) is not None)


def _run_item(run: Any, store: Any) -> CollectedItem | None:
    """One run as the lane collects it: None for a run that has not ended, and for a digest."""
    from personalclaw.ledger import read_events
    from personalclaw.ledger.kinds import EFFECT
    from personalclaw.proactive.surface import TRIAGE_WORKFLOW
    from personalclaw.triggers.delivery import status_url

    if str(getattr(run, "workflow_name", "") or "") == TRIAGE_WORKFLOW:
        # The digest reports what happened. Listing the one before it under "What your machine
        # did" would report the report.
        return None
    status = str(getattr(getattr(run, "status", ""), "value", getattr(run, "status", "")))
    effects = 0
    try:
        effects = _effects_that_landed(read_events(store, str(run.id), kinds={EFFECT}))
    except Exception:  # noqa: BLE001 - a run with no ledger file yet is not an error
        effects = 0
    materiality = _run_materiality(status, effects)
    if materiality == MATERIALITY_NONE:
        # A run that has not ended is not an outcome. Collecting it would put a running job in
        # the "what your machine did" section, which is a claim about the past.
        return None
    wrote = f" ({effects} effect{'s' if effects != 1 else ''})" if effects else ""
    return CollectedItem(
        source=SOURCE_RUN,
        source_id=str(run.id),
        title=f"{run.workflow_name}: {status}{wrote}",
        detail=str(getattr(run, "error_message", "") or "")[:DETAIL_CHARS],
        materiality=materiality,
        # The run's page in the dashboard, from the one builder every run link uses. It was the
        # path `/runs/<id>`, which the gateway answers with the app itself, so the card's link to
        # the item opened Home.
        permalink=status_url(run_id=str(run.id)),
        ts=str(getattr(run, "created_at", "") or ""),
    )


def collect_runs(*, since: str = "", limit: int = RUN_SCAN_LIMIT) -> list[CollectedItem]:
    """Recent background runs that ENDED in the window, weighted by the effects their ledger says
    landed.

    `since` is an ISO stamp compared lexicographically with when each run ended (its
    `completed_at`, else its `created_at`): exact for the ISO-8601 stamps the run store writes,
    and it avoids parsing a timestamp only to compare it. When it ENDED, not when it started: a
    run still going when the last digest collected was not in that digest, and one filtered on its
    start would never be in any.
    """
    try:
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
    out: list[CollectedItem] = []
    for run in runs:
        try:
            ended = str(getattr(run, "completed_at", "") or getattr(run, "created_at", "") or "")
            if since and ended and ended < since:
                continue
            collected = _run_item(run, run_store)
            if collected is not None:
                out.append(collected)
        except Exception:  # noqa: BLE001
            logger.warning("triage: skipped an unreadable run row", exc_info=True)
    return out


class LaneUnreadable(Exception):
    """The lane an item belongs to cannot be read here, so nothing is known about the item."""


def current_item(
    source: str, source_id: str, *, inbox_store: Any = None, state: Any = None
) -> CollectedItem | None:
    """*source_id* as its lane collects it now, whatever the window, or None when it no longer
    wants your attention: dealt with, answered, or gone, or an Inbox row the digest does not read.

    Raises :class:`LaneUnreadable` when its lane cannot be read here (an absent handle, a read
    that fails, a lane this module does not have): a caller must not take not knowing for "gone".
    """
    try:
        if source == SOURCE_INBOX and inbox_store is not None:
            row = dict(getattr(inbox_store, "items", {}) or {}).get(source_id)
            return _inbox_item(row, _digest_reads(state)) if row is not None else None
        if source == SOURCE_CHANNEL and state is not None:
            session = dict(getattr(state, "_sessions", {}) or {}).get(source_id)
            return _channel_item(source_id, session) if session is not None else None
        if source == SOURCE_RUN:
            from personalclaw.workflows import store as run_store

            run = run_store.get(source_id)
            return _run_item(run, run_store) if run is not None else None
    except Exception as exc:  # noqa: BLE001 - a lane that cannot be read is unknown, not empty
        raise LaneUnreadable(f"{source} lane unreadable: {type(exc).__name__}") from exc
    raise LaneUnreadable(f"{source} lane is not readable here")


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
        items.extend(collect_inbox(inbox_store, since_ts=since_ts, state=state))
    if state is not None:
        items.extend(collect_channels(state, since_ts=since_ts))
    if include_runs:
        items.extend(collect_runs(since=since_iso))
    return items


__all__ = [
    "ATTENTION_STATUSES",
    "DETAIL_CHARS",
    "RUN_SCAN_LIMIT",
    "LaneUnreadable",
    "collect_all",
    "collect_channels",
    "collect_inbox",
    "collect_runs",
    "current_item",
]
