"""The SDK says which models think adaptively, by the rule core's Anthropic-compatible wire uses.

A model app on a wire of its own that asks a model to reason in one shape, adaptive thinking
(``thinking: {type: adaptive}`` with the depth as an effort level), has to know which models take
that shape: an older Claude takes a thinking budget instead, and another vendor's model neither.
The SDK said which levels core's wire sends a model (``wire_efforts``) but not in which shape, so
``bedrock-models`` kept a copy of the rule of its own, pinned to core's by a differential test.
``personalclaw.sdk.model.thinks_adaptively(model_id)`` says it, read from the table core's wire
builds its request from (``catalog.thinking_shape``), and the ``adaptive-thinking`` core feature
names it, so an app that imports it can require it.

The differential drives core's wire (``AnthropicProvider``) through an SDK fake for every Claude
family and version, in each form a listing names a model by (bare, dated, dotted, the vendor's
own, a ``us.`` and a ``global.`` inference profile, the older version-first spelling), and for
other vendors' ids, and holds the answer to the request the wire built: ``thinks_adaptively`` is
true exactly when that request asks for adaptive thinking.

Every call goes to ``tests/anthropic_sdk_fake.py``; nothing leaves the machine.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from personalclaw.llm.credentials import Credential
from tests import anthropic_sdk_fake

_HI = [{"role": "user", "content": "hi"}]


def _thinks_adaptively(model_id: str) -> bool:
    from personalclaw.sdk.model import thinks_adaptively

    return thinks_adaptively(model_id)


def _claude_ids() -> Iterator[str]:
    """Every Claude family and version a listing may name, in each form it names one by."""
    for family in ("opus", "sonnet", "haiku", "fable", "mythos"):
        for major in (3, 4, 5, 6):
            for minor in ("", "0", "1", "5", "6", "7", "8", "9", "50"):
                base = f"claude-{family}-{major}" + (f"-{minor}" if minor else "")
                yield base
                yield f"{base}-20251101"
                if minor:
                    yield f"claude-{family}-{major}.{minor}"
                yield f"anthropic.{base}-v1:0"
                yield f"us.anthropic.{base}-v1:0"
                yield f"global.anthropic.{base}-20251101-v1:0"
    # The older spelling, version first, and the first Claude models.
    yield "claude-3-7-sonnet-20250219"
    yield "claude-3-opus-20240229"
    yield "us.anthropic.claude-3-5-haiku-20241022-v1:0"
    yield "global.anthropic.claude-3-7-sonnet-20250219-v1:0"
    yield "claude-2.1"
    yield "anthropic.claude-v2:1"
    yield "anthropic.claude-instant-v1"


#: Other vendors' models, a reasoning model among them: none takes the adaptive shape.
OTHER_IDS = (
    "gpt-5",
    "o3",
    "glm-5.2",
    "deepseek-r1",
    "gemini-2.5-pro",
    "amazon.nova-pro-v1:0",
    "us.amazon.nova-premier-v1:0",
    "global.amazon.nova-2-lite-v1:0",
    "mistral.mistral-large-2407-v1:0",
    "cohere.command-r-plus-v1:0",
    "us.meta.llama4-maverick-17b-instruct-v1:0",
)


async def _wire_thinks_adaptively(sdk, provider, model_id: str) -> bool:
    """Whether core's Anthropic-compatible wire asks ``model_id`` for adaptive thinking when a call
    names that model, as a use-case binding does."""
    before = len(sdk.calls)
    async for _ in provider.complete(_HI, model=model_id, reasoning_effort="high"):
        pass
    sent = dict(sdk.calls[before])
    sent.update(sent.pop("extra_body", None) or {})
    thinking = sent["thinking"]
    assert thinking["type"] in ("adaptive", "enabled"), (model_id, thinking)
    return thinking["type"] == "adaptive"


@pytest.mark.anyio
async def test_the_sdk_answers_as_the_wire_decides_for_every_claude_and_other_id(monkeypatch):
    from personalclaw.llm.anthropic import AnthropicProvider

    sdk = anthropic_sdk_fake.install(monkeypatch)
    provider = AnthropicProvider(
        model="claude-haiku-4-5",
        credential=Credential(name="x", kind="api_key", secret="fake-key-test", source="env"),
        max_tokens=128_000,
    )
    differ = {}
    seen = {True: 0, False: 0}
    for model_id in (*_claude_ids(), *OTHER_IDS):
        wire = await _wire_thinks_adaptively(sdk, provider, model_id)
        seen[wire] += 1
        if _thinks_adaptively(model_id) is not wire:
            differ[model_id] = {"sdk": _thinks_adaptively(model_id), "wire": wire}
    assert differ == {}
    # The ids reach both of the wire's shapes, so the agreement is not one answer for all.
    assert seen[True] > 100 and seen[False] > 100, seen


def test_the_answer_names_each_line_in_each_form():
    for model_id in (
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4.6",
        "claude-sonnet-5-20251101",
        "us.anthropic.claude-haiku-5-5-v1:0",
        "global.anthropic.claude-opus-4-8-20251101-v1:0",
        "claude-fable-5",
        "claude-mythos-5",
    ):
        assert _thinks_adaptively(model_id) is True, model_id
    for model_id in (
        "claude-sonnet-4-5",
        "claude-haiku-4-5-20251001",
        "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "global.anthropic.claude-opus-4-1-v1:0",
        "claude-3-7-sonnet-20250219",
        "amazon.nova-pro-v1:0",
        "gpt-5",
        "",
        "   ",
    ):
        assert _thinks_adaptively(model_id) is False, model_id


def test_the_sdk_publishes_it_beside_wire_efforts():
    from personalclaw.sdk import model

    assert "thinks_adaptively" in model.__all__
    snapshot = Path(model.__file__).with_name("signatures.json")
    recorded = json.loads(snapshot.read_text(encoding="utf-8"))
    assert recorded["personalclaw.sdk.model.thinks_adaptively"] == {
        "kind": "function",
        "params": [{"annotation": "str", "kind": "positional_or_keyword", "name": "model_id"}],
        "returns": "bool",
    }
