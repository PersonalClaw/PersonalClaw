"""A model offers a reasoning effort only when its provider's record says it takes one.

The composer listed Low, Medium, High and Max for every native model, so the control could be
set on a model with no effort setting and change nothing. A model now declares its levels the way
it declares its jobs: through its provider's record of it. A vendor's listing is read by the app's
``efforts_of`` (beside ``capabilities_of``), and a curated record names its own ``efforts``. The
levels reach ``ModelInfo.efforts`` and the composer's one model list (``GET /api/models/chat``),
and a model whose record names none carries none, so the composer offers it no effort.

The listings below are recorded shapes, served by fakes; no vendor is called.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import model_registry as mr
from personalclaw.llm import registry as llm_registry
from personalclaw.llm.catalog import ModelCatalog, ModelInfo, openai_compatible_discover_models


def _run(coro):
    return asyncio.run(coro)


class _Answer:
    def __init__(self, payload: Any) -> None:
        self.status = 200
        self.text = json.dumps(payload)


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch):
    """Serve one recorded ``/models`` answer to the shared listing; returns a setter."""
    served: dict[str, Any] = {}

    async def _fetch(url, *, policy=None, method="GET", headers=None, data=None):
        return _Answer(served["payload"])

    monkeypatch.setattr("personalclaw.sdk.net.fetch", _fetch)

    def _serve(payload: Any) -> None:
        served["payload"] = payload

    return _serve


#: A recorded OpenAI-shaped answer whose vendor says, on each record, how deep the model reasons.
_VENDOR_RECORDS = {
    "object": "list",
    "data": [
        {"id": "acme-thinker", "object": "model", "reasoning": ["low", "medium", "high"]},
        {"id": "acme-quick", "object": "model"},
    ],
}


def _efforts_of(record: dict[str, Any]) -> list[str]:
    return list(record.get("reasoning") or [])


# ── The record says it ─────────────────────────────────────────────────────────────────────────


def test_the_vendor_record_reaches_the_app_and_decides_the_levels(listing) -> None:
    listing(_VENDOR_RECORDS)
    rows = _run(
        openai_compatible_discover_models(
            "https://api.acme.example/v1", "fake-key", efforts_of=_efforts_of
        )
    )
    assert {r.id: r.efforts for r in rows} == {
        "acme-thinker": ["low", "medium", "high"],
        "acme-quick": [],
    }


def test_with_no_reading_of_its_own_a_record_declares_no_levels(listing) -> None:
    """An id is no declaration: a listing the app does not read for levels offers none."""
    listing(_VENDOR_RECORDS)
    rows = _run(openai_compatible_discover_models("https://api.acme.example/v1", "fake-key"))
    assert {r.id: r.efforts for r in rows} == {"acme-thinker": [], "acme-quick": []}


def test_a_reading_that_raises_costs_that_model_its_levels_only(listing) -> None:
    listing(_VENDOR_RECORDS)

    def _brittle(record: dict[str, Any]) -> list[str]:
        if record["id"] == "acme-quick":
            raise KeyError("reasoning")
        return list(record["reasoning"])

    rows = _run(
        openai_compatible_discover_models(
            "https://api.acme.example/v1", "fake-key", efforts_of=_brittle
        )
    )
    assert {r.id: r.efforts for r in rows} == {
        "acme-thinker": ["low", "medium", "high"],
        "acme-quick": [],
    }


def test_the_levels_are_on_the_wire_only_when_there_are_some() -> None:
    assert ModelInfo(id="a", name="a", efforts=["low", "high"]).to_dict()["efforts"] == [
        "low",
        "high",
    ]
    assert "efforts" not in ModelInfo(id="b", name="b").to_dict()


def test_a_branded_app_names_how_its_vendor_records_read_for_levels(listing) -> None:
    from personalclaw.sdk.model import BrandedProviderSpec, register_branded_app

    listing(_VENDOR_RECORDS)
    spec = BrandedProviderSpec(
        type="acme-effort-vendor", default_base_url="https://api.acme.example/v1"
    )
    with patch.object(llm_registry, "_default_registry", llm_registry.ProviderRegistry()):
        _, _, create_catalog = register_branded_app(spec, efforts_of=_efforts_of)
        rows = _run(create_catalog({"api_key": "fake-key"}).list_models())
    assert {r.id: r.efforts for r in rows} == {
        "acme-thinker": ["low", "medium", "high"],
        "acme-quick": [],
    }


def test_a_curated_record_names_its_own_levels() -> None:
    """A wire with no models route lists the app's curated records, which say their levels."""
    from personalclaw.sdk.model import BrandedProviderSpec, register_branded_app

    spec = BrandedProviderSpec(
        type="acme-curated-vendor",
        protocol="anthropic",
        default_base_url="https://api.acme.example",
        fallback_models=(
            {"id": "acme-deep", "capabilities": ["chat"], "efforts": ["low", "medium", "max"]},
            {"id": "acme-flat", "capabilities": ["chat"]},
        ),
    )
    with patch.object(llm_registry, "_default_registry", llm_registry.ProviderRegistry()):
        _, _, create_catalog = register_branded_app(spec)
        rows = _run(create_catalog({"api_key": "fake-key"}).list_models())
    assert {r.id: r.efforts for r in rows} == {
        "acme-deep": ["low", "medium", "max"],
        "acme-flat": [],
    }


# ── The composer's one model list carries them ─────────────────────────────────────────────────


class _Catalog(ModelCatalog):
    def __init__(self, rows: list[ModelInfo]) -> None:
        self._rows = rows

    async def list_models(self) -> list[ModelInfo]:
        return list(self._rows)


def _acme_rows() -> list[ModelInfo]:
    return [
        ModelInfo(
            id="acme-thinker", name="Acme Thinker", capabilities=["chat"], efforts=["low", "high"]
        ),
        ModelInfo(id="acme-quick", name="Acme Quick", capabilities=["chat"]),
    ]


def _chat_list() -> list[dict[str, Any]]:
    resp = _run(mr.api_models_chat(make_mocked_request("GET", "/api/models/chat")))
    return json.loads(resp.text)


@pytest.fixture
def acme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mr,
        "_get_providers_from_config",
        lambda: [{"name": "Acme", "type": "acme-effort-vendor", "options": {}}],
    )
    catalog = _Catalog(_acme_rows())
    monkeypatch.setattr(mr, "_catalog_for_config_provider", lambda p: catalog)


def test_a_bound_chat_model_carries_the_levels_its_provider_lists(monkeypatch, acme) -> None:
    monkeypatch.setattr(
        mr,
        "load_active_models",
        lambda: {"chat": ["Acme:acme-thinker", "Acme:acme-quick", "Acme:not-listed"]},
    )
    rows = {r["name"]: r for r in _chat_list()}
    assert rows["Acme:acme-thinker"]["efforts"] == ["low", "high"]
    # A model whose record names no levels, or that no listing describes, declares none.
    assert "efforts" not in rows["Acme:acme-quick"]
    assert "efforts" not in rows["Acme:not-listed"]


def test_a_discovered_chat_model_carries_the_levels_its_provider_lists(monkeypatch, acme) -> None:
    monkeypatch.setattr(mr, "load_active_models", lambda: {})
    rows = {r["model_id"]: r for r in _chat_list()}
    assert rows["acme-thinker"]["efforts"] == ["low", "high"]
    assert "efforts" not in rows["acme-quick"]
