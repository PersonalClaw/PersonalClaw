"""The prices you set are part of your configuration, and win over every price PersonalClaw knows.

A model's price is data: the shipped table (``model_pricing.json``, each row naming whose list
price it is and the day it was recorded) and what a provider app declares
(``BrandedProviderSpec.pricing``). Whatever provider and model you use, you can set its price,
and that includes overriding a known one: your price is stored in ``config.json``
(``model_prices.overrides``), round-trips through ``AppConfig`` like every other setting, and is
the first thing the one resolution function reads, before this machine's $0, the app's price and
the table. Model prices lists each model with where its price comes from and its date (the day you
set yours, the day the table recorded its row), the default your price stands in front of, and
resets your price to that default.

The prices you had set before lived in a file of their own, ``model_rates.json``. The gateway's
boot carries them into ``config.json`` once (a price already set there wins) and removes the file.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw.config.loader import AppConfig
from personalclaw.guardrails.budgets import Budget, SpendMeter
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import clear_rate, rate_for, rates_view, set_rate
from personalclaw.usage_ledger import Attribution, recorder

SONNET = "global.anthropic.claude-sonnet-5-5"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: tmp_path / "config.json")
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


def _config(home) -> dict:
    return json.loads((home / "config.json").read_text(encoding="utf-8"))


# ── Stored in your configuration ─────────────────────────────────────────────────────────────


def test_a_price_you_set_is_stored_in_your_config(home):
    (home / "config.json").write_text(json.dumps({"timezone": "Europe/Berlin"}), encoding="utf-8")

    set_rate(f"bedrock:{SONNET}", {"in_per_mtok": 2.5, "out_per_mtok": 12.0}, home=home)

    stored = _config(home)
    assert stored["timezone"] == "Europe/Berlin", "the rest of the configuration is kept"
    row = stored["model_prices"]["overrides"][f"bedrock:{SONNET}"]
    assert (row["in_per_mtok"], row["out_per_mtok"]) == (2.5, 12.0)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["recorded"]), "the day you set it"
    assert not (home / "model_rates.json").exists()


def test_your_prices_round_trip_through_the_configuration(home):
    cfg = AppConfig()
    cfg.model_prices.overrides = {
        "acme:frontier-9": {"in_per_mtok": 1.0, "out_per_mtok": 2.0, "recorded": "2026-10-01"},
        "studio:flux-pro": {"unit": "image", "per_image": 0.05},
    }
    cfg.save()

    loaded = AppConfig.load()

    assert loaded.model_prices.overrides == cfg.model_prices.overrides
    assert AppConfig().to_dict()["model_prices"] == {"overrides": {}}


def test_a_row_in_the_configuration_that_is_no_price_is_not_loaded_as_one(home):
    (home / "config.json").write_text(
        json.dumps({"model_prices": {"overrides": {"acme:x": "cheap", "": {}, "ok:m": {}}}}),
        encoding="utf-8",
    )

    assert AppConfig.load().model_prices.overrides == {"ok:m": {}}
    assert rate_for("ok", "m", home=home) is None, "a row with no price is no price"


# ── Your price wins, for any provider and any model ──────────────────────────────────────────


def test_your_price_overrides_a_known_one_and_resetting_it_brings_the_known_one_back(home):
    known = rate_for("bedrock", SONNET, home=home)
    assert known is not None and known.source == "builtin"

    set_rate(f"bedrock:{SONNET}", {"in_per_mtok": 2.5, "out_per_mtok": 12.0}, home=home)
    mine = rate_for("bedrock", SONNET, home=home)
    assert mine is not None and (mine.in_per_mtok, mine.source) == (2.5, "overlay")

    assert clear_rate(f"bedrock:{SONNET}", home=home) is True
    back = rate_for("bedrock", SONNET, home=home)
    assert back == known and back.source == "builtin"


def test_any_provider_and_model_can_be_given_a_price(home):
    assert rate_for("my-gateway", "house-model-3", home=home) is None

    set_rate("my-gateway:house-model-3", {"in_per_mtok": 0.4, "out_per_mtok": 1.6}, home=home)

    rate = rate_for("my-gateway", "house-model-3", home=home)
    assert rate is not None and (rate.in_per_mtok, rate.out_per_mtok) == (0.4, 1.6)


def test_model_prices_shows_where_a_price_comes_from_its_date_and_the_default_it_overrides(home):
    set_rate(f"bedrock:{SONNET}", {"in_per_mtok": 2.5, "out_per_mtok": 12.0}, home=home)

    view = rates_view([("bedrock", SONNET), ("bedrock", "amazon.nova-pro-v1:0")], home=home)

    mine, known = view["models"]
    assert (mine["source"], mine["in_per_mtok"]) == ("overlay", 2.5)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", mine["recorded"])
    default = mine["default"]
    assert (default["source"], default["vendor"], default["in_per_mtok"]) == (
        "builtin",
        "Anthropic",
        2.0,
    )
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", default["recorded"])
    assert (known["source"], known["vendor"], known["default"]) == ("builtin", "Amazon", None)
    assert view["rates"][0]["recorded"] == mine["recorded"]


class _Answers:
    supports_tools = False

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="draft")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=1_000_000, output_tokens=100_000)


def test_your_price_is_what_the_budget_the_call_log_and_usage_all_count(home):
    set_rate(f"bedrock:{SONNET}", {"in_per_mtok": 2.5, "out_per_mtok": 12.0}, home=home)
    meter = SpendMeter(config_dir=home)
    guard = wrap_model_call_guard(
        _Answers(),
        use_case="background",
        provider_name="bedrock",
        model=SONNET,
        budget=Budget(max_dollars=10.0),
        meter=meter,
    )
    guard.served_ref = f"bedrock:{SONNET}"
    write_row = recorder(guard, Attribution(source="background"))

    async def call() -> None:
        async for event in guard.stream("Draft a reply."):
            if event.kind == EVENT_COMPLETE:
                write_row(event)

    asyncio.run(call())

    (row,) = [
        json.loads(line)
        for line in (home / "usage" / "turns.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    (logged,) = [
        json.loads(line)
        for line in (home / "model_calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert row["cost_usd"] == meter.day_totals().dollars == logged["dollars_est"] == 3.7


# ── The prices set before, in a file of their own ────────────────────────────────────────────


def _legacy(home, rates: dict) -> None:
    (home / "model_rates.json").write_text(
        json.dumps({"version": 2, "rates": rates}), encoding="utf-8"
    )


def test_the_prices_set_before_move_into_your_config_once(home):
    from personalclaw.routing.rates import adopt_prices_set_before

    (home / "config.json").write_text(
        json.dumps(
            {"model_prices": {"overrides": {"acme:a": {"in_per_mtok": 9.0, "out_per_mtok": 9.0}}}}
        ),
        encoding="utf-8",
    )
    _legacy(
        home,
        {
            "acme:a": {"in_per_mtok": 1.0, "out_per_mtok": 1.0},
            "acme:b": {"in_per_mtok": 2.0, "out_per_mtok": 3.0},
            "studio:flux": {"unit": "image", "per_image": 0.05},
            "acme:typo": {"input": 3.0},
        },
    )

    assert adopt_prices_set_before(home=home) == 2
    overrides = _config(home)["model_prices"]["overrides"]
    assert overrides["acme:a"]["in_per_mtok"] == 9.0, "a price already in your config wins"
    assert overrides["acme:b"] == {"in_per_mtok": 2.0, "out_per_mtok": 3.0}
    assert overrides["studio:flux"] == {"unit": "image", "per_image": 0.05}
    assert "acme:typo" not in overrides, "a row that was no price was in effect nowhere"
    assert not (home / "model_rates.json").exists()

    assert adopt_prices_set_before(home=home) == 0, "a second boot changes nothing"
    assert _config(home)["model_prices"]["overrides"] == overrides


def test_a_file_of_prices_that_cannot_be_read_is_left_where_it_is_and_said(home, caplog):
    from personalclaw.routing.rates import adopt_prices_set_before

    (home / "model_rates.json").write_text('{"rates": {"acme:a": ', encoding="utf-8")

    assert adopt_prices_set_before(home=home) == 0
    assert (home / "model_rates.json").read_text(encoding="utf-8") == '{"rates": {"acme:a": '
    assert any("Model prices" in r.getMessage() for r in caplog.records)


def test_the_gateway_boot_carries_them_over(home):
    from personalclaw.cli_server import _boot_config

    _legacy(home, {"acme:b": {"in_per_mtok": 2.0, "out_per_mtok": 3.0}})

    cfg = _boot_config()

    assert cfg.model_prices.overrides["acme:b"]["in_per_mtok"] == 2.0
    assert not (home / "model_rates.json").exists()
    assert rate_for("acme", "b", home=home) is not None


# ── Set, edit and reset from Settings ────────────────────────────────────────────────────────


class _Sel:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_api_access(self, **row) -> None:
        self.rows.append(row)


async def _client() -> TestClient:
    from personalclaw.dashboard.handlers.model_rates import register_model_rates_routes

    app = web.Application()
    register_model_rates_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_a_known_price_is_overridden_and_reset_from_settings(home, monkeypatch):
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    sel = _Sel()
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: sel)
    get_default_registry().register_entry(
        ProviderEntry(
            name="cloud",
            type="openai_compatible",
            model="",
            options={"endpoint": "https://models.example.com/v1"},
        )
    )
    (home / "config.json").write_text(
        json.dumps({"providers": [{"name": "cloud", "type": "openai_compatible"}]}),
        encoding="utf-8",
    )
    (home / "active_models.json").write_text(
        json.dumps({"background": [f"cloud:{SONNET}"]}), encoding="utf-8"
    )
    c = await _client()
    try:
        before = await (await c.get("/api/models/rates")).json()
        await c.put(
            "/api/models/rates",
            json={"key": f"cloud:{SONNET}", "in_per_mtok": 2.5, "out_per_mtok": 12.0},
        )
        during = await (await c.get("/api/models/rates")).json()
        reset = await c.delete("/api/models/rates", params={"key": f"cloud:{SONNET}"})
        after = await reset.json()
    finally:
        await c.close()

    assert before["models"][0]["source"] == "builtin"
    assert during["models"][0]["source"] == "overlay"
    assert during["models"][0]["default"]["in_per_mtok"] == 2.0
    assert reset.status == 200 and after["models"][0]["source"] == "builtin"
    assert _config(home)["model_prices"]["overrides"] == {}
    assert _config(home)["providers"] == [{"name": "cloud", "type": "openai_compatible"}]
    assert [r["operation"] for r in sel.rows] == ["model_rates.set", "model_rates.clear"]
