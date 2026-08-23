"""The model-upgrade watchdog (EVALUATION-SUBSTRATE §3.2).

Harness components compensate for the weaknesses of a SPECIFIC model, so a model rebind
silently invalidates every measurement taken against the old one. ``active_models.json`` is
the single file where use-case bindings change
(:func:`personalclaw.providers.use_cases.active_models_path`), so that file is the seam:
this watchdog notices a change, computes the new **model fingerprint**, queues small-budget
re-benchmarks, and files **exactly ONE digest notification** — never N.

The "exactly one" is the load-bearing part. A rebind that touched three use cases and
queued nine re-benchmarks is one event to a human; twelve notifications for it is the
failure mode §3.2 names outright ("never N notifications"). :func:`check` therefore calls
the notifier at most once per invocation, and not at all when nothing changed.

Baselines are per-fingerprint rows in ``results.tsv`` (the ledger already carries a
``model_fp`` column), so "did the upgrade change anything" is
:func:`baselines_by_fingerprint` — a query, not a feeling.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import pinning, store

logger = logging.getLogger(__name__)

#: The bindings §3.2 names as trust-invalidating. ``eval_judge`` is included because the
#: plan names it — but see :data:`WATCHED_USE_CASES`.
PLAN_WATCHED_USE_CASES: tuple[str, ...] = ("chat", "reasoning", "background", "eval_judge")


def _bindable(names: tuple[str, ...]) -> tuple[str, ...]:
    """Filter to use cases ``active_models.json`` can actually bind.

    ``eval_judge`` is a PROMPT use case (``providers/prompt_use_cases.py``) consumed by
    ``eval/judge.py``'s ``factory("eval_judge")``; it is NOT in ``providers.use_cases``'
    ``VALID_USE_CASES``, so it can never appear in ``active_models.json`` and its model
    resolves through the ``chat`` binding. Filtering here rather than hardcoding three names
    keeps the drift VISIBLE: watching a binding that cannot exist would be a control that
    reports "no change" forever.
    """
    from personalclaw.providers.use_cases import VALID_USE_CASES

    return tuple(name for name in names if name in VALID_USE_CASES)


#: The bindings actually watched — the plan's list, minus any that cannot be bound.
WATCHED_USE_CASES: tuple[str, ...] = _bindable(PLAN_WATCHED_USE_CASES)

#: Trials per queued re-benchmark. "Small budget" (§3.2 step 1) means a smoke-sized replay
#: that says whether anything MOVED, not a full study — the study is the human's call after
#: reading the digest.
SMALL_BUDGET_TRIALS = 1

#: Dollar cap stamped on every queued re-benchmark. Deliberately NOT
#: ``evals.default_budget_usd``: that default may legitimately be 0 (= uncapped), and work
#: the user never asked for must be capped even when work they did ask for is not.
SMALL_BUDGET_USD = 1.0

#: How many of the most-run templates get queued (§3.2 step 1's "top-N most-run").
DEFAULT_TOP_N = 3

#: The one notification kind this module emits. Registered as ``info``: a rebind is
#: informational — the watchdog queues work, it does not block anything.
NOTIFY_KIND = "info"


# ── state ────────────────────────────────────────────────────────────────────


def state_path() -> Path:
    """``evals/model_watchdog.json`` — the last-seen fingerprint and file mtime."""
    return store.evals_root() / "model_watchdog.json"


def load_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {"model_fp": "", "fingerprint": {}, "mtime": 0.0, "checked_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"model_fp": "", "fingerprint": {}, "mtime": 0.0, "checked_at": ""}
    if not isinstance(data, dict):
        return {"model_fp": "", "fingerprint": {}, "mtime": 0.0, "checked_at": ""}
    data.setdefault("model_fp", "")
    data.setdefault("fingerprint", {})
    data.setdefault("mtime", 0.0)
    data.setdefault("checked_at", "")
    return data


def save_state(state: dict) -> None:
    atomic_write(state_path(), json.dumps(state, indent=2, sort_keys=True) + "\n")


# ── the fingerprint ──────────────────────────────────────────────────────────


def fingerprint_now() -> tuple[dict[str, str], str]:
    """``(per-use-case fingerprint, its short digest)`` for the CURRENT bindings.

    Reuses the ES-2 pin machinery (:func:`personalclaw.evals.pinning.model_fingerprint` and
    :meth:`~personalclaw.evals.pinning.RunPin.model_fp`) rather than hashing
    ``active_models.json`` here — the ledger's ``model_fp`` column is written from the pin,
    so a second digest of the same facts would compare unequal to every row it exists to
    match.
    """
    fingerprint = pinning.model_fingerprint()
    probe = pinning.RunPin(
        scenario_id="_watchdog",
        scenario_sha256="_watchdog",
        model_fingerprint=fingerprint,
    )
    return fingerprint, probe.model_fp()


def _mtime() -> float:
    from personalclaw.providers.use_cases import active_models_path

    path = active_models_path()
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@dataclass
class BindingChange:
    """One watched use case's binding move."""

    use_case: str
    before: str = ""
    after: str = ""

    def to_dict(self) -> dict:
        return {"use_case": self.use_case, "before": self.before, "after": self.after}


def changed_bindings(before: dict[str, str], after: dict[str, str]) -> list[BindingChange]:
    """Watched use cases whose head model moved. Sorted, so a digest reads the same twice.

    A use case that GAINED or LOST a binding counts as a change: an unbound use case resolves
    through a fallback, which is a different model than the one the old evidence was taken
    against.
    """
    out: list[BindingChange] = []
    for use_case in WATCHED_USE_CASES:
        old = str((before or {}).get(use_case) or "")
        new = str((after or {}).get(use_case) or "")
        if old != new:
            out.append(BindingChange(use_case=use_case, before=old, after=new))
    return sorted(out, key=lambda c: c.use_case)


# ── per-fingerprint baselines (§3.2's closing sentence) ──────────────────────


def baselines_by_fingerprint(rows: list[dict] | None = None) -> dict[str, dict[str, dict]]:
    """``{model_fp: {scenario_id: {"mean": float|None, "n": int, "latest_ts": str}}}``.

    The ``results.tsv`` ledger already carries ``model_fp`` per row, so an upgrade's effect
    is a group-by, not a new store. Rows with no fingerprint are skipped rather than pooled
    under ``""``: an unattributable score is not a baseline for anything.

    ``mean`` is ``None`` when no row for that (fingerprint, scenario) carried a numeric
    score — the §1.2 rule that an absent measurement is never a zero, applied to the ledger
    read as well as to the run.
    """
    ledger = store.read_results() if rows is None else rows
    grouped: dict[str, dict[str, dict]] = {}
    for row in ledger:
        fp = str(row.get("model_fp") or "")
        scenario = str(row.get("scenario_id") or "")
        if not fp or not scenario:
            continue
        bucket = grouped.setdefault(fp, {}).setdefault(
            scenario, {"mean": None, "n": 0, "latest_ts": "", "_scored": []}
        )
        bucket["n"] = int(bucket["n"]) + 1
        ts = str(row.get("ts") or "")
        if ts > str(bucket["latest_ts"]):
            bucket["latest_ts"] = ts
        raw = row.get("score_new")
        if raw not in (None, ""):
            try:
                bucket["_scored"].append(float(raw))
            except (TypeError, ValueError):
                continue
    for scenarios in grouped.values():
        for bucket in scenarios.values():
            scored = bucket.pop("_scored")
            bucket["mean"] = (sum(scored) / len(scored)) if scored else None
    return grouped


# ── the re-benchmark queue (§3.2 step 1) ─────────────────────────────────────


def queue_path() -> Path:
    """``evals/rebench_queue.json`` — what the watchdog asked for, awaiting a runner."""
    return store.evals_root() / "rebench_queue.json"


def load_queue() -> list[dict]:
    path = queue_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else data
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def save_queue(entries: list[dict]) -> None:
    atomic_write(queue_path(), json.dumps({"entries": entries}, indent=2, sort_keys=True) + "\n")


def top_templates(limit: int = DEFAULT_TOP_N) -> list[str]:
    """The most-run workflow templates, most-run first.

    Counted from the run table rather than from a counter maintained beside it: a count kept
    next to the rows can disagree with them, and this one only has to be right at the moment
    a rebind happens.
    """
    try:
        from personalclaw.workflows import store as wf_store

        runs, _total = wf_store.list_runs(limit=500)
    except Exception:
        logger.debug("watchdog could not read the run table", exc_info=True)
        return []
    counts: dict[str, int] = {}
    for run in runs:
        name = str(getattr(run, "workflow_name", "") or "")
        if name:
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _n in ranked[: max(0, int(limit))]]


def build_queue_entries(
    *,
    model_fp: str,
    changes: list[BindingChange],
    top_n: int = DEFAULT_TOP_N,
    now: datetime | None = None,
) -> list[dict]:
    """The re-benchmark entries one rebind queues: the judge fixtures, then top-N templates.

    Judge prompts are queued on ANY watched change, not only on an ``eval_judge`` change:
    ``eval_judge`` is not separately bindable (see :func:`_bindable`), so its model moves
    when ``chat`` does — queueing only on a change that cannot be observed would be a
    control that never fires.
    """
    moment = now or datetime.now(tz=timezone.utc)
    stamp = moment.isoformat()
    entries: list[dict] = [
        {
            "kind": "judge",
            "subject": "judge",
            "trials": SMALL_BUDGET_TRIALS,
            "budget_usd": SMALL_BUDGET_USD,
            "model_fp": model_fp,
            "reason": "model rebind: " + ",".join(c.use_case for c in changes),
            "queued_at": stamp,
            "status": "queued",
        }
    ]
    for name in top_templates(top_n):
        entries.append(
            {
                "kind": "template",
                "subject": name,
                "trials": SMALL_BUDGET_TRIALS,
                "budget_usd": SMALL_BUDGET_USD,
                "model_fp": model_fp,
                "reason": "model rebind: top-run template",
                "queued_at": stamp,
                "status": "queued",
            }
        )
    return entries


# ── the ONE digest ───────────────────────────────────────────────────────────


@dataclass
class WatchdogResult:
    """What one :func:`check` did — the summary the caller logs or renders."""

    changed: bool = False
    reason: str = ""
    model_fp: str = ""
    previous_model_fp: str = ""
    changes: list[dict] = field(default_factory=list)
    queued: list[dict] = field(default_factory=list)
    notifications: int = 0
    baseline_scenarios: int = 0

    def to_dict(self) -> dict:
        return {
            "changed": self.changed,
            "reason": self.reason,
            "model_fp": self.model_fp,
            "previous_model_fp": self.previous_model_fp,
            "changes": list(self.changes),
            "queued": list(self.queued),
            "notifications": self.notifications,
            "baseline_scenarios": self.baseline_scenarios,
        }


def digest_body(result: WatchdogResult) -> str:
    """The ONE digest's body: what moved, what was queued, what baseline exists.

    Every fact in one string on purpose — this is the whole content of the single
    notification, so anything not in here is a fact the user never learns.
    """
    lines = [
        f"Model bindings changed (fingerprint {result.previous_model_fp or '—'} "
        f"→ {result.model_fp or '—'}).",
        "",
        "Rebound:",
    ]
    for change in result.changes:
        lines.append(
            f"  • {change['use_case']}: {change['before'] or '(unbound)'} "
            f"→ {change['after'] or '(unbound)'}"
        )
    lines.append("")
    lines.append(f"Queued {len(result.queued)} small-budget re-benchmark(s):")
    for entry in result.queued:
        lines.append(
            f"  • {entry['kind']}: {entry['subject']} "
            f"({entry['trials']} trial(s), ≤${entry['budget_usd']:.2f})"
        )
    lines.append("")
    lines.append(
        f"{result.baseline_scenarios} scenario baseline(s) exist for the previous "
        "fingerprint in evals/results.tsv — the comparison is a query over that column, "
        "not a re-run."
    )
    return "\n".join(lines)


def check(
    *,
    now: datetime | None = None,
    notifier=None,
    top_n: int = DEFAULT_TOP_N,
    force: bool = False,
) -> WatchdogResult:
    """One watchdog tick. Returns what it did; calls ``notifier`` AT MOST ONCE.

    ``notifier`` is a ``DashboardState.notify``-shaped callable ``(kind, title, body)``;
    ``None`` (headless/CLI) still queues and still records state — it just has nobody to
    tell. Delivery goes through that one gate rather than a second channel, so mute-all and
    quiet hours keep applying.

    No change ⇒ nothing queued, nothing notified, and the state's ``checked_at`` refreshed:
    a tick that found nothing must not look like a tick that never ran.
    """
    moment = now or datetime.now(tz=timezone.utc)
    state = load_state()
    fingerprint, model_fp = fingerprint_now()
    mtime = _mtime()
    previous_fp = str(state.get("model_fp") or "")
    previous = dict(state.get("fingerprint") or {})

    changes = changed_bindings(previous, fingerprint)
    first_run = not previous_fp and not previous
    # The mtime is a cheap pre-filter, NOT the decision: a rewrite that reordered the file
    # without changing a head model is not a rebind, and an editor that preserved mtime while
    # changing a binding still is. The fingerprint is the truth.
    file_touched = float(state.get("mtime") or 0.0) != float(mtime)

    if not force and not changes:
        state.update(
            {
                "model_fp": model_fp,
                "fingerprint": fingerprint,
                "mtime": mtime,
                "checked_at": moment.isoformat(),
            }
        )
        save_state(state)
        return WatchdogResult(
            changed=False,
            reason="file_touched_no_rebind" if file_touched else "no_change",
            model_fp=model_fp,
            previous_model_fp=previous_fp,
        )

    if first_run and not force:
        # The FIRST observation is a baseline, not an upgrade. Notifying here would greet
        # every fresh install with a "your models changed" digest about nothing.
        state.update(
            {
                "model_fp": model_fp,
                "fingerprint": fingerprint,
                "mtime": mtime,
                "checked_at": moment.isoformat(),
            }
        )
        save_state(state)
        return WatchdogResult(
            changed=False,
            reason="baseline_recorded",
            model_fp=model_fp,
            previous_model_fp="",
            changes=[c.to_dict() for c in changes],
        )

    entries = build_queue_entries(model_fp=model_fp, changes=changes, top_n=top_n, now=moment)
    save_queue(load_queue() + entries)
    baselines = baselines_by_fingerprint()
    result = WatchdogResult(
        changed=True,
        reason="rebind",
        model_fp=model_fp,
        previous_model_fp=previous_fp,
        changes=[c.to_dict() for c in changes],
        queued=entries,
        baseline_scenarios=len(baselines.get(previous_fp) or {}),
    )

    # ── exactly ONE digest ──
    if notifier is not None:
        try:
            notifier(
                NOTIFY_KIND,
                f"Models rebound — {len(entries)} re-benchmark(s) queued",
                digest_body(result),
            )
            result.notifications = 1
        except Exception:  # noqa: BLE001 - a dead notifier must not lose the queue
            logger.debug("watchdog digest notification skipped", exc_info=True)

    state.update(
        {
            "model_fp": model_fp,
            "fingerprint": fingerprint,
            "mtime": mtime,
            "checked_at": moment.isoformat(),
        }
    )
    save_state(state)
    return result
