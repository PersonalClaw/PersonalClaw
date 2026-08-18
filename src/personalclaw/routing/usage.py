"""Usage/spend read model — the durable per-day fold ``usage_stats.json`` (MRT-3).

The spend-framed lens over telemetry that is ALREADY recorded (zero new collection), riding §1.3's
fold discipline: ``~/.personalclaw/usage_stats.json`` (``atomic_write``), keyed
``date -> "provider:model" -> purpose`` of ``{calls, tokens_in, tokens_out, dollars_est, …}``.

**The fold reads ONE record and CENSUSES the other. Why (this is the atom's whole design
question, and the reason it was BLOCKED before — see the plan's MRT-3 execution log ①–⑥):**

PersonalClaw records model cost in two places that cannot be safely summed:

* ``usage/turns.jsonl`` (``usage_ledger.py``) — one row per streamed TURN, five live writers, with
  real caller provenance in ``source``: ``chat`` | an app name | ``loop`` | ``cron`` | ``channel``
  | ``cli`` | ``subagent`` | ``background``. This covers interactive chat — the user's largest line
  item — so it is THE spend record and the only honest input for a "~$X this month" sentence.
* ``model_calls.jsonl`` (``guardrails/audit.py``) — one row per guarded ``ModelProvider.complete()``
  ATTEMPT. ``provider_bridge`` attaches the guard only for
  ``use_case in ("reasoning", "background", "loops", "orchestration")`` and states the exclusion as
  a design decision ("The interactive chat/code_tools stream stays OUT OF SCOPE … both
  human-watched"), so this record structurally cannot answer "what did this cost me".

A union of the two double-counts: a loop worker's turn is recorded as a ``source="loop"`` turn AND
its inner inference resolves under the ``loops`` axis into a guarded attempt row. Neither row
carries the other's identity — there is no ``audit_id`` on a turn and no session key on an attempt —
so there is NO join key with which to deduplicate. A cross-store total is therefore not merely
expensive to get right, it is currently *unavailable*, and a money surface that silently
double-counts is worse than one that admits a gap.

So: the fold sums the ledger, and :func:`audit_census` counts what is being left out
(``fold["uncounted"]``) so the surface can say "N unattended calls (~$X) are recorded but not
included here, because they cannot be merged without double-counting loops". That turns an
invisible gap into a stated one. Closing it properly means widening the attempt audit to cover
every axis and retiring one of the two records — a class-B change across five writers, out of this
atom's scope.

**Purpose is the unifying vocabulary.** The ledger's ``source`` maps into the fixed
``interactive | background | loop | eval | app`` vocabulary. A source that is none of the known
literals is an APP NAME (``chat_runner`` sets ``source = session._app or "chat"``), so it maps to
``app`` and the name is recorded in ``fold["app_sources"]`` — a census, not an error. ``eval`` is
declared for a first writer and has none today; :func:`reachable_purposes` reports what the current
writers can actually produce so a UI never renders a permanent zero row.

**A row that cannot be attributed to a day is counted, never dropped** — ``fold["unmapped"]``
carries it, because a fold that discards rows produces a plausible number for every input.

**Two honesty disclosures, deliberately not merged:**

* ``estimated_share`` — the fraction of a figure's DOLLARS that is a rate-table estimate rather
  than a provider-reported charge. ``TurnUsage`` carries no "estimated" flag, so a ledger dollar is
  treated as an estimate CONSERVATIVELY (over-disclosing an estimate is safe; claiming absent
  precision is not) and the share reads 1.0 today. It is not decoration: the fold keeps per-cell
  ``estimated_dollars``, so it drops below 1.0 as soon as a writer marks a reported cost.
* ``unpriced_calls`` / ``priced`` — calls whose model has NO rate at all (``priced: false`` on the
  turn, or ``rates.rate_for`` → ``None``). Their dollars are structurally 0, so a total containing
  them is a FLOOR. An unpriced model must never read as "$0 spent". Locally-served refs are
  different and legitimate: they price 0.0 with ``source="local"`` and are counted in
  ``local_calls``.

Rebuild: :func:`rebuild` refolds from scratch — the §1.3 ``--rebuild`` discipline applied to this
fold (``routing.stats.rebuild`` is its sibling over the audit JSONL). Note that flag does not exist
yet: ``--rebuild-routing-stats`` appears in §1.3 and in ``stats.py``'s docstring but no ``cli.py``
argument implements it, so the rebuild is reached through :func:`refresh`, which every
``GET /api/usage`` calls — a deleted fold self-heals on the next read. :func:`refresh` merges the
refold OVER the persisted fold so days that have aged out of the capped JSONL survive (the ledger
trims at 2×50000): per cell it keeps whichever saw more ``calls``, which is correct because trimming
can only remove rows from a completed day, never add them. That is why a durable fold earns its
place beside the ledger's own ``group_by="day"`` rollup, which can only see the retained tail.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from personalclaw.atomic_write import atomic_write
from personalclaw.routing.rates import rate_for
from personalclaw.routing.stats import ref_of

logger = logging.getLogger(__name__)

#: File under the home; small JSON, atomic_write (the universal convention).
_USAGE_FILE = "usage_stats.json"
#: Bump when the fold's schema changes.
USAGE_VERSION = 1

#: The fixed purpose vocabulary (plan §"The usage story").
PURPOSES = ("interactive", "background", "loop", "eval", "app")

#: ``usage/turns.jsonl`` ``source`` -> purpose. ``chat``/``cli``/``channel`` are the human-watched
#: turns. Anything NOT listed here is an app name (``session._app``) and maps to ``app``.
PURPOSE_BY_SOURCE = {
    "chat": "interactive",
    "cli": "interactive",
    "channel": "interactive",
    "loop": "loop",
    "cron": "background",
    "subagent": "background",
    "background": "background",
    "system": "background",
    "eval": "eval",
}
#: Where an unrecognized ``source`` lands. NOT a fallback for bad data: an unknown source is the
#: expected shape for an app-initiated turn, so it is a real bucket and the name is censused.
APP_PURPOSE = "app"

#: ``?window=`` -> days included, counting back from and including the reference day.
WINDOW_DAYS = {"day": 1, "week": 7, "month": 30}
#: ``?group=`` keys. ``model`` groups by the full ``provider:model`` ref because a bare model id
#: is ambiguous across providers (and the ref is the ``active_models.json`` spelling).
GROUPS = ("model", "provider", "purpose")


# ── fold storage ────────────────────────────────────────────────────────────────────────


def _usage_path(home: Path) -> Path:
    return Path(home) / _USAGE_FILE


def empty_fold() -> dict[str, Any]:
    return {
        "version": USAGE_VERSION,
        "days": {},
        "app_sources": {},
        "unmapped": {},
        "uncounted": {},
        "sources": {},
    }


def load_usage(home: Path) -> dict[str, Any]:
    """Read the fold. A missing/corrupt file reads as an empty fold (never fatal)."""
    try:
        data = json.loads(_usage_path(home).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return empty_fold()
    if not isinstance(data, dict):
        return empty_fold()
    for key, default in empty_fold().items():
        data.setdefault(key, default)
    return data


def save_usage(home: Path, fold: dict[str, Any]) -> None:
    atomic_write(_usage_path(home), json.dumps(fold, indent=2, sort_keys=True) + "\n")


def _empty_cell() -> dict[str, Any]:
    return {
        "calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "dollars_est": 0.0,
        "estimated_dollars": 0.0,
        "estimated_calls": 0,
        "unpriced_calls": 0,
        "local_calls": 0,
    }


# ── purpose mapping ─────────────────────────────────────────────────────────────────────


def purpose_for_source(source: str) -> tuple[str, str]:
    """``(purpose, app_name)`` for a ledger turn's ``source``.

    ``app_name`` is non-empty exactly when the source was not a known literal — i.e. it is an
    app-initiated turn — so the caller can census which apps are spending without treating an
    unrecognized value as corrupt data.
    """
    raw = str(source or "").strip()
    mapped = PURPOSE_BY_SOURCE.get(raw)
    if mapped is not None:
        return mapped, ""
    return APP_PURPOSE, raw or "(unnamed)"


def reachable_purposes() -> tuple[str, ...]:
    """The purposes the CURRENT writers can actually produce.

    ``eval`` has no writer today, so a surface uses this to avoid rendering a permanent zero row
    for a bucket nothing can fill.
    """
    got = set(PURPOSE_BY_SOURCE.values()) - {"eval"}
    got.add(APP_PURPOSE)
    return tuple(p for p in PURPOSES if p in got)


# ── rate lookups ────────────────────────────────────────────────────────────────────────


def _rate_lookup(home: Path | None) -> Callable[[str, str], tuple[bool, bool]]:
    """Memoized ``(unpriced, local)`` per (provider, model) — one rate read per distinct ref, not
    per row, so a full refold over a capped JSONL stays cheap."""
    cache: dict[tuple[str, str], tuple[bool, bool]] = {}

    def look(provider: str, model: str) -> tuple[bool, bool]:
        key = (provider, model)
        hit = cache.get(key)
        if hit is None:
            try:
                rate = rate_for(provider, model, home=home)
            except Exception:  # noqa: BLE001 — a rate read must never break the fold
                logger.debug("rate lookup failed for %s:%s", provider, model, exc_info=True)
                rate = None
            hit = (rate is None, bool(rate is not None and getattr(rate, "source", "") == "local"))
            cache[key] = hit
        return hit

    return look


# ── row folding ─────────────────────────────────────────────────────────────────────────


def _count(bucket: dict[str, Any], key: str) -> None:
    if not key:
        return
    bucket[key] = int(bucket.get(key, 0)) + 1


def _day_from_iso(ts: Any) -> str:
    """``YYYY-MM-DD`` for the ledger's ISO ``ts`` (same prefix rule as ``usage_ledger._day_of``)."""
    text = str(ts or "")
    return text[:10] if len(text) >= 10 else ""


def _day_from_epoch(ts: Any) -> str:
    """``YYYY-MM-DD`` (UTC) for the attempt audit's float-epoch ``ts`` — census use only."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def fold_turn_row(
    fold: dict[str, Any],
    row: dict[str, Any],
    *,
    look: Callable[[str, str], tuple[bool, bool]] | None = None,
) -> bool:
    """Fold one ``usage/turns.jsonl`` ledger turn in place. Returns whether it landed in a cell."""
    look = look or _rate_lookup(None)
    date = _day_from_iso(row.get("ts"))
    provider = str(row.get("provider", "") or "")
    model = str(row.get("model", "") or "")
    if not date or not (provider or model):
        _count(fold.setdefault("unmapped", {}), "row:no_date" if not date else "row:no_ref")
        return False
    purpose, app_name = purpose_for_source(row.get("source", ""))
    if app_name:
        _count(fold.setdefault("app_sources", {}), app_name)

    rate_unpriced, local = look(provider, model)
    # ``priced`` is the ledger's own disclosure and wins when present; the rate table is the
    # fallback for a row written before that field existed.
    priced = row.get("priced")
    unpriced = (not bool(priced)) if priced is not None else rate_unpriced
    dollars = float(row.get("cost_usd", 0.0) or 0.0)
    # TurnUsage has no estimated flag, so a rate-derived cost is indistinguishable from a
    # provider-reported one. Treated as an estimate deliberately (see the module docstring).
    estimated = bool(row.get("estimated", True))

    cell = (
        fold.setdefault("days", {})
        .setdefault(date, {})
        .setdefault(ref_of(provider, model), {})
        .setdefault(purpose, _empty_cell())
    )
    cell["calls"] = int(cell["calls"]) + 1
    cell["tokens_in"] = int(cell["tokens_in"]) + max(0, int(row.get("input_tokens", 0) or 0))
    cell["tokens_out"] = int(cell["tokens_out"]) + max(0, int(row.get("output_tokens", 0) or 0))
    cell["dollars_est"] = round(float(cell["dollars_est"]) + dollars, 6)
    if estimated:
        cell["estimated_dollars"] = round(float(cell["estimated_dollars"]) + dollars, 6)
        cell["estimated_calls"] = int(cell["estimated_calls"]) + 1
    if unpriced:
        cell["unpriced_calls"] = int(cell["unpriced_calls"]) + 1
    if local:
        cell["local_calls"] = int(cell["local_calls"]) + 1
    return True


# ── the audit census (what this fold deliberately does NOT count) ────────────────────────


def audit_census(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count the guarded-attempt spend the fold leaves out, so the gap is stated not hidden.

    Returns ``{calls, dollars_est, by_use_case, days}``. This is NOT added to any total: a loop's
    inner inference appears both here and as a ledger turn, and the two rows share no id, so
    summing them would double-count with no way to detect it. See the module docstring.
    """
    out: dict[str, Any] = {"calls": 0, "dollars_est": 0.0, "by_use_case": {}, "days": {}}
    for rec in rows:
        out["calls"] += 1
        out["dollars_est"] = round(
            float(out["dollars_est"]) + float(rec.get("dollars_est", 0.0) or 0.0), 6
        )
        _count(out["by_use_case"], str(rec.get("use_case", "") or "(blank)"))
        day = _day_from_epoch(rec.get("ts"))
        if day:
            _count(out["days"], day)
    return out


# ── rebuild / refresh ───────────────────────────────────────────────────────────────────


def _iter_json_lines(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerant read — a corrupt line is skipped, the rest still folds
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _default_paths(audit_path: Path | None, ledger_path: Path | None) -> tuple[Path, Path]:
    if audit_path is None:
        from personalclaw.guardrails.audit import _audit_path

        audit_path = _audit_path()
    if ledger_path is None:
        from personalclaw.usage_ledger import _path as _ledger_path

        ledger_path = _ledger_path()
    return Path(audit_path), Path(ledger_path)


def fold_files(
    *,
    home: Path | None = None,
    audit_path: Path | None = None,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """A fold built from scratch: the ledger summed, the attempt audit censused."""
    audit_path, ledger_path = _default_paths(audit_path, ledger_path)
    look = _rate_lookup(home)
    fold = empty_fold()
    turns = 0
    for row in _iter_json_lines(ledger_path):
        if fold_turn_row(fold, row, look=look):
            turns += 1
    fold["uncounted"] = audit_census(_iter_json_lines(audit_path))
    fold["sources"] = {"usage_ledger": turns}
    return fold


def rebuild(
    home: Path,
    *,
    audit_path: Path | None = None,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Refold ``usage_stats.json`` from scratch and persist it.

    The §1.3 rebuild discipline for this fold: the JSONL is capped, so this recovers whatever
    forensic tail remains. A day already aged out of the JSONL is NOT recoverable here — that is
    what :func:`refresh` preserves, and why the fold is the durable record.
    """
    fold = fold_files(home=home, audit_path=audit_path, ledger_path=ledger_path)
    save_usage(home, fold)
    return fold


def _merge_days(prior: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """Fresh over prior, per (date, ref, purpose) cell, keeping whichever saw more calls.

    Trimming can only REMOVE rows from a completed day, so for an aged day the archived cell is the
    more complete record; for the still-growing current day the refold is. Monotone either way.
    """
    out: dict[str, Any] = {}
    for date in set(prior) | set(fresh):
        p_day, f_day = prior.get(date) or {}, fresh.get(date) or {}
        if not isinstance(p_day, dict) or not isinstance(f_day, dict):
            out[date] = f_day if isinstance(f_day, dict) else p_day
            continue
        merged_day: dict[str, Any] = {}
        for ref in set(p_day) | set(f_day):
            p_ref, f_ref = p_day.get(ref) or {}, f_day.get(ref) or {}
            merged_ref: dict[str, Any] = {}
            for purpose in set(p_ref) | set(f_ref):
                p_cell, f_cell = p_ref.get(purpose), f_ref.get(purpose)
                if not isinstance(p_cell, dict):
                    merged_ref[purpose] = f_cell
                elif not isinstance(f_cell, dict):
                    merged_ref[purpose] = p_cell
                else:
                    merged_ref[purpose] = (
                        f_cell
                        if int(f_cell.get("calls", 0)) >= int(p_cell.get("calls", 0))
                        else p_cell
                    )
            merged_day[ref] = merged_ref
        out[date] = merged_day
    return out


def refresh(
    home: Path,
    *,
    audit_path: Path | None = None,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Refold, merge over the persisted fold, persist and return it.

    This is what ``GET /api/usage`` calls, so a deleted ``usage_stats.json`` self-heals on the next
    read (the "reproducible after delete" contract) while days that have aged out of the capped
    JSONL survive in the fold. ``app_sources``/``unmapped``/``uncounted``/``sources`` describe the
    most recent pass over the retained tail, not all history.
    """
    prior = load_usage(home)
    fresh = fold_files(home=home, audit_path=audit_path, ledger_path=ledger_path)
    fresh["days"] = _merge_days(prior.get("days") or {}, fresh.get("days") or {})
    save_usage(home, fresh)
    return fresh


# ── query (the read model behind GET /api/usage) ────────────────────────────────────────


def _today_utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def window_dates(window: str, *, today: str = "") -> list[str]:
    """The dates a window covers, oldest first — a rolling N days including the reference day."""
    days = WINDOW_DAYS.get(window, WINDOW_DAYS["day"])
    ref = today or _today_utc()
    try:
        end = datetime.strptime(ref, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        end = datetime.now(tz=timezone.utc)
    return [(end - timedelta(days=n)).strftime("%Y-%m-%d") for n in range(days - 1, -1, -1)]


def _agg() -> dict[str, Any]:
    return {
        "calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "dollars_est": 0.0,
        "estimated_dollars": 0.0,
        "unpriced_calls": 0,
        "local_calls": 0,
    }


def _add(into: dict[str, Any], cell: dict[str, Any]) -> None:
    into["calls"] += int(cell.get("calls", 0) or 0)
    into["tokens_in"] += int(cell.get("tokens_in", 0) or 0)
    into["tokens_out"] += int(cell.get("tokens_out", 0) or 0)
    into["dollars_est"] = round(into["dollars_est"] + float(cell.get("dollars_est", 0.0) or 0.0), 6)
    into["estimated_dollars"] = round(
        into["estimated_dollars"] + float(cell.get("estimated_dollars", 0.0) or 0.0), 6
    )
    into["unpriced_calls"] += int(cell.get("unpriced_calls", 0) or 0)
    into["local_calls"] += int(cell.get("local_calls", 0) or 0)


def _share(agg: dict[str, Any]) -> float:
    """Dollar-weighted estimated share. 0.0 when there are no dollars to qualify — the honest
    answer for a window that spent nothing, rather than a made-up 1.0."""
    dollars = float(agg.get("dollars_est", 0.0) or 0.0)
    if dollars <= 0:
        return 0.0
    return round(min(1.0, float(agg.get("estimated_dollars", 0.0) or 0.0) / dollars), 4)


def _finish(agg: dict[str, Any], key: str = "") -> dict[str, Any]:
    row = dict(agg)
    row["tokens"] = row["tokens_in"] + row["tokens_out"]
    row["estimated_share"] = _share(agg)
    row["priced"] = row["unpriced_calls"] == 0
    if key:
        row["key"] = key
    return row


def _uncounted_in_window(fold: dict[str, Any], dates: list[str]) -> dict[str, Any]:
    """The censused (not summed) attempt-audit rows whose day falls in the window.

    Per-day dollars are not kept in the census — only counts — so this reports the window's call
    count and the census-wide dollar figure it belongs to, never a fabricated per-window dollar.
    """
    census = fold.get("uncounted") or {}
    days = census.get("days") or {}
    in_window = sum(int(days.get(d, 0) or 0) for d in dates)
    return {
        "calls": in_window,
        "total_calls": int(census.get("calls", 0) or 0),
        "total_dollars_est": float(census.get("dollars_est", 0.0) or 0.0),
        "by_use_case": dict(census.get("by_use_case") or {}),
    }


def query(
    fold: dict[str, Any],
    *,
    window: str = "day",
    group: str = "model",
    today: str = "",
) -> dict[str, Any]:
    """``{rows, total, series, uncounted, …}`` over the fold — derived on request, no collector.

    ``rows`` are grouped by ``model`` (the ``provider:model`` ref), ``provider`` or ``purpose`` and
    sorted by dollars descending then key, so the biggest line item is first. ``series`` is the
    per-day total across the window (oldest first, zero-filled) — the daily/weekly chart's data.
    ``uncounted`` is the guarded-attempt spend deliberately excluded from every figure here.
    """
    if window not in WINDOW_DAYS:
        window = "day"
    if group not in GROUPS:
        group = "model"
    dates = window_dates(window, today=today)
    days = fold.get("days") or {}
    buckets: dict[str, dict[str, Any]] = {}
    total = _agg()
    series: list[dict[str, Any]] = []
    for date in dates:
        day_agg = _agg()
        for ref, purposes in (days.get(date) or {}).items():
            if not isinstance(purposes, dict):
                continue
            for purpose, cell in purposes.items():
                if not isinstance(cell, dict):
                    continue
                if group == "model":
                    key = str(ref)
                elif group == "provider":
                    key = str(ref).split(":", 1)[0]
                else:
                    key = str(purpose)
                _add(buckets.setdefault(key, _agg()), cell)
                _add(day_agg, cell)
                _add(total, cell)
        series.append(
            {
                "date": date,
                "calls": day_agg["calls"],
                "dollars_est": day_agg["dollars_est"],
                "tokens": day_agg["tokens_in"] + day_agg["tokens_out"],
            }
        )
    rows = [_finish(agg, key) for key, agg in buckets.items()]
    rows.sort(key=lambda r: (-float(r["dollars_est"]), str(r["key"])))
    out_total = _finish(total)
    return {
        "window": window,
        "group": group,
        "dates": dates,
        "rows": rows,
        "total": out_total,
        "series": series,
        "estimated_share": out_total["estimated_share"],
        "unmapped": dict(fold.get("unmapped") or {}),
        "app_sources": dict(fold.get("app_sources") or {}),
        "uncounted": _uncounted_in_window(fold, dates),
        "reachable_purposes": list(reachable_purposes()),
    }


# ── monthly recap (template-rendered, never an LLM) ─────────────────────────────────────


def _usd(value: float) -> str:
    """Two decimals once there is a dollar to show, four below — so a real $0.0012 of spend does
    not render as the "$0.00" of no spend."""
    return f"${value:.2f}" if value >= 1 else f"${value:.4f}"


def month_dates(month: str, fold: dict[str, Any]) -> list[str]:
    """The fold's dates inside a ``YYYY-MM`` calendar month, oldest first."""
    prefix = str(month or "")[:7]
    return sorted(d for d in (fold.get("days") or {}) if str(d).startswith(prefix))


def usage_recap(month: str, *, fold: dict[str, Any] | None = None, home: Path | None = None) -> str:
    """A plain-language spend recap for a ``YYYY-MM`` calendar month — template-rendered, so the
    same fold always renders the same sentence (no LLM anywhere near a number).

    Every dollar carries a ``~`` because every dollar in the fold is an estimate today; unpriced
    calls and the uncounted guarded-attempt rows each get their own sentence rather than quietly
    reading as $0.
    """
    if fold is None:
        fold = load_usage(Path(home) if home is not None else Path("."))
    try:
        label = datetime.strptime(str(month)[:7], "%Y-%m").strftime("%B %Y")
    except ValueError:
        label = str(month)
    dates = month_dates(month, fold)
    days = fold.get("days") or {}
    total = _agg()
    by_purpose: dict[str, dict[str, Any]] = {}
    by_ref: dict[str, dict[str, Any]] = {}
    for date in dates:
        for ref, purposes in (days.get(date) or {}).items():
            if not isinstance(purposes, dict):
                continue
            for purpose, cell in purposes.items():
                if not isinstance(cell, dict):
                    continue
                _add(total, cell)
                _add(by_purpose.setdefault(str(purpose), _agg()), cell)
                _add(by_ref.setdefault(str(ref), _agg()), cell)
    uncounted = _uncounted_in_window(fold, dates)
    if total["calls"] == 0:
        base = f"{label}: no model turns recorded."
        return f"{base} {_uncounted_sentence(uncounted)}".strip() if uncounted["calls"] else base

    parts = [f"{label}: ~{_usd(total['dollars_est'])} across {total['calls']} turns."]
    local_pct = round(100 * total["local_calls"] / total["calls"])
    parts.append(f"{local_pct}% of those turns ran locally at $0.")
    if by_purpose:
        ordered = sorted(by_purpose.items(), key=lambda kv: (-float(kv[1]["dollars_est"]), kv[0]))
        parts.append(
            "By purpose: "
            + ", ".join(f"{name} ~{_usd(agg['dollars_est'])}" for name, agg in ordered)
            + "."
        )
    if by_ref:
        top_ref, top_agg = sorted(
            by_ref.items(), key=lambda kv: (-float(kv[1]["dollars_est"]), kv[0])
        )[0]
        parts.append(f"Biggest line item: {top_ref} (~{_usd(top_agg['dollars_est'])}).")
    parts.append("Every dollar here is an estimate, not a provider-reported charge.")
    if total["unpriced_calls"]:
        n = total["unpriced_calls"]
        parts.append(
            f"{n} {'turn' if n == 1 else 'turns'} ran on a model with no price row and "
            "counted as $0, so the total is a floor."
        )
    if uncounted["calls"]:
        parts.append(_uncounted_sentence(uncounted))
    return " ".join(parts)


def _uncounted_sentence(uncounted: dict[str, Any]) -> str:
    n = int(uncounted.get("calls", 0) or 0)
    return (
        f"Separately, {n} unattended model {'call' if n == 1 else 'calls'} were recorded this "
        "month but are not included above — they cannot be merged with turns without "
        "double-counting loops."
    )
