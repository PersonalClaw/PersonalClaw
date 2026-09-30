"""An endpoint on this machine is not proof that the model behind it runs here.

A model was taken as served on this machine whenever its entry's endpoint was, so a cloud model
reached through an OpenAI-compatible instance at a loopback address was priced as local: 110M
tokens counted $0, the daily dollar cap never saw them, Usage read "ran locally at $0", and Model
prices had no row for the model, so nothing pointed at the gap. A local proxy can front a paid
cloud API, and the same misreading relaxed the outbound scan for prompts that did leave the
machine, and ordered the proxy first as a free local model.

Only a type that runs its models where its endpoint is (a model server such as Ollama declares so,
``ProviderCapability.hosts_model``) is served on this machine at a loopback address. An entry of
any other type there is priced by its model's known id, else has no price: Model prices and Usage
show it, a daily dollar cap refuses its calls until the owner sets its price, and a price of $0
declares it free.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw.guardrails.budgets import Budget, SpendMeter
from personalclaw.guardrails.failure import BudgetExceededError, SecretLeakBlocked
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, get_default_registry, served_on_this_machine
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.policy import is_local_ref
from personalclaw.routing.rates import ModelRate, rate_for, set_rate
from personalclaw.sdk.provider_helpers import BrandedProviderSpec, register_branded_app

#: An OpenAI-compatible instance on this machine, in front of a cloud service.
_RELAY_URL = "http://127.0.0.1:18907/v1"
_KEY = "AKIAIOSFODNN7EXAMPLE"
_PROMPT = f"deploy the release with {_KEY} tonight"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


@pytest.fixture
def relay() -> str:
    """The OpenAI-compatible app's type, and an instance of it at a loopback address."""
    register_branded_app(BrandedProviderSpec(type="openai_compatible", protocol="openai"))
    get_default_registry().register_entry(
        ProviderEntry(
            name="relay", type="openai_compatible", model="", options={"endpoint": _RELAY_URL}
        )
    )
    return "relay"


def _ollama_here() -> str:
    """The bundled Ollama app, and an instance of it on this machine."""
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

    load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    get_default_registry().register_entry(
        ProviderEntry(
            name="here", type="ollama", model="", options={"endpoint": "http://127.0.0.1:11434"}
        )
    )
    return "here"


def _prescan(entry: str, model: str, mode: str = "block") -> str:
    class _Model:
        supports_tools = False

    guard = wrap_model_call_guard(
        _Model(), use_case="background", provider_name=entry, model=model, scan_mode=mode
    )
    return guard._prescan(_PROMPT)


def test_a_known_cloud_model_through_a_loopback_relay_is_priced_by_its_id(relay):
    """🔴 Measured before the fix: ``ModelRate(0.0, 0.0, source="local")``, local-first, and the
    prompt sent unscanned whatever the setting said."""
    rate = rate_for(relay, "gpt-4o-mini")

    assert rate == ModelRate(0.15, 0.6, cache_read_per_mtok=0.075, cache_write_per_mtok=0.0)
    assert rate is not None and rate.source == "builtin"
    assert served_on_this_machine(relay) is False
    assert is_local_ref(f"{relay}:gpt-4o-mini") is False
    with pytest.raises(SecretLeakBlocked):
        _prescan(relay, "gpt-4o-mini")


def test_a_model_the_relay_serves_that_nothing_prices_has_no_price(relay):
    """🔴 Measured before the fix: a known $0."""
    assert rate_for(relay, "house-blend") is None


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
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=10_000_000, output_tokens=1_000_000)


async def _call(meter: SpendMeter, sent: list[str], entry: str, model: str) -> str:
    guard = wrap_model_call_guard(
        _Answers(sent),
        use_case="background",
        provider_name=entry,
        model=model,
        budget=Budget(max_dollars=4.0),
        meter=meter,
    )
    return "".join(
        [e.text async for e in guard.stream("Draft a reply.") if e.kind == EVENT_TEXT_CHUNK]
    )


def test_a_dollar_cap_refuses_a_call_it_could_not_count_and_says_where_to_price_it(tmp_path, relay):
    """🔴 Measured before the fix: the call was sent and charged as $0 against the cap."""
    meter = SpendMeter(config_dir=tmp_path)
    sent: list[str] = []

    with pytest.raises(BudgetExceededError) as refused:
        asyncio.run(_call(meter, sent, relay, "house-blend"))

    assert sent == []
    assert refused.value.sentence() == (
        "relay:house-blend has no price, so the daily dollar budget cannot count what a call to "
        "it would spend: set its price in Settings → Usage → Model prices, or $0 if it costs "
        "nothing."
    )


def test_a_price_of_zero_declares_the_instance_free(tmp_path, relay):
    """The owner's way out: $0 for every model of the instance, and its calls run past the cap."""
    set_rate("relay:*", {"in_per_mtok": 0.0, "out_per_mtok": 0.0})
    meter = SpendMeter(config_dir=tmp_path)
    meter.charge(1_000, 10.5)
    sent: list[str] = []

    assert asyncio.run(_call(meter, sent, relay, "house-blend")) == "draft"
    assert sent == ["Draft a reply."]
    assert meter.day_totals().dollars == pytest.approx(10.5)


def test_an_ollama_on_this_machine_is_still_a_local_model():
    """The control: a model server running its models where its endpoint is stays local, free,
    local-first and scanned as a prompt that never leaves the machine."""
    here = _ollama_here()

    rate = rate_for(here, "gemma4:12b")
    assert rate == ModelRate(0.0, 0.0) and rate is not None and rate.source == "local"
    assert is_local_ref(f"{here}:gemma4:12b") is True
    assert _prescan(here, "gemma4:12b") == _PROMPT


def _model_server_type(name: str, *, hosts_model: bool) -> None:
    get_default_registry().register_type(
        ProviderCapability(
            type=name,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
            hosts_model=hosts_model,
        ),
        lambda **_kw: None,
    )


def test_a_type_that_runs_its_models_where_its_endpoint_is_is_local_only_there():
    _model_server_type("weights-server", hosts_model=True)
    _model_server_type("request-forwarder", hosts_model=False)
    registry = get_default_registry()
    for name, type_, url in (
        ("box-here", "weights-server", "http://localhost:8000/v1"),
        ("box-there", "weights-server", "http://192.0.2.10:8000/v1"),
        ("forwarder-here", "request-forwarder", "http://localhost:4000/v1"),
    ):
        registry.register_entry(
            ProviderEntry(name=name, type=type_, model="", options={"endpoint": url})
        )

    assert served_on_this_machine("box-here") is True
    assert served_on_this_machine("box-there") is False
    assert served_on_this_machine("forwarder-here") is False


def test_model_prices_lists_a_model_that_was_used_though_nothing_is_bound_to_it(
    tmp_path, relay, monkeypatch
):
    """🔴 Measured before the fix: the page listed only the models bound now, so a model that spent
    money and was unbound after had no row, and nothing on the page pointed at it."""
    from datetime import datetime, timezone

    from personalclaw.dashboard.handlers.model_rates import _view
    from personalclaw.usage_ledger import TurnUsage, record_turn

    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: {})
    record_turn(
        TurnUsage(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key="",
            source="cron",
            agent="",
            provider=relay,
            model="house-blend",
            input_tokens=1_000,
            output_tokens=100,
            priced=False,
        )
    )

    view = json.loads(_view().text)

    assert [(m["ref"], m["priced"]) for m in view["models"]] == [("relay:house-blend", False)]
