"""The spend-ceiling price-key rail.

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
from personalclaw.pricing import _candidates, price_row


def has_pricing(model: str) -> bool:
    return price_row(model) is not None


def _in_per_mtok(model: str) -> float:
    found = price_row(model)
    return float(found.fields["in"]) if found is not None else 0.0


SRC = Path(pricing.__file__).resolve().parent


#: Open-weight families: no vendor list price, because whoever serves one bills for it (or does
#: not). A model server on this machine prices them at a known $0 (the rate table's local tier,
#: by where the entry's endpoint is); one elsewhere takes a rate set or declared for it.
_PRICED_WHERE_THEY_RUN = ("llama3.1", "llama3.2", "qwen2.5", "mistral", "phi3")


def _census() -> list[str]:
    """The live catalog census: every id model_tokens.json knows about."""
    tokens = json.loads((SRC / "model_tokens.json").read_text(encoding="utf-8"))
    ids = [k for k in tokens if not k.startswith("_")]
    assert len(ids) >= 30, f"census suspiciously small ({len(ids)}) — wrong file?"
    return ids


class TestTheRail:
    def test_every_catalog_id_resolves_to_a_price_row(self) -> None:
        """The rail: an id the app serves but cannot price is a regression.

        Every id but an open-weight family's, which has no list price to carry, so absence
        always means 'someone added a model and forgot the price table'.
        """
        census = _census()
        assert set(_PRICED_WHERE_THEY_RUN) <= set(census), "premise: the families are catalogued"
        unresolved = [m for m in census if m not in _PRICED_WHERE_THEY_RUN and not has_pricing(m)]
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
            assert _in_per_mtok(form) > 0.0, f"{form!r} priced at 0 — the ceiling is inert again"

    def test_no_row_prices_a_model_free_by_its_name(self) -> None:
        """The defect: an explicit zero row per open-weight family made the model free wherever
        it ran, and the row matched by prefix. So an Ollama on another machine serving
        ``llama3.1`` counted $0 against the daily cap, and so did every model of Mistral's
        billed API (``mistral-large-latest`` starts with ``mistral``). A local model's $0 is
        the rate table's, decided by where its entry sends (``tests/test_routing_rates.py``)."""
        for m in _PRICED_WHERE_THEY_RUN:
            assert not has_pricing(m), f"{m} has a row, which prices it the same on any machine"
        for billed in ("mistral-large-latest", "mistral-small-2503", "qwen2.5-72b-instruct"):
            assert not has_pricing(billed), f"{billed} matched a free family's row by prefix"


class TestTheNormalizationPin:
    """Regression pins for _candidates — the dot/hyphen shim's exact contract."""

    def test_hyphenated_version_tail_re_dots(self) -> None:
        assert "claude-opus-4.8" in _candidates("claude-opus-4-8")

    def test_provider_and_region_prefixes_strip(self) -> None:
        assert "claude-opus-4.8" in _candidates("global.anthropic.claude-opus-4-8")
        assert "claude-3-7-sonnet-20250219" in _candidates(
            "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
        )
        assert "amazon.nova-pro-v1:0" in _candidates("us.amazon.nova-pro-v1:0")

    def test_a_date_suffix_is_not_a_version_tail(self) -> None:
        """8-digit dates must NOT be re-dotted — claude-sonnet-4-20250514 keys
        the table verbatim and must stay resolvable by the raw-first path."""
        assert has_pricing("claude-sonnet-4-20250514")
        assert _in_per_mtok("claude-sonnet-4-20250514") > 0.0

    def test_raw_id_always_wins_over_canonical(self) -> None:
        """An id that already keys the table resolves as itself — the shim only
        fires for ids the raw path cannot place."""
        assert _in_per_mtok("claude-opus-4.7") > 0.0

    def test_unknown_model_has_no_row(self) -> None:
        assert not has_pricing("totally-unknown-model-xyz")
