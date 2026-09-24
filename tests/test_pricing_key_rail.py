"""SM-10 — the spend-ceiling price-key rail.

The daily spend ceiling was inert: catalog/provider model ids (hyphenated
version parts, provider prefixes — ``claude-opus-4-8``,
``global.anthropic.claude-opus-4-8``) did not resolve against the dotted
price-table keys (``claude-opus-4.8``), so ``estimate_cost`` returned 0.0 and
computed spend read $0.00 forever.  These tests are the rail: every id the
catalog serves must resolve to a price row, and the dot/hyphen normalization
is pinned so it cannot silently regress.
"""

from __future__ import annotations

import json
from pathlib import Path

from personalclaw import pricing
from personalclaw.pricing import _canonical, estimate_cost, has_pricing

SRC = Path(pricing.__file__).resolve().parent


def _census() -> list[str]:
    """The live catalog census: every id model_tokens.json knows about."""
    tokens = json.loads((SRC / "model_tokens.json").read_text(encoding="utf-8"))
    ids = [k for k in tokens if not k.startswith("_")]
    assert len(ids) >= 30, f"census suspiciously small ({len(ids)}) — wrong file?"
    return ids


class TestTheRail:
    def test_every_catalog_id_resolves_to_a_price_row(self) -> None:
        """The rail: an id the app serves but cannot price is a regression.

        A model that is genuinely free (local runtime) carries an explicit
        zero row — a REAL price — rather than being absent, so absence always
        means 'someone added a model and forgot the price table'.
        """
        unresolved = [m for m in _census() if not has_pricing(m)]
        assert not unresolved, (
            "catalog ids with no resolvable price row (add a row to "
            f"model_pricing.json or fix _canonical): {unresolved}"
        )

    def test_the_flagship_bedrock_forms_price_nonzero(self) -> None:
        """Daily spend renders a nonzero value for a session on a priced model —
        through every id form the providers emit for the same family."""
        for form in (
            "claude-opus-4.8",
            "claude-opus-4-8",
            "global.anthropic.claude-opus-4-8",
        ):
            cost = estimate_cost(form, input_tokens=1_000_000, output_tokens=0)
            assert cost > 0.0, f"{form!r} priced at 0 — the ceiling is inert again"

    def test_local_models_price_to_a_real_zero(self) -> None:
        """A local model is free: priced (has a row) AND zero — never 'unpriced'."""
        for m in ("llama3.1", "mistral", "phi3"):
            assert has_pricing(m), f"{m} lost its explicit zero row"
            assert estimate_cost(m, input_tokens=1_000_000) == 0.0


class TestTheNormalizationPin:
    """Regression pins for _canonical — the dot/hyphen shim's exact contract."""

    def test_hyphenated_version_tail_re_dots(self) -> None:
        assert _canonical("claude-opus-4-8") == "claude-opus-4.8"

    def test_provider_and_region_prefixes_strip(self) -> None:
        assert _canonical("global.anthropic.claude-opus-4-8") == "claude-opus-4.8"
        assert _canonical("us.anthropic.claude-3-7-sonnet-20250219-v1:0") == (
            "claude-3-7-sonnet-20250219"
        )

    def test_a_date_suffix_is_not_a_version_tail(self) -> None:
        """8-digit dates must NOT be re-dotted — claude-sonnet-4-20250514 keys
        the table verbatim and must stay resolvable by the raw-first path."""
        assert has_pricing("claude-sonnet-4-20250514")
        row_direct = estimate_cost("claude-sonnet-4-20250514", input_tokens=1_000_000)
        assert row_direct > 0.0

    def test_raw_id_always_wins_over_canonical(self) -> None:
        """An id that already keys the table resolves as itself — the shim only
        fires for ids the raw path cannot place."""
        dotted = estimate_cost("claude-opus-4.7", input_tokens=1_000_000)
        assert dotted > 0.0

    def test_unknown_model_still_costs_zero(self) -> None:
        assert estimate_cost("totally-unknown-model-xyz", input_tokens=1_000_000) == 0.0
        assert not has_pricing("totally-unknown-model-xyz")
