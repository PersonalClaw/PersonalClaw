"""Scheduled research reports — the DEFINITION, what it reads, and how its runs went.

A research report is a standing question ("what changed in my sources about X?") that runs on a
schedule, reads a *source* scope of your knowledge, writes findings as ``research-finding``
knowledge nodes, and advances a watermark so the next run only considers what arrived since. This
module owns the persisted definition, the sentence a report states its sources in, and the record
of its runs. Scope resolution, the model loop, node writing and delivery live in the runner
(``action_providers.knowledge_report_provider``); WHEN a report runs belongs to the clock.

**One schedule.** A report's schedule is stored here and nowhere else. Its automation on the
Triggers page mirrors it (``knowledge.report_schedules``), the clock fires the automation, and a
fire IS the report's run: nothing reads the schedule a second time when a fire arrives. A second
reading was a second schedule — an automation moved on the Triggers page fired at its new time and
was skipped as "not due" against this one, and its history called the skip a success. An edit on
either side moves both (``report_schedules.adopt``).

**What a report reads is said in the words of what decides it.** :func:`sources_shown` words the
same ``Scope`` the runner resolves — no tags is anything in your knowledge, a window is a rolling
span — and always says that a report does not search the web: it reads only what your sources,
notes and imports have brought into the library.

Three rules about a run's record, each a test in ``tests/test_research_reports.py``:

1. **A run says what it found.** ``last_status`` is ``ok`` for a run that wrote a finding,
   :data:`NOTHING_NEW` for one that read its sources and found no new material, and ``error`` for
   one that failed; ``last_result`` is that run's sentence for a person. Nothing new is the normal
   outcome of a frequent schedule and never a failure, and it is never an "ok" that says nothing.
2. **A failed run records its error WITHOUT advancing the last-run stamp or the watermark**, so the
   next run reads the same new material again instead of skipping it. ``last_result`` keeps the
   sentence of the last run that finished, which is the run ``last_run_ts`` dates.
3. **The watermark belongs to scope-resolution time, not completion time.** ``record_run`` takes it
   as a parameter instead of stamping ``time.time()`` itself. A run that resolves its scope at T
   and finishes at T+90s would, if it stamped its own completion, set the watermark past anything
   captured during those 90 seconds — and those items would never be considered by any future
   run. The RUNNER passes the timestamp it resolved the scope at.

The store is one JSON file under ``config_dir()``. A corrupt or absent file loads as an empty list
and never raises: these definitions are read by the runner and by the reports API, so an unreadable
store must degrade to "no reports" rather than take the gateway down. The file is hand-editable by
design, which is why ``from_dict`` is tolerant. Every write re-reads it under its lock
(``record_files.locked``), which a sync and a restore's merge hold too when they bring another
machine's reports in, so neither writes over the other.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from personalclaw import record_files
from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.knowledge.semantics import RESEARCH_FINDING_KIND as _RESEARCH_FINDING_KIND
from personalclaw.schedule import ScheduleDefinition
from personalclaw.security import redact_credentials, redact_exfiltration_urls


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


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

#: ``last_status`` of a run that read its sources and found no new material in them: a finished
#: run, not a failure, and not the ``ok`` of a run that wrote a finding.
NOTHING_NEW = "nothing_new"

_REPORTS_FILE = "research_reports.json"

# Iteration ceiling. Each iteration is a full model call over the resolved scope,
# so an unbounded cap is an unbounded spend on a surface that fires unattended,
# on a schedule, forever. Ten is deliberately generous for a research loop that
# converges in two or three and still bounds one scheduled run's worst case.
MIN_ITERATION_CAP = 1
MAX_ITERATION_CAP = 10

# Persisted run text is capped: the store is read by the runner and the reports API, and a provider
# traceback pasted verbatim would grow the file without adding signal.
_MAX_ERROR_CHARS = 500


# ── Model ──


@dataclass
class Scope:
    """What a report may read. ``tags`` are tag-subtree roots; ``()`` means no tag
    filter (the whole knowledge base). ``window_secs`` of 0 means "since this
    report's watermark" — the incremental default — rather than "no window".
    :func:`sources_shown` says it in these terms, and the runner reads exactly this."""

    tags: tuple[str, ...] = ()
    window_secs: int = 0


@dataclass
class ReportDefinition:
    """A standing research question plus its cadence and its watermark.

    ``schedule`` REUSES ``personalclaw.schedule.ScheduleDefinition`` — the same
    ``every``/``at``/``cron`` shape the trigger store speaks. A second cadence
    vocabulary for reports would be a dialect that drifts from the first one. It is
    the report's one schedule; its automation mirrors it (``report_schedules``).

    ``context is None`` means nothing may be searched while writing the report:
    the model sees the source scope's items and nothing else. That is the
    conservative default because a context scope is a second, wider read that the
    citation policy then has to police.
    """

    id: str
    name: str
    prompt: str
    schedule: ScheduleDefinition
    #: IANA zone the cron is evaluated in. `""` == resolved by `personalclaw.timezones`
    #: (`config.timezone`, else this machine's zone, else UTC) — NOT UTC outright (#2520).
    tz: str = ""
    source: Scope = field(default_factory=Scope)
    context: Scope | None = None
    citation_policy: str = CITE_SOURCE_ONLY
    iteration_cap: int = 3
    enabled: bool = True
    created_ts: float = 0.0
    last_run_ts: float | None = None
    last_status: str = ""  # "ok" | NOTHING_NEW | "error" | ""
    last_error: str = ""
    #: What the last run that finished found, in a sentence for a person ("Found no new material in
    #: your knowledge tagged perf since its previous run.").
    last_result: str = ""
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
    # silently-hot schedule, whereas None gives the mirror no cadence to arm (`clock_spec`).
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
        "last_result": defn.last_result,
        "watermark_ts": defn.watermark_ts,
    }


#: What belongs to one home rather than to what a report IS: whether it is switched on there, and
#: what its runs there did — when it last ran, how that went, and how far it has read. A report
#: from another home arrives without them (:func:`arrived_from_another_home`), and two homes never
#: compare them (:func:`what_it_is`), so a run in one is not an edit the other must review.
RUNTIME_FIELDS: tuple[str, ...] = (
    "enabled",
    "last_run_ts",
    "last_status",
    "last_error",
    "last_result",
    "watermark_ts",
)


def what_it_is(row: dict) -> dict:
    """A stored report as a person made it: without :data:`RUNTIME_FIELDS`. What two homes compare
    to tell whether either changed it."""
    return {name: value for name, value in row.items() if name not in RUNTIME_FIELDS}


def arrived_from_another_home(row: dict) -> dict:
    """A stored report from another home, as it is brought into this one: by a sync, a restore's
    merge or an import, and a sync conflict resolved with the other machine's version of a report
    this home no longer has (one it has takes the version in as an edit: its switch and runs here
    stay).

    What it is (:func:`what_it_is`), switched off: it runs on its cadence, unattended, and each run
    is a model call, so it runs here only once someone here switches it on — which saves it here,
    and schedules it (:func:`save_report`). It first reads from where its window starts, as a new
    report does, since how far another home's runs read is theirs."""
    arrived = what_it_is(row)
    arrived["enabled"] = False
    return arrived


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
        last_result=_as_str(raw.get("last_result")),
        watermark_ts=_as_float(raw.get("watermark_ts")),
    )


# ── Store ──


def _store_path() -> Path:
    # Resolved per call, not bound at import: a test that points
    # PERSONALCLAW_HOME at tmp_path must not be able to hit the real home.
    return config_dir() / _REPORTS_FILE


#: The store's document is the list of definitions itself.
_REPORTS = record_files.Shape()


def load_reports(*, strict: bool = False) -> list[ReportDefinition]:
    """Every persisted definition. A corrupt, truncated or absent file loads as an
    empty list — the scheduler and the API both read this, and an unreadable store
    must degrade to "no reports", never to a 500 or a dead gateway. A write reads it
    *strict*: it refuses with ``record_files.Unreadable`` rather than replace the
    definitions it could not read."""
    path = _store_path()
    read = record_files.records if strict else record_files.records_or_empty
    raw = read(path, _REPORTS)
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
    ``from_dict`` is tolerant, so rejecting here could never be the guarantee. The
    API refuses one at the door, and the mirror (``report_schedules.to_trigger``)
    arms nothing for a cadence the clock cannot read.
    """
    if defn.citation_policy not in CITATION_POLICIES:
        raise ValueError(
            f"invalid citation_policy {defn.citation_policy!r} "
            f"(expected one of {list(CITATION_POLICIES)})"
        )
    if not defn.id:
        defn.id = f"rpt-{uuid4().hex[:8]}"
    if not defn.created_ts:
        defn.created_ts = time.time()
    defn.iteration_cap = _clamp_iteration_cap(defn.iteration_cap)
    with record_files.locked(_store_path()):
        defns = [d for d in load_reports(strict=True) if d.id != defn.id]
        defns.append(defn)
        _write(defns)
    # The schedule is attached HERE rather than in the handler, because this is the one home
    # of the definition store: a second writer (a CLI, an app, a future importer) would
    # otherwise persist a report that never fires, which is precisely the state this change
    # started in. Best-effort by construction — see `report_schedules.sync`.
    _sync_schedule(defn)
    return defn


def delete_report(report_id: str) -> bool:
    with record_files.locked(_store_path()):
        defns = load_reports(strict=True)
        kept = [d for d in defns if d.id != report_id]
        if len(kept) == len(defns):
            return False
        _write(kept)
    # Order matters: the definition is gone first, so a failure to remove the trigger leaves
    # a row whose provider then refuses ("no report definition") instead of a live schedule
    # for a report that no longer exists.
    _remove_schedule(report_id)
    return True


def _sync_schedule(defn: ReportDefinition) -> str:
    """Attach/refresh this report's clock trigger. Imported lazily and never raising.

    Lazy because `triggers/` is a heavier subtree than the definition store needs at import
    time, and `triggers.web_poll` already imports `knowledge` inside a function for the
    mirror-image reason — a module-level pair would be a cycle.
    """
    try:
        from personalclaw.knowledge import report_schedules

        return report_schedules.sync(defn)
    except Exception as exc:  # noqa: BLE001 — a save must not fail on its scheduling
        logger.warning("research report %s: schedule sync failed (%s)", defn.id, exc)
        return f"schedule sync failed: {exc}"


def _remove_schedule(report_id: str) -> str:
    """Detach this report's clock trigger. Never raising, for the same reason."""
    try:
        from personalclaw.knowledge import report_schedules

        return report_schedules.remove(report_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("research report %s: schedule removal failed (%s)", report_id, exc)
        return f"schedule removal failed: {exc}"


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
    result: str = "",
    nothing_new: bool = False,
    error: str = "",
    watermark_ts: float | None = None,
) -> None:
    """Persist the outcome of one run.

    On success: advance ``last_run_ts`` to now and, when supplied, the watermark; record
    ``ok`` — or :data:`NOTHING_NEW` for a run that found nothing new — and the run's sentence
    (``result``) in ``last_result`` (rule 1).

    On failure: record ``last_status``/``last_error`` and leave ``last_run_ts`` and
    ``last_result`` ALONE, so they still describe the last run that finished (rule 2). The
    watermark is left alone too: advancing it past items a failed run never successfully read
    would skip them forever.

    ``watermark_ts`` is a parameter, not ``time.time()``, because it belongs to the moment the
    run RESOLVED ITS SCOPE, not the moment it finished (rule 3). Stamping completion would
    silently skip everything captured mid-run. The runner owns that timestamp and passes it
    here.

    A missing id is a no-op: a report deleted while its run was in flight must not make the
    runner's bookkeeping raise.
    """
    with record_files.locked(_store_path()):
        defns = load_reports(strict=True)
        target = next((d for d in defns if d.id == report_id), None)
        if target is None:
            logger.warning("record_run for unknown research report %s, ignoring", report_id)
            return
        now = time.time()
        if ok:
            target.last_run_ts = now
            target.last_status = NOTHING_NEW if nothing_new else "ok"
            target.last_error = ""
            target.last_result = result[:_MAX_ERROR_CHARS]
            if watermark_ts is not None:
                target.watermark_ts = watermark_ts
        else:
            target.last_status = "error"
            target.last_error = _redact(error)
        _write(defns)


# ── What a report says it reads, and what a run found ──


def _where(tags: tuple[str, ...]) -> str:
    """``your knowledge``, or ``your knowledge tagged perf`` / ``perf or ops`` /
    ``perf, ops or ai`` — the part of the library a scope with these tags reads."""
    names = [t for t in tags if t]
    if not names:
        return "your knowledge"
    listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
    return f"your knowledge tagged {listed}"


def _span(window_secs: int) -> str:
    """``from the last 7 days`` — a rolling window, as every surface words a length of time."""
    from personalclaw.auth.lifetimes import duration_words

    return f"from the last {duration_words(window_secs)}"


def sources_shown(defn: ReportDefinition) -> str:
    """What *defn* reads, in a sentence for its card: which part of your knowledge, over which
    window, what it may look at while writing, and that it does not search the web.

    Worded from the very ``Scope`` the runner resolves, so the card cannot promise a source the
    run does not read. A report has no web source: what reaches it from the web is what a watched
    source brought into the library first.
    """
    source = defn.source
    if source.window_secs > 0:
        parts = [f"Reads {_where(source.tags)} {_span(source.window_secs)} each time it runs."]
    else:
        parts = [f"Reads what is new in {_where(source.tags)} each time it runs."]
    if defn.context is not None:
        span = f" {_span(defn.context.window_secs)}" if defn.context.window_secs > 0 else ""
        parts.append(f"While writing, it may also look at {_where(defn.context.tags)}{span}.")
    parts.append("It does not search the web.")
    return " ".join(parts)


def wrote_words(defn: ReportDefinition, items: int) -> str:
    """The sentence of a run that wrote its finding from *items* source items."""
    noun = "item" if items == 1 else "items"
    return f"Wrote a finding from {items} {noun} in {_where(defn.source.tags)}."


def nothing_new_words(defn: ReportDefinition) -> str:
    """The sentence of a run that found no new material, for the window that run read: its rolling
    window, or what arrived since its previous run, or — before any run — everything so far."""
    where = _where(defn.source.tags)
    if defn.source.window_secs > 0:
        return f"Found no material in {where} {_span(defn.source.window_secs)}."
    if defn.watermark_ts > 0:
        return f"Found no new material in {where} since its previous run."
    return f"Found nothing in {where} to report on yet."
