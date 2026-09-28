"""``rates.cache_savings_usd`` — counterfactual-minus-actual, and its honest ``None``.

The function under test answers "what did the prompt cache save on this turn?" by pricing the
same turn twice at the effective rate (``ModelRate.cost``), the rate the turn's own cost is
priced at. These tests pin what a future refactor could quietly break: the arithmetic (against
the REAL price row read out of ``model_pricing.json``, not a fixture table), the negative
first-turn result, the ``None``-vs-``0.0`` distinction for an unpriced model — including a
vacuity assertion proving that distinction is the price's own, not a restatement of a bare zero —
and that a rate the owner set prices the saving as it prices the turn.
"""

import json

import pytest

from personalclaw.pricing import _PRICING_FILE
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import cache_savings_usd, price_call, save_overlay

# A real row (every Anthropic row in the table carries both `cache_read` and `cache_write`).
PRICED_MODEL = "claude-sonnet-4.6"
# Deliberately not a prefix of any table key, so `_rates`' longest-prefix match misses too.
UNPRICED_MODEL = "zzz-not-a-real-model-9000"
#: A provider entry no tier knows, so the shipped table is what prices a model.
CLOUD = "some-cloud"

_PER = 1_000_000.0


@pytest.fixture(autouse=True)
def _clear_rate_caches():
    """The overlay memo is process-global (stat-keyed); reset it around each test."""
    rates_mod._overlay_cache = None
    yield
    rates_mod._overlay_cache = None


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
    """Price one turn by hand, rounded the way ``ModelRate.cost`` rounds."""
    cost = (
        input_tokens * row["in"]
        + output_tokens * row["out"]
        + cache_read_tokens * row["cache_read"]
        + cache_creation_tokens * row["cache_write"]
    ) / _PER
    return round(cost, 6)


def test_priced_model_with_cache_reads_saves_the_hand_computed_delta(tmp_path) -> None:
    """A cache-read turn saves counterfactual-minus-actual, to the cent-fraction."""
    row = _row(PRICED_MODEL)
    in_tok, out_tok, read = 10_000, 2_000, 100_000

    actual = _hand_cost(row, input_tokens=in_tok, output_tokens=out_tok, cache_read_tokens=read)
    counterfactual = _hand_cost(row, input_tokens=in_tok + read, output_tokens=out_tok)
    expected = round(counterfactual - actual, 6)

    saved = cache_savings_usd(
        CLOUD,
        PRICED_MODEL,
        cache_read_tokens=read,
        input_tokens=in_tok,
        output_tokens=out_tok,
        home=tmp_path,
    )
    # For the current sonnet row (in 3.0 / cache_read 0.3 per 1M): 0.36 - 0.09 = 0.27.
    assert saved == expected
    assert saved is not None and saved > 0


def test_first_turn_that_only_writes_the_cache_is_negative(tmp_path) -> None:
    """Creation-only turns cost MORE than uncached — the honest number, not a bug.

    Every Anthropic row prices ``cache_write`` above ``in`` (a 25% write premium), so writing the
    cache is strictly more expensive than the plain call it replaces. The saving only
    materializes on the later reads. The clause requires this negative be reported, not clamped
    to zero.
    """
    saved = cache_savings_usd(
        CLOUD,
        PRICED_MODEL,
        cache_read_tokens=0,
        cache_creation_tokens=50_000,
        input_tokens=1_000,
        output_tokens=500,
        home=tmp_path,
    )
    assert saved is not None
    assert saved < 0

    row = _row(PRICED_MODEL)
    assert row["cache_write"] > row["in"], "premise: a cache write costs more than plain input"


def test_unpriced_model_is_none_and_not_a_zero(tmp_path) -> None:
    """No rate → ``None``. A ``0.0`` here would read as "the cache saved nothing"."""
    assert not price_call(CLOUD, UNPRICED_MODEL, home=tmp_path).priced, "premise: no rate"

    saved = cache_savings_usd(
        CLOUD,
        UNPRICED_MODEL,
        cache_read_tokens=100_000,
        cache_creation_tokens=5_000,
        input_tokens=10_000,
        output_tokens=2_000,
        home=tmp_path,
    )
    assert saved is None
    # i.e. `saved is not 0.0` — spelled without an `is` float comparison (flake8 F632).
    assert not isinstance(saved, float)


def test_unpriced_none_is_a_real_distinction_not_a_bare_zero(tmp_path) -> None:
    """VACUITY: prove the honest-zero branch can fail.

    The unpriced call's price is a bare ``0.0`` of dollars, so "unpriced" and "cost nothing"
    share a value there and only ``priced`` tells them apart. ``cache_savings_usd`` returning
    ``None`` is that same distinction carried to the saving — if it ever returned ``0.0``
    instead, this test's sibling above would be a tautology.
    """
    args = dict(input_tokens=10_000, output_tokens=2_000, cache_read_tokens=400)
    price = price_call(CLOUD, UNPRICED_MODEL, cache_creation_tokens=100, home=tmp_path, **args)
    assert price.dollars == pytest.approx(0.0) and price.priced is False
    saving = cache_savings_usd(
        CLOUD, UNPRICED_MODEL, cache_creation_tokens=100, home=tmp_path, **args
    )
    assert saving is None


def test_priced_model_with_no_cache_activity_is_a_measured_zero(tmp_path) -> None:
    """Priced but uncached → ``0.0`` (a real measurement), never ``None``."""
    saved = cache_savings_usd(
        CLOUD, PRICED_MODEL, input_tokens=10_000, output_tokens=2_000, home=tmp_path
    )
    assert saved is not None
    assert saved == pytest.approx(0.0)


def test_empty_model_name_is_unpriced(tmp_path) -> None:
    """An empty model has no rate, so an unlabelled turn is reported unpriced, not free."""
    saved = cache_savings_usd(
        CLOUD, "", cache_read_tokens=50_000, input_tokens=1_000, home=tmp_path
    )
    assert saved is None


def test_a_rate_the_owner_set_prices_the_saving_as_it_prices_the_turn(tmp_path) -> None:
    """The defect: the saving was priced from the shipped table whatever the turn's cost was
    priced at, so a model the owner priced read "unpriced" beside a priced cost, and one the
    table knew kept the table's discount under the owner's own rate."""
    save_overlay(
        {
            f"{CLOUD}:{UNPRICED_MODEL}": {
                "in_per_mtok": 2.0,
                "out_per_mtok": 8.0,
                "cache_read_per_mtok": 0.5,
            }
        },
        home=tmp_path,
    )
    saved = cache_savings_usd(
        CLOUD,
        UNPRICED_MODEL,
        cache_read_tokens=1_000_000,
        input_tokens=0,
        output_tokens=0,
        home=tmp_path,
    )
    # One million cached tokens: 2.0 uncached, 0.5 read from the cache.
    assert saved == pytest.approx(1.5)
