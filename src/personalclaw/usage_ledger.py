"""The per-turn cost/token ledger (COST-AND-TOKEN-OBSERVABILITY §2.4, C1).

The durable answer to "what did this cost me?" — a fail-open, append-only JSONL of
one :class:`TurnUsage` row per model turn, with rollups by model / source / agent /
provider / day. This module owns the STORE only; the write sites that feed it (C2)
and the surfaces that render it (S2) are later atoms.

Soul guardrails this module enforces:
- **Observation only, never enforcement.** A ledger records; it can never block,
  throttle, or refuse a turn. Budget caps live in ``guardrails`` (``SpendMeter``).
- **Honest zero over invented precision.** A model with no ``model_pricing.json``
  row records its tokens with ``cost_usd = 0.0`` and ``priced = False`` — a caller
  MUST render "unpriced", never ``$0.00``. A rollup whose total mixes any unpriced
  row reports ``priced = False`` so a partial total can't present as complete.
- **Fail-open.** :func:`record_turn` never raises into a turn — a ledger write
  failure degrades to a DEBUG log. This is a user-facing availability surface, not
  a security control (§2.7).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

# Newest-N kept when the log exceeds 2× (the house convention — mirrors feedback.py).
_CAP = 50_000

_GROUP_KEYS = ("model", "source", "agent", "provider", "day")


@dataclass
class TurnUsage:
    """One model turn's token + cost accounting (§C1).

    ``priced`` is False ⇒ there was no ``model_pricing.json`` row AND the provider
    reported no cost, so ``cost_usd`` is an honest 0.0 that MUST render "unpriced".
    """

    ts: str  # ISO-UTC, matching the SEL timestamp convention
    session_key: str
    source: str  # chat | loop | cron | subagent | channel | cli | background
    agent: str  # "" = the default agent
    provider: str  # resolved provider entry name (e.g. "anthropic")
    model: str  # resolved model id — the join key to model_pricing.json
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    priced: bool = True
    duration_ms: int = 0


def _path() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir() / "usage" / "turns.jsonl"


def record_turn(u: TurnUsage) -> None:
    """Append one row. Best-effort and NEVER raises into a turn (§2.7) — a ledger
    write failure degrades to a DEBUG log, never breaks the user's conversation."""
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(u), ensure_ascii=False) + "\n")
        _maybe_trim(p)
    except Exception:  # noqa: BLE001 — the never-raises contract
        logger.debug("usage ledger append failed", exc_info=True)


def record_from_event(
    event: object,
    *,
    source: str,
    session_key: str = "",
    agent: str = "",
    provider: str = "",
    model: str = "",
    estimate_if_missing: bool = True,
) -> None:
    """Record one ledger row from a terminal ``EVENT_COMPLETE`` LLM event (C2).

    The one seam every write-site shares: it reads the token counts + provider cost
    off the event, derives cost via ``pricing.estimate_cost`` ONLY when the provider
    reported none (vendor cost wins when present), and sets ``priced`` False only when
    the model has no price row AND the provider reported no cost — then ``cost_usd`` is
    an honest 0.0 the UI renders "unpriced". Fail-open through :func:`record_turn`.

    ``estimate_if_missing=False`` skips the fallback estimate — for a caller (the chat
    write-site) that ALREADY resolved ``event.cost_usd`` via ``estimate_cost`` itself,
    so re-estimating here would both waste the call and double-count it. ``priced`` still
    reflects the price table (``has_pricing``) so an unpriced model with a caller-supplied
    0.0 renders "unpriced", not a free turn.
    """
    from datetime import datetime, timezone

    from personalclaw.pricing import estimate_cost, has_pricing

    input_tokens = int(getattr(event, "input_tokens", 0) or 0)
    output_tokens = int(getattr(event, "output_tokens", 0) or 0)
    cache_read = int(getattr(event, "cache_read_tokens", 0) or 0)
    cache_creation = int(getattr(event, "cache_creation_tokens", 0) or 0)
    cost = float(getattr(event, "cost_usd", 0.0) or 0.0)
    if not cost and model and estimate_if_missing:
        cost = estimate_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )
    record_turn(
        TurnUsage(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key=session_key,
            source=source,
            agent=agent,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            cost_usd=cost,
            priced=bool(cost) or has_pricing(model),
            duration_ms=int(getattr(event, "duration_ms", 0) or 0),
        )
    )


def _maybe_trim(p: Path) -> None:
    """Trim to the newest ``_CAP`` lines when the file exceeds 2× (atomic rewrite)."""
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) <= 2 * _CAP:
            return
        atomic_write(p, "\n".join(lines[-_CAP:]) + "\n")
    except OSError:
        logger.debug("usage ledger trim failed", exc_info=True)


def _iter_rows() -> list[dict]:
    """Every ledger row as a dict (tolerant: a corrupt line is skipped, fail-open)."""
    p = _path()
    if not p.is_file():
        return []
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict):
                    out.append(d)
            except (json.JSONDecodeError, ValueError):
                continue  # tolerant read — skip the bad line, keep the rest
    except OSError:
        logger.debug("usage ledger read failed", exc_info=True)
    return out


def _day_of(ts: str) -> str:
    """The YYYY-MM-DD prefix of an ISO timestamp (the ``day`` group key)."""
    return ts[:10] if len(ts) >= 10 else ts


def _in_window(ts: str, since: str, until: str) -> bool:
    if since and ts < since:
        return False
    if until and ts >= until:
        return False
    return True


def _blank_agg() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.0,
        "turns": 0,
        "priced": True,
    }


def _fold(agg: dict, row: dict) -> None:
    agg["input_tokens"] += int(row.get("input_tokens", 0) or 0)
    agg["output_tokens"] += int(row.get("output_tokens", 0) or 0)
    agg["cache_read_tokens"] += int(row.get("cache_read_tokens", 0) or 0)
    agg["cache_creation_tokens"] += int(row.get("cache_creation_tokens", 0) or 0)
    agg["cost_usd"] += float(row.get("cost_usd", 0.0) or 0.0)
    agg["turns"] += 1
    # A single unpriced constituent taints the total — it can never present as complete.
    if not row.get("priced", True):
        agg["priced"] = False


def _row_selected(row: dict, since: str, until: str, session_key: str) -> bool:
    """Whether a ledger row is in the query window AND (if given) its session."""
    if not _in_window(str(row.get("ts", "")), since, until):
        return False
    if session_key and str(row.get("session_key", "")) != session_key:
        return False
    return True


def rollup(
    *, since: str = "", until: str = "", group_by: str = "model", session_key: str = ""
) -> list[dict]:
    """Aggregate the ledger, grouped by one of ``model|source|agent|provider|day``.

    Rows carry summed tokens + cost + a ``priced`` flag that is False when ANY
    constituent row was unpriced (so a partially-unpriced group can't look complete).
    Sorted by descending cost then the group key, for a stable, useful default order.
    ``session_key`` (when given) restricts to one session — the session-total surface.
    """
    if group_by not in _GROUP_KEYS:
        raise ValueError(f"group_by must be one of {_GROUP_KEYS}, got {group_by!r}")
    groups: dict[str, dict] = {}
    for row in _iter_rows():
        if not _row_selected(row, since, until, session_key):
            continue
        ts = str(row.get("ts", ""))
        key = _day_of(ts) if group_by == "day" else str(row.get(group_by, ""))
        agg = groups.setdefault(key, _blank_agg())
        _fold(agg, row)
    out = [{group_by: k, **v} for k, v in groups.items()]
    out.sort(key=lambda r: (-r["cost_usd"], str(r[group_by])))
    return out


def totals(*, since: str = "", until: str = "", session_key: str = "") -> dict:
    """Grand total over the window — the same agg shape, ungrouped. ``session_key``
    (when given) restricts to one session, answering "what did this chat cost?"."""
    agg = _blank_agg()
    for row in _iter_rows():
        if _row_selected(row, since, until, session_key):
            _fold(agg, row)
    return agg
