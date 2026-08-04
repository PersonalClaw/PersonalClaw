"""Health-scored self-remediation engine (PLATFORM-RESILIENCE §4).

ONE background engine that replaces N independent maintenance crons, shaped on
GBrain's ``doctor --remediate --target-score --max-usd``: compute a health score
from **measured** deficits, build a dependency-ordered plan, execute step-by-step
re-checking the score after each step, and stop at whichever comes first —
``target_score`` reached, ``max_cost_usd`` spent, or plan exhausted.

Design tenets (from the plan's risk table): this is a **plan-executor over declared
jobs, not a policy brain**. Deficit inputs are measured counts, ordering is declared
``after:`` edges, stopping is three plain caps, and per-job cooldowns are the "dumb
cooldown". If it misbehaves, disabling it (``resilience.remediation.enabled``)
restores today's heartbeat maintenance, which is kept callable.

Cost: deterministic jobs (FTS rebuild, faiss re-index, prune) cost $0 and never block
on budget. Judgment jobs (re-extraction, semantic lint) would run through
``one_shot_completion`` under the SpendMeter — none are registered yet (their inputs
are future flywheel/knowledge infra); the two-lane mechanism is here for them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_LEDGER_FILE = "remediation.jsonl"
_JOBS_FILE = "jobs.json"
_LEDGER_CAP = 500  # trim at 2× (notifications.jsonl pattern)


def _doctor_dir() -> Path:
    return config_dir() / "doctor"


# ── Deficits (measured problems only — never guesses) ─────────────────────────


@dataclass(frozen=True)
class Deficit:
    """One measured health deficit.

    ``count`` is the current problem magnitude (0 = healthy). ``weight`` is the score
    penalty per unit, capped so one noisy source can't dominate. ``max_penalty`` is
    the ceiling this deficit can subtract (GBrain's ``max_reachable_score`` inverse):
    when the deficit is unfixable right now (e.g. no embedder bound), the caller sets
    ``reachable=False`` so the engine never burns budget on futile work.
    """

    key: str
    count: int
    weight: float
    max_penalty: float
    reachable: bool = True
    job_id: str = ""  # the remediation job that reduces this deficit (if any)

    @property
    def penalty(self) -> float:
        return min(self.max_penalty, self.count * self.weight)


def measure_deficits() -> list[Deficit]:
    """Measure every deficit source that has a REAL count today. Read-only and
    exception-safe — a source that can't be read contributes nothing (never a guess).

    Sources with no count function yet (FTS desync, failed-run backlog, LEARN-R19
    staging) are deliberately absent — the plan forbids guessing.
    """
    out: list[Deficit] = []

    # Knowledge: active items missing an embedding — reachable only if an embedder is
    # bound (else the deficit is at its floor and we must not burn budget re-indexing).
    try:
        from personalclaw.knowledge import get_knowledge_store
        from personalclaw.providers.provider_bridge import can_resolve_use_case

        missing = int(get_knowledge_store().count_items_missing_embedding())
        out.append(
            Deficit(
                key="knowledge_missing_embeddings",
                count=missing,
                weight=0.5,
                max_penalty=20.0,
                reachable=can_resolve_use_case("embedding"),
                job_id="knowledge.reindex-embeddings",
            )
        )
    except Exception:
        logger.debug("deficit: knowledge embeddings measure failed", exc_info=True)

    # Serving/fs: orphaned stale locks — always reachable (deterministic prune).
    try:
        from personalclaw.resilience.fixes import _dead_locks

        out.append(
            Deficit(
                key="orphan_locks",
                count=len(_dead_locks()),
                weight=2.0,
                max_penalty=10.0,
                job_id="serving-fs.prune-orphans",
            )
        )
    except Exception:
        logger.debug("deficit: orphan-lock measure failed", exc_info=True)

    # Skills: entries due for aging (a dry-run count — no mutation).
    try:
        from personalclaw.skills.curator import run_aging

        due = int(getattr(run_aging(dry_run=True), "changed", 0))
        out.append(
            Deficit(
                key="skill_aging_due",
                count=due,
                weight=1.0,
                max_penalty=10.0,
                job_id="skills.age",
            )
        )
    except Exception:
        logger.debug("deficit: skill-aging measure failed", exc_info=True)

    return out


def health_score(deficits: list[Deficit]) -> float:
    """``100 − Σ penalties`` over REACHABLE deficits (an unreachable deficit is at its
    floor — it doesn't count against a score the engine can't improve). Clamped 0-100."""
    total = sum(d.penalty for d in deficits if d.reachable)
    return max(0.0, min(100.0, 100.0 - total))


# ── Jobs (the remediation plan's steps) ───────────────────────────────────────


@dataclass(frozen=True)
class RemediationJob:
    """A remediation step. ``run()`` returns a result string (deterministic jobs cost
    $0). ``after`` are job ids that must run first (dependency ordering). ``cooldown_hours``
    + success-only timestamps + ``content_hash`` are the storm guard."""

    id: str
    title: str
    run: Callable[[], str]
    lane: str = "deterministic"  # deterministic | judgment
    after: tuple[str, ...] = ()
    cooldown_hours: float = 0.0
    fixes_deficit: str = ""  # the deficit key this job reduces


_JOBS: dict[str, RemediationJob] = {}


def register_job(job: RemediationJob) -> None:
    _JOBS[job.id] = job


def all_jobs() -> list[RemediationJob]:
    return list(_JOBS.values())


def _ordered(jobs: list[RemediationJob]) -> list[RemediationJob]:
    """Topological order by ``after`` edges (stable; a missing/ cyclic edge degrades to
    insertion order rather than raising)."""
    by_id = {j.id: j for j in jobs}
    done: set[str] = set()
    out: list[RemediationJob] = []

    def visit(job: RemediationJob, stack: set[str]) -> None:
        if job.id in done or job.id in stack:
            return
        stack.add(job.id)
        for dep in job.after:
            if dep in by_id:
                visit(by_id[dep], stack)
        stack.discard(job.id)
        if job.id not in done:
            done.add(job.id)
            out.append(job)

    for j in jobs:
        visit(j, set())
    return out


# ── Job-state store (cooldown + idempotency) ──────────────────────────────────


def _jobs_state_path() -> Path:
    return _doctor_dir() / _JOBS_FILE


def _load_job_state() -> dict[str, dict]:
    p = _jobs_state_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_job_state(state: dict[str, dict]) -> None:
    d = _doctor_dir()
    d.mkdir(parents=True, exist_ok=True)
    atomic_write(_jobs_state_path(), json.dumps(state, indent=2))


def _in_cooldown(job: RemediationJob, state: dict[str, dict], *, now: float) -> bool:
    if job.cooldown_hours <= 0:
        return False
    last = state.get(job.id, {}).get("last_success_ts")
    return isinstance(last, (int, float)) and (now - last) < job.cooldown_hours * 3600.0


# ── The engine run ────────────────────────────────────────────────────────────


@dataclass
class RunResult:
    score_before: float
    score_after: float
    jobs: list[dict] = field(default_factory=list)
    stopped_reason: str = ""


def run_remediation(
    *,
    target_score: float = 90.0,
    max_cost_usd: float = 1.0,
    now: float,
    dry_run: bool = False,
) -> RunResult:
    """Execute a dependency-ordered remediation plan under the caps. Re-measures the
    score after each step, stops at target/cost/exhausted. Charges the guardrails
    SpendMeter under run_key ``doctor`` for judgment jobs; deterministic jobs are free.

    ``dry_run`` computes the plan + score without running any job (the Doctor preview).
    """
    from personalclaw.guardrails.budgets import (
        get_meter,
        reset_current_run_key,
        set_current_run_key,
    )

    deficits = measure_deficits()
    score_before = health_score(deficits)
    result = RunResult(score_before=score_before, score_after=score_before)

    if score_before >= target_score:
        result.stopped_reason = "target_score already met"
        return result

    state = _load_job_state()
    meter = get_meter()
    # Only run jobs whose deficit is present + reachable + non-zero.
    candidates = [
        j
        for j in all_jobs()
        if j.fixes_deficit
        and any(d.key == j.fixes_deficit and d.reachable and d.count > 0 for d in deficits)
    ]
    plan = _ordered(candidates)

    for job in plan:
        # Budget cap (judgment lane only — deterministic jobs are $0).
        if job.lane == "judgment":
            spent = meter.run_totals("doctor").dollars
            if spent >= max_cost_usd:
                result.stopped_reason = f"max_cost_usd ${max_cost_usd} reached"
                break
        if _in_cooldown(job, state, now=now):
            result.jobs.append({"id": job.id, "status": "skipped_cooldown", "cost": 0.0})
            continue
        if dry_run:
            result.jobs.append({"id": job.id, "status": "would_run", "cost": 0.0})
            continue
        try:
            # 🔴 BIND the run scope the cap above READS (S153). This function's own docstring has
            # always said it "charges the SpendMeter under run_key `doctor`" — and nothing ever
            # did: `run_totals("doctor").dollars` was 0.0 on a fresh meter and stayed 0.0 after any
            # number of model calls, so the judgment-lane cap never bound. A live reader of a
            # total nothing writes. Binding it here means a judgment job's model spend actually
            # accrues, so the `max_cost_usd` break becomes real.
            token = set_current_run_key("doctor")
            try:
                detail = job.run()
            finally:
                reset_current_run_key(token)
            state.setdefault(job.id, {})["last_success_ts"] = now
            result.jobs.append({"id": job.id, "status": "ok", "cost": 0.0, "detail": detail[:200]})
        except Exception as exc:
            logger.warning("remediation job %s failed", job.id, exc_info=True)
            result.jobs.append(
                {"id": job.id, "status": "error", "cost": 0.0, "error": str(exc)[:200]}
            )
            continue
        # Re-check the score after each step.
        result.score_after = health_score(measure_deficits())
        if result.score_after >= target_score:
            result.stopped_reason = "target_score reached"
            break

    if not result.stopped_reason:
        result.stopped_reason = "plan exhausted"
    if not dry_run:
        _save_job_state(state)
        _write_ledger(result, now=now)
    else:
        result.score_after = score_before  # dry-run never changes the score
    return result


# ── Ledger ────────────────────────────────────────────────────────────────────


def _write_ledger(result: RunResult, *, now: float) -> None:
    """Append one ledger row (trim at 2× cap, atomic-write rewrite — the audit.jsonl
    pattern)."""
    try:
        d = _doctor_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / _LEDGER_FILE
        row = {
            "ts": now,
            "score_before": round(result.score_before, 1),
            "score_after": round(result.score_after, 1),
            "jobs": result.jobs,
            "stopped_reason": result.stopped_reason,
        }
        existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        existing.append(json.dumps(row))
        if len(existing) > _LEDGER_CAP * 2:
            existing = existing[-_LEDGER_CAP:]
        atomic_write(path, "\n".join(existing) + "\n")
    except Exception:
        logger.debug("remediation ledger write failed", exc_info=True)


def recent_runs(limit: int = 10) -> list[dict]:
    """Read recent remediation ledger rows newest-first (the Doctor rendering)."""
    path = _doctor_dir() / _LEDGER_FILE
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict] = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ── Registered deterministic jobs (real, $0) ──────────────────────────────────


def _job_prune_orphans() -> str:
    from personalclaw.resilience.fixes import _orphan_prune_apply

    return _orphan_prune_apply()


def _job_age_skills() -> str:
    from personalclaw.skills.curator import run_aging

    report = run_aging()  # real pass (not dry_run) — reversible aging
    return getattr(report, "summary", lambda: "aged")() if report.changed else "no skills due"


def _job_reindex_embeddings() -> str:
    # A deterministic-cost job: re-embedding uses the bound embedder (local or cheap),
    # driven by the knowledge store's own resumable re-index. Deterministic lane
    # because it never invokes the reasoning-model chokepoint (no model spend); the
    # embedder cost is negligible and not metered through the model-call chokepoint.
    # Only runs when an embedder is actually resolvable (the deficit's reachable gate
    # already ensured this, but re-check defensively).
    from personalclaw.knowledge import get_knowledge_store
    from personalclaw.skills.surfacing import _active_embedder

    embed_fn, _model = _active_embedder()
    if embed_fn is None:
        return "no embedder bound — skipped"
    store = get_knowledge_store()
    report = store.reembed_all(embed_fn)
    n = report.get("embedded", report.get("count", 0)) if isinstance(report, dict) else 0
    return f"re-embedded {n} item(s)"


def _register_builtin_jobs() -> None:
    register_job(
        RemediationJob(
            id="serving-fs.prune-orphans",
            title="Prune orphaned locks + rollback leftovers",
            run=_job_prune_orphans,
            lane="deterministic",
            cooldown_hours=24.0,
            fixes_deficit="orphan_locks",
        )
    )
    register_job(
        RemediationJob(
            id="skills.age",
            title="Age the skill library (active→stale→archived)",
            run=_job_age_skills,
            lane="deterministic",
            cooldown_hours=24.0,
            fixes_deficit="skill_aging_due",
        )
    )
    register_job(
        RemediationJob(
            id="knowledge.reindex-embeddings",
            title="Backfill missing knowledge embeddings",
            run=_job_reindex_embeddings,
            lane="deterministic",
            after=("serving-fs.prune-orphans",),
            cooldown_hours=6.0,
            fixes_deficit="knowledge_missing_embeddings",
        )
    )


_register_builtin_jobs()
