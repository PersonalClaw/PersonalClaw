"""The prices model calls are counted at are set in Settings, one rate at a time.

Your prices (``config.json`` → ``model_prices.overrides``) are what a model's calls are counted at
by the daily and per-run dollar caps, the Usage page and cost-aware routing, and before this they
could only be written by hand: the Usage page told the owner to edit a file in their home to price
a model the caps could not count. ``GET /api/models/rates`` reads what Settings → Usage → Model
prices shows, ``PUT`` sets one rate and ``DELETE`` resets one. Every field a price holds is one the
route sets (and the day it was set), a price set here is the price the next call is charged at,
and the routes are the owner's.
"""

from __future__ import annotations

import json
import math

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw.apps.permissions import OwnerOnly, route_authz
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import rate_for

_REF = "acme-cloud:acme-large"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    get_default_registry().register_entry(
        ProviderEntry(
            name="acme-cloud",
            type="openai_compatible",
            model="",
            options={"endpoint": "https://models.example.com/v1"},
        )
    )
    # Configured as the Add-instance form writes it, and bound to two uses.
    (tmp_path / "config.json").write_text(
        json.dumps({"providers": [{"name": "acme-cloud", "type": "openai_compatible"}]}),
        encoding="utf-8",
    )
    (tmp_path / "active_models.json").write_text(
        json.dumps({"background": [_REF], "chat": [_REF, "acme-cloud:"]}), encoding="utf-8"
    )
    yield tmp_path
    rates_mod._overlay_cache = None


class _Sel:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def log_api_access(self, **row) -> None:
        self.rows.append(row)


@pytest.fixture
def sel(monkeypatch):
    fake = _Sel()
    monkeypatch.setattr("personalclaw.dashboard.handlers.sel", lambda: fake)
    return fake


async def _client() -> TestClient:
    from personalclaw.dashboard.handlers.model_rates import register_model_rates_routes

    app = web.Application()
    register_model_rates_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _stored(home) -> dict:
    """Your prices as ``config.json`` holds them, without the day each was set."""
    overrides = (
        json.loads((home / "config.json").read_text(encoding="utf-8"))
        .get("model_prices", {})
        .get("overrides", {})
    )
    return {k: {f: v for f, v in row.items() if f != "recorded"} for k, row in overrides.items()}


@pytest.mark.asyncio
async def test_a_bound_model_nothing_prices_is_listed_as_unpriced(home):
    c = await _client()
    try:
        body = await (await c.get("/api/models/rates")).json()
    finally:
        await c.close()

    assert body["rates"] == [] and body["unreadable"] == ""
    # Each model a use is bound to, once; a ref that names no model has none to price.
    assert [m["ref"] for m in body["models"]] == [_REF]
    assert (body["models"][0]["priced"], body["models"][0]["source"]) == (False, "")


@pytest.mark.asyncio
async def test_a_price_set_here_is_stored_listed_and_charged(home, sel):
    rate = {
        "key": _REF,
        "in_per_mtok": 3.0,
        "out_per_mtok": 15.0,
        "cache_read_per_mtok": 0.3,
        "cache_write_per_mtok": 3.75,
    }
    c = await _client()
    try:
        resp = await c.put("/api/models/rates", json=rate)
        body = await resp.json()
    finally:
        await c.close()

    assert resp.status == 200
    # Every field the file holds is one this route sets: nothing is left for a hand edit.
    assert _stored(home) == {
        _REF: {k: v for k, v in rate.items() if k != "key"},
    }
    [listed] = body["rates"]
    assert {k: v for k, v in listed.items() if k != "recorded"} == {**rate, "unit": "token"}
    assert listed["recorded"], "the day it was set"
    [model] = body["models"]
    assert (model["priced"], model["source"], model["in_per_mtok"]) == (True, "overlay", 3.0)
    # The price the next call is charged at: the rate table reads it on the very next lookup.
    charged = rate_for("acme-cloud", "acme-large")
    assert charged is not None and (charged.in_per_mtok, charged.source) == (3.0, "overlay")
    assert [row["operation"] for row in sel.rows] == ["model_rates.set"]


@pytest.mark.asyncio
async def test_a_rate_without_cache_rates_bills_cached_tokens_as_input(home):
    """The row stored is the row sent: setting a key again without cache rates drops them."""
    c = await _client()
    try:
        await c.put(
            "/api/models/rates",
            json={"key": _REF, "in_per_mtok": 3, "out_per_mtok": 15, "cache_read_per_mtok": 0.3},
        )
        await c.put("/api/models/rates", json={"key": _REF, "in_per_mtok": 2, "out_per_mtok": 8})
    finally:
        await c.close()

    assert _stored(home) == {_REF: {"in_per_mtok": 2.0, "out_per_mtok": 8.0}}


@pytest.mark.asyncio
async def test_a_price_set_for_a_pattern_is_kept_beside_the_others(home):
    c = await _client()
    try:
        await c.put("/api/models/rates", json={"key": _REF, "in_per_mtok": 3, "out_per_mtok": 15})
        await c.put(
            "/api/models/rates",
            json={"key": "anthropic:claude-*", "in_per_mtok": 1, "out_per_mtok": 5},
        )
        body = await (await c.get("/api/models/rates")).json()
    finally:
        await c.close()

    assert [row["key"] for row in body["rates"]] == ["acme-cloud:acme-large", "anthropic:claude-*"]


@pytest.mark.parametrize(
    ("rate", "said"),
    [
        ({"in_per_mtok": 3, "out_per_mtok": 15}, "Name the model"),
        ({"key": "  ", "in_per_mtok": 3, "out_per_mtok": 15}, "Name the model"),
        ({"key": "acme large", "in_per_mtok": 3, "out_per_mtok": 15}, "no spaces"),
        ({"key": "x" * 201, "in_per_mtok": 3, "out_per_mtok": 15}, "at most 200"),
        ({"key": _REF, "in_per_mtok": 3}, "output rate"),
        ({"key": _REF, "in_per_mtok": -1, "out_per_mtok": 15}, "zero or more"),
        ({"key": _REF, "in_per_mtok": "3", "out_per_mtok": 15}, "a number"),
        ({"key": _REF, "in_per_mtok": True, "out_per_mtok": 15}, "a number"),
        ({"key": _REF, "in_per_mtok": 3, "out_per_mtok": 15, "cache_red_per_mtok": 1}, "no field"),
    ],
)
@pytest.mark.asyncio
async def test_a_rate_that_is_no_price_is_refused_and_nothing_is_written(home, rate, said):
    c = await _client()
    try:
        resp = await c.put("/api/models/rates", json=rate)
        body = await resp.json()
    finally:
        await c.close()

    assert resp.status == 400
    assert said in body["error"]["message"]
    assert _stored(home) == {}


@pytest.mark.asyncio
async def test_a_rate_that_is_not_finite_is_refused(home):
    """JSON as Python reads it carries NaN and Infinity; neither is a price."""
    c = await _client()
    try:
        resp = await c.put(
            "/api/models/rates",
            data=json.dumps({"key": _REF, "in_per_mtok": math.inf, "out_per_mtok": 1}),
            headers={"Content-Type": "application/json"},
        )
    finally:
        await c.close()

    assert resp.status == 400
    assert _stored(home) == {}


@pytest.mark.asyncio
async def test_a_price_is_removed_and_one_never_set_is_not_found(home, sel):
    c = await _client()
    try:
        await c.put("/api/models/rates", json={"key": _REF, "in_per_mtok": 3, "out_per_mtok": 15})
        gone = await c.delete("/api/models/rates", params={"key": _REF})
        body = await gone.json()
        missing = await c.delete("/api/models/rates", params={"key": _REF})
    finally:
        await c.close()

    assert gone.status == 200 and body["rates"] == []
    assert _stored(home) == {}
    assert rate_for("acme-cloud", "acme-large") is None
    assert missing.status == 404
    assert [row["operation"] for row in sel.rows] == ["model_rates.set", "model_rates.clear"]


@pytest.mark.asyncio
async def test_a_config_that_cannot_be_read_is_said_and_never_overwritten(home):
    broken = '{"model_prices": {"overrides": {"acme-cloud:acme-large": {"in_per_mtok": 3,'
    (home / "config.json").write_text(broken, encoding="utf-8")
    c = await _client()
    try:
        view = await (await c.get("/api/models/rates")).json()
        resp = await c.put(
            "/api/models/rates", json={"key": _REF, "in_per_mtok": 1, "out_per_mtok": 1}
        )
        refused = await resp.json()
    finally:
        await c.close()

    assert "config.json is not valid JSON" in view["unreadable"]
    assert resp.status == 409
    assert refused["error"]["code"] == "model_rates_unreadable"
    assert (home / "config.json").read_text(encoding="utf-8") == broken


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_the_prices_are_the_owners(method):
    """A price is what the dollar caps count a call at: an app that set one could make a model
    read free to them."""
    assert isinstance(route_authz(method, "/api/models/rates"), OwnerOnly)
