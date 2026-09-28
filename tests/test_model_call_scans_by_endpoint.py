"""A model call is scanned before it leaves the machine, whatever kind of provider makes it.

The outbound scan at the model-call guard (``guardrails.model_call``) forces ``warn`` for a
provider it counts as local, because that provider's text never leaves the machine. It counted
every Ollama provider as local by its TYPE NAME, so an Ollama server on another machine got none
of the scan the setting asked for: a credential in a one-shot prompt reached it as written. Its
loopback check was a substring test besides, so ``http://localhost.example.test`` and
``http://[2001:db8::1]`` read as local too.

Local now means the endpoint's host is ``localhost`` or a loopback address, and nothing else. The
bundled Ollama provider itself is built here, so the rule is held for the class the product ships
rather than for a stand-in with a similar name. ``_prescan`` is the chokepoint every generation
path passes before the provider is called, so nothing here reaches a network.
"""

from __future__ import annotations

import pytest

from personalclaw.guardrails.failure import SecretLeakBlocked
from personalclaw.guardrails.model_call import (
    _is_local_provider,
    _provider_endpoint,
    wrap_model_call_guard,
)

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


def _guard(provider, scan_mode: str):
    return wrap_model_call_guard(
        provider,
        use_case="background",
        provider_name="ollama",
        model="qwen3:4b",
        scan_mode=scan_mode,
    )


class _HttpProvider:
    """An OpenAI-compatible client's shape: the URL it sends to, in ``_base_url``."""

    supports_tools = False

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url


_ELSEWHERE = [
    "http://gpu.example.test:11434",
    "http://192.0.2.10:11434",
    "http://[2001:db8::1]:11434",
]


@pytest.mark.parametrize("endpoint", _ELSEWHERE)
def test_an_ollama_on_another_machine_is_blocked_when_the_setting_says_block(home, endpoint):
    guard = _guard(_ollama(endpoint), "block")

    with pytest.raises(SecretLeakBlocked):
        guard._prescan(_PROMPT)


@pytest.mark.parametrize("endpoint", _ELSEWHERE)
def test_an_ollama_on_another_machine_is_redacted_when_the_setting_says_redact(home, endpoint):
    guard = _guard(_ollama(endpoint), "redact")

    sent = guard._prescan(_PROMPT)

    assert _KEY not in sent, f"the credential went to {endpoint} as written: {sent!r}"


@pytest.mark.parametrize(
    "endpoint", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
)
def test_an_ollama_on_this_machine_keeps_warn(home, endpoint):
    """The control: the local rule still holds where it is true, so the block above is caused by
    the endpoint and not by a guard that now scans everything."""
    guard = _guard(_ollama(endpoint), "block")

    assert guard._prescan(_PROMPT) == _PROMPT


def test_the_bundled_provider_keeps_its_endpoint_where_the_guard_reads_it():
    """Non-vacuity: the loopback control above passes only because the guard FOUND the endpoint.
    If it read nothing, every Ollama would be remote and the control would still be green."""
    assert _provider_endpoint(_ollama("http://localhost:11434")) == "http://localhost:11434"


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
    assert _is_local_provider(_HttpProvider(base_url)) is False


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
    assert _is_local_provider(_HttpProvider(base_url)) is True


def test_a_provider_that_names_no_endpoint_is_not_local():
    """A model run inside the gateway or behind a CLI names no endpoint: it gets the setting's
    scan, the conservative reading of "nothing says where this goes"."""

    class _NoEndpoint:
        supports_tools = False

    assert _is_local_provider(_NoEndpoint()) is False
