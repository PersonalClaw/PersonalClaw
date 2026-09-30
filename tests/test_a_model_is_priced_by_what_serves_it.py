"""A model's rate is decided by what serves it, never by a name.

Two lookups in the rate table (``routing.rates``) read a name where they meant the thing it names:

* An app's declared rate was found by the provider ENTRY's name, matching the registered types
  spelled inside it (``llm.branded_specs.registered_spec``). An Acme entry the owner named
  ``work`` found no Acme rate and read unpriced, and an OpenAI-compatible entry named
  ``acme-proxy`` was priced at Acme's rate for a model it serves from somewhere else. It is found
  by the entry's type now.
* The shipped price table carried an explicit $0 row per open-weight family (``llama3.1``,
  ``mistral``, …), matched by prefix. So a model of one of those families was free on any
  machine: an Ollama elsewhere serving ``llama3.1`` counted $0 against the daily cap, and so did
  every model of Mistral's billed API (``mistral-large-latest`` starts with ``mistral``). Free is
  the local tier's answer, decided by what serves the entry and where it sends
  (``served_on_this_machine``).
"""

from __future__ import annotations

import pytest

import personalclaw.sdk.model  # noqa: F401 — ensure package import order (sdk.model first)
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.rates import ModelRate, rate_for
from personalclaw.sdk.provider_helpers import BrandedProviderSpec

_ACME = {"acme-large": {"in_per_mtok": 3.0, "out_per_mtok": 15.0}}


@pytest.fixture(autouse=True)
def _clear_rate_caches():
    rates_mod._overlay_cache = None
    yield
    rates_mod._overlay_cache = None


@pytest.fixture
def acme_declares_its_rates(monkeypatch):
    """The Acme provider app is installed, and declares a rate for its model."""
    from personalclaw.llm import branded_specs

    specs = dict(branded_specs._REGISTERED_SPECS)
    specs["acme"] = BrandedProviderSpec(type="acme", pricing=_ACME)
    monkeypatch.setattr(branded_specs, "_REGISTERED_SPECS", specs)


def _configured(name: str, provider_type: str, **options: object) -> None:
    """Register the provider entry *name*, as the config sync does; conftest drops it after."""
    get_default_registry().register_entry(
        ProviderEntry(name=name, type=provider_type, model="", options=dict(options))
    )


def test_an_app_rate_prices_its_instance_whatever_the_owner_named_it(
    tmp_path, acme_declares_its_rates
):
    _configured("work", "acme", base_url="https://models.example.com/v1")

    rate = rate_for("work", "acme-large", home=tmp_path)

    assert rate == ModelRate(3.0, 15.0)
    assert rate is not None and rate.source == "app_default"


def test_a_name_that_spells_an_apps_type_does_not_take_its_rate(tmp_path, acme_declares_its_rates):
    _configured("acme-proxy", "openai_compatible", base_url="https://proxy.example.com/v1")

    assert rate_for("acme-proxy", "acme-large", home=tmp_path) is None


def test_a_name_no_entry_has_takes_no_apps_rate(tmp_path, acme_declares_its_rates):
    """No entry, no type: nothing an app declared can price it."""
    assert rate_for("acme-unconfigured", "acme-large", home=tmp_path) is None


@pytest.mark.parametrize("model", ["llama3.1", "mistral", "qwen2.5-72b-instruct", "phi3"])
def test_an_open_weight_model_is_free_only_on_this_machine(tmp_path, model, ollama_app):
    _configured("here", "ollama", endpoint="http://localhost:11434")
    _configured("there", "ollama", endpoint="http://192.0.2.10:11434")

    here = rate_for("here", model, home=tmp_path)
    assert here == ModelRate(0.0, 0.0)
    assert here is not None and here.source == "local"
    assert rate_for("there", model, home=tmp_path) is None


def test_a_billed_api_model_is_not_free_by_a_family_prefix(tmp_path):
    assert rate_for("mistral-api", "mistral-large-latest", home=tmp_path) is None
