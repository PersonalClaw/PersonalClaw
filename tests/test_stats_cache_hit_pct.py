"""``cache_hit_pct`` — the prompt-cache hit share, and its honest ``None`` (PCS-7).

Three things are pinned here. (1) The DENOMINATOR is the turn's whole prompt,
``input + cache_read + cache_creation``, because those three buckets are disjoint on
``LLMEvent`` — drop either cache term and the hit share silently overstates. (2) A zero
denominator is ``None``, not ``0%``: no prompt tokens is no measurement, the same rule
``context_pct`` already follows on the turn-complete line. (3) The clause's "reusing
stats.py counters with no second store" — the process-lifetime counters keep both cache
keys, and computing a per-turn ratio must not touch them.
"""

import pytest

from personalclaw.stats import Stats, cache_hit_pct


def test_zero_denominator_is_unmeasured_not_zero_percent() -> None:
    """No prompt tokens at all → ``None``. A ``0.0`` would fabricate "0% cached"."""
    assert cache_hit_pct(cache_read_tokens=0, cache_creation_tokens=0, input_tokens=0) is None


def test_plain_hit_uses_the_whole_prompt_as_denominator() -> None:
    """750 of 1000 prompt tokens came from cache → exactly 75.0."""
    assert cache_hit_pct(
        cache_read_tokens=750, cache_creation_tokens=0, input_tokens=250
    ) == pytest.approx(75.0)


def test_cache_creation_is_in_the_denominator_but_not_the_numerator() -> None:
    """THE DENOMINATOR RAIL: 300 read / (100 in + 300 read + 600 created) = 30%.

    Written cache tokens are part of the prompt the provider processed, so they belong
    below the line; they are not hits, so they never go above it. Dropping the
    ``cache_creation`` term would report 75.0 here, and dropping ``input`` 33.3 —
    both wrong in the flattering direction.
    """
    assert cache_hit_pct(
        cache_read_tokens=300, cache_creation_tokens=600, input_tokens=100
    ) == pytest.approx(30.0)


def test_fully_cached_prompt_is_one_hundred_percent() -> None:
    """Every prompt token served from cache → 100.0."""
    assert cache_hit_pct(
        cache_read_tokens=1_000, cache_creation_tokens=0, input_tokens=0
    ) == pytest.approx(100.0)


def test_uncached_prompt_is_a_measured_zero() -> None:
    """Prompt tokens present, none cached → ``0.0``, a real answer (not ``None``)."""
    result = cache_hit_pct(cache_read_tokens=0, cache_creation_tokens=0, input_tokens=1_000)
    assert result is not None
    assert result == pytest.approx(0.0)


def test_write_only_first_turn_is_zero_percent_not_none() -> None:
    """A creation-only turn has a prompt but no hits — measured 0%, not unmeasured."""
    result = cache_hit_pct(cache_read_tokens=0, cache_creation_tokens=500, input_tokens=0)
    assert result is not None
    assert result == pytest.approx(0.0)


def test_stats_counters_still_carry_both_cache_keys() -> None:
    """RAIL: the two existing counters remain the ONLY cache-token tally.

    The clause says this aggregate reuses ``stats.py``'s counters "with no second
    store". If a refactor adds a parallel per-turn store, or renames/drops either
    counter, this reds instead of leaving two stores to drift apart.
    """
    snapshot = Stats().snapshot()
    assert "cache_read_tokens" in snapshot
    assert "cache_creation_tokens" in snapshot
    assert hasattr(Stats, "inc_cache_read_tokens")
    assert hasattr(Stats, "inc_cache_creation_tokens")


def test_cache_hit_pct_is_module_level_and_stateless() -> None:
    """It derives a ratio from arguments; it must not read or write the singleton."""
    assert not hasattr(Stats, "cache_hit_pct"), "must not become a method on the singleton"
    before = Stats().snapshot()
    cache_hit_pct(cache_read_tokens=750, cache_creation_tokens=250, input_tokens=1_000)
    assert Stats().snapshot() == before
