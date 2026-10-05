"""Model cost estimation from the pricing table.

Pins the contract the cost ticker / usage ledger rely on: known models compute
a real number from token counts; unknown models cost exactly 0.0 (honest — never
an invented price); prefix matching handles date/region-suffixed live model ids;
cache rates are applied separately.
"""

from __future__ import annotations

import json

from personalclaw import pricing
from personalclaw.pricing import _PRICING_FILE, price_row


def has_pricing(model: str) -> bool:
    return price_row(model) is not None


def estimate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """USD for one call's tokens at *model*'s shipped row, 0.0 when it has none."""
    found = price_row(model)
    if found is None:
        return 0.0
    row = found.fields
    in_rate = float(row.get("in", 0.0))
    return round(
        (
            input_tokens * in_rate
            + output_tokens * float(row.get("out", 0.0))
            + cache_read_tokens * float(row.get("cache_read", in_rate))
            + cache_creation_tokens * float(row.get("cache_write", 0.0))
        )
        / 1_000_000,
        6,
    )


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


def test_bedrock_inference_profile_ids_resolve():
    """Every Bedrock inference-profile shape prices to its family row. This is what
    makes a real Bedrock run report 'saved $X' instead of 'saved unpriced' — exactly
    what the telemetry verification needs. Region prefixes are a pattern, not a list,
    because Bedrock mints new ones (apac. arrived after the original us/global/eu
    triple was hardcoded); and the version pair must re-dot BEFORE a trailing date
    stamp, not at $ — ``…-4-8-20260101-v1:0`` anchored at the end re-dotted the
    wrong pair."""
    base = estimate_cost("claude-opus-4.8", input_tokens=1_000_000)
    assert base > 0.0
    for live_id in (
        "global.anthropic.claude-opus-4-8",
        "us.anthropic.claude-opus-4-8",
        "eu.anthropic.claude-opus-4-8",
        "apac.anthropic.claude-opus-4-8",
        "anthropic.claude-opus-4-8",
        "us.anthropic.claude-opus-4-8-v1:0",
        "us.anthropic.claude-opus-4-8-20260101-v1:0",
    ):
        assert estimate_cost(live_id, input_tokens=1_000_000) == base, live_id
        assert has_pricing(live_id), live_id


def test_unknown_stays_unpriced_through_canonicalization():
    """The vacuity partner: canonicalization must never conjure a price. A
    prefixed id whose family has no row, and a non-Anthropic id that merely
    looks region-prefixed, both stay an honest 0.0."""
    assert estimate_cost("us.anthropic.claude-nonexistent-9-9", input_tokens=1_000_000) == 0.0
    assert estimate_cost("us.meta.llama-4-8", input_tokens=1_000_000) == 0.0
    assert not has_pricing("us.anthropic.claude-nonexistent-9-9")


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
    """Every token row has numeric in/out, and every other row its unit's price — guards
    against a typo'd table."""
    from personalclaw.routing.rates import UNIT_PRICE_FIELDS, UnitRate

    data = json.loads(_PRICING_FILE.read_text(encoding="utf-8"))
    for key, row in data.items():
        if key.startswith("_"):
            continue
        assert isinstance(row, dict), f"{key} row is not an object"
        unit = row.get("unit", "token")
        if unit == "token":
            assert isinstance(row.get("in"), (int, float)), f"{key} missing numeric 'in'"
            assert isinstance(row.get("out"), (int, float)), f"{key} missing numeric 'out'"
        else:
            assert unit in UNIT_PRICE_FIELDS, f"{key} is priced in an unknown unit {unit!r}"
            rate = UnitRate.from_obj(row, unit=unit)
            assert rate is not None, f"{key} holds no price per {unit}"
            assert rate.unit_price() is not None, f"{key} prices no call made at its defaults"


def test_pricing_keys_subset_of_token_table():
    """Token-priced keys should exist in model_tokens.json (same model namespace).

    Keeps the two tables aligned — a priced model the rest of the app doesn't
    know about is almost certainly a typo. A model billed per image, second, minute or
    character reads no prompt, so it has no context window to list.
    """
    tokens_file = _PRICING_FILE.parent / "model_tokens.json"
    tokens = {
        k for k in json.loads(tokens_file.read_text(encoding="utf-8")) if not k.startswith("_")
    }
    priced = {k for k, row in pricing._PRICES.items() if row.get("unit", "token") == "token"}
    orphans = priced - tokens
    assert not orphans, f"priced models absent from model_tokens.json: {orphans}"
