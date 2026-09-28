"""The shipped model price table, ``model_pricing.json`` — the rate table's builtin tier.

Providers report token counts but not always a dollar cost (most set ``cost_usd=0.0``). This
module reads the table core ships (USD per 1,000,000 tokens per bucket) for
:mod:`personalclaw.routing.rates`, which prices every call: the table is its fourth tier, under a
rate the owner set, a local model's known zero and a rate the serving app declared. Nothing else
reads it (``tests/test_every_dollar_is_priced_by_one_function.py``), because a consumer that did
priced by this table alone, which never sees those three.

A model absent from the table has no row: ``has_pricing`` is False and the rate table reads it as
unpriced. We never invent a price for an unknown model. And no row prices a model free by its
name: an open-weight family (``llama3.1``, ``mistral``) costs whatever the machine serving it
bills, which is the rate table's local tier's question, not this table's.
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

# Provider/catalog ids and price-table keys disagree on separator style
# (catalog/Bedrock ids use hyphenated version parts — ``claude-opus-4-8``,
# ``global.anthropic.claude-opus-4-8`` — while this table keys on the dotted
# family form ``claude-opus-4.8``). Canonicalize at THIS single seam only:
# strip a provider/region prefix (any ``<region>.anthropic.`` inference-profile
# form — us/global/eu/apac and whatever region Bedrock mints next — plus the
# bare ``anthropic.``) and re-dot a hyphenated version tail so both forms
# resolve to one row. The raw id always wins first — a table key that IS
# hyphenated (e.g. ``claude-sonnet-4-20250514``) keeps resolving exactly as
# before. PCS-9 audit: the version tail must be found BEFORE a trailing date
# stamp, not at the end of the string — a live Bedrock id like
# ``us.anthropic.claude-opus-4-8-20260101-v1:0`` carries ``-<date>`` after the
# version pair, and anchoring at ``$`` re-dotted the wrong pair
# (``…4-8.20260101``), leaving the id unpriced.
_PROVIDER_PREFIX = re.compile(r"^(?:[a-z]{2,6}\.)?anthropic\.")
_VERSION_TAIL = re.compile(r"-(\d+)-(\d+)(?=-\d{8}|$)")


def _canonical(model: str) -> str:
    """Best-effort canonical (dotted-family) form of a catalog/provider id."""
    m = _PROVIDER_PREFIX.sub("", model)
    m = m.removesuffix("-v1:0")
    return _VERSION_TAIL.sub(r"-\1.\2", m, count=1)


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
    """USD for one call's token usage at *model*'s row, the builtin tier's rate.

    0.0 for a model with no row; the rate table asks :func:`has_pricing` first, so that 0.0 is
    never read as a price. Cache-read/write default to the input rate / 0 when the row omits them.
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
    """True if *model* has a row in the shipped table."""
    return _rates(model) is not None
