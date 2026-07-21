"""Branded/generic provider apps (sdk.provider_helpers) — the shared wiring behind
the openai-/anthropic-compatible provider apps and every branded preset.

Guards two bugs found during the provider-integrity validation sweep:
  1. ``api_key`` (+ other routing/credential fields) leaked from an entry's
     ``options`` into ``extra_options`` → the SDK's stream()/create() ("unexpected
     keyword argument 'api_key'"). Only genuine model-call params may pass through.
  2. ``test_connection`` for an Anthropic-wire provider reported "No models
     available" for a perfectly good key (the Anthropic protocol has no /v1/models
     endpoint). It must probe a real completion instead.
"""

from __future__ import annotations

import asyncio

import personalclaw.sdk.model  # noqa: F401 — ensure package import order
from personalclaw.llm.capabilities import Capability
from personalclaw.llm.registry import ProviderEntry
from personalclaw.sdk.provider_helpers import (
    BrandedCatalog,
    BrandedProviderSpec,
    register_branded_app,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _spec(protocol: str = "anthropic") -> BrandedProviderSpec:
    return BrandedProviderSpec(
        type=f"test_{protocol}",
        protocol=protocol,
        default_base_url="https://example.invalid",
        api_key_env="",
        default_model="test-model",
        capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
    )


def test_factory_strips_credential_and_routing_fields_from_extra_options():
    spec = _spec("anthropic")
    factory, _, _ = register_branded_app(spec)
    entry = ProviderEntry(
        name="T",
        type=spec.type,
        model="test-model",
        options={
            "api_key": "secret",
            "endpoint": "https://x",
            "base_url": "https://x",
            "model": "test-model",
            "temperature": 0.5,
            "top_p": 0.9,
        },
    )
    prov = factory(entry=entry)
    extra = getattr(prov, "_extra_options", {})
    # Credential/routing fields must NOT reach the SDK call kwargs.
    assert "api_key" not in extra and "endpoint" not in extra and "base_url" not in extra
    assert "model" not in extra
    # Genuine model-call params survive.
    assert extra.get("temperature") == 0.5 and extra.get("top_p") == 0.9


def test_factory_uses_per_instance_key_over_env(monkeypatch):
    """A config entry's own options.api_key MUST win over the spec's api_key_env —
    otherwise a ZAI/Alibaba instance sends a global ANTHROPIC_API_KEY/OPENAI_API_KEY
    meant for a DIFFERENT provider → 'token expired or incorrect' 401."""
    spec = BrandedProviderSpec(
        type="test_perinstance",
        protocol="anthropic",
        default_base_url="https://x",
        api_key_env="SOME_GLOBAL_KEY",
        default_model="m",
        capabilities=frozenset({Capability.CHAT}),
    )
    factory, _, _ = register_branded_app(spec)
    monkeypatch.setenv("SOME_GLOBAL_KEY", "GLOBAL-WRONG-KEY")
    entry = ProviderEntry(
        name="ZAIlike",
        type=spec.type,
        model="m",
        options={"api_key": "PER-INSTANCE-RIGHT-KEY", "endpoint": "https://z"},
    )
    prov = factory(entry=entry)
    # the provider must carry the per-instance key, NOT the env key
    assert prov._client.api_key == "PER-INSTANCE-RIGHT-KEY"  # noqa: SLF001


def test_factory_falls_back_to_env_key_when_no_options_key(monkeypatch):
    spec = BrandedProviderSpec(
        type="test_envfallback",
        protocol="anthropic",
        default_base_url="https://x",
        api_key_env="MY_ENV_KEY",
        default_model="m",
        capabilities=frozenset({Capability.CHAT}),
    )
    factory, _, _ = register_branded_app(spec)
    monkeypatch.setenv("MY_ENV_KEY", "ENV-KEY-USED")
    entry = ProviderEntry(
        name="EnvOnly", type=spec.type, model="m", options={"endpoint": "https://z"}
    )
    prov = factory(entry=entry)
    assert prov._client.api_key == "ENV-KEY-USED"  # noqa: SLF001


def test_anthropic_test_connection_probes_completion(monkeypatch):
    """An Anthropic-wire provider with no models list must NOT report 'No models
    available' — it probes a completion. An auth-looking error → not ok; a
    model/validation error → ok (the key authenticated)."""
    spec = _spec("anthropic")

    # Auth failure → ok False.
    async def _auth_fail(*a, **k):
        raise RuntimeError("401 authentication_error: invalid x-api-key")
        yield  # pragma: no cover

    cat = BrandedCatalog(spec, endpoint="https://x", api_key="k")
    import personalclaw.llm.anthropic as anth

    monkeypatch.setattr(anth.AnthropicProvider, "complete", lambda self, *a, **k: _auth_fail())
    res = _run(cat.test_connection())
    assert res.ok is False and "auth" in (res.detail or "").lower()

    # Model-not-found → ok True (credentials authenticated, model is the issue).
    async def _model_bad(*a, **k):
        raise RuntimeError("not_found_error: model: nope")
        yield  # pragma: no cover

    monkeypatch.setattr(anth.AnthropicProvider, "complete", lambda self, *a, **k: _model_bad())
    res = _run(cat.test_connection())
    assert res.ok is True


def test_anthropic_test_connection_no_key_is_not_ok():
    spec = _spec("anthropic")
    cat = BrandedCatalog(spec, endpoint="https://x", api_key="")
    res = _run(cat.test_connection())
    assert res.ok is False and "key" in (res.detail or "").lower()
