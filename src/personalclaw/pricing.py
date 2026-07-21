"""Model cost estimation from a static per-token price table.

Providers report token counts but not always a dollar cost (most set
``cost_usd=0.0``). ``estimate_cost`` derives a cost from
``model_pricing.json`` (USD per 1,000,000 tokens) so the dashboard's cost
ticker and the usage ledger show a real number.

Design: ONE source of truth for prices (the JSON), ONE function to apply it.
The caller prefers a provider-reported cost when it has one and only falls back
to this estimate when it's zero. A model absent from the table costs ``0.0`` —
we never invent a price for an unknown model.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PRICING_FILE = Path(__file__).resolve().parent / "model_pricing.json"

# model name -> {"in", "out", "cache_read", "cache_write"} USD per 1M tokens.
_PRICES: dict[str, dict[str, float]] = {}
if _PRICING_FILE.exists():
    try:
        with open(_PRICING_FILE, encoding="utf-8") as _fp:
            _PRICES = {
                k: v
                for k, v in json.load(_fp).items()
                if not k.startswith("_") and isinstance(v, dict)
            }
    except (OSError, ValueError):
        logger.warning("Could not load model_pricing.json; cost estimates disabled")

_PER = 1_000_000.0


def _rates(model: str) -> dict[str, float] | None:
    """Resolve a model name to its price row.

    Exact match first; then a longest-prefix match so a live id that carries a
    date/region/version suffix (e.g. ``claude-sonnet-4.5-20250101``) still maps
    to its family row. Returns None when nothing matches (→ cost 0.0).
    """
    if not model:
        return None
    row = _PRICES.get(model)
    if row is not None:
        return row
    best: tuple[int, dict[str, float]] | None = None
    for key, rates in _PRICES.items():
        if model.startswith(key) and (best is None or len(key) > best[0]):
            best = (len(key), rates)
    return best[1] if best else None


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Estimate USD cost for one turn's token usage.

    Returns 0.0 for an unknown model (no row in ``model_pricing.json``) — an
    honest "unpriced", never a guess. Cache-read/write default to the input
    rate / 0 when the row omits them.
    """
    rates = _rates(model)
    if rates is None:
        return 0.0
    in_rate = float(rates.get("in", 0.0))
    out_rate = float(rates.get("out", 0.0))
    cache_read_rate = float(rates.get("cache_read", in_rate))
    cache_write_rate = float(rates.get("cache_write", 0.0))
    cost = (
        (input_tokens or 0) * in_rate
        + (output_tokens or 0) * out_rate
        + (cache_read_tokens or 0) * cache_read_rate
        + (cache_creation_tokens or 0) * cache_write_rate
    ) / _PER
    return round(cost, 6)


def has_pricing(model: str) -> bool:
    """True if *model* has a price row (used to decide whether to estimate)."""
    return _rates(model) is not None
