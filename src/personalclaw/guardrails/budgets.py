"""Spend metering + budget ceilings for unattended work (AUTONOMY-GUARDRAILS §1.1).

The §2 model-call chokepoint is where metering becomes possible — every attempt
carries a token count and a dollar estimate, so the ``SpendMeter`` can fold them
into per-scope counters. A ceiling that bites pauses the run into needs-input
rather than silently overspending.

Two scopes matter for a personal gateway:

* ``run`` — one unattended run (a goal-loop cycle, a cron fire, a subagent). The
  counter is in-memory, keyed by a caller-supplied run key, and reset when the run
  ends. It stops a single runaway from burning a whole day's budget in one go.
* ``day`` — all unattended spend for a calendar day, persisted to
  ``~/.personalclaw/spend.json`` (atomic_write, pruned >30 days) so it survives a
  restart. It is the real cost guardrail.

Dollar estimates reuse ``pricing.estimate_cost`` (provider-reported cost preferred
by the caller; this is the heuristic fallback, flagged ``estimated``). Budgets
compare against the conservative (higher) estimate — a token ceiling and a dollar
ceiling both apply, either can bite.

This is harness mechanics: ``spend.json`` is a file under the config dir, NOT a
memory entry or knowledge item (§7 boundary).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_SPEND_FILENAME = "spend.json"
_PRUNE_DAYS = 30


@dataclass(frozen=True)
class Budget:
    """A spend ceiling. Zero in any dimension means UNLIMITED for that dimension."""

    max_tokens: int = 0
    max_dollars: float = 0.0

    @property
    def is_unlimited(self) -> bool:
        return self.max_tokens <= 0 and self.max_dollars <= 0.0


class BudgetVerdict(str, Enum):
    """The meter's verdict after folding a charge into a scope's running total."""

    OK = "ok"
    WARN = "warn"  # crossed 80% of a ceiling — surface, don't stop
    EXCEEDED = "exceeded"  # at/over a ceiling — the run must pause


_WARN_FRACTION = 0.8


def _today_key() -> str:
    """The calendar-day key for the day-scope counter (local date, ISO)."""
    return datetime.now().strftime("%Y-%m-%d")


@dataclass
class _ScopeTotal:
    tokens: int = 0
    dollars: float = 0.0


class SpendMeter:
    """Folds per-attempt spend into run- and day-scope counters, and verdicts a
    prospective charge against a :class:`Budget`.

    Thread-safety: the day-scope counter is persisted and may be touched from the
    gateway loop + a subagent thread, so mutations take a lock. Run-scope counters
    are in-memory dicts keyed by run key.
    """

    def __init__(self, *, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir
        self._lock = threading.Lock()
        self._run_totals: dict[str, _ScopeTotal] = {}

    # ── Paths / persistence ─────────────────────────────────────────────

    def _spend_path(self) -> Path:
        base = self._config_dir
        if base is None:
            from personalclaw.config.loader import config_dir

            base = config_dir()
        return base / _SPEND_FILENAME

    def _load_day(self) -> dict:
        path = self._spend_path()
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_day(self, data: dict) -> None:
        # Prune days older than the retention window before writing.
        try:
            cutoff = datetime.now().toordinal() - _PRUNE_DAYS

            def _keep(day_key: str) -> bool:
                ordinal = _ordinal_of(day_key)
                # Unparseable keys are kept (never silently dropped by the pruner).
                return ordinal is None or ordinal >= cutoff

            pruned = {day: v for day, v in data.items() if _keep(day)}
        except Exception:
            pruned = data
        try:
            atomic_write(self._spend_path(), json.dumps(pruned, separators=(",", ":")))
        except Exception:
            logger.warning("spend.json write failed", exc_info=True)

    # ── Recording spend ─────────────────────────────────────────────────

    def charge(self, tokens: int, dollars: float, *, run_key: str | None = None) -> None:
        """Record ``tokens`` + ``dollars`` of spend against the day scope (always)
        and a run scope (when ``run_key`` is given). Best-effort; never raises."""
        tokens = max(0, int(tokens or 0))
        dollars = max(0.0, float(dollars or 0.0))
        if tokens == 0 and dollars == 0.0:
            return
        with self._lock:
            # Day scope (persisted).
            data = self._load_day()
            day = _today_key()
            existing = data.get(day)
            prev = existing if isinstance(existing, dict) else {}
            data[day] = {
                "tokens": int(prev.get("tokens", 0)) + tokens,
                "dollars": round(float(prev.get("dollars", 0.0)) + dollars, 6),
            }
            self._save_day(data)
            # Run scope (in-memory).
            if run_key:
                rt = self._run_totals.setdefault(run_key, _ScopeTotal())
                rt.tokens += tokens
                rt.dollars += dollars

    def end_run(self, run_key: str) -> None:
        """Drop a run's in-memory counter when the run completes."""
        with self._lock:
            self._run_totals.pop(run_key, None)

    # ── Verdicts ─────────────────────────────────────────────────────────

    def day_totals(self) -> _ScopeTotal:
        with self._lock:
            row = self._load_day().get(_today_key(), {})
        return _ScopeTotal(tokens=int(row.get("tokens", 0)), dollars=float(row.get("dollars", 0.0)))

    def run_totals(self, run_key: str) -> _ScopeTotal:
        with self._lock:
            rt = self._run_totals.get(run_key)
            return _ScopeTotal(tokens=rt.tokens, dollars=rt.dollars) if rt else _ScopeTotal()

    def check_day(self, budget: Budget) -> tuple[BudgetVerdict, str]:
        """Verdict the CURRENT day total against ``budget`` (before a new charge).

        Returns (verdict, reason). A caller that gets ``EXCEEDED`` must refuse the
        next unattended LLM call / skip the fire; ``WARN`` is surfaced, not blocking.
        """
        return self._verdict(self.day_totals(), budget, scope="day")

    def check_run(self, run_key: str, budget: Budget) -> tuple[BudgetVerdict, str]:
        """Verdict a run's accumulated total against ``budget``."""
        return self._verdict(self.run_totals(run_key), budget, scope="run")

    @staticmethod
    def _verdict(total: _ScopeTotal, budget: Budget, *, scope: str) -> tuple[BudgetVerdict, str]:
        if budget.is_unlimited:
            return BudgetVerdict.OK, ""
        verdict = BudgetVerdict.OK
        reason = ""
        if budget.max_tokens > 0:
            if total.tokens >= budget.max_tokens:
                return (
                    BudgetVerdict.EXCEEDED,
                    f"{scope} token budget exceeded ({total.tokens}/{budget.max_tokens})",
                )
            if total.tokens >= budget.max_tokens * _WARN_FRACTION:
                verdict, reason = (
                    BudgetVerdict.WARN,
                    f"{scope} token budget at {total.tokens}/{budget.max_tokens}",
                )
        if budget.max_dollars > 0.0:
            if total.dollars >= budget.max_dollars:
                return (
                    BudgetVerdict.EXCEEDED,
                    f"{scope} dollar budget exceeded "
                    f"(${total.dollars:.4g}/${budget.max_dollars:.4g})",
                )
            if total.dollars >= budget.max_dollars * _WARN_FRACTION:
                verdict, reason = (
                    BudgetVerdict.WARN,
                    f"{scope} dollar budget at ${total.dollars:.4g}/${budget.max_dollars:.4g}",
                )
        return verdict, reason


def _ordinal_of(day_key: str) -> int | None:
    try:
        return datetime.strptime(day_key, "%Y-%m-%d").toordinal()
    except (ValueError, TypeError):
        return None


# ── Process-global meter (one per gateway) ───────────────────────────────────

_METER: SpendMeter | None = None


def get_meter() -> SpendMeter:
    """The shared spend meter for this gateway (lazy-created)."""
    global _METER
    if _METER is None:
        _METER = SpendMeter()
    return _METER


def reset_meter() -> None:
    """Drop the process-global meter — invoked by an autouse test fixture so a
    test's spend/run state never leaks into the next (the SEL/breaker discipline)."""
    global _METER
    _METER = None


def budget_from_config() -> Budget:
    """Build the day-scope :class:`Budget` from the loaded GuardrailsConfig.

    Fail-open to unlimited on any config read failure — a broken config must not
    wedge every unattended run (the ceiling is a guardrail, not a gate; the scan +
    breaker remain the hard controls).
    """
    try:
        from personalclaw.config.loader import AppConfig

        b = AppConfig.load().guardrails.budgets
        return Budget(max_tokens=b.max_tokens_per_day, max_dollars=b.max_dollars_per_day)
    except Exception:
        logger.debug("budget_from_config: falling back to unlimited", exc_info=True)
        return Budget()


def run_budget_from_config() -> Budget:
    """Build the run-scope :class:`Budget` (tokens only) from GuardrailsConfig."""
    try:
        from personalclaw.config.loader import AppConfig

        b = AppConfig.load().guardrails.budgets
        return Budget(max_tokens=b.max_tokens_per_run)
    except Exception:
        logger.debug("run_budget_from_config: falling back to unlimited", exc_info=True)
        return Budget()
