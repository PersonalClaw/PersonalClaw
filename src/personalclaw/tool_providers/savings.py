"""TokenJuice savings ledger (Context Economy §1.3) — the counterfactual meter.

Every projection that truncated a large tool result "saved" the difference between the raw
size and the projected preview it fed the model. This module records those savings as
**aggregated** rows keyed ``(month, model, compressor)`` in a single small JSON file
(``~/.personalclaw/tokenjuice_savings.json``) — bounded by construction (no per-event log,
so it can't grow without limit). Surfaced read-only via ``GET /api/tools/savings`` and a
Settings → Tools card.

**This is the SAVINGS (counterfactual) ledger, not spend metering.** Authoritative token/
dollar spend is AUTONOMY-GUARDRAILS' attempt records (``model_calls.jsonl``); ``cost_usd``
on LLM events is currently unpopulated. Tokens here are ESTIMATED (``chars/4``, flagged
``estimated``); this store will cross-reference the guardrails real token counts once that
lands rather than duplicating metering.

Accounting must never block or slow dispatch: every write is best-effort and swallows all
errors (a metering failure must not affect a tool call).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_FILENAME = "tokenjuice_savings.json"
_CHARS_PER_TOKEN = 4  # the standard rough estimate; flagged `estimated` in the surface


def _path():
    return config_dir() / _FILENAME


def record_saving(
    *, month: str, model: str, compressor: str, chars_in: int, chars_out: int
) -> None:
    """Add one projection's savings into the aggregated ledger. Best-effort; never raises.

    Rows are keyed ``"<month>|<model>|<compressor>"`` and accumulate ``count``,
    ``chars_in``, ``chars_out`` so the file size is bounded by (# months × # models ×
    # compressors), not by call volume. A projection that didn't actually shrink the
    output (``chars_out >= chars_in``) is not recorded — only real savings count.
    """
    if chars_in <= 0 or chars_out >= chars_in:
        return
    try:
        data = _load()
        rows: dict[str, Any] = data.setdefault("rows", {})
        key = f"{month}|{model or 'unknown'}|{compressor or 'generic'}"
        row = rows.setdefault(
            key,
            {
                "month": month,
                "model": model or "unknown",
                "compressor": compressor or "generic",
                "count": 0,
                "chars_in": 0,
                "chars_out": 0,
            },
        )
        row["count"] += 1
        row["chars_in"] += chars_in
        row["chars_out"] += chars_out
        atomic_write(_path(), json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        logger.debug("tokenjuice savings write failed", exc_info=True)


def _load() -> dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {"schema": 1, "rows": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "rows" not in data:
            return {"schema": 1, "rows": {}}
        return data
    except (json.JSONDecodeError, OSError):
        return {"schema": 1, "rows": {}}


def summary() -> dict[str, Any]:
    """Read-only savings summary for the API/UI. Aggregates rows into totals + a
    per-compressor breakdown + the top compressor, with estimated token counts.

    Never raises — returns an empty summary if the file is absent/corrupt.
    """
    data = _load()
    rows = list(data.get("rows", {}).values())
    total_in = sum(int(r.get("chars_in", 0)) for r in rows)
    total_out = sum(int(r.get("chars_out", 0)) for r in rows)
    saved_chars = max(0, total_in - total_out)

    by_compressor: dict[str, int] = {}
    for r in rows:
        c = str(r.get("compressor", "generic"))
        by_compressor[c] = by_compressor.get(c, 0) + max(
            0, int(r.get("chars_in", 0)) - int(r.get("chars_out", 0))
        )
    top = max(by_compressor.items(), key=lambda kv: kv[1])[0] if by_compressor else None

    return {
        "saved_chars": saved_chars,
        "saved_tokens_estimated": saved_chars // _CHARS_PER_TOKEN,
        "estimated": True,
        "projection_count": sum(int(r.get("count", 0)) for r in rows),
        "top_compressor": top,
        "by_compressor": by_compressor,
        "rows": rows,
    }
