"""Model cost estimation from the pricing table.

Pins the contract the cost ticker / usage ledger rely on: known models compute
a real number from token counts; unknown models cost exactly 0.0 (honest — never
an invented price); prefix matching handles date/region-suffixed live model ids;
cache rates are applied separately.
"""

from __future__ import annotations

import json

from personalclaw import pricing
from personalclaw.pricing import _PRICING_FILE, estimate_cost, has_pricing


def test_known_model_input_output():
    # claude-sonnet-4.5: in 3.0 / out 15.0 per 1M
    cost = estimate_cost("claude-sonnet-4.5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert abs(cost - 18.0) < 1e-6


def test_unknown_model_is_zero():
    assert estimate_cost("totally-made-up-xyz", input_tokens=999_999, output_tokens=999_999) == 0.0
    assert not has_pricing("totally-made-up-xyz")


def test_empty_model_is_zero():
    assert estimate_cost("", input_tokens=1_000_000) == 0.0


def test_prefix_match_handles_suffix():
    # A live id with a date suffix maps to its family row.
    base = estimate_cost("claude-sonnet-4.5", input_tokens=1_000_000)
    suffixed = estimate_cost("claude-sonnet-4.5-20991231", input_tokens=1_000_000)
    assert suffixed == base == 3.0


def test_cache_rates_applied():
    # sonnet-4.5: cache_read 0.3, cache_write 3.75 per 1M
    cost = estimate_cost(
        "claude-sonnet-4.5",
        cache_read_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
    )
    assert abs(cost - (0.3 + 3.75)) < 1e-6


def test_zero_tokens_zero_cost():
    assert estimate_cost("claude-sonnet-4.5") == 0.0


def test_proportional():
    half = estimate_cost("gpt-4o", input_tokens=500_000)
    full = estimate_cost("gpt-4o", input_tokens=1_000_000)
    assert abs(full - 2 * half) < 1e-6


def test_pricing_rows_well_formed():
    """Every non-comment row has numeric in/out — guards against a typo'd table."""
    data = json.loads(_PRICING_FILE.read_text(encoding="utf-8"))
    for key, row in data.items():
        if key.startswith("_"):
            continue
        assert isinstance(row, dict), f"{key} row is not an object"
        assert isinstance(row.get("in"), (int, float)), f"{key} missing numeric 'in'"
        assert isinstance(row.get("out"), (int, float)), f"{key} missing numeric 'out'"


def test_pricing_keys_subset_of_token_table():
    """Pricing keys should exist in model_tokens.json (same model namespace).

    Keeps the two tables aligned — a priced model the rest of the app doesn't
    know about is almost certainly a typo.
    """
    tokens_file = _PRICING_FILE.parent / "model_tokens.json"
    tokens = {
        k for k in json.loads(tokens_file.read_text(encoding="utf-8")) if not k.startswith("_")
    }
    priced = {k for k in pricing._PRICES}
    orphans = priced - tokens
    assert not orphans, f"priced models absent from model_tokens.json: {orphans}"
