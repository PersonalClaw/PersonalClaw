"""A model call is scanned before it leaves the machine, whatever kind of provider makes it.

The outbound scan at the model-call guard (``guardrails.model_call``) forces ``warn`` for a
provider it counts as local, because that provider's text never leaves the machine. It counted
every Ollama provider as local by its TYPE NAME, so an Ollama server on another machine got none
of the scan the setting asked for: a credential in a one-shot prompt reached it as written. Its
loopback check was a substring test besides, so ``http://localhost.example.test`` and
``http://[2001:db8::1]`` read as local too.

Local now means the entry the call is made for sends to this machine
(``llm.registry.served_on_this_machine``, which pricing and routing ask too): its endpoint's host is
``localhost`` or a loopback address, and nothing else. The bundled Ollama provider itself is built
here, so the rule is held for the class the product ships rather than for a stand-in with a similar
name. ``_prescan`` is the chokepoint every generation path passes before the provider is called, so
nothing here reaches a network.
"""

from __future__ import annotations

import re

import pytest

from personalclaw.guardrails.failure import SecretLeakBlocked
from personalclaw.guardrails.model_call import wrap_model_call_guard
from personalclaw.llm.registry import ProviderEntry, get_default_registry, served_on_this_machine

#: A credential the outbound scan recognises by its shape (the documented example key id).
_KEY = "AKIAIOSFODNN7EXAMPLE"
_PROMPT = f"deploy the release with {_KEY} tonight"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The guard audits every attempt and a block writes a security event: keep both here."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _ollama(endpoint: str):
    """The bundled Ollama provider, pointed at *endpoint*. Building it opens no connection."""
    from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module

    module = load_bundle_module(NATIVE_DIR / "ollama-models", "ollama-models", "provider")
    return module.OllamaProvider(model="qwen3:4b", endpoint=endpoint)


def _entry(option: str, url: str, *, provider_type: str) -> str:
    """Register a configured provider entry that sends to *url*; its name. Conftest drops it."""
    name = "at-" + re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")
    get_default_registry().register_entry(
        ProviderEntry(name=name, type=provider_type, model="", options={option: url})
    )
    return name


def _guard(endpoint: str, scan_mode: str):
    """The guard over the Ollama provider an entry sending to *endpoint* builds."""
    return wrap_model_call_guard(
        _ollama(endpoint),
        use_case="background",
        provider_name=_entry("endpoint", endpoint, provider_type="ollama"),
        model="qwen3:4b",
        scan_mode=scan_mode,
    )


_ELSEWHERE = [
    "http://gpu.example.test:11434",
    "http://192.0.2.10:11434",
    "http://[2001:db8::1]:11434",
]


@pytest.mark.parametrize("endpoint", _ELSEWHERE)
def test_an_ollama_on_another_machine_is_blocked_when_the_setting_says_block(home, endpoint):
    guard = _guard(endpoint, "block")

    with pytest.raises(SecretLeakBlocked):
        guard._prescan(_PROMPT)


@pytest.mark.parametrize("endpoint", _ELSEWHERE)
def test_an_ollama_on_another_machine_is_redacted_when_the_setting_says_redact(home, endpoint):
    guard = _guard(endpoint, "redact")

    sent = guard._prescan(_PROMPT)

    assert _KEY not in sent, f"the credential went to {endpoint} as written: {sent!r}"


@pytest.mark.parametrize(
    "endpoint", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
)
def test_an_ollama_on_this_machine_keeps_warn(home, endpoint):
    """The control: the local rule still holds where it is true, so the block above is caused by
    the endpoint and not by a guard that now scans everything."""
    guard = _guard(endpoint, "block")

    assert guard._prescan(_PROMPT) == _PROMPT


def test_a_provider_no_configured_entry_names_gets_the_settings_scan(home):
    """Non-vacuity for the control above: the guard asks where the ENTRY sends, and one it cannot
    find is not local, whatever the provider object it wraps says about itself."""
    guard = wrap_model_call_guard(
        _ollama("http://localhost:11434"),
        use_case="background",
        provider_name="nobody-configured-this",
        model="qwen3:4b",
        scan_mode="block",
    )

    with pytest.raises(SecretLeakBlocked):
        guard._prescan(_PROMPT)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost.example.test:8000/v1",
        "http://notlocalhost.example.test:8000/v1",
        "https://api.example.com/v1?via=127.0.0.1",
        "http://10.0.0.0:8000/v1",
        "http://[2001:db8::1]:8000/v1",
    ],
)
def test_an_endpoint_that_only_contains_a_local_spelling_is_not_local(base_url):
    entry = _entry("base_url", base_url, provider_type="openai_compatible")

    assert served_on_this_machine(entry) is False


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8000/v1",
        "http://127.0.0.1:1234/v1",
        "http://[::1]:8000/v1",
        # A connection to the unspecified address reaches this machine.
        "http://0.0.0.0:8000/v1",
        "http://[::]:8000/v1",
    ],
)
def test_a_loopback_endpoint_is_local(base_url):
    entry = _entry("base_url", base_url, provider_type="openai_compatible")

    assert served_on_this_machine(entry) is True


def test_an_entry_that_names_no_endpoint_and_has_no_default_is_not_local():
    """No endpoint named and none its type declares: nothing says where it sends, so it gets the
    setting's scan, the conservative reading."""
    get_default_registry().register_entry(
        ProviderEntry(name="nowhere-named", type="openai_compatible", model="", options={})
    )

    assert served_on_this_machine("nowhere-named") is False
