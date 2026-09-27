"""A model bound in Settings → Models reaches its provider under its whole id, slash included.

Found driving the Groq app (PersonalClawApps' vision fix): chat bound to
``groq:meta-llama/llama-4-scout-17b-16e-instruct`` sent every turn to Groq as
``llama-4-scout-17b-16e-instruct``, a model Groq does not serve, and the platform's image record
then looked up that id, found no row, and sent the attached image as text.

The chain walk names the entry it resolves (``provider_hint``) and hands
``_resolve_from_config_registry`` the model id alone. That function still parsed the id for a
provider prefix, and the legacy ``"Provider/model"`` branch split any id with a slash in it. Every
OpenRouter id (``anthropic/claude-sonnet-4.5``), Together's and NVIDIA's ``meta/…`` ids hit it
whenever the binding, not a per-chat pick, chose the model.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

ENTRY = "groq"
MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
REF = f"{ENTRY}:{MODEL}"


class _Answers:
    """A provider that says which model it was built for."""

    served_ref = ""

    def __init__(self, model: str) -> None:
        self.model = model

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered by {self.model}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def complete(self, messages: list[dict], **_kw: Any):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered by {self.model}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)


@pytest.fixture
def built(monkeypatch) -> tuple[dict[str, list[str]], list[str]]:
    """One entry of a slash-id provider type; returns (Settings → Models map, models built)."""
    registry = ProviderRegistry()
    models: list[str] = []

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        model = str(kwargs.get("model") or entry.model)
        models.append(model)
        return _Answers(model)

    registry.register_type(
        ProviderCapability(
            type="slash-ids",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=8192,
        ),
        _factory,
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type="slash-ids", model=""))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    bound: dict[str, list[str]] = {}
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: bound)
    return bound, models


@pytest.mark.asyncio
async def test_a_bound_model_with_a_slash_in_its_id_is_the_model_that_answers(built):
    """🔴 Red on main: the one-shot path built ``llama-4-scout-17b-16e-instruct``."""
    from personalclaw.llm_helpers import one_shot_completion

    bound, models = built
    bound["chat"] = [REF]
    answer = await one_shot_completion("Name a colour.", use_case="background")
    assert models == [MODEL], f"built {models!r} for the binding {REF!r}"
    assert answer == f"answered by {MODEL}"


def test_the_chat_runtime_names_the_whole_model_it_serves(built):
    """🔴 Red on main: the runtime named ``groq:llama-4-scout-17b-16e-instruct``, the id the
    image record then found no catalog row for."""
    from personalclaw.providers import provider_bridge

    bound, models = built
    bound["chat"] = [REF]
    runtime = provider_bridge._build_native_runtime(
        use_case="chat", session_key="dashboard:slash-id", agent=None, model_override=None, cwd=None
    )
    assert models == [MODEL]
    assert runtime.served_model_ref == REF


def test_a_provider_prefix_is_still_read_when_nothing_else_names_one(built):
    """The legacy ``"Provider/model"`` override keeps naming its provider: only an id whose
    entry is already named is taken whole."""
    from personalclaw.providers import provider_bridge

    _bound, models = built
    provider = provider_bridge._resolve_from_config_registry("chat", model_override=f"{ENTRY}/m-1")
    assert models == ["m-1"]
    assert provider.served_ref == f"{ENTRY}:m-1"
