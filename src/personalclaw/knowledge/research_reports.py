"""Scheduled research reports — the DEFINITION and its dueness (WF2KNO-12).

A research report is a standing question ("what changed in my sources about X?")
that fires on a schedule, searches a *source* scope, writes findings as
``research-finding`` knowledge nodes, and advances a watermark so the next run
only considers what arrived since. This module owns the persisted definition and
the single question a runner asks of it: *is this due right now?*

It deliberately owns nothing else. Scope resolution, the model loop, node
writing and delivery live in sibling modules; the runner is what calls
``is_due`` and ``record_run``.

Four scheduling failures this module exists to prevent — each is a test in
``tests/test_research_reports.py``:

1. **An unparseable expression fails CLOSED.** ``is_due`` never raises. A runner
   iterates *every* definition on every tick, so one malformed cron expression
   that escaped as an exception would wedge the whole sweep and silently stop
   every other report. A bad expression is therefore "not due, and here is why",
   with the offending expression named in the reason so the user can fix it.
2. **A never-run report anchors its first fire on its CREATION time.** The
   tempting spelling is ``last_run_ts or 0`` — which anchors a brand-new report
   on the Unix epoch, making every schedule already overdue by 56 years, so
   every report a user creates fires the instant it is saved. ``_anchor_ts``
   falls back to ``created_ts`` instead, and refuses to guess when even that is
   unset (fail closed rather than fire at 1970).
3. **A missed window fires ONCE, not once per window skipped.** Dueness compares
   the anchor against the *most recent* boundary at or before now, never against
   a count of elapsed boundaries. Catch-up-per-window is the wrong reading of a
   schedule: a laptop asleep overnight would wake to fifty queued model calls
   for a report the user only ever wanted the latest answer from. The user wants
   one report, not fifty. ``record_run`` stamps *now* — not the boundary it
   missed — for the same reason.
4. **A failed run records its error WITHOUT advancing the last-run timestamp**,
   so the next tick retries instead of skipping the window. The consequence is
   deliberate: a persistently failing report retries on every tick rather than
   on its own cadence, which is the honest trade (a visible ``last_status ==
   "error"`` plus retries beats a report that silently produces nothing until
   tomorrow). Runner-side backoff, if wanted, belongs to the runner.

And one watermark rule:

5. **The watermark belongs to scope-resolution time, not completion time.**
   ``record_run`` takes it as a parameter instead of stamping ``time.time()``
   itself. A run that resolves its scope at T and finishes at T+90s would, if it
   stamped its own completion, set the watermark past anything captured during
   those 90 seconds — and those items would never be considered by any future
   run. The RUNNER passes the timestamp it resolved the scope at.

The store is one JSON file under ``config_dir()``. A corrupt or absent file
loads as an empty list and never raises: these definitions are read on the
gateway's scheduler path and by the reports API, so an unreadable store must
degrade to "no reports" rather than take the gateway down. The file is
hand-editable by design, which is the other half of why rule 1 lives in
``is_due`` and not only in ``save_report``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from croniter import croniter  # type: ignore[import-untyped]

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.knowledge.semantics import RESEARCH_FINDING_KIND as _RESEARCH_FINDING_KIND
from personalclaw.schedule import ScheduleDefinition, get_local_tz, validate_cron_expr
from personalclaw.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

# The knowledge-node kind every report writes its output as. Siblings (the runner
# and the retrieval surfaces) key off this constant, never off a literal.
#: The kind a report's finding is written as. Aliased from `semantics`, which owns the
#: taxonomy: two literals for one kind is how the default-list exclusion and the writer drift
#: apart, and only one of them would be wrong at a time.
FINDING_KIND = _RESEARCH_FINDING_KIND

# Citation policies. "cite-source-only" is the default because a finding whose
# citation points at the assistant's own earlier context is not evidence — it is
# hearsay one hop removed from the source that justified it.
CITE_SOURCE_ONLY = "cite-source-only"
ALLOW_CITING_CONTEXT = "allow-citing-context"
#: The single-flight key a report run holds while it is in flight. Both halves of the
#: feature read it — the RUNNER writes it around a run, the manual-run route refuses while
#: it is held — so it lives here rather than in either of them: the same string spelled in
#: two places is a lease that silently never matches, which is a 409 that can never fire.
CLAIM_ID_PREFIX = "research-report:"


def report_claim_id(report_id: str) -> str:
    """The claim id for one report's run. See :data:`CLAIM_ID_PREFIX`."""
    return f"{CLAIM_ID_PREFIX}{report_id}"


CITATION_POLICIES = (CITE_SOURCE_ONLY, ALLOW_CITING_CONTEXT)

_REPORTS_FILE = "research_reports.json"

# Iteration ceiling. Each iteration is a full model call over the resolved scope,
# so an unbounded cap is an unbounded spend on a surface that fires unattended,
# on a schedule, forever. Ten is deliberately generous for a research loop that
# converges in two or three and still bounds one scheduled run's worst case.
MIN_ITERATION_CAP = 1
MAX_ITERATION_CAP = 10

# Persisted error text is capped: the store is loaded on the scheduler path, and
# a provider traceback pasted verbatim would grow the file without adding signal.
_MAX_ERROR_CHARS = 500


# ── Model ──


@dataclass
class Scope:
    """What a report may read. ``tags`` are tag-subtree roots; ``()`` means no tag
    filter (the whole knowledge base). ``window_secs`` of 0 means "since this
    report's watermark" — the incremental default — rather than "no window"."""

    tags: tuple[str, ...] = ()
    window_secs: int = 0


@dataclass
class ReportDefinition:
    """A standing research question plus its cadence and its watermark.

    ``schedule`` REUSES ``personalclaw.schedule.ScheduleDefinition`` — the same
    ``every``/``at``/``cron`` shape the trigger store speaks. A second cadence
    vocabulary for reports would be a dialect that drifts from the first one.

    ``context is None`` means nothing may be searched while writing the report:
    the model sees the source scope's items and nothing else. That is the
    conservative default because a context scope is a second, wider read that the
    citation policy then has to police.
    """

    id: str
    name: str
    prompt: str
    schedule: ScheduleDefinition
    tz: str = ""  # "" == host local (get_local_tz)
    source: Scope = field(default_factory=Scope)
    context: Scope | None = None
    citation_policy: str = CITE_SOURCE_ONLY
    iteration_cap: int = 3
    enabled: bool = True
    created_ts: float = 0.0
    last_run_ts: float | None = None
    last_status: str = ""  # "ok" | "error" | ""
    last_error: str = ""
    watermark_ts: float = 0.0


# ── Serialization ──


def _as_str(raw: object, default: str = "") -> str:
    return raw if isinstance(raw, str) else default


def _as_bool(raw: object, default: bool) -> bool:
    return raw if isinstance(raw, bool) else default


def _as_float(raw: object, default: float = 0.0) -> float:
    # bool is an int subclass; a stray `true` must not become 1.0.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    return float(raw)


def _as_int(raw: object, default: int = 0) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    return int(raw)


def _as_opt_float(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _scope_to_dict(scope: Scope) -> dict:
    return {"tags": list(scope.tags), "window_secs": scope.window_secs}


def _scope_from_dict(raw: object) -> Scope:
    if not isinstance(raw, dict):
        return Scope()
    tags = raw.get("tags")
    clean = tuple(t for t in tags if isinstance(t, str) and t) if isinstance(tags, list) else ()
    return Scope(tags=clean, window_secs=max(0, _as_int(raw.get("window_secs"))))


def _schedule_to_dict(sched: ScheduleDefinition) -> dict:
    return {
        "kind": sched.kind,
        "every_secs": sched.every_secs,
        "at_ts": sched.at_ts,
        "cron_expr": sched.cron_expr,
    }


def _schedule_from_dict(raw: object) -> ScheduleDefinition:
    if not isinstance(raw, dict):
        return ScheduleDefinition(kind="")
    # An unusable cadence becomes None rather than 0: a 0-second "every" would be a
    # silently-hot schedule, whereas None fails closed in is_due with a named reason.
    every = _as_int(raw.get("every_secs"))
    return ScheduleDefinition(
        kind=_as_str(raw.get("kind")),
        every_secs=every if every > 0 else None,
        at_ts=_as_opt_float(raw.get("at_ts")),
        cron_expr=raw.get("cron_expr") if isinstance(raw.get("cron_expr"), str) else None,
    )


def to_dict(defn: ReportDefinition) -> dict:
    """JSON-safe projection — what the API layer serves and what the store writes."""
    return {
        "id": defn.id,
        "name": defn.name,
        "prompt": defn.prompt,
        "schedule": _schedule_to_dict(defn.schedule),
        "tz": defn.tz,
        "source": _scope_to_dict(defn.source),
        "context": None if defn.context is None else _scope_to_dict(defn.context),
        "citation_policy": defn.citation_policy,
        "iteration_cap": defn.iteration_cap,
        "enabled": defn.enabled,
        "created_ts": defn.created_ts,
        "last_run_ts": defn.last_run_ts,
        "last_status": defn.last_status,
        "last_error": defn.last_error,
        "watermark_ts": defn.watermark_ts,
    }


def from_dict(raw: dict) -> ReportDefinition:
    """Tolerant inverse of ``to_dict``: unknown keys ignored, bad types coerced or
    defaulted. Tolerant because the store is hand-editable and because a single
    malformed row must not cost the user the other twenty definitions in the file."""
    policy = _as_str(raw.get("citation_policy"), CITE_SOURCE_ONLY)
    return ReportDefinition(
        id=_as_str(raw.get("id")),
        name=_as_str(raw.get("name")),
        prompt=_as_str(raw.get("prompt")),
        schedule=_schedule_from_dict(raw.get("schedule")),
        tz=_as_str(raw.get("tz")),
        source=_scope_from_dict(raw.get("source")),
        context=None if raw.get("context") is None else _scope_from_dict(raw.get("context")),
        citation_policy=policy if policy in CITATION_POLICIES else CITE_SOURCE_ONLY,
        iteration_cap=_clamp_iteration_cap(_as_int(raw.get("iteration_cap"), 3)),
        enabled=_as_bool(raw.get("enabled"), True),
        created_ts=_as_float(raw.get("created_ts")),
        last_run_ts=_as_opt_float(raw.get("last_run_ts")),
        last_status=_as_str(raw.get("last_status")),
        last_error=_as_str(raw.get("last_error")),
        watermark_ts=_as_float(raw.get("watermark_ts")),
    )


# ── Store ──


def _store_path() -> Path:
    # Resolved per call, not bound at import: a test that points
    # PERSONALCLAW_HOME at tmp_path must not be able to hit the real home.
    return config_dir() / _REPORTS_FILE


def load_reports() -> list[ReportDefinition]:
    """Every persisted definition. A corrupt, truncated or absent file loads as an
    empty list — the scheduler and the API both read this, and an unreadable store
    must degrade to "no reports", never to a 500 or a dead gateway."""
    path = _store_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError, ValueError):
        logger.warning("Unreadable research-report store at %s, treating as empty", path)
        return []
    if not isinstance(raw, list):
        logger.warning("Research-report store at %s is not a list, treating as empty", path)
        return []
    out: list[ReportDefinition] = []
    for row in raw:
        if isinstance(row, dict) and _as_str(row.get("id")):
            out.append(from_dict(row))
    return out


def get_report(report_id: str) -> ReportDefinition | None:
    for defn in load_reports():
        if defn.id == report_id:
            return defn
    return None


def _write(defns: list[ReportDefinition]) -> None:
    atomic_write(_store_path(), json.dumps([to_dict(d) for d in defns], indent=2))


def _clamp_iteration_cap(cap: int) -> int:
    """An unbounded cap is an unbounded spend on an unattended, recurring surface,
    so the cap is clamped rather than trusted — including on the load path, where
    the value may have been hand-edited past the ceiling."""
    return max(MIN_ITERATION_CAP, min(MAX_ITERATION_CAP, cap))


def save_report(defn: ReportDefinition) -> ReportDefinition:
    """Insert or replace by id, assigning ``id`` and ``created_ts`` when absent.

    Validates the citation policy (an unknown policy would leave the runner with
    no rule to apply) and clamps ``iteration_cap``. It deliberately does NOT
    reject a malformed schedule expression: the store is hand-editable and
    ``from_dict`` is tolerant, so rejecting here could never be the guarantee.
    ``is_due`` failing closed is the guarantee (rule 1).
    """
    if defn.citation_policy not in CITATION_POLICIES:
        raise ValueError(
            f"invalid citation_policy {defn.citation_policy!r} "
            f"(expected one of {list(CITATION_POLICIES)})"
        )
    if not defn.id:
        defn.id = f"rpt-{uuid4().hex[:8]}"
    if not defn.created_ts:
        # created_ts is the first-fire anchor (rule 2), so it can never stay 0.
        defn.created_ts = time.time()
    defn.iteration_cap = _clamp_iteration_cap(defn.iteration_cap)
    defns = [d for d in load_reports() if d.id != defn.id]
    defns.append(defn)
    _write(defns)
    return defn


def delete_report(report_id: str) -> bool:
    defns = load_reports()
    kept = [d for d in defns if d.id != report_id]
    if len(kept) == len(defns):
        return False
    _write(kept)
    return True


# ── Dueness ──


def _report_tz(defn: ReportDefinition) -> ZoneInfo:
    """The report's timezone, falling back to host local then UTC. A cron
    expression is a wall-clock statement; evaluating "0 7 * * *" in UTC for a
    user in Los Angeles delivers their morning report at midnight."""
    if defn.tz:
        try:
            return ZoneInfo(defn.tz)
        except Exception:
            logger.warning("Report %s has unusable tz %r, using host local", defn.id, defn.tz)
    return get_local_tz()[1]


def _anchor_ts(defn: ReportDefinition) -> float | None:
    """The instant the current window is measured from: the last run, or — for a
    report that has never run — its CREATION time. Never 0. ``last_run_ts or 0``
    would anchor every new report on the Unix epoch, making it instantly overdue
    (rule 2). ``None`` means there is no honest anchor, and the caller fails closed
    rather than inventing 1970."""
    if defn.last_run_ts is not None:
        return defn.last_run_ts
    return defn.created_ts if defn.created_ts > 0 else None


def is_due(defn: ReportDefinition, *, now: float) -> tuple[bool, str]:
    """Whether *defn* should fire at *now*, and why (or why not).

    Never raises: a runner sweeps every definition on every tick, so an exception
    escaping here would let one malformed report wedge every other report's
    schedule (rule 1). Every failure path returns ``(False, <reason>)`` with the
    offending value named, so the reason is something a UI can show the user.
    """
    try:
        if not defn.enabled:
            return False, "disabled"
        sched = defn.schedule
        anchor = _anchor_ts(defn)
        if anchor is None:
            return False, "no anchor: created_ts is unset (refusing to anchor on the epoch)"

        if sched.kind == "every":
            every = sched.every_secs
            if not isinstance(every, int) or every <= 0:
                return False, f"invalid every_secs {every!r} (expected a positive integer)"
            # One comparison against the CURRENT window, never a count of the
            # windows that elapsed — fifty missed windows are still one report
            # (rule 3).
            due_at = anchor + every
            if now >= due_at:
                return True, f"every {every}s elapsed since {anchor:.0f}"
            return False, f"next fire at {due_at:.0f}"

        if sched.kind == "at":
            at_ts = sched.at_ts
            if at_ts is None:
                return False, "invalid at schedule: at_ts is unset"
            if defn.last_run_ts is not None:
                return False, "one-shot 'at' schedule already ran"
            if now >= at_ts:
                return True, f"one-shot time {at_ts:.0f} reached"
            return False, f"next fire at {at_ts:.0f}"

        if sched.kind == "cron":
            expr = sched.cron_expr or ""
            if not expr or not validate_cron_expr(expr):
                # Fail CLOSED and name the expression: this is the reason the
                # whole function is wrapped, and the user's only repair hint.
                return False, f"invalid cron expression {expr!r}"
            base = datetime.fromtimestamp(now, tz=_report_tz(defn))
            # The most recent boundary at or before now. Comparing that single
            # boundary against the anchor is what makes fifty skipped windows
            # fire once (rule 3).
            prev_fire = float(croniter(expr, base).get_prev(float))
            if prev_fire > anchor:
                return True, f"cron {expr!r} boundary at {prev_fire:.0f} passed"
            return False, f"no cron {expr!r} boundary since {anchor:.0f}"

        return False, f"unsupported schedule kind {sched.kind!r}"
    except Exception as exc:  # fail closed — a bad definition must not wedge a sweep
        logger.warning("Dueness evaluation failed for report %s", defn.id, exc_info=True)
        return False, f"schedule evaluation failed: {exc}"


# ── Run bookkeeping ──


def _redact(text: str) -> str:
    if not text:
        return ""
    text, _ = redact_exfiltration_urls(text)
    text, _ = redact_credentials(text)
    return text[:_MAX_ERROR_CHARS]


def record_run(
    report_id: str,
    *,
    ok: bool,
    error: str = "",
    watermark_ts: float | None = None,
) -> None:
    """Persist the outcome of one run.

    On success: advance ``last_run_ts`` to now (NOT to the boundary that was
    missed — stamping the boundary is exactly the catch-up-per-window bug of rule
    3) and, when supplied, the watermark.

    On failure: record ``last_status``/``last_error`` and leave ``last_run_ts``
    ALONE, so the next tick retries instead of skipping the window (rule 4). The
    watermark is left alone too: advancing it past items a failed run never
    successfully read would skip them forever.

    ``watermark_ts`` is a parameter, not ``time.time()``, because it belongs to
    the moment the run RESOLVED ITS SCOPE, not the moment it finished (rule 5).
    Stamping completion would silently skip everything captured mid-run. The
    runner owns that timestamp and passes it here.

    A missing id is a no-op: a report deleted while its run was in flight must
    not make the runner's bookkeeping raise.
    """
    defns = load_reports()
    target = next((d for d in defns if d.id == report_id), None)
    if target is None:
        logger.warning("record_run for unknown research report %s, ignoring", report_id)
        return
    now = time.time()
    if ok:
        target.last_run_ts = now
        target.last_status = "ok"
        target.last_error = ""
        if watermark_ts is not None:
            target.watermark_ts = watermark_ts
    else:
        target.last_status = "error"
        target.last_error = _redact(error)
    _write(defns)
