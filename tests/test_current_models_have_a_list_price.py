"""The models a provider serves today have a price, found through every id it calls them by.

A fresh install on Amazon Bedrock bound Claude Sonnet 5.5 and Opus 5.5 through Bedrock's global
inference profile (``global.anthropic.claude-sonnet-5-5``), and neither had a row in the shipped
price table: the chat footer read "unpriced", Usage read "Partial — 1 unpriced model", and a daily
dollar cap refused every unattended call to the models the owner chose. Only Haiku 4.5 had a row.
The table also priced Opus 4.6–4.8 at three times their list price, and an inference profile was
read as a prefix of Anthropic's ids only, so ``us.amazon.nova-pro-v1:0`` found no row though
``amazon.nova-pro-v1:0`` had one.

So: a profile id resolves to its base model's row, whichever vendor made the model; every row names
whose list price it is and the day the table recorded it, and Model prices shows both and the row it
priced the model as; a row for one version never prices another (``claude-opus-5`` is not
``claude-opus-5.6``); and a model nothing prices is still refused under a dollar cap, with words
that say how to lift the refusal.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw import pricing
from personalclaw.guardrails.budgets import Budget, SpendMeter
from personalclaw.guardrails.failure import BudgetExceededError
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import rate_for, rates_view


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


@pytest.mark.parametrize(
    ("model", "rates"),
    [
        ("global.anthropic.claude-sonnet-5-5", (2.0, 10.0)),
        ("global.anthropic.claude-opus-5-5", (4.0, 20.0)),
        ("us.anthropic.claude-sonnet-5-5", (2.0, 10.0)),
        ("eu.anthropic.claude-opus-5-5", (4.0, 20.0)),
        ("apac.anthropic.claude-sonnet-5-5", (2.0, 10.0)),
        ("us-gov.anthropic.claude-sonnet-5-5", (2.0, 10.0)),
        ("anthropic.claude-opus-5-5", (4.0, 20.0)),
        ("global.anthropic.claude-haiku-4-5-20251001-v1:0", (1.0, 5.0)),
        ("us.amazon.nova-pro-v1:0", (0.8, 3.2)),
        ("amazon.titan-embed-text-v2:0", (0.02, 0.0)),
        ("claude-sonnet-5-5", (2.0, 10.0)),
        ("claude-opus-4-8", (5.0, 25.0)),
    ],
)
def test_a_profile_id_is_priced_as_its_base_model(model, rates, tmp_path):
    rate = rate_for("bedrock", model, home=tmp_path)

    assert rate is not None, f"{model} has no price"
    assert (rate.in_per_mtok, rate.out_per_mtok) == rates
    assert rate.source == "builtin"


def test_model_prices_say_whose_list_price_it_is_and_when_it_was_recorded(tmp_path):
    view = rates_view([("bedrock", "global.anthropic.claude-sonnet-5-5")], home=tmp_path)

    (row,) = view["models"]
    assert row["ref"] == "bedrock:global.anthropic.claude-sonnet-5-5"
    assert row["priced"] is True
    assert row["source"] == "builtin"
    assert row["vendor"] == "Anthropic"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["recorded"])
    assert row["priced_as"] == "claude-sonnet-5.5"


def test_a_price_you_set_says_it_is_yours_the_day_you_set_it_and_names_no_vendor(tmp_path):
    rates_mod.set_rate(
        "bedrock:global.anthropic.claude-sonnet-5-5",
        {"in_per_mtok": 2.2, "out_per_mtok": 11.0},
        home=tmp_path,
    )

    (row,) = rates_view([("bedrock", "global.anthropic.claude-sonnet-5-5")], home=tmp_path)[
        "models"
    ]
    assert (row["source"], row["in_per_mtok"], row["vendor"]) == ("overlay", 2.2, "")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["recorded"])
    assert row["default"]["source"] == "builtin", "the list price it stands in front of"


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-5-6",
        "global.anthropic.claude-sonnet-5-9",
        "claude-sonnet-4-7",
        "gpt-4.1",
        "gpt-4o-mini-tts",
        "us.anthropic.claude-nonexistent-9-9",
        "us.meta.llama-4-8",
    ],
)
def test_a_row_for_one_model_never_prices_another(model, tmp_path):
    """A family row matched any id it was a prefix of, so a version the table has never heard of
    took the price of an older one: ``gpt-4.1`` read as ``gpt-4`` at $30/$60."""
    assert rate_for("some-cloud", model, home=tmp_path) is None


def test_a_dated_snapshot_is_priced_as_its_model(tmp_path):
    rate = rate_for("anthropic", "claude-sonnet-5-5-20261001", home=tmp_path)

    assert rate is not None and (rate.in_per_mtok, rate.out_per_mtok) == (2.0, 10.0)


def test_every_row_names_its_vendor_and_the_day_it_was_recorded():
    data = json.loads(pricing._PRICING_FILE.read_text(encoding="utf-8"))
    rows = {k: v for k, v in data.items() if not k.startswith("_")}
    assert len(rows) >= 20, "premise: the shipped table has its rows"
    for key, row in rows.items():
        assert str(row.get("vendor", "")).strip(), f"{key} names no vendor"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("recorded", ""))), key


class _Answers:
    supports_tools = False

    def __init__(self, sent: list[str]) -> None:
        self.sent = sent

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        self.sent.append(message)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="draft")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1_000, output_tokens=100)


async def _call(meter: SpendMeter, sent: list[str], model: str) -> str:
    guard = wrap_model_call_guard(
        _Answers(sent),
        use_case="background",
        provider_name="bedrock",
        model=model,
        budget=Budget(max_dollars=4.0),
        meter=meter,
    )
    return "".join(
        [e.text async for e in guard.stream("Draft a reply.") if e.kind == EVENT_TEXT_CHUNK]
    )


def test_an_unattended_call_to_a_current_model_runs_under_a_dollar_cap(tmp_path):
    meter = SpendMeter(config_dir=tmp_path)
    sent: list[str] = []

    assert asyncio.run(_call(meter, sent, "global.anthropic.claude-sonnet-5-5")) == "draft"

    assert sent == ["Draft a reply."]
    assert meter.day_totals().dollars == pytest.approx((1_000 * 2.0 + 100 * 10.0) / 1e6)
    assert meter.day_totals().unpriced == 0


def test_a_model_nothing_prices_is_refused_under_a_dollar_cap_with_how_to_lift_it(tmp_path):
    meter = SpendMeter(config_dir=tmp_path)
    sent: list[str] = []

    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(_call(meter, sent, "us.anthropic.claude-nonexistent-9-9"))

    assert sent == []
    assert refused.value.sentence() == (
        "bedrock:us.anthropic.claude-nonexistent-9-9 has no price, so the daily dollar budget "
        "cannot count what a call to it would spend: set its price in Settings → Usage → Model "
        "prices ($0 if it costs nothing), or set Max dollars / day to 0 in Settings → Guardrails "
        "to lift the daily dollar cap."
    )


def test_a_row_that_names_no_cache_rate_bills_cached_tokens_as_plain_input(tmp_path):
    """The shipped table read a missing cache-write rate as $0, so a model whose row names none
    (an embedding model, an older one) showed "$0 cache write" and billed cache writes as free:
    a row that states no cache discount is not evidence of one."""
    rate = rate_for("bedrock", "amazon.titan-embed-text-v2:0", home=tmp_path)

    assert rate is not None
    assert (rate.cache_read_per_mtok, rate.cache_write_per_mtok) == (None, None)
    assert rate.cost(cache_creation_tokens=1_000_000) == pytest.approx(0.02)
