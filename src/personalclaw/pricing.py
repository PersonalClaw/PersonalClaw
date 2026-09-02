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
import re
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

# SM-10: provider/catalog ids and price-table keys disagree on separator style
# (catalog/Bedrock ids use hyphenated version parts — ``claude-opus-4-8``,
# ``global.anthropic.claude-opus-4-8`` — while this table keys on the dotted
# family form ``claude-opus-4.8``). Canonicalize at THIS single seam only:
# strip a provider/region prefix (``us.anthropic.`` / ``global.anthropic.`` /
# ``anthropic.``) and re-dot a hyphenated version tail so both forms resolve
# to one row. The raw id always wins first — a table key that IS hyphenated
# (e.g. ``claude-sonnet-4-20250514``) keeps resolving exactly as before.
_PROVIDER_PREFIXES = ("us.anthropic.", "global.anthropic.", "eu.anthropic.", "anthropic.")
_VERSION_TAIL = re.compile(r"-(\d+)-(\d+)$")


def _canonical(model: str) -> str:
    """Best-effort canonical (dotted-family) form of a catalog/provider id."""
    m = model
    for prefix in _PROVIDER_PREFIXES:
        if m.startswith(prefix):
            m = m[len(prefix) :]
            break
    m = m.removesuffix("-v1:0")
    return _VERSION_TAIL.sub(r"-\1.\2", m)


def _rates(model: str) -> dict[str, float] | None:
    """Resolve a model name to its price row.

    Exact match first; then a longest-prefix match so a live id that carries a
    date/region/version suffix (e.g. ``claude-sonnet-4.5-20250101``) still maps
    to its family row. When the raw id resolves nothing, retry with the
    canonicalized form (provider prefix stripped, hyphenated version re-dotted)
    so a catalog id like ``global.anthropic.claude-opus-4-8`` finds the
    ``claude-opus-4.8`` row. Returns None when nothing matches (→ cost 0.0).
    """
    if not model:
        return None
    for candidate in dict.fromkeys((model, _canonical(model))):
        row = _PRICES.get(candidate)
        if row is not None:
            return row
        best: tuple[int, dict[str, float]] | None = None
        for key, rates in _PRICES.items():
            if candidate.startswith(key) and (best is None or len(key) > best[0]):
                best = (len(key), rates)
        if best is not None:
            return best[1]
    return None


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


def cache_savings_usd(
    model: str,
    *,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float | None:
    """USD the prompt cache saved (or cost) on one turn — counterfactual minus actual.

    Both sides are computed through :func:`estimate_cost` itself, never from a
    hand-derived rate delta: one function owns the rate lookup, so the two paths
    cannot drift when a price row gains a field or a default changes.

    * ``actual`` — what this turn cost with its real cache split.
    * ``counterfactual`` — the same turn with NO prompt cache at all: every
      cached token re-billed at the plain input rate
      (``input + cache_read + cache_creation`` as input, cache buckets zero).
      Sound because the three token buckets are DISJOINT populations, not
      overlapping views of one number — see :func:`cache_hit_pct`'s docstring in
      ``stats.py`` for the file:line evidence.

    Returns ``None`` — never ``0.0`` — when *model* has no price row. An unpriced
    model must be reportable as *unpriced*: a ``0.0`` would be indistinguishable
    from a priced model that saved nothing, and this is exactly the "never
    estimates when the provider reported nothing" clause. A priced model with no
    cache activity at all returns ``0.0``, which is a real measurement.

    The result is NEGATIVE on a first turn that only WROTE the cache (every real
    row prices ``cache_write`` above ``in`` — a 25% write premium for Anthropic
    rows), and that negative is returned as-is rather than clamped to zero. A
    cache write genuinely costs more than the uncached call it replaces; hiding
    it would make the cache look free on the one turn where it is not, and the
    saving only materializes on the later reads.
    """
    if _rates(model) is None:
        return None
    actual = estimate_cost(
        model,
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_creation_tokens,
    )
    uncached_prompt_tokens = (
        (input_tokens or 0) + (cache_read_tokens or 0) + (cache_creation_tokens or 0)
    )
    counterfactual = estimate_cost(model, uncached_prompt_tokens, output_tokens, 0, 0)
    return round(counterfactual - actual, 6)
