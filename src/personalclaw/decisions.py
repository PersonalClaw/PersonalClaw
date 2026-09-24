"""The personal decision journal — core (PROACTIVE-ASSISTANT §2, atom PA-4).

A decision entry is a **user item**: a document about the user's life, so it lives in
``knowledge.db`` as the 13th ``NATIVE_TYPES`` entry, created through the one true path
(``store.create_typed_item(item_type="decision", provider="native")``) and riding the
Passthrough pipeline graph exactly like a note or a journal entry. What the research
shape (TradingAgents' one markdown file with HTML-comment delimiters) contributes is the
*lifecycle*, not the file: append-only entries, ``pending → resolved``, pending entries
never evicted.

The distilled lesson the harness learns from a resolution is **memory** — a ``lesson.*``
semantic row written through :meth:`MemoryService.write_lesson`. The two stores stay
structurally uncoupled: ``lesson_memory_key`` is a soft string reference, deliberately
not a foreign key, and it is read back out of the memory store rather than re-derived
here so there is only ever one spelling of a lesson's key.

Nothing in this module writes memory except :func:`resolve_decision`'s final lesson step,
and nothing writes knowledge except the user's own entries and their resolution updates.

**Every store is an injected seam.** ``store`` / ``trigger_store`` / ``memory`` default to
the live singletons but are parameters, so each branch below is testable against a
tmp-path store without a gateway, an embedder, or a model — the same seam
``triggers.tools.create`` uses for its cadence converter.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: The item_type. One string, imported by every registration site, so the type cannot be
#: spelled two ways between the provider, the pipeline graph map and the HTTP handler.
DECISION_TYPE = "decision"

#: The lifecycle. ``pending`` entries are never evicted; ``abandoned`` retires the review
#: trigger without pretending an outcome exists.
DECISION_STATUSES: tuple[str, ...] = ("pending", "resolved", "abandoned")

#: The domain axis the calibration strip groups by.
DECISION_DOMAINS: tuple[str, ...] = (
    "career",
    "financial",
    "technical",
    "personal",
    "health",
    "other",
)

#: The resolution grade. ``too_early`` is not an outcome — it re-arms the review instead
#: of resolving the item, which is why it is in this vocabulary rather than a separate flag.
RESOLUTION_GRADES: tuple[str, ...] = (
    "better",
    "as_expected",
    "worse",
    "mixed",
    "too_early",
)

#: Grades that count toward calibration. ``mixed``/``too_early`` are deliberately excluded:
#: a calibration strip that scored "mixed" as a hit or a miss would be inventing a verdict
#: the user declined to give.
CALIBRATED_GRADES: tuple[str, ...] = ("better", "as_expected", "worse")

#: Resolved decisions per domain below which a rate is reported as not-yet-meaningful
#: (§2.5's "7 decisions — too few to mean much"). Named rather than left as a bare default
#: because the HTTP surface forwards the threshold to the view: two spellings of ten and the
#: strip could caveat a bucket the backend had already called honest.
CALIBRATION_MIN_N = 10

#: How many times a ``too_early`` may re-arm the review before the item goes stale-pending.
#: Two, then the journal view surfaces it — the alternative is nagging forever.
MAX_DEFERRALS = 2

#: Each deferral extends the horizon by half of the original span.
DEFERRAL_FACTOR = 0.5

#: The bundled template the review trigger fires.
REVIEW_WORKFLOW = "decision-review"

#: The trigger-id namespace. DETERMINISTIC because convergence needs it: a generated slug
#: would add a second reminder every time the horizon was edited, and there would be no way
#: to find the reminder belonging to a decision in order to retire it. This is the
#: substrate's commitment-conversion pattern, and the same reasoning as
#: ``selfqa.install.WATCH_TRIGGER_ID``.
TRIGGER_NAMESPACE = "system:decision-journal"

#: `created_by` for the review triggers. Namespaced so the Automations page can attribute
#: a row to this feature rather than showing an anonymous "system" reminder.
TRIGGER_CREATED_BY = TRIGGER_NAMESPACE

#: The lesson category. Filterable as a decision lesson rather than mixed into general
#: knowledge — the same reason the grill protocol writes ``category="decision"``.
LESSON_CATEGORY = "decision"


class DecisionError(ValueError):
    """A caller-fixable problem: an unknown id, a bad grade, an out-of-range confidence.

    ``ValueError`` on purpose — the chat-tool dispatcher already turns a ``ValueError``
    into a surfaced tool error with the message intact, so the three tools need no
    per-error plumbing.
    """


def review_trigger_id(item_id: str) -> str:
    """The one review trigger's id for *item_id*."""
    return f"{TRIGGER_NAMESPACE}:{item_id}"


# ── stores (injected seams, live singletons by default) ───────────────────────


def _knowledge_store(store: Any = None) -> Any:
    if store is not None:
        return store
    from personalclaw.knowledge import get_knowledge_store

    return get_knowledge_store()


def _triggers(trigger_store: Any = None) -> Any:
    if trigger_store is not None:
        return trigger_store
    # Rooted at the ACTIVE home through `config.loader.config_dir`, which is what makes a
    # test's isolated home actually isolate this path.
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=config_dir())


def _memory(memory: Any = None) -> Any:
    """A :class:`MemoryService` over the record store, or *memory* if supplied.

    The standalone construction the CLI's ``_learn`` uses: memory.db is the sole lesson
    store and it persists lessons with no embedder configured (vector is optional), so a
    decision resolved on a machine with embeddings off still gets its lesson.
    """
    if memory is not None:
        return memory
    from personalclaw.embedding_providers.registry import get_active_embedding_dim
    from personalclaw.memory_service import MemoryService
    from personalclaw.vector_memory import VectorMemoryStore

    vs = VectorMemoryStore(embedding_dim=get_active_embedding_dim() or 384)
    vs.init()
    return MemoryService.over_vector_store(vs)


def default_horizon_days() -> int:
    """``proactive.decision_default_horizon_days`` (90), or 90 if the config is unreadable."""
    try:
        from personalclaw.config.loader import AppConfig

        return int(AppConfig.load().proactive.decision_default_horizon_days)
    except Exception:
        logger.debug("decision journal: could not read the horizon default", exc_info=True)
        return 90


# ── metadata accessors ───────────────────────────────────────────────────────


def decision_meta(item: dict) -> dict:
    """The ``decision`` sub-object of an item's metadata JSON, or ``{}``.

    ``file_metadata`` is the items table's metadata column (``store.get_item`` parses it),
    which is why the structured fields need no new column and leave ``_migrate`` untouched.
    """
    meta = item.get("file_metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta or "{}")
        except (TypeError, ValueError):
            meta = {}
    if not isinstance(meta, dict):
        return {}
    sub = meta.get("decision")
    return dict(sub) if isinstance(sub, dict) else {}


def _write_meta(store: Any, item_id: str, patch: dict, *, touch: bool = True) -> dict:
    """Merge *patch* into the item's ``decision`` metadata and persist it.

    Read-modify-write of the WHOLE metadata dict rather than a JSON patch, because the
    column also carries non-decision keys (``also_seen_in``, ``content_hash``) that a
    replacing write would drop.
    """
    item = store.get_item(item_id)
    if not item:
        raise DecisionError(f"no decision item {item_id!r}")
    meta = item.get("file_metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta or "{}")
        except (TypeError, ValueError):
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    current = meta.get("decision")
    merged = {**(current if isinstance(current, dict) else {}), **patch}
    meta["decision"] = merged
    store.update_item(item_id, touch=touch, file_metadata=meta)
    return merged


def _require_decision(store: Any, item_id: str) -> dict:
    item = store.get_item(item_id)
    if not item or item.get("item_type") != DECISION_TYPE:
        raise DecisionError(f"no decision item {item_id!r}")
    return item


# ── horizons ─────────────────────────────────────────────────────────────────


def _parse_horizon(value: str, *, now: datetime | None = None) -> datetime:
    """``YYYY-MM-DD`` or a full ISO timestamp → an aware-naive local datetime.

    Refuses a horizon in the past: a one-shot ``at`` trigger whose time has elapsed is
    unarmable (``triggers.arm`` returns ``""`` rather than guessing), so accepting it would
    persist a decision whose reminder can never fire — an inert control, not a reminder.
    """
    text = (value or "").strip()
    if not text:
        raise DecisionError("review_horizon is required")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise DecisionError(
            f"review_horizon {text!r} is not a date — use YYYY-MM-DD or an ISO timestamp"
        ) from None
    ref = now or datetime.now()
    if parsed <= ref:
        raise DecisionError(
            f"review_horizon {text!r} is in the past — a review that cannot fire is not a review"
        )
    return parsed


def horizon_from_days(days: int, *, now: datetime | None = None) -> str:
    """``days`` from *now* as ``YYYY-MM-DD`` — the default horizon's spelling."""
    ref = now or datetime.now()
    return (ref + timedelta(days=max(1, int(days)))).date().isoformat()


# ── the review trigger ───────────────────────────────────────────────────────


def mint_review_trigger(
    item_id: str,
    *,
    title: str,
    horizon: str,
    expectation: str,
    confidence: float,
    trigger_store: Any = None,
    now: datetime | None = None,
) -> str:
    """Create (or re-point) the ONE review trigger for *item_id*; return its id.

    ``store.upsert`` on a deterministic id is what makes this idempotent: logging the same
    decision twice, or editing its horizon, re-points the existing row instead of stacking
    reminders. That is the substrate's commitment-conversion pattern, and the bug
    ``reconcile_digest_cron`` records is what a generated slug costs here.

    The expectation and confidence are carried as workflow INPUTS rather than looked up at
    fire time on purpose: the review card has to quote the prediction the user made *when
    they made it*, and every mutation path below re-mints, so the inputs cannot go stale.
    """
    from personalclaw.triggers import screen as _screen
    from personalclaw.triggers.arm import arm as _arm
    from personalclaw.triggers.models import Trigger

    store = _triggers(trigger_store)
    at = _parse_horizon(horizon, now=now)
    tid = review_trigger_id(item_id)
    existing = store.get(tid)
    # `store.get` returns a LoadedTrigger — the row plus whatever was wrong with reading it.
    # The entity to write is the `.trigger` inside; upserting the pair would persist
    # something with no id.
    trigger = (
        existing.trigger
        if existing is not None
        else Trigger(id=tid, name=f"Decision review: {title}"[:120], kind="clock")
    )
    trigger.name = f"Decision review: {title}"[:120]
    trigger.kind = "clock"
    trigger.enabled = True
    trigger.created_by = TRIGGER_CREATED_BY
    # ONE-SHOT. `delete_after_run` retires the row on its single fire, so a decision that is
    # never resolved leaves no dormant reminder behind and no re-nag loop.
    trigger.spec = {"kind": "at", "at": at.timestamp(), "delete_after_run": True}
    trigger.workflow = {
        "inline": {
            "provider": "run-workflow",
            "config": {
                "workflow": REVIEW_WORKFLOW,
                "inputs": {
                    "decision_id": item_id,
                    "summary": title,
                    "expectation": expectation,
                    "confidence": f"{float(confidence):.2f}",
                    "review_horizon": horizon,
                },
            },
        }
    }
    # The review card is an attention event, so it routes to the inbox — the notify gate
    # (quiet hours) applies there, which is the whole reason not to hand-roll a channel post.
    trigger.delivery = "inbox"
    # Decision 7 / R3: FREEZE the capability set at save. A system-created trigger's opt-in
    # is the code path that created it; without the frozen grant the fence denies on empty
    # and every review would refuse on its only fire.
    trigger.capabilities = _screen.capabilities_for_action(trigger)
    armed = _arm(trigger)
    if armed:
        trigger.next_fire_at = armed
    store.upsert(trigger)
    return tid


def retire_review_trigger(item_id: str, *, trigger_store: Any = None) -> bool:
    """Delete the review trigger for *item_id*. True if a row was there to delete.

    DELETE rather than disable, unlike ``selfqa.install.reconcile``: that watcher is a
    standing setting the user configured and wants to see switched off, while this is a
    one-shot reminder for a decision that is now resolved or abandoned. A disabled row here
    would be a reminder in the user's automation list for something already answered.
    """
    store = _triggers(trigger_store)
    tid = review_trigger_id(item_id)
    if store.get(tid) is None:
        return False
    store.delete(tid)
    return True


# ── log ──────────────────────────────────────────────────────────────────────


def log_decision(
    *,
    summary: str,
    content: str = "",
    expectation: str,
    # UNTRUSTED at this boundary: the chat tool's caller is a model, which will send
    # `"0.7"` or omit the field entirely. Typed as what it actually receives and coerced
    # below, so the refusal is this function's (a `DecisionError` the tool surfaces) rather
    # than a `TypeError` raised somewhere deeper with no message a model can act on.
    confidence: float | int | str | None,
    domain: str = "other",
    review_horizon: str = "",
    tags: list[str] | None = None,
    store: Any = None,
    trigger_store: Any = None,
    enqueue: Any = None,
    now: datetime | None = None,
) -> dict:
    """Record a decision and arm its single review. Returns the created row's projection.

    The knowledge item is created BEFORE the trigger and the trigger is minted against the
    item's real id, because the id is what makes the trigger deterministic. If arming
    raises (an unparseable horizon reaches here as a :class:`DecisionError`) the item is
    left in place with ``reminder_trigger_id: null`` rather than deleted — a decision the
    user typed is theirs whether or not a reminder could be scheduled, and
    :func:`reschedule_review` can arm it later.
    """
    title = (summary or "").strip()
    if not title:
        raise DecisionError("log_decision requires a one-line summary of the decision")
    prediction = (expectation or "").strip()
    if not prediction:
        raise DecisionError(
            "log_decision requires an expectation — a decision with no prediction cannot "
            "teach anything at its horizon"
        )
    if confidence is None:
        raise DecisionError("confidence must be a number between 0 and 1")
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        raise DecisionError("confidence must be a number between 0 and 1") from None
    if not 0.0 <= conf <= 1.0:
        raise DecisionError(f"confidence {conf!r} is out of range — it must be between 0 and 1")
    dom = (domain or "other").strip().lower()
    if dom not in DECISION_DOMAINS:
        raise DecisionError(f"unknown domain {dom!r} — one of {', '.join(DECISION_DOMAINS)}")
    horizon = (review_horizon or "").strip() or horizon_from_days(default_horizon_days(), now=now)
    # Parsed HERE, before the item exists, even though `mint_review_trigger` parses it again.
    # Validating only at the mint would persist a decision and then refuse — leaving an entry
    # with no reminder and no way for the caller to tell that from a successful log.
    _parse_horizon(horizon, now=now)

    ks = _knowledge_store(store)
    # The ONE true create path: the same call the native provider makes, so a decision is
    # an ordinary library item — FTS-indexed at insert, embeddable, @-pickable, and visible
    # in the knowledge graph through entity extraction.
    item_id = ks.create_typed_item(
        item_type=DECISION_TYPE,
        title=title,
        content=content or "",
        tags=[str(t) for t in (tags or [])],
        provider="native",
        extra={
            "processing_status": "queued",
            "file_metadata": {
                "decision": {
                    "status": "pending",
                    "domain": dom,
                    "expectation": prediction,
                    "confidence": conf,
                    "options_considered": [],
                    "review_horizon": horizon,
                    "reminder_trigger_id": None,
                    "deferrals": 0,
                    "outcome": None,
                    "outcome_grade": None,
                    "outcome_captured_at": None,
                    "lesson_memory_key": None,
                }
            },
        },
    )
    # `create_typed_item` returns None only on the source-poll dedup path (source_id + guid
    # supplied); a native create never dedups, so the id is always present.
    if not item_id:
        raise DecisionError("could not create the decision item")

    tid = mint_review_trigger(
        item_id,
        title=title,
        horizon=horizon,
        expectation=prediction,
        confidence=conf,
        trigger_store=trigger_store,
        now=now,
    )
    _write_meta(ks, item_id, {"reminder_trigger_id": tid})
    # Enrich through the node graph (Passthrough → consolidate → entities → embed) so a
    # logged decision is vector-searchable and graph-visible like any other library item.
    _reingest(item_id, enqueue)
    return projection(_require_decision(ks, item_id))


def reschedule_review(
    item_id: str,
    review_horizon: str,
    *,
    store: Any = None,
    trigger_store: Any = None,
    now: datetime | None = None,
) -> dict:
    """Move a pending decision's horizon and re-point its one review trigger."""
    ks = _knowledge_store(store)
    item = _require_decision(ks, item_id)
    meta = decision_meta(item)
    if meta.get("status") != "pending":
        raise DecisionError(
            f"decision {item_id!r} is {meta.get('status')!r} — "
            f"only a pending decision has a horizon"
        )
    tid = mint_review_trigger(
        item_id,
        title=str(item.get("title") or ""),
        horizon=review_horizon,
        expectation=str(meta.get("expectation") or ""),
        confidence=float(meta.get("confidence") or 0.0),
        trigger_store=trigger_store,
        now=now,
    )
    _write_meta(ks, item_id, {"review_horizon": review_horizon, "reminder_trigger_id": tid})
    return projection(_require_decision(ks, item_id))


def abandon_decision(item_id: str, *, store: Any = None, trigger_store: Any = None) -> dict:
    """Mark a decision abandoned and retire its reminder.

    A distinct status rather than a resolution, and NO lesson: an abandoned decision has no
    outcome to compare against its expectation, and distilling one anyway would put a
    fabricated verdict in long-term memory.
    """
    ks = _knowledge_store(store)
    _require_decision(ks, item_id)
    retire_review_trigger(item_id, trigger_store=trigger_store)
    _write_meta(ks, item_id, {"status": "abandoned", "reminder_trigger_id": None})
    return projection(_require_decision(ks, item_id))


# ── resolve ──────────────────────────────────────────────────────────────────


def lesson_text(
    *, summary: str, expectation: str, confidence: float, outcome: str, grade: str
) -> str:
    """The R18 lesson body: expectation vs outcome, in the write-time contract's shape.

    Composed deterministically rather than by a model. The contract the research source
    proved is "2-4 sentences, plain prose, cite the stated expectation against the captured
    outcome, one concrete lesson" — and the citation half is exactly the part a summarizer
    is free to drop. A model call here would also make resolving a decision cost tokens on
    a path the user reaches by answering a reminder.
    """
    pct = f"{max(0.0, min(1.0, float(confidence))) * 100:.0f}%"
    verdict = {
        "better": "it turned out better than that",
        "as_expected": "that is what happened",
        "worse": "it turned out worse than that",
        "mixed": "the result was mixed against that",
    }.get(grade, "the result did not settle against that")
    return (
        f"On '{summary}' I expected: {expectation} (stated confidence {pct}). "
        f"What actually happened: {outcome} — {verdict}. "
        f"Weigh that when making the next call of this kind."
    )


def _lesson_key_for(memory: Any, rule: str) -> str | None:
    """The key of the lesson row holding *rule*, read back OUT of the memory store.

    Re-deriving ``lesson.<md5-12>`` here would be a second spelling of a contract
    ``vector_memory.write_lesson`` owns, and it would be wrong whenever dedup let an
    existing longer lesson win instead of writing the new one. Reading the key back means
    the soft reference either points at a row that exists or is left null.

    A lesson's ``value_json`` is the rule text encoded directly (``'"…"'``); the dict branch
    is here because a negative-bearing lesson carries ``{rule, negative}`` and matching only
    the bare-string shape would silently return None for exactly those.
    """
    try:
        for row in memory.get_lessons():
            try:
                value = json.loads(row.get("value_json") or "null")
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                value = value.get("rule")
            if isinstance(value, str) and value == rule:
                return str(row.get("key") or "") or None
    except Exception:
        logger.debug("decision journal: could not read the lesson key back", exc_info=True)
    return None


def resolve_decision(
    item_id: str,
    *,
    outcome: str,
    grade: str,
    store: Any = None,
    trigger_store: Any = None,
    memory: Any = None,
    enqueue: Any = None,
    now: datetime | None = None,
) -> dict:
    """Capture an outcome. ``too_early`` defers; every other grade resolves.

    A resolution does four things in order: update the item, retire the reminder, write the
    R18 lesson, stamp the soft reference back. The lesson is written LAST so a memory store
    that is unavailable cannot leave the knowledge item stuck pending — the user's answer is
    recorded either way, and ``lesson_memory_key`` stays null to say so honestly.
    """
    g = (grade or "").strip().lower()
    if g not in RESOLUTION_GRADES:
        raise DecisionError(f"unknown grade {g!r} — one of {', '.join(RESOLUTION_GRADES)}")
    text = (outcome or "").strip()
    if not text:
        raise DecisionError("resolve requires the outcome — what actually happened")

    ks = _knowledge_store(store)
    item = _require_decision(ks, item_id)
    meta = decision_meta(item)
    if meta.get("status") == "resolved":
        raise DecisionError(f"decision {item_id!r} is already resolved")

    if g == "too_early":
        return _defer(
            ks,
            item,
            meta,
            note=text,
            trigger_store=trigger_store,
            now=now,
        )

    captured = (now or datetime.now()).isoformat()
    _write_meta(
        ks,
        item_id,
        {
            "status": "resolved",
            "outcome": text,
            "outcome_grade": g,
            "outcome_captured_at": captured,
        },
    )
    retire_review_trigger(item_id, trigger_store=trigger_store)

    rule = lesson_text(
        summary=str(item.get("title") or ""),
        expectation=str(meta.get("expectation") or ""),
        confidence=float(meta.get("confidence") or 0.0),
        outcome=text,
        grade=g,
    )
    svc = _memory(memory)
    key: str | None = None
    try:
        # source="user_explicit": the user typed this outcome, and a weaker source lets the
        # memory write blocker drop it.
        if svc.write_lesson(rule, category=LESSON_CATEGORY, source="user_explicit"):
            key = _lesson_key_for(svc, rule)
    except Exception:
        logger.warning("decision journal: lesson write failed for %s", item_id, exc_info=True)
    if key:
        _write_meta(ks, item_id, {"lesson_memory_key": key})

    # Re-enqueue so the outcome text embeds too — a resolved decision whose outcome is not
    # searchable is half a record.
    _reingest(item_id, enqueue)
    return projection(_require_decision(ks, item_id))


def _defer(
    ks: Any,
    item: dict,
    meta: dict,
    *,
    note: str,
    trigger_store: Any = None,
    now: datetime | None = None,
) -> dict:
    """``too_early``: re-arm at +50% of the original span, at most :data:`MAX_DEFERRALS` times.

    Past the cap the item stays pending with NO trigger — the journal view surfaces it as
    stale-pending. Re-arming forever is the nag this cap exists to prevent, and silently
    resolving it would invent an outcome.
    """
    item_id = str(item.get("id") or "")
    used = int(meta.get("deferrals") or 0)
    if used >= MAX_DEFERRALS:
        retire_review_trigger(item_id, trigger_store=trigger_store)
        _write_meta(ks, item_id, {"reminder_trigger_id": None, "stale_pending": True})
        return projection(_require_decision(ks, item_id))

    ref = now or datetime.now()
    try:
        original = datetime.fromisoformat(str(meta.get("review_horizon") or ""))
    except ValueError:
        original = ref
    # Half of the ORIGINAL span, measured from the horizon rather than from today, so a
    # decision reviewed late does not silently earn a longer deferral than one reviewed on time.
    span_days = max(1, (original.date() - _created_date(item, ref)).days)
    next_horizon = (
        (max(original, ref) + timedelta(days=max(1, int(span_days * DEFERRAL_FACTOR))))
        .date()
        .isoformat()
    )
    tid = mint_review_trigger(
        item_id,
        title=str(item.get("title") or ""),
        horizon=next_horizon,
        expectation=str(meta.get("expectation") or ""),
        confidence=float(meta.get("confidence") or 0.0),
        trigger_store=trigger_store,
        now=ref,
    )
    _write_meta(
        ks,
        item_id,
        {
            "review_horizon": next_horizon,
            "reminder_trigger_id": tid,
            "deferrals": used + 1,
            "deferral_note": note,
        },
    )
    return projection(_require_decision(ks, item_id))


def _created_date(item: dict, fallback: datetime):
    try:
        return datetime.fromisoformat(str(item.get("created_at") or "")).date()
    except ValueError:
        return fallback.date()


def _reingest(item_id: str, enqueue: Any) -> None:
    """Best-effort re-enqueue for ingestion. Never raises into a resolution.

    ``enqueue`` is INJECTED rather than looked up because the ingest queue is owned by the
    gateway's ``DashboardState``, and a core module that reached for it would either import
    the dashboard or silently no-op in every non-gateway caller. The chat-tool call site
    passes the same background-enrich callable ``knowledge_create`` uses; a caller with no
    ingestion available passes nothing and the item stays keyword-searchable (its FTS row is
    written by ``create_typed_item``) without a vector.
    """
    if enqueue is None:
        return
    try:
        enqueue(item_id)
    except Exception:
        logger.debug("decision journal: re-enqueue skipped for %s", item_id, exc_info=True)


# ── read ─────────────────────────────────────────────────────────────────────


def projection(item: dict) -> dict:
    """One decision, flattened for a tool or an API response."""
    meta = decision_meta(item)
    return {
        "id": item.get("id"),
        "summary": item.get("title"),
        "status": meta.get("status") or "pending",
        "domain": meta.get("domain") or "other",
        "expectation": meta.get("expectation") or "",
        "confidence": meta.get("confidence"),
        "review_horizon": meta.get("review_horizon") or "",
        "reminder_trigger_id": meta.get("reminder_trigger_id"),
        "deferrals": int(meta.get("deferrals") or 0),
        "stale_pending": bool(meta.get("stale_pending")),
        "outcome": meta.get("outcome"),
        "outcome_grade": meta.get("outcome_grade"),
        "outcome_captured_at": meta.get("outcome_captured_at"),
        "lesson_memory_key": meta.get("lesson_memory_key"),
        "created_at": item.get("created_at"),
    }


def list_decisions(
    *,
    status: str = "",
    domain: str = "",
    store: Any = None,
    limit: int = 50,
    now: datetime | None = None,
) -> list[dict]:
    """Decisions, newest first. ``status="overdue"`` is a derived filter, not a stored one.

    Filtering happens in python over the item rows rather than in SQL because the status
    and domain live inside the metadata JSON: a ``json_extract`` predicate here would be a
    second place that knows the metadata shape, and the journal is personal-scale.
    """
    ks = _knowledge_store(store)
    want_status = (status or "").strip().lower()
    if want_status and want_status not in (*DECISION_STATUSES, "overdue"):
        raise DecisionError(
            f"unknown status {want_status!r} — one of {', '.join(DECISION_STATUSES)}, overdue"
        )
    want_domain = (domain or "").strip().lower()
    if want_domain and want_domain not in DECISION_DOMAINS:
        raise DecisionError(
            f"unknown domain {want_domain!r} — one of {', '.join(DECISION_DOMAINS)}"
        )
    rows = ks.db.execute(
        "SELECT * FROM items WHERE item_type = ? AND status = 'active' "
        "ORDER BY created_at DESC LIMIT ?",
        (DECISION_TYPE, max(1, int(limit))),
    ).fetchall()
    ref = now or datetime.now()
    out: list[dict] = []
    for row in rows:
        item = ks.get_item(row["id"])
        if not item:
            continue
        p = projection(item)
        if want_domain and p["domain"] != want_domain:
            continue
        if want_status == "overdue":
            if p["status"] != "pending" or not _is_overdue(p, ref):
                continue
        elif want_status and p["status"] != want_status:
            continue
        p["overdue"] = p["status"] == "pending" and _is_overdue(p, ref)
        out.append(p)
    return out


def _is_overdue(p: dict, ref: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(p.get("review_horizon") or "")) <= ref
    except ValueError:
        return False


def calibration(*, store: Any = None, min_n: int = CALIBRATION_MIN_N) -> dict:
    """Per-domain stated-confidence vs realized-outcome rates. Computed, never stored.

    One pass over the resolved decision items: no new store, no LLM call, and
    ``count_honest`` is False below *min_n* so a surface can say "too few to mean much"
    instead of rendering a rate off three data points as if it meant something.
    """
    ks = _knowledge_store(store)
    buckets: dict[str, dict[str, Any]] = {}
    for p in list_decisions(store=ks, status="resolved", limit=1000):
        if p["outcome_grade"] not in CALIBRATED_GRADES:
            continue
        b = buckets.setdefault(
            p["domain"],
            {"n": 0, "better": 0, "as_expected": 0, "worse": 0, "confidence_sum": 0.0},
        )
        b["n"] += 1
        b[str(p["outcome_grade"])] += 1
        b["confidence_sum"] += float(p["confidence"] or 0.0)
    for b in buckets.values():
        n = b["n"]
        b["mean_confidence"] = round(b.pop("confidence_sum") / n, 3) if n else None
        b["as_expected_rate"] = round(b["as_expected"] / n, 3) if n else None
        b["count_honest"] = n >= min_n
    return buckets
