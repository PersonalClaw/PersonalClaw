"""A model that thinks gets room to think and still answer, when nothing set its output cap.

Opus 5.5 always thinks, and Sonnet 5.5, Haiku 5.5 and Fable 5.1 think by default. Their thinking is
spent out of the request's ``max_tokens``, and core's Anthropic wire fell back to 4,096 when neither
the operator nor the call set a cap: a model that reasons at length spent all of it before it wrote
a word or made a call, and the turn ended with no answer. With nothing set, a request to a model
that thinks adaptively now sends 32,000 (``build_kwargs.default_output_cap``), and any other model
still sends 4,096. A configured cap and a per-call budget still win.

Every call goes to ``tests/anthropic_sdk_fake.py``; nothing leaves the machine.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.llm.anthropic import AnthropicProvider
from personalclaw.llm.branded_specs import BrandedProviderSpec
from personalclaw.llm.build_kwargs import (
    DEFAULT_OUTPUT_CAP,
    THINKING_OUTPUT_CAP,
    default_output_cap,
)
from personalclaw.llm.capabilities import Capability
from personalclaw.llm.credentials import Credential
from personalclaw.llm.registry import ProviderEntry
from personalclaw.sdk.provider_helpers import register_branded_app
from tests import anthropic_sdk_fake

_HI = [{"role": "user", "content": "hi"}]
_KEY = Credential(name="x", kind="api_key", secret="fake-key-test", source="env")


@pytest.fixture(autouse=True)
def sdk(monkeypatch):
    return anthropic_sdk_fake.install(monkeypatch)


async def _sent_cap(sdk, provider: AnthropicProvider, **call: Any) -> int:
    before = len(sdk.calls)
    async for _ in provider.complete(_HI, **call):
        pass
    return int(sdk.calls[before]["max_tokens"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    "model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
)
async def test_a_model_that_thinks_is_sent_room_to_answer(sdk, model):
    """🔴 Red before: 4,096, which a model that reasons at length spends before it answers."""
    sent = await _sent_cap(sdk, AnthropicProvider(model=model, credential=_KEY))
    assert sent == THINKING_OUTPUT_CAP == 32_000


@pytest.mark.anyio
async def test_the_model_a_call_names_decides_it(sdk):
    """A binding names its model per call; the cap is that model's, not the one built for."""
    provider = AnthropicProvider(model="claude-3-5-haiku-latest", credential=_KEY)
    assert await _sent_cap(sdk, provider) == DEFAULT_OUTPUT_CAP
    assert await _sent_cap(sdk, provider, model="claude-opus-5-5") == THINKING_OUTPUT_CAP


@pytest.mark.anyio
@pytest.mark.parametrize(
    "model", ["claude-3-5-haiku-latest", "claude-opus-4-1", "claude-haiku-4-5"]
)
async def test_a_model_that_does_not_think_adaptively_still_sends_4096(sdk, model):
    assert await _sent_cap(sdk, AnthropicProvider(model=model, credential=_KEY)) == 4096


@pytest.mark.anyio
async def test_a_cap_of_0_is_none_set_and_sends_the_models_default(sdk):
    """`max_tokens` stays an `int` for every app that passes one; 0, its default, is none set."""
    for model in ("claude-opus-5-5", "claude-3-5-haiku-latest"):
        built = AnthropicProvider(model=model, credential=_KEY, max_tokens=0)
        assert await _sent_cap(sdk, built) == default_output_cap(model)


@pytest.mark.anyio
async def test_a_cap_the_caller_set_still_wins(sdk):
    built = AnthropicProvider(model="claude-opus-5-5", credential=_KEY, max_tokens=900)
    assert await _sent_cap(sdk, built) == 900


def _branded() -> Any:
    spec = BrandedProviderSpec(
        type="fixture-thinking-anthropic",
        protocol="anthropic",
        default_base_url="http://127.0.0.1:9/v1",
    )
    factory, _create, _catalog = register_branded_app(spec)
    return factory


def _entry(**options: Any) -> ProviderEntry:
    return ProviderEntry(
        name="claude",
        type="fixture-thinking-anthropic",
        model="claude-opus-5-5",
        options={"api_key": "fake-key-fixture", **options},
        declared_capabilities=frozenset({Capability.CHAT}),
    )


@pytest.mark.anyio
async def test_a_branded_app_on_the_anthropic_wire_sends_the_same(sdk):
    """🔴 Red before: a branded spec with no cap of its own fell back to 4,096 for every model."""
    factory = _branded()
    assert await _sent_cap(sdk, factory(entry=_entry())) == THINKING_OUTPUT_CAP
    assert await _sent_cap(sdk, factory(entry=_entry(), max_tokens=321)) == 321
    assert await _sent_cap(sdk, factory(entry=_entry(max_tokens=900), max_tokens=321)) == 900


def test_the_default_is_held_to_a_ceiling_the_caller_knows():
    assert default_output_cap("claude-opus-5-5") == 32_000
    assert default_output_cap("claude-opus-5-5", ceiling=16_000) == 16_000
    assert default_output_cap("claude-opus-5-5", ceiling=0) == 32_000, "no ceiling known"
    assert default_output_cap("gpt-5") == 4096
    assert default_output_cap("claude-haiku-4-5", ceiling=128_000) == 4096


def test_the_sdk_publishes_it_under_a_feature_an_app_can_require():
    from personalclaw.sdk import features
    from personalclaw.sdk.model import default_output_cap as published

    assert published is default_output_cap
    assert features.core_has(features.THINKING_OUTPUT_CAP)
