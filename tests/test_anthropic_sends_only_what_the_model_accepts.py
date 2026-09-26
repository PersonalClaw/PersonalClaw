"""``AnthropicProvider`` sends a sampling option as a body field, and only to a model that takes it.

Two defects, measured by the apps SDK lane:

* It forwarded every extra option as an SDK keyword. ``anthropic`` 1.x dropped ``temperature``,
  ``top_p`` and ``top_k`` from ``messages.stream``'s signature, so any call carrying one — every
  best-of-N candidate — raised ``TypeError: ... unexpected keyword argument 'temperature'`` before
  a request left the machine. The older tests' fakes took ``**kwargs`` and could not see it;
  ``tests/anthropic_sdk_fake.py`` declares the real 1.8.0 signature, so Python raises the same
  ``TypeError`` here.
* It sent a custom temperature to models that refuse one: Claude Opus 4.7 and later, the Fable
  and Mythos 5 lines and Sonnet 5 answer 400, and every other Claude 4 model refuses a
  temperature together with a top_p. What a model refuses is classified by
  ``llm.catalog.refused_sampling``; the provider leaves it off and says why
  (``unsent_options``), and best-of-N reports that reason instead of only "not sent".
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.llm.anthropic import AnthropicProvider
from personalclaw.llm.capabilities import Capability
from personalclaw.llm.credentials import Credential
from personalclaw.llm.registry import ProviderEntry, get_default_registry
from tests import anthropic_sdk_fake

PROMPT = "Name one primary color in one word."


@pytest.fixture
def sdk(monkeypatch):
    fake = anthropic_sdk_fake.install(monkeypatch)
    fake.sample_prompt = PROMPT
    return fake


def _provider(model: str, **options: Any) -> AnthropicProvider:
    return AnthropicProvider(
        model=model,
        credential=Credential(name="x", kind="api_key", secret="sk-test", source="env"),
        extra_options=options,
    )


async def _drain(events) -> None:
    async for _ in events:
        pass


def _sent_fields(call: dict[str, Any]) -> dict[str, Any]:
    """What the request JSON carries: the keywords plus ``extra_body``, as the SDK merges them."""
    fields = {k: v for k, v in call.items() if k != "extra_body"}
    fields.update(call.get("extra_body") or {})
    return fields


# ── the SDK keyword defect ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_temperature_rides_the_body_and_the_sdk_accepts_the_call(sdk):
    provider = _provider("claude-sonnet-4-6", temperature=0.7)

    await _drain(provider.stream(PROMPT))

    (call,) = sdk.calls
    assert "temperature" not in call, "sent as an SDK keyword, which anthropic 1.x refuses"
    assert call["extra_body"] == {"temperature": 0.7}
    assert provider.sampling_temperature == 0.7
    assert provider.unsent_options == {}


@pytest.mark.anyio
async def test_complete_keeps_its_own_fields_authoritative(sdk):
    provider = _provider("claude-sonnet-4-6", system="an option's system", top_k=40)

    await _drain(
        provider.complete(
            [{"role": "system", "content": "the turn's system"}, {"role": "user", "content": "hi"}]
        )
    )

    (call,) = sdk.calls
    assert call["system"] == "the turn's system"
    assert call["extra_body"] == {"top_k": 40}


# ── the model-capability defect ──────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    "model", ["claude-opus-4-8", "claude-opus-5", "claude-sonnet-5", "claude-fable-5-1"]
)
async def test_a_model_that_refuses_sampling_is_sent_none_and_says_why(sdk, model):
    provider = _provider(model, temperature=0.7, top_p=0.9)

    await _drain(provider.stream(PROMPT))

    sent = _sent_fields(sdk.calls[0])
    assert "temperature" not in sent and "top_p" not in sent
    assert provider.sampling_temperature is None
    assert provider.unsent_options == {
        "temperature": f"{model} does not accept a custom temperature",
        "top_p": f"{model} does not accept a custom top_p",
    }


@pytest.mark.anyio
async def test_a_claude_4_model_gets_the_temperature_and_not_the_top_p(sdk):
    provider = _provider("claude-haiku-4-5", top_p=0.9, temperature=0.4)

    await _drain(provider.stream(PROMPT))

    sent = _sent_fields(sdk.calls[0])
    assert sent["temperature"] == 0.4
    assert "top_p" not in sent
    assert provider.unsent_options == {
        "top_p": "claude-haiku-4-5 takes a temperature or a top_p, not both"
    }


@pytest.mark.anyio
async def test_a_model_the_table_does_not_name_is_sent_everything(sdk):
    provider = _provider("glm-5.2", temperature=0.7, top_p=0.9)

    await _drain(provider.stream(PROMPT))

    assert sdk.calls[0]["extra_body"] == {"temperature": 0.7, "top_p": 0.9}
    assert provider.unsent_options == {}


@pytest.mark.anyio
async def test_a_thinking_turn_drops_the_temperature(sdk):
    provider = _provider("claude-sonnet-4-6", temperature=0.7)

    await _drain(provider.complete([{"role": "user", "content": "hi"}], reasoning_effort="low"))

    sent = _sent_fields(sdk.calls[0])
    assert sent["thinking"]["type"] == "enabled"
    assert "temperature" not in sent


@pytest.mark.anyio
async def test_a_per_call_model_is_classified_not_the_instance_model(sdk):
    provider = _provider("claude-sonnet-4-6", temperature=0.7)

    await _drain(provider.complete([{"role": "user", "content": "hi"}], model="claude-opus-4-8"))

    assert "temperature" not in _sent_fields(sdk.calls[0])


def test_the_dotted_and_regional_spellings_classify_alike():
    from personalclaw.llm.catalog import refused_sampling

    for model in ("claude-opus-4.8", "global.anthropic.claude-opus-4-8", "anthropic.claude-opus-5"):
        assert set(refused_sampling(model, ["temperature"])) == {"temperature"}
    assert refused_sampling("claude-3-7-sonnet-20250219", ["temperature", "top_p"]) == {}


# ── end to end: best-of-N says why the ladder was not sent ───────────────────


def _bind_branded_anthropic_app(monkeypatch, model: str) -> None:
    from personalclaw.llm.branded_specs import BrandedProviderSpec
    from personalclaw.sdk.provider_helpers import register_branded_app

    spec = BrandedProviderSpec(
        type="fixture-branded-anthropic-sampling",
        protocol="anthropic",
        default_base_url="http://127.0.0.1:9",
        max_tokens=4096,
    )
    register_branded_app(spec)
    ref = f"fixture-claude:{model}"
    monkeypatch.setattr("personalclaw.providers.use_cases.resolution_chain", lambda uc: [ref])
    monkeypatch.setattr("personalclaw.providers.use_cases.active_model_refs", lambda uc: [ref])

    async def _budget(_ref: str) -> int:
        return 256

    monkeypatch.setattr("personalclaw.local_models.budgets.output_budget", _budget)
    get_default_registry().register_entry(
        ProviderEntry(
            name="fixture-claude",
            type=spec.type,
            model="",
            options={"api_key": "sk-fixture"},
            declared_capabilities=frozenset({Capability.CHAT}),
        )
    )


@pytest.mark.anyio
async def test_best_of_n_on_a_model_that_refuses_a_temperature_says_so(sdk, monkeypatch):
    from personalclaw.sampling import best_of_n

    _bind_branded_anthropic_app(monkeypatch, "claude-opus-4-8")

    result = await best_of_n(PROMPT, 2)

    samples = sdk.sample_calls()
    assert len(samples) == 2
    assert all("temperature" not in _sent_fields(c) for c in samples)
    assert all(c["model"] == "claude-opus-4-8" for c in samples)
    reason = "claude-opus-4-8 does not accept a custom temperature"
    assert [c.get("unsent") for c in result["candidates"]] == [{"temperature": reason}] * 2
    assert result["note"] == (
        f"not temperature-varied: {reason}, so the 2 candidates are samples at its default"
    )


@pytest.mark.anyio
async def test_best_of_n_on_a_model_that_takes_one_sends_the_ladder(sdk, monkeypatch):
    from personalclaw.sampling import best_of_n

    _bind_branded_anthropic_app(monkeypatch, "claude-sonnet-4-6")

    result = await best_of_n(PROMPT, 2)

    assert sorted(_sent_fields(c)["temperature"] for c in sdk.sample_calls()) == [0.2, 0.7]
    assert [c["sampled_at"] for c in result["candidates"]] == [0.2, 0.7]
    assert result["note"] == ""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
