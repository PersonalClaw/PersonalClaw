"""Feedback Signal — the deterministic capture + attribution substrate (plan 58).

A small 👍/👎 on the platform's AI judgment outputs (inbox classifications, drafted
replies, digest items, loop findings, routing suggestions) feeding three layers:

* **Capture** — an append-only ``FeedbackRecord`` JSONL store
  (``~/.personalclaw/feedback.jsonl``, 0600, atomic trim at 2× cap) with ONE write
  API (:func:`record_feedback`) used identically by core surfaces and apps. 👍 is
  SILENT-POSITIVE (owner ruling): recorded only so accuracy has a denominator —
  it never generates lessons or proposals. Only 👎 (with an optional short "why")
  ever feeds learning, and the interpretive half of that belongs to
  LEARNING-FLYWHEEL, not here.
* **Attribution** — per-producer rolling accuracy as a pure GROUP BY over records
  (:func:`producer_stats`), keyed to the PRODUCING ARTIFACT (the bound prompt ref,
  loop-judge kind, workflow id, routing pair, app producer). Nothing is stored —
  recomputed from the JSONL through a small write-invalidated cache.
* **Learning, deterministic arm only** — threshold policies over accuracy
  (:func:`suppressed_producers`, :func:`check_retire_candidates`): a persistently
  wrong producer stops surfacing (where a surfacing gate exists) and gets ONE
  "retire this rule?" proposal. Pure counting — no LLM anywhere in this module;
  feedback never leaves the instance (zero telemetry); retire actions are
  propose-don't-write.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from personalclaw import notification_kinds

logger = logging.getLogger(__name__)

# Closed, append-only vocabularies (C1). A new surface must name its kind here
# first — the hard rule is JUDGMENT OUTPUTS only (never chat messages).
TARGET_KINDS = (
    "inbox_classification",
    "inbox_draft",
    "inbox_digest",
    "loop_finding",
    "routing_suggestion",
    "proposal_content",
    "app_judgment",
)
PRODUCER_KINDS = (
    "prompt",
    "loop_judge",
    "workflow_surfacing",
    "skill_synthesis",
    "routing_pair",
    "app",
)

#: The producer kinds for which falling below the retire threshold actually WITHHOLDS output.
#:
#: Suppression is computed for every kind in :data:`PRODUCER_KINDS`, but only a kind with a real
#: surfacing gate can act on it. Today exactly one does: ``skills.surfacing.surface_skills``
#: withholds a matched skill whose identity ``("skill_synthesis", <key>)`` is in the set. For the
#: other five, a below-threshold producer keeps surfacing normally and gets the retire PROPOSAL only
#: — which is the design (see :func:`check_retire_candidates`), not an oversight.
#:
#: This exists because the distinction was invisible at the API boundary:
#: ``GET /api/feedback/producers`` reported ``suppressed: true`` for any below-threshold producer of
#: ANY kind, and the Settings panel renders that as a pill titled "Stopped surfacing". For five of
#: six kinds that was simply untrue — the panel's own docstring even used a ``prompt``-kind row as
#: its worked example. Callers must consult this set before claiming an effect.
#: ``tests/test_feedback_suppression_enforcement.py`` asserts it matches the code.
ENFORCED_SUPPRESSION_KINDS = ("skill_synthesis",)

_MAX_REASON_CHARS = 500
# Append-only cap discipline (§2.4, the notifications/SEL pattern): trim to the
# newest _CAP records when the file exceeds 2×.
_CAP = 5_000


@dataclass(frozen=True)
class FeedbackRecord:
    """One verdict on one judgment output, with provenance to its producer."""

    id: str
    created_at: float
    target_kind: str
    target_id: str
    verdict: str  # "up" | "down"
    reason: str = ""  # 👎 only; stored verbatim, redact()-ed on any render
    snapshot: dict = field(default_factory=dict)  # the judgment AS SHOWN
    producer_kind: str = ""
    producer_id: str = ""
    source_app: str = ""  # stamped server-side from request["app"]; "" = core
    session_key: str = ""


def _path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "feedback.jsonl"


# ── In-process index (supersede-by-target) + stats cache ─────────────────────
# Rebuilt lazily from the JSONL on first read after a write (or process start).
_INDEX: "dict[tuple[str, str], FeedbackRecord] | None" = None


def _invalidate() -> None:
    global _INDEX
    _INDEX = None


def _load_index() -> dict[tuple[str, str], FeedbackRecord]:
    """Current verdict per (target_kind, target_id): last record wins (supersede).
    Tolerant reads — a corrupt line is skipped with a warning (fail OPEN)."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    index: dict[tuple[str, str], FeedbackRecord] = {}
    p = _path()
    if p.is_file():
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    rec = FeedbackRecord(
                        id=str(d.get("id", "")),
                        created_at=float(d.get("created_at", 0.0)),
                        target_kind=str(d.get("target_kind", "")),
                        target_id=str(d.get("target_id", "")),
                        verdict=str(d.get("verdict", "")),
                        reason=str(d.get("reason", "")),
                        snapshot=dict(d.get("snapshot") or {}),
                        producer_kind=str(d.get("producer_kind", "")),
                        producer_id=str(d.get("producer_id", "")),
                        source_app=str(d.get("source_app", "")),
                        session_key=str(d.get("session_key", "")),
                    )
                except (json.JSONDecodeError, TypeError, ValueError):
                    logger.warning("feedback.jsonl: skipping corrupt line")
                    continue
                if rec.target_kind and rec.target_id and rec.verdict in ("up", "down"):
                    index[(rec.target_kind, rec.target_id)] = rec
        except OSError:
            logger.warning("feedback.jsonl unreadable — starting empty", exc_info=True)
    _INDEX = index
    return index


def record_feedback(
    *,
    target_kind: str,
    target_id: str,
    verdict: str,
    reason: str = "",
    snapshot: dict | None = None,
    producer_kind: str = "",
    producer_id: str = "",
    source_app: str = "",
    session_key: str = "",
    state=None,
) -> FeedbackRecord | None:
    """Append one verdict. Never raises to callers — feedback must never break
    the surface hosting it (returns None on any failure).

    Re-thumbing the same target supersedes (last-verdict-wins in the index; the
    old record stays in the JSONL for audit). ``reason`` rides 👎 only and is
    clipped to 500 chars. SEL-logged + WS-broadcast.
    """
    try:
        if target_kind not in TARGET_KINDS:
            logger.warning("record_feedback: unknown target_kind %r — dropped", target_kind)
            return None
        if verdict not in ("up", "down"):
            logger.warning("record_feedback: bad verdict %r — dropped", verdict)
            return None
        if producer_kind and producer_kind not in PRODUCER_KINDS:
            logger.warning("record_feedback: unknown producer_kind %r — dropped", producer_kind)
            return None
        rec = FeedbackRecord(
            id=f"fb_{uuid.uuid4().hex[:8]}",
            created_at=time.time(),
            target_kind=target_kind,
            target_id=str(target_id),
            verdict=verdict,
            reason=(reason or "")[:_MAX_REASON_CHARS] if verdict == "down" else "",
            snapshot=dict(snapshot or {}),
            producer_kind=producer_kind,
            producer_id=producer_id,
            source_app=source_app,
            session_key=session_key,
        )
        _append(rec)
        _load_index()[(rec.target_kind, rec.target_id)] = rec
        _log_sel(rec)
        _broadcast(rec, state)
        return rec
    except Exception:  # noqa: BLE001 — the never-raises contract
        logger.warning("record_feedback failed", exc_info=True)
        return None


def _append(rec: FeedbackRecord) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    exists = p.is_file()
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
    if not exists:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    _maybe_trim(p)


def _maybe_trim(p: Path) -> None:
    """Trim to the newest _CAP lines when the file exceeds 2× (atomic rewrite)."""
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) <= 2 * _CAP:
            return
        from personalclaw.atomic_write import atomic_write

        atomic_write(p, "\n".join(lines[-_CAP:]) + "\n")
        _invalidate()
    except OSError:
        logger.debug("feedback trim failed", exc_info=True)


def _log_sel(rec: FeedbackRecord) -> None:
    try:
        from personalclaw.sel import sel

        producer = f"{rec.producer_kind}:{rec.producer_id}"
        sel().log_api_access(
            caller=rec.source_app or "user",
            operation="feedback.record",
            outcome=rec.verdict,
            source="dashboard",
            resources=f"{rec.target_kind}:{rec.target_id} producer={producer}",
        )
    except Exception:  # noqa: BLE001
        logger.debug("feedback SEL log failed", exc_info=True)


def _broadcast(rec: FeedbackRecord, state=None) -> None:
    """WS-notify listeners (the FE reflects the thumb). ``state`` is the
    DashboardState the route handler carries; None (SDK/core callers with no
    handle) skips the broadcast — the record itself is the durable truth."""
    if state is None:
        return
    try:
        state.broadcast_ws(
            "feedback_recorded",
            {"target_kind": rec.target_kind, "target_id": rec.target_id, "verdict": rec.verdict},
        )
    except Exception:  # noqa: BLE001
        logger.debug("feedback WS broadcast failed", exc_info=True)


def current_verdict(target_kind: str, target_id: str) -> FeedbackRecord | None:
    """The current (non-superseded) verdict on one target, or None."""
    return _load_index().get((target_kind, str(target_id)))


def producer_stats(*, window_days: int | None = None) -> dict[tuple[str, str], dict]:
    """Per-producer rolling accuracy: a pure GROUP BY over current verdicts in the
    window. ``{(producer_kind, producer_id): {ups, downs, n, accuracy}}``.
    No stored scores — recomputed from the index each call (index is cached)."""
    if window_days is None:
        window_days = _config().window_days
    cutoff = time.time() - window_days * 86_400
    out: dict[tuple[str, str], dict] = {}
    for rec in _load_index().values():
        if rec.created_at < cutoff or not rec.producer_kind or not rec.producer_id:
            continue
        row = out.setdefault(
            (rec.producer_kind, rec.producer_id), {"ups": 0, "downs": 0, "n": 0, "accuracy": 0.0}
        )
        row["ups" if rec.verdict == "up" else "downs"] += 1
    for row in out.values():
        row["n"] = row["ups"] + row["downs"]
        row["accuracy"] = row["ups"] / row["n"] if row["n"] else 0.0
    return out


# ── Layer 3 (deterministic arm): thresholds + retire proposals ───────────────


def _config():
    from personalclaw.config.loader import AppConfig

    return AppConfig.load().feedback


def _settings() -> dict[str, Any]:
    """entity_settings/feedback.json — snoozed/cleared/retire_proposed. Tolerant
    reads: a corrupt file suppresses NOTHING (fail open, warn)."""
    try:
        from personalclaw.providers.entity_routes import _load_entity_settings

        data = _load_entity_settings("feedback")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        logger.warning("feedback settings unreadable — suppressing nothing", exc_info=True)
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings("feedback", data)


def _producer_key(kind: str, pid: str) -> str:
    return f"{kind}:{pid}"


def suppressed_producers(
    *, threshold: float | None = None, min_n: int | None = None
) -> set[tuple[str, str]]:
    """Producers whose accuracy fell below the retire threshold with enough
    verdicts — minus snoozed/user-cleared ones.

    Consulted by SKILL surfacing as one membership check (``skills.surfacing`` withholds a
    matched skill whose ``("skill_synthesis", <key>)`` identity is in the set); everything
    else gets the proposal only. This docstring used to say "workflow/skill surfacing" —
    no workflow path consults this function, and nothing under ``workflows/`` ever did.
    Membership therefore does NOT imply an effect: see
    :data:`ENFORCED_SUPPRESSION_KINDS` before reporting one to a user.

    Fail-open: any error returns the empty set (never suppress on a fault)."""
    try:
        cfg = _config()
        if not cfg.enabled:
            return set()
        threshold = cfg.retire_threshold if threshold is None else threshold
        min_n = cfg.min_n if min_n is None else min_n
        settings = _settings()
        snoozed = settings.get("snoozed", {}) or {}
        cleared = set(settings.get("cleared", []) or [])
        now = time.time()
        out: set[tuple[str, str]] = set()
        for (kind, pid), row in producer_stats().items():
            if row["n"] < min_n or row["accuracy"] >= threshold:
                continue
            key = _producer_key(kind, pid)
            if key in cleared:
                continue
            snooze_until = snoozed.get(key)
            if isinstance(snooze_until, (int, float)) and now < snooze_until:
                continue
            out.add((kind, pid))
        return out
    except Exception:  # noqa: BLE001
        logger.warning("suppressed_producers failed — suppressing nothing", exc_info=True)
        return set()


_SNOOZE_SECS = 30 * 86_400


def snooze_producer(producer_kind: str, producer_id: str) -> None:
    """30-day snooze: the threshold check skips this producer until then."""
    data = _settings()
    snoozed = data.setdefault("snoozed", {})
    snoozed[_producer_key(producer_kind, producer_id)] = time.time() + _SNOOZE_SECS
    # A re-snooze should also allow a future re-proposal after it lapses.
    proposed = data.setdefault("retire_proposed", [])
    key = _producer_key(producer_kind, producer_id)
    if key in proposed:
        proposed.remove(key)
    _save_settings(data)


def clear_producer(producer_kind: str, producer_id: str) -> None:
    """Un-suppress after the user edited the producing artifact: the producer is
    exempt from suppression until it crosses the threshold again with NEW data
    (its retire_proposed dedup entry is also reset)."""
    data = _settings()
    key = _producer_key(producer_kind, producer_id)
    cleared = data.setdefault("cleared", [])
    if key not in cleared:
        cleared.append(key)
    proposed = data.setdefault("retire_proposed", [])
    if key in proposed:
        proposed.remove(key)
    snoozed = data.setdefault("snoozed", {})
    snoozed.pop(key, None)
    _save_settings(data)


def check_retire_candidates(state=None) -> list[dict]:
    """One-time "retire this rule?" proposal per producer per threshold crossing.

    Runs on the existing inbox-service maintenance tick (no new loop). Dedup by
    producer via ``retire_proposed`` in the settings file. Emits ``notify(kind=
    "feedback_retire")`` pre-plan-42; when INBOX-NOTIFICATIONS-UNIFICATION lands,
    this emit site swaps to ``emit_attention_item(kind="proposal")`` — one
    function, one swap. Returns the emitted candidates (for tests/logs).
    """
    try:
        cfg = _config()
        if not cfg.enabled:
            return []
        data = _settings()
        proposed = set(data.get("retire_proposed", []) or [])
        candidates: list[dict] = []
        stats = producer_stats()
        for kind, pid in suppressed_producers() | _proposal_only_candidates(stats):
            key = _producer_key(kind, pid)
            if key in proposed:
                continue
            row = stats.get((kind, pid), {})
            candidates.append(
                {
                    "producer_kind": kind,
                    "producer_id": pid,
                    "ups": row.get("ups", 0),
                    "downs": row.get("downs", 0),
                    "accuracy": row.get("accuracy", 0.0),
                }
            )
            proposed.add(key)
        if not candidates:
            return []
        data["retire_proposed"] = sorted(proposed)
        _save_settings(data)
        if state is not None:
            for c in candidates:
                link = (
                    "#/settings/prompts"
                    if c["producer_kind"] == "prompt"
                    else "#/settings/feedback"
                )
                state.notify(
                    notification_kinds.FEEDBACK_RETIRE,
                    f"AI judgment source keeps missing: {c['producer_kind']}:{c['producer_id']}",
                    (
                        f"Wrong {c['downs']} of {c['downs'] + c['ups']} times recently "
                        f"(accuracy {c['accuracy']:.0%}). Review it, or snooze this check."
                    ),
                    meta={
                        "link": link,
                        "producer_kind": c["producer_kind"],
                        "producer_id": c["producer_id"],
                    },
                )
        return candidates
    except Exception:  # noqa: BLE001
        logger.warning("check_retire_candidates failed", exc_info=True)
        return []


def _proposal_only_candidates(stats: dict[tuple[str, str], dict]) -> set[tuple[str, str]]:
    """Below-threshold producers with NO surfacing gate (inbox prompts, the judge)
    — they can't be suppressed, so they get the proposal only."""
    cfg = _config()
    settings = _settings()
    cleared = set(settings.get("cleared", []) or [])
    snoozed = settings.get("snoozed", {}) or {}
    now = time.time()
    out: set[tuple[str, str]] = set()
    for (kind, pid), row in stats.items():
        if kind not in ("prompt", "loop_judge"):
            continue  # gated producers are covered by suppressed_producers()
        if row["n"] < cfg.min_n or row["accuracy"] >= cfg.retire_threshold:
            continue
        key = _producer_key(kind, pid)
        if key in cleared:
            continue
        until = snoozed.get(key)
        if isinstance(until, (int, float)) and now < until:
            continue
        out.add((kind, pid))
    return out
