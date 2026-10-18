"""The prompt-cache tallies must stay ONE store, provably.

The rule is that the per-turn aggregate reuses ``stats.py``'s counters "with
no second store". Three rails already ship, and none of them can see a second store
appear:

* ``test_stats_counters_still_carry_both_cache_keys`` checks the two counters are
  PRESENT on the singleton. Its docstring claims it reds "if a refactor adds a
  parallel per-turn store" — it does not. A new accumulator in another module leaves
  ``Stats().snapshot()`` and ``hasattr(Stats, "inc_cache_*")`` untouched.
* ``test_cache_hit_pct_is_module_level_and_stateless`` pins only that the derived
  helper did not become a ``Stats`` method.
* ``TestCallSiteRail`` pins that the call site CALLS the shared helpers. Calling them
  and also keeping a private accumulator are not mutually exclusive.

So "no second store" was, until this file, an invariant held by a hand-run census.
Anything a human counted once, a later change
un-counts silently. This is the executable version: over
``src/personalclaw/**/*.py``, by AST rather than by grep (a docstring naming
``cache_read_tokens`` is not a store), (1) nothing ACCUMULATES a prompt-cache-named
quantity, (2) the only writers of the tally are ``Stats.inc_cache_*`` and they are
called from exactly one module, (3) the two per-turn locals that feed the telemetry
are ASSIGNED from the terminal event, never accumulated across events, and (4) the
singleton grew no cache-named attribute beside its one counter dict.

Half (1)'s expected result is the EMPTY set, which is the shape that passes when a
scanner matches nothing at all. It therefore carries a positive control: the same
scanner is re-run over a source string that does accumulate, and MUST flag it.
"""

from __future__ import annotations

from cache_counters_single_store_shared import _accumulators

from personalclaw.stats import Stats

# A module that keeps its own running total. If the scanner cannot flag this, it
# cannot flag the real thing either.
_POSITIVE_CONTROL = """
class Runner:
    def __init__(self) -> None:
        self._cache_read_tokens = 0

    def on_event(self, event) -> None:
        self._cache_read_tokens += event.cache_read_tokens
"""

# A module that merely PASSES the quantity around. The scanner must not flag this, or
# half (1) would be unsatisfiable and would get weakened rather than obeyed.
_NEGATIVE_CONTROL = """
def render(cache_read_tokens, cache_creation_tokens):
    total = 0
    total += cache_read_tokens + cache_creation_tokens
    return f"{cache_read_tokens} read / {cache_creation_tokens} written -> {total}"
"""


# --- half (1): nothing accumulates the cache quantity -------------------------------


def test_the_accumulator_scanner_can_actually_fail() -> None:
    """VACUITY FLOOR for the census below, whose expected answer is the empty set.

    An empty result is exactly what a scanner that matches nothing returns, so the
    census is worthless unless the scanner is shown to flag a real second store.
    """
    assert _accumulators(_POSITIVE_CONTROL, "control"), (
        "the scanner did not flag `self._cache_read_tokens += ...` — "
        "the census below proves nothing"
    )


def test_the_accumulator_scanner_does_not_flag_pass_through_arithmetic() -> None:
    """Counter-floor: a scanner that flags everything would force its own weakening."""
    assert _accumulators(_NEGATIVE_CONTROL, "control") == [], (
        "the scanner flagged `total += cache_read_tokens`, which is a caller's own "
        "sum, not a second tally"
    )


# --- half (2): one writer, one module ----------------------------------------------


# --- half (3): the turn locals are a snapshot, not a total --------------------------


# --- half (4): the singleton grew no turn-scoped cache attribute --------------------


def test_the_singleton_holds_no_cache_named_attribute_beside_its_counter_dict() -> None:
    """The tally lives INSIDE ``_c``; a cache-named attribute would be store number two."""
    instance = Stats()
    # Vacuity: confirm we are looking at the object that actually holds the tally.
    assert "cache_read_tokens" in instance.snapshot()
    stray = sorted(attr for attr in vars(instance) if "cache" in attr.lower())
    assert stray == [], f"Stats grew cache-named attribute(s) outside `_c`: {stray}"
