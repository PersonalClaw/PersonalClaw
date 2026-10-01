"""A model behind the spend guard caches its stable prefix like the same model unguarded.

Every non-interactive turn (a Code loop's planner and workers, background work, workflows,
reasoning) runs its model behind ``ModelCallGuard``. The native loop decides whether to place a
cache marker by reading the model's ``prompt_cache`` posture, and the guard is a
``ModelProvider`` itself: the ABC's ``NONE`` default answered for it, so a guarded Bedrock or
Anthropic model never got a marker. Measured: a planner session on Bedrock sent 536,804 input
tokens over 25 calls with not one token read from or written to the cache, while a chat on the
same model and instance read 104,818 from it.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.guardrails.model_call import ModelCallGuard
from personalclaw.llm.base import ModelProvider
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.llm.prompt_cache import CACHE_HINT_KEY, VOLATILE_KEY, PromptCache
from tests.test_prompt_cache_wire_translation import (  # noqa: F401 — the fixture is used by name
    _cred,
    _FakeMessages,
    fake_anthropic_module,
)


class _Recording(ModelProvider):
    """A model that declares a posture and keeps every message list it is handed."""

    supports_tools = True

    def __init__(self, posture: PromptCache) -> None:
        self.prompt_cache = posture
        self.requests: list[list[dict]] = []

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        yield AgentEvent(kind=EVENT_COMPLETE)

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.requests.append(list(messages))
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Step written.")
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)

    async def approve_tool(self, request_id) -> None:
        return None

    async def reject_tool(self, request_id) -> None:
        return None

    def context_usage_pct(self) -> float | None:
        return None


def _guarded(inner: ModelProvider) -> ModelCallGuard:
    """``inner`` the way a loop's planner holds it: behind the spend guard, on ``loops``."""
    return ModelCallGuard(inner, use_case="loops", provider_name="planner-test", model="m")


async def _planner_turns(model: ModelProvider, *nudges: str) -> None:
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="planner", provider="native", model="m"),
        model_provider=model,
        tool_providers=[],
    )
    await rt.start()
    for nudge in nudges:
        async for _ in rt.stream(nudge):
            pass


@pytest.mark.parametrize("posture", list(PromptCache))
def test_the_spend_guard_reports_the_posture_of_the_model_it_wraps(posture):
    assert _guarded(_Recording(posture)).prompt_cache is posture


#: Every value the ``ModelProvider`` ABC declares a default for, each with a value a provider
#: could declare instead. The guard has to answer with the wrapped provider's own: a default it
#: answers with is read off the ABC by normal lookup and never reaches its ``__getattr__``, which
#: is how ``prompt_cache`` read ``NONE`` for every guarded model. ``served_ref`` is not here: the
#: resolution seam stamps the guard with the ref it serves, as it does an unguarded provider.
_DECLARED: dict[str, object] = {
    "supports_tools": True,
    "prompt_cache": PromptCache.EXPLICIT,
    "request_only": True,
    "request_timeout_secs": 42.0,
    "supports_native_commands": True,
    "compacts_in_process": True,
    "compacts_automatically": True,
    "keeps_cancelled_turns": True,
    "sampling_temperature": 0.3,
    "unsent_options": {"seed": "this endpoint takes none"},
}


def _abc_defaults() -> set[str]:
    """The ABC's declared values: its public plain attributes and properties."""
    return {
        name
        for name, value in vars(ModelProvider).items()
        if not name.startswith("_") and (isinstance(value, property) or not callable(value))
    } - {"served_ref", "session_id"}


def test_the_rail_lists_every_value_the_abc_declares():
    assert set(_DECLARED) == _abc_defaults()


@pytest.mark.parametrize(("name", "declared"), sorted(_DECLARED.items()))
def test_the_spend_guard_answers_with_the_wrapped_providers_own_value(name, declared):
    if name == "prompt_cache":
        inner = _Recording(declared)
    else:
        declares = type("_Declares", (_Recording,), {name: property(lambda self: declared)})
        inner = declares(PromptCache.NONE)

    assert getattr(_guarded(inner), name) == declared


def _hinted(request: list[dict]) -> int:
    marked = [i for i, m in enumerate(request) if CACHE_HINT_KEY in m]
    assert len(marked) == 1, f"one cache marker a request, got {len(marked)}"
    return marked[0]


def _stable(messages: list[dict]) -> str:
    """The span as the model is served it, without the loop's bookkeeping keys."""
    return json.dumps(
        [
            {k: v for k, v in m.items() if k != CACHE_HINT_KEY}
            for m in messages
            if not m.get(VOLATILE_KEY)
        ],
        sort_keys=True,
    )


@pytest.mark.asyncio
async def test_two_planner_turns_cache_one_identical_prefix():
    """Two consecutive planner turns on a guarded model that needs a marker: each request carries
    exactly one, on its last stable message, and everything up to the first turn's marker reaches
    the second turn byte for byte, so the second can read what the first cached."""
    inner = _Recording(PromptCache.EXPLICIT)

    await _planner_turns(_guarded(inner), "Plan step one for the issue.", "Plan step two.")

    first, second = inner.requests
    cut = _hinted(first) + 1
    assert _hinted(second) > _hinted(first)
    assert _stable(first[:cut]) == _stable(second[:cut])


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake_anthropic_module")
async def test_a_guarded_anthropic_planner_turn_carries_its_breakpoint():
    """On the wire: each planner turn's request to a guarded Anthropic model carries one cache
    breakpoint. Before, a guarded turn carried none, and nothing it sent was ever cached."""
    from personalclaw.llm.anthropic import AnthropicProvider

    inner = AnthropicProvider(model="claude-x", credential=_cred())
    fake = _FakeMessages()
    inner._client.messages = fake

    await _planner_turns(_guarded(inner), "Plan step one for the issue.", "Plan step two.")

    assert len(fake.calls) == 2
    assert [json.dumps(call).count('"cache_control"') for call in fake.calls] == [1, 1]
