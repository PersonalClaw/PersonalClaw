"""``cache_savings_usd`` — counterfactual-minus-actual, and its honest ``None`` (PCS-7).

The function under test answers "what did the prompt cache save on this turn?" by
pricing the same turn twice through ``estimate_cost``. These tests pin three things a
future refactor could quietly break: the arithmetic (against the REAL price row read out
of ``model_pricing.json``, not a fixture table), the negative first-turn result, and the
``None``-vs-``0.0`` distinction for an unpriced model — including a vacuity assertion
proving that distinction is new behaviour and not a restatement of ``estimate_cost``.
"""

import json

import pytest

from personalclaw.pricing import _PRICING_FILE, cache_savings_usd, estimate_cost, has_pricing

# A real row (all 26 rows in the table carry both `cache_read` and `cache_write`).
PRICED_MODEL = "claude-sonnet-4.6"
# Deliberately not a prefix of any table key, so `_rates`' longest-prefix match misses too.
UNPRICED_MODEL = "zzz-not-a-real-model-9000"

_PER = 1_000_000.0


def _row(model: str) -> dict[str, float]:
    """The model's price row, read from the shipped JSON (no fixture table)."""
    with open(_PRICING_FILE, encoding="utf-8") as fp:
        return json.load(fp)[model]


def _hand_cost(
    row: dict[str, float],
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Price one turn by hand, rounded the way ``estimate_cost`` rounds."""
    cost = (
        input_tokens * row["in"]
        + output_tokens * row["out"]
        + cache_read_tokens * row["cache_read"]
        + cache_creation_tokens * row["cache_write"]
    ) / _PER
    return round(cost, 6)


def test_priced_model_with_cache_reads_saves_the_hand_computed_delta() -> None:
    """A cache-read turn saves counterfactual-minus-actual, to the cent-fraction."""
    row = _row(PRICED_MODEL)
    in_tok, out_tok, read = 10_000, 2_000, 100_000

    actual = _hand_cost(row, input_tokens=in_tok, output_tokens=out_tok, cache_read_tokens=read)
    counterfactual = _hand_cost(row, input_tokens=in_tok + read, output_tokens=out_tok)
    expected = round(counterfactual - actual, 6)

    saved = cache_savings_usd(
        PRICED_MODEL,
        cache_read_tokens=read,
        input_tokens=in_tok,
        output_tokens=out_tok,
    )
    # For the current sonnet row (in 3.0 / cache_read 0.3 per 1M): 0.36 - 0.09 = 0.27.
    assert saved == expected
    assert saved is not None and saved > 0


def test_first_turn_that_only_writes_the_cache_is_negative() -> None:
    """Creation-only turns cost MORE than uncached — the honest number, not a bug.

    Every real row prices ``cache_write`` above ``in`` (a 25% write premium on the
    Anthropic rows), so writing the cache is strictly more expensive than the plain
    call it replaces. The saving only materializes on the later reads. The clause
    requires this negative be reported, not clamped to zero.
    """
    saved = cache_savings_usd(
        PRICED_MODEL,
        cache_read_tokens=0,
        cache_creation_tokens=50_000,
        input_tokens=1_000,
        output_tokens=500,
    )
    assert saved is not None
    assert saved < 0

    row = _row(PRICED_MODEL)
    assert row["cache_write"] > row["in"], "premise: a cache write costs more than plain input"


def test_unpriced_model_is_none_and_not_a_zero() -> None:
    """No price row → ``None``. A ``0.0`` here would read as "the cache saved nothing"."""
    assert not has_pricing(UNPRICED_MODEL), "premise: this model must have no price row"

    saved = cache_savings_usd(
        UNPRICED_MODEL,
        cache_read_tokens=100_000,
        cache_creation_tokens=5_000,
        input_tokens=10_000,
        output_tokens=2_000,
    )
    assert saved is None
    # i.e. `saved is not 0.0` — spelled without an `is` float comparison (flake8 F632).
    assert not isinstance(saved, float)


def test_unpriced_none_is_a_real_distinction_not_existing_behaviour() -> None:
    """VACUITY: prove the honest-zero branch can fail.

    ``estimate_cost`` returns a bare ``0.0`` for this same unpriced model, so
    "unpriced" and "cost nothing" are the same value there. ``cache_savings_usd``
    returning ``None`` is therefore a NEW distinction this function introduces — if it
    ever returned ``0.0`` instead, this test's sibling above would be a tautology.
    """
    args = dict(input_tokens=10_000, output_tokens=2_000, cache_read_tokens=400)
    assert estimate_cost(
        UNPRICED_MODEL,
        args["input_tokens"],
        args["output_tokens"],
        args["cache_read_tokens"],
        100,
    ) == pytest.approx(0.0)
    assert cache_savings_usd(UNPRICED_MODEL, cache_creation_tokens=100, **args) is None


def test_priced_model_with_no_cache_activity_is_a_measured_zero() -> None:
    """Priced but uncached → ``0.0`` (a real measurement), never ``None``."""
    saved = cache_savings_usd(PRICED_MODEL, input_tokens=10_000, output_tokens=2_000)
    assert saved is not None
    assert saved == pytest.approx(0.0)


def test_empty_model_name_is_unpriced() -> None:
    """``_rates("")`` is None, so an unlabelled turn is reported unpriced, not free."""
    assert cache_savings_usd("", cache_read_tokens=50_000, input_tokens=1_000) is None
