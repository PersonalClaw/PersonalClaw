"""Core's own wires declare the effort levels they send, so a native chat has its effort row.

The composer offers a model an effort only for the levels it declares (``ModelInfo.efforts``),
and no provider app declared any, so every native chat lost its effort row. Most chat apps run on
core's own wires, ``AnthropicProvider`` for the anthropic protocol and ``OpenAIProvider`` for the
openai one, and which levels those wires send a model is core's knowledge. ``wire_efforts(protocol,
model_id)`` says it, from the tables the wires build their requests from. The branded catalog and
the shared OpenAI-compatible listing declare those levels for a model the app's own records say
nothing about, and so does the row core adds for an instance's configured model when discovery
lists none. An app that declares levels itself (``efforts_of``, a curated row's ``efforts``) is
taken at its word, ``[]`` included.

The differential drives each wire with every level of the native ladder and holds it to the
declaration: a request carries a level as itself exactly when ``wire_efforts`` declares it, and a
level it does not declare goes as a declared one, or not at all.

Second defect, at the OpenAI wire's cause: it read ``o1``, ``o3`` and ``o4`` as substrings, so a
model whose id only contains one (``sao10k/l3-lunaris-8b`` holds ``o1``) was sent
``reasoning_effort``, which a model that does not reason refuses. They are name tokens now. And
``xhigh``, which the wire has no rule for, goes as ``high``, as ``max`` does.

Every call goes to an SDK fake or a recorded listing; nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.llm.catalog import ModelCatalog, ModelInfo
from personalclaw.llm.credentials import Credential
from tests import anthropic_sdk_fake

#: The native effort ladder, lowest first: every level a chat can hold for a native model.
LADDER = ("low", "medium", "high", "xhigh", "max")

#: The thinking budget the Anthropic wire keeps for each level, read back to its level.
_BUDGET_LEVEL = {4_096: "low", 10_240: "medium", 24_576: "high", 40_960: "xhigh", 63_999: "max"}

#: Ids across the Anthropic wire's shapes: adaptive with xhigh, adaptive without it, a thinking
#: budget, and ids it does not recognise, which keep the budget.
ANTHROPIC_IDS = (
    "claude-opus-4-8",
    "claude-opus-4-7",
    "us.anthropic.claude-opus-4-7-v1:0",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-fable-5-1",
    "claude-mythos-5",
    "claude-opus-4-6",
    "claude-sonnet-4.6",
    "claude-haiku-4-5",
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-20250219",
    "glm-5.2",
)

#: Ids across the OpenAI wire's rule: reasoning models in their spellings, and models that only
#: contain a reasoning model's name (the planted false positives), or none.
OPENAI_IDS = (
    "o1",
    "o1-mini",
    "o3",
    "o3-mini-2025-01-31",
    "o4-mini",
    "openai/o3",
    "gpt-5",
    "gpt-5.1-codex",
    "openai/gpt-5-mini",
    "sao10k/l3-lunaris-8b",
    "sao10k/l3.3-euryale-70b",
    "gpt-4o",
    "gpt-4.1-mini",
    "deepseek-r1",
)


def _wire_efforts(protocol: str, model_id: str) -> tuple[str, ...]:
    from personalclaw.sdk.model import wire_efforts

    return wire_efforts(protocol, model_id)


def _run(coro):
    return asyncio.run(coro)


def _cred() -> Credential:
    return Credential(name="x", kind="api_key", secret="fake-key-test", source="env")


# ── the Anthropic wire ───────────────────────────────────────────────────────────────────────


def _anthropic_sends(sdk: Any, model: str, level: str) -> str | None:
    """The level the Anthropic wire's request for ``level`` carries, or ``None`` for none."""
    from personalclaw.llm.anthropic import AnthropicProvider

    # An output room no budget is clamped to, so a budget request carries its own level's.
    provider = AnthropicProvider(model=model, credential=_cred(), max_tokens=128_000)

    async def _drive() -> None:
        async for _ in provider.complete(
            [{"role": "user", "content": "hi"}], reasoning_effort=level
        ):
            pass

    before = len(sdk.calls)
    _run(_drive())
    sent = dict(sdk.calls[before])
    sent.update(sent.pop("extra_body", None) or {})
    thinking = sent.get("thinking")
    if not thinking:
        return None
    if thinking.get("type") == "adaptive":
        return sent["output_config"]["effort"]
    return _BUDGET_LEVEL.get(thinking["budget_tokens"], f"budget {thinking['budget_tokens']}")


@pytest.mark.parametrize("model", ANTHROPIC_IDS)
def test_the_anthropic_wire_sends_a_level_as_itself_exactly_when_it_declares_it(
    monkeypatch, model
) -> None:
    sdk = anthropic_sdk_fake.install(monkeypatch)
    declared = _wire_efforts("anthropic", model)
    assert declared, f"{model}: the wire sends it an effort, so it declares levels"
    for level in LADDER:
        sent = _anthropic_sends(sdk, model, level)
        assert (sent == level) == (level in declared), (model, level, sent, declared)
        assert sent is None or sent in declared, (model, level, sent, declared)


def test_the_anthropic_declarations_name_each_shape() -> None:
    assert _wire_efforts("anthropic", "claude-opus-4-8") == LADDER
    assert _wire_efforts("anthropic", "claude-opus-4-6") == ("low", "medium", "high", "max")
    assert _wire_efforts("anthropic", "claude-haiku-4-5") == LADDER


# ── the OpenAI wire ──────────────────────────────────────────────────────────────────────────


class _Stream:
    def __init__(self) -> None:
        done = types.SimpleNamespace(
            delta=types.SimpleNamespace(content="ok", tool_calls=None), finish_reason="stop"
        )
        self._chunks = iter([types.SimpleNamespace(choices=[done], usage=None)])

    def __aiter__(self) -> "_Stream":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.fixture
def openai_calls(monkeypatch) -> list[dict[str, Any]]:
    """A fake ``openai`` whose chat completions record each request's keywords."""
    calls: list[dict[str, Any]] = []

    class _Completions:
        async def create(self, **kwargs: Any) -> _Stream:
            calls.append(kwargs)
            return _Stream()

    class AsyncOpenAI:
        def __init__(self, *, api_key: str, base_url: str | None = None, http_client=None):
            self.chat = types.SimpleNamespace(completions=_Completions())

        async def close(self) -> None:
            return None

    fake = types.ModuleType("openai")
    fake.AsyncOpenAI = AsyncOpenAI  # type: ignore[attr-defined]
    fake.OpenAIError = type("OpenAIError", (Exception,), {})  # type: ignore[attr-defined]
    fake.BadRequestError = type("BadRequestError", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake)
    return calls


def _openai_sends(calls: list[dict[str, Any]], model: str, level: str) -> str | None:
    """The ``reasoning_effort`` the OpenAI wire's request for ``level`` carries, or ``None``."""
    from personalclaw.llm.openai import OpenAIProvider

    provider = OpenAIProvider(model=model, credential=_cred())

    async def _drive() -> None:
        async for _ in provider.complete(
            [{"role": "user", "content": "hi"}], reasoning_effort=level
        ):
            pass

    before = len(calls)
    _run(_drive())
    return calls[before].get("reasoning_effort")


@pytest.mark.parametrize("model", OPENAI_IDS)
def test_the_openai_wire_sends_a_level_as_itself_exactly_when_it_declares_it(
    openai_calls, model
) -> None:
    declared = _wire_efforts("openai", model)
    for level in LADDER:
        sent = _openai_sends(openai_calls, model, level)
        assert (sent == level) == (level in declared), (model, level, sent, declared)
        assert sent is None or sent in declared, (model, level, sent, declared)


def test_the_openai_declarations_name_the_reasoning_models_only() -> None:
    for model in ("o1", "o3-mini-2025-01-31", "openai/o3", "gpt-5", "openai/gpt-5-mini"):
        assert _wire_efforts("openai", model) == ("low", "medium", "high"), model
    for model in ("sao10k/l3-lunaris-8b", "gpt-4o", "deepseek-r1"):
        assert _wire_efforts("openai", model) == (), model


@pytest.mark.parametrize("model", ["sao10k/l3-lunaris-8b", "sao10k/l3.3-euryale-70b"])
def test_a_model_that_only_contains_a_reasoning_models_name_is_sent_no_effort(
    openai_calls, model
) -> None:
    """The regression: ``sao10k`` holds ``o1``, and the wire sent these ``reasoning_effort``."""
    assert _openai_sends(openai_calls, model, "high") is None


def test_xhigh_reaches_a_reasoning_model_as_high_as_max_does(openai_calls) -> None:
    assert _openai_sends(openai_calls, "o3", "xhigh") == "high"
    assert _openai_sends(openai_calls, "o3", "max") == "high"


def test_a_protocol_core_has_no_wire_for_declares_nothing() -> None:
    assert _wire_efforts("bedrock", "claude-opus-4-8") == ()
    assert _wire_efforts("anthropic", "") == ()
    assert _wire_efforts("openai", "  ") == ()


# ── the listings declare the wire's levels ───────────────────────────────────────────────────


class _Answer:
    def __init__(self, payload: Any) -> None:
        self.status = 200
        self.text = json.dumps(payload)


@pytest.fixture
def listing(monkeypatch):
    """Serve one recorded ``/models`` answer to the shared listing; returns a setter."""
    served: dict[str, Any] = {"payload": {"object": "list", "data": []}}

    async def _fetch(url, *, policy=None, method="GET", headers=None, data=None):
        return _Answer(served["payload"])

    monkeypatch.setattr("personalclaw.sdk.net.fetch", _fetch)

    def _serve(payload: Any) -> None:
        served["payload"] = payload

    return _serve


_OPENAI_RECORDS = {
    "object": "list",
    "data": [
        {"id": "o3-mini", "object": "model"},
        {"id": "gpt-4o", "object": "model"},
        {"id": "sao10k/l3-lunaris-8b", "object": "model"},
    ],
}

_OPENAI_WIRE = {"o3-mini": ["low", "medium", "high"], "gpt-4o": [], "sao10k/l3-lunaris-8b": []}


def test_the_shared_listing_declares_the_openai_wires_levels(listing) -> None:
    from personalclaw.sdk.model import openai_compatible_list_models

    listing(_OPENAI_RECORDS)
    rows = _run(openai_compatible_list_models("https://api.acme.example/v1", "fake-key"))
    assert {r.id: r.efforts for r in rows} == _OPENAI_WIRE


def test_an_apps_own_reading_of_its_records_wins_even_when_it_finds_none(listing) -> None:
    from personalclaw.sdk.model import openai_compatible_list_models

    listing(_OPENAI_RECORDS)
    rows = _run(
        openai_compatible_list_models(
            "https://api.acme.example/v1", "fake-key", efforts_of=lambda record: []
        )
    )
    assert {r.id: r.efforts for r in rows} == {m: [] for m in _OPENAI_WIRE}


def _branded(**fields: Any):
    from personalclaw.sdk.model import BrandedProviderSpec
    from personalclaw.sdk.provider_helpers import BrandedCatalog

    default_model = fields.pop("default_model", "")
    spec = BrandedProviderSpec(
        type="acme-wire", default_base_url="https://api.acme.example/v1", **fields
    )
    return BrandedCatalog(spec, api_key="fake-key", default_model=default_model)


def test_a_branded_openai_app_declares_its_wires_levels(listing) -> None:
    listing(_OPENAI_RECORDS)
    rows = _run(_branded().list_models())
    assert {r.id: r.efforts for r in rows} == _OPENAI_WIRE


def test_a_curated_row_that_names_no_levels_takes_its_wires(listing) -> None:
    """A wire with no models route lists the curated rows: one naming levels keeps its own, one
    naming ``[]`` declares none, and one that names nothing declares what its wire sends."""
    catalog = _branded(
        protocol="anthropic",
        fallback_models=(
            {"id": "claude-opus-4-8", "capabilities": ["chat"]},
            {"id": "claude-opus-4-6", "capabilities": ["chat"], "efforts": ["low", "high"]},
            {"id": "claude-haiku-4-5", "capabilities": ["chat"], "efforts": []},
        ),
    )
    rows = _run(catalog.list_models())
    assert {r.id: r.efforts for r in rows} == {
        "claude-opus-4-8": list(LADDER),
        "claude-opus-4-6": ["low", "high"],
        "claude-haiku-4-5": [],
    }


def test_the_configured_model_core_adds_declares_its_wires_levels(listing) -> None:
    """Discovery lists none, so the instance's own model is the row offered, with its levels."""
    rows = _run(_branded(protocol="anthropic", default_model="claude-sonnet-4-6").list_models())
    assert {r.id: r.efforts for r in rows} == {
        "claude-sonnet-4-6": ["low", "medium", "high", "max"]
    }

    listing({"object": "list", "data": []})
    rows = _run(_branded(default_model="gpt-5-mini").list_models())
    assert {r.id: r.efforts for r in rows} == {"gpt-5-mini": ["low", "medium", "high"]}


# ── the composer's list, where core adds the configured model ────────────────────────────────


class _Empty(ModelCatalog):
    async def list_models(self) -> list[ModelInfo]:
        return []


def test_the_chat_list_gives_the_configured_model_its_wires_levels(monkeypatch) -> None:
    from personalclaw.dashboard.handlers import model_registry as mr
    from personalclaw.llm import branded_specs
    from personalclaw.sdk.model import BrandedProviderSpec

    monkeypatch.setitem(
        branded_specs._REGISTERED_SPECS,
        "acme-claude-wire",
        BrandedProviderSpec(type="acme-claude-wire", protocol="anthropic"),
    )
    monkeypatch.setattr(
        mr,
        "_get_providers_from_config",
        lambda: [
            {"name": "Claude", "type": "acme-claude-wire", "model": "claude-opus-4-8"},
            # An app on a wire of its own: core does not know what that wire sends.
            {"name": "Own", "type": "acme-own-wire", "model": "claude-opus-4-8"},
        ],
    )
    monkeypatch.setattr(mr, "_catalog_for_config_provider", lambda p: _Empty())
    monkeypatch.setattr(mr, "load_active_models", lambda: {})

    resp = _run(mr.api_models_chat(make_mocked_request("GET", "/api/models/chat")))
    rows = {r["provider"]: r for r in json.loads(resp.text)}
    assert rows["Claude"]["efforts"] == list(LADDER)
    assert "efforts" not in rows["Own"]
