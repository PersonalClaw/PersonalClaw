"""One rule says which models run on this machine, and it knows where an entry sends by default.

Three places ask whether a model runs here: the rate table (a model here is a known $0), local-first
routing (which tries one first) and the spend guard's outbound scan (relaxed for a prompt that never
leaves the machine). The rate table and routing read the entry's own endpoint options, and the guard
read the provider object it built. So a hand-written entry that named no endpoint and relied on its
type's default (Ollama's localhost) was unpriced and ordered as a cloud model, while the guard
treated it as local. And the bundled offline model, which runs inside the gateway and names no
endpoint at all, was unpriced, and its prompts were redacted for a trip they never take.

All three ask ``llm.registry.served_on_this_machine`` now. It counts a type that runs its model in
the gateway's own process (``ProviderCapability.in_process``) as local, and a type that runs the
models it serves where its endpoint is (``ProviderCapability.hosts_model``) as local when the
entry's endpoints, else the default its type declares (``ProviderCapability.default_endpoint``),
are on this machine. An entry of a type that passes requests on is not a model here wherever it
sends.
"""

from __future__ import annotations

import pytest

import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
from personalclaw.guardrails.failure import SecretLeakBlocked
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.registry import (
    SCRIPTED_PROVIDER_CAPABILITY,
    ProviderEntry,
    get_default_registry,
)
from personalclaw.routing import rates as rates_mod
from personalclaw.routing.policy import is_local_ref
from personalclaw.routing.rates import rate_for

#: A credential the outbound scan recognises by its shape (the documented example key id).
_KEY = "AKIAIOSFODNN7EXAMPLE"
_PROMPT = f"deploy the release with {_KEY} tonight"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """The guard audits every attempt and a block writes a security event: keep both here."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


def _native(app: str):
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

    return load_bundle_module(NATIVE_DIR / app, app, "provider")


def _configured(name: str, provider_type: str, **options: object) -> None:
    """Register the provider entry *name*, as the config sync does; conftest drops it after."""
    get_default_registry().register_entry(
        ProviderEntry(name=name, type=provider_type, model="", options=dict(options))
    )


class _Model:
    """Whatever provider an entry builds: the guard is asked about the entry, not this."""

    supports_tools = False


def _scanned(entry: str, model: str) -> str:
    """What the spend guard sends for *entry* under the setting ``block``."""
    guard = wrap_model_call_guard(
        _Model(), use_case="background", provider_name=entry, model=model, scan_mode="block"
    )
    return guard._prescan(_PROMPT)


def _the_three_answers(entry: str, model: str) -> tuple[str, bool, bool]:
    """(the rate tier, local-first routing's answer, whether the guard left the prompt alone)."""
    rate = rate_for(entry, model)
    try:
        unscanned = _scanned(entry, model) == _PROMPT
    except SecretLeakBlocked:
        unscanned = False
    return (
        rate.source if rate is not None else "unpriced",
        is_local_ref(f"{entry}:{model}"),
        unscanned,
    )


def test_an_entry_relying_on_its_types_local_default_is_local_to_all_three():
    """A hand-written Ollama entry naming no endpoint sends to the app's localhost default."""
    _native("ollama-models")
    _configured("my-ollama", "ollama")

    assert _the_three_answers("my-ollama", "llama3.1") == ("local", True, True)


def test_an_entry_naming_an_endpoint_elsewhere_is_elsewhere_to_all_three():
    """The control: the default applies only where the entry names nothing."""
    _native("ollama-models")
    _configured("far-ollama", "ollama", endpoint="http://192.0.2.10:11434")

    assert _the_three_answers("far-ollama", "llama3.1") == ("unpriced", False, False)


def test_a_branded_entry_naming_no_endpoint_sends_where_its_app_says_and_is_no_local_model():
    """A branded app passes each request on to the service its endpoint names, so even a default
    on this machine is where the request goes, not where the model runs: a proxy there can answer
    for a paid cloud API. Unpriced, ordered as remote, and scanned as the setting says."""
    from personalclaw.llm.registry import sends_to_this_machine
    from personalclaw.sdk.provider_helpers import BrandedProviderSpec, register_branded_app

    register_branded_app(
        BrandedProviderSpec(type="acme-desk-models", default_base_url="http://127.0.0.1:1234/v1")
    )
    _configured("desk", "acme-desk-models")

    assert sends_to_this_machine("desk") is True
    assert _the_three_answers("desk", "acme-small") == ("unpriced", False, False)


def test_the_in_process_offline_model_is_local_to_all_three():
    """The bundled offline model runs its weight inside the gateway: a known $0 whose prompts
    never leave the machine, however little its entry names."""
    module = _native("bundled-chat")
    module.register()
    _configured("offline", module.PROVIDER_TYPE)

    assert _the_three_answers("offline", "smollm2-135m") == ("local", True, True)


def test_the_offline_replay_fixture_declares_it_runs_in_process():
    """It replays a file inside the gateway and sends nothing anywhere."""
    assert getattr(SCRIPTED_PROVIDER_CAPABILITY, "in_process", None) is True
