"""A provider's selftest runs on that provider, and a name no provider has is a 404.

``POST /api/model-providers/{name}/selftest`` is the Providers page's "Self-test" on one provider.
It ignored the name: it ran the Background model, the active embedder and the active voice,
whichever provider served them, and reported what they did as this provider's. So a provider with
a revoked key read green while the Background model on another one answered, and a working one read
red when someone else's embedder was down. Any name at all was "tested" the same way.

Now each probe runs on the named provider: chat on its model bound for Chat or one of its sub-uses
(else its default model), embedding on its model bound for Embedding, and speech only when the
voice in use is its own. A capability with none of its models chosen is left out, not failed.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import doctor
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

pytestmark = pytest.mark.asyncio

LAB, OTHER = "lab", "other"


@pytest.fixture
def world(monkeypatch):
    """Two chat providers, what Settings → Models binds, and every probe the selftest makes,
    recorded: which model each chat probe named and which provider each embed asked."""
    registry = ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type="lab-type",
            capabilities=frozenset({Capability.CHAT, Capability.EMBEDDING}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=True,
            supports_vision=False,
            max_context_tokens=8_000,
        ),
        lambda **_kw: None,
    )
    registry.register_entry(ProviderEntry(name=LAB, type="lab-type", model="lab-default"))
    registry.register_entry(ProviderEntry(name=OTHER, type="lab-type", model="other-default"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)

    bound: dict[str, list[str]] = {}
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.active_model_refs", lambda axis: bound.get(axis, [])
    )
    asked: dict[str, list[Any]] = {"chat": [], "embed": []}

    async def _one_shot(prompt: str, **kwargs: Any) -> str:
        asked["chat"].append(kwargs.get("model"))
        return "pong"

    def _embed_fn_for(provider: str, model: str):
        asked["embed"].append((provider, model))
        return lambda _text: [0.1, 0.2, 0.3]

    async def _no_voice(_timed, _name):
        return None

    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _one_shot)
    monkeypatch.setattr("personalclaw.embedding_providers.registry.embed_fn_for", _embed_fn_for)
    monkeypatch.setattr(doctor, "_tts_clone_probe", _no_voice)
    return bound, asked


async def test_chat_runs_on_the_named_provider_s_bound_model(world):
    """🔴 Red before the fix: the probe ran the Background model, on whatever provider served it."""
    bound, asked = world
    bound.update(chat=[f"{OTHER}:other-large", f"{LAB}:lab-large"], background=[f"{OTHER}:x"])

    result = await doctor._run_selftest(LAB)

    assert asked["chat"] == [f"{LAB}:lab-large"]
    assert result["capabilities"]["chat"] == {"ok": True, "detail": "lab-large replied"}


async def test_a_chat_sub_use_counts_and_the_default_model_is_the_fallback(world):
    bound, asked = world
    bound.update(reasoning=[f"{LAB}:lab-thinker"])
    await doctor._run_selftest(LAB)
    assert asked["chat"] == [f"{LAB}:lab-thinker"]

    bound.clear()
    await doctor._run_selftest(OTHER)
    assert asked["chat"][-1] == f"{OTHER}:other-default"


async def test_embedding_runs_on_the_named_provider_s_embedding_model(world):
    bound, asked = world
    bound.update(embedding=[f"{OTHER}:other-embed", f"{LAB}:lab-embed"])

    result = await doctor._run_selftest(LAB)

    assert asked["embed"] == [(LAB, "lab-embed")]
    assert result["capabilities"]["embedding"] == {"ok": True, "detail": "3 dims"}


async def test_another_provider_s_embedding_model_is_not_this_one_s_probe(world):
    """Left out rather than failed: nothing of this provider's is chosen for embedding."""
    bound, asked = world
    bound.update(embedding=[f"{OTHER}:other-embed"])

    result = await doctor._run_selftest(LAB)

    assert asked["embed"] == []
    assert "embedding" not in result["capabilities"]


async def test_the_voice_in_use_is_probed_only_on_the_provider_that_speaks_it(monkeypatch):
    import personalclaw.tts.registry as reg

    class _Voice:
        name = OTHER
        supports_cloning = False

    monkeypatch.setattr(reg, "active_voice_params", lambda **kw: {"provider": _Voice()})

    async def _timed(coro, timeout: float = 15.0):
        return await coro

    assert await doctor._tts_clone_probe(_timed, LAB) is None


async def test_a_name_no_provider_has_is_a_404(world):
    """🔴 Red before the fix: any name was "tested", and answered with the Background model."""
    request = make_mocked_request("POST", "/api/model-providers/nobody/selftest")
    request.match_info.update({"name": "nobody"})

    response = await doctor.api_provider_selftest(request)

    assert response.status == 404
    assert b"No model provider is named 'nobody'." in response.body


#: A provider's refusal and the step it names after it: past 200 characters, and inside the bound.
_REFUSAL = (
    "The provider refused the request: this key has no access to the model it names, because the "
    "project it belongs to has not been granted that model, and every model call it makes is "
    "refused the same way. Grant the model to the project in the provider's console, then run the "
    "selftest again."
)


async def test_a_failure_says_its_whole_sentence_and_no_credential(world, monkeypatch):
    """🔴 Red before the fix: cut at 200 characters, which dropped the step the provider names."""
    bound, _ = world
    bound.update(chat=[f"{LAB}:lab-large"])

    async def _refused(prompt: str, **kwargs: Any) -> str:
        raise RuntimeError(f"{_REFUSAL}\n  (request signed with AKIAIOSFODNN7EXAMPLE)")

    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _refused)
    assert 200 < len(_REFUSAL) < 600, "the premise: a sentence the old cut broke, inside the bound"

    detail = (await doctor._run_selftest(LAB))["capabilities"]["chat"]["detail"]

    assert detail.startswith(_REFUSAL), detail
    assert "AKIAIOSFODNN7EXAMPLE" not in detail and "\n" not in detail


async def test_a_failure_past_the_bound_is_cut_there_and_says_so():
    from personalclaw.llm.catalog import FAILURE_DETAIL_CHARS
    from personalclaw.providers.failure_copy import failure_detail

    detail = failure_detail("word " * 400)
    assert detail.endswith("…")
    assert FAILURE_DETAIL_CHARS - 5 < len(detail) <= FAILURE_DETAIL_CHARS + 1


async def test_a_failure_that_cannot_be_masked_is_withheld(monkeypatch):
    """The Test's row fell back to the unmasked words when the redactor failed; the one rule the
    Test and this selftest share now withholds them instead."""
    from personalclaw.providers.failure_copy import failure_detail

    def _broken(_text: str) -> str:
        raise RuntimeError("the redactor could not load its patterns")

    monkeypatch.setattr("personalclaw.security.redact", _broken)
    assert failure_detail("signed with AKIAIOSFODNN7EXAMPLE") == ""
