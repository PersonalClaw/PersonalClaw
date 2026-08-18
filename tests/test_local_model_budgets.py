"""Per-model context-budget derivation (LOCAL-MODEL-MANAGER-V2 §2.2, LMMV-7).

Two halves, and the second is the one that matters:

* the HELPER (``local_models/budgets.py``): a declared ``context_tokens`` derives the
  window; the ``0`` = unknown card (the normal case for a model whose card predates the
  field) falls back to the shared hosted-model window table and, only then, to the 4096
  the adapters used to hardcode. Neither direction divides by zero or yields a negative.
* the CONSUMER (``llm_helpers.one_shot_completion``): the budget that actually reaches
  the provider-resolution call MOVES when the catalog's ``context_tokens`` moves. A
  helper nothing consumes is a declared-but-inert control, so the assertion lives at the
  call site rather than on the helper's return value.

Every provider registered here is unregistered in a fixture — the local-model registry is
a process-global dict and a leaked fake would change another test's budget.
"""

from __future__ import annotations

import pytest

from personalclaw.local_models import registry as lm_registry
from personalclaw.local_models.budgets import (
    DEFAULT_OUTPUT_TOKENS,
    MAX_OUTPUT_FRACTION,
    catalog_window,
    model_budget,
    output_budget,
)
from personalclaw.local_models.provider import LocalModel, LocalModelProvider
from personalclaw.model_windows import DEFAULT_CONTEXT_WINDOW


class _FakeLocalProvider(LocalModelProvider):
    """A local provider whose catalog is whatever the test hands it."""

    def __init__(self, name: str, models: list[LocalModel]) -> None:
        self._name = name
        self._models = models

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name.title()

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[LocalModel]:
        return list(self._models)

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True


@pytest.fixture
def clean_registry():
    """Snapshot + restore the process-global local-model registry."""
    before = dict(lm_registry._providers)
    yield lm_registry
    lm_registry._providers.clear()
    lm_registry._providers.update(before)


def _register(clean_registry, provider: _FakeLocalProvider) -> None:
    clean_registry.register_provider(provider, capabilities=["chat"], name=provider.name)


# ── The helper: a declared window derives from the catalog ─────────────────────


@pytest.mark.asyncio
async def test_declared_context_tokens_derives_from_the_catalog(clean_registry):
    """A card that declares both fields wins outright — no table, no constant."""
    _register(
        clean_registry,
        _FakeLocalProvider(
            "FakeLocal",
            [LocalModel(name="tiny-chat", context_tokens=8192, output_tokens=1024)],
        ),
    )

    budget = await model_budget("FakeLocal:tiny-chat")

    assert budget.source == "catalog"
    assert budget.context_tokens == 8192
    assert budget.output_tokens == 1024
    assert budget.input_tokens == 8192 - 1024
    # And the catalog reader itself agrees, so a failure localizes.
    assert await catalog_window("FakeLocal:tiny-chat") == (8192, 1024)


@pytest.mark.asyncio
async def test_unqualified_id_resolves_across_registered_providers(clean_registry):
    """A bare model id (no ``Provider:`` qualifier) still finds its catalog entry."""
    _register(
        clean_registry,
        _FakeLocalProvider("FakeLocal", [LocalModel(name="tiny-chat", context_tokens=8192)]),
    )
    assert (await model_budget("tiny-chat")).source == "catalog"


@pytest.mark.asyncio
async def test_colon_bearing_ollama_style_id_is_not_split_on_the_wrong_colon(clean_registry):
    """``FakeLocal:qwen3:4b`` must ask the catalog for ``qwen3:4b``, not for ``4b``.

    Splitting on the first colon unconditionally would look up a model called ``4b``,
    miss, and silently fall back to the 200k table — the failure mode that reads as
    "the helper works" because a number still comes back.
    """
    _register(
        clean_registry,
        _FakeLocalProvider("FakeLocal", [LocalModel(name="qwen3:4b", context_tokens=32768)]),
    )
    budget = await model_budget("FakeLocal:qwen3:4b")
    assert (budget.source, budget.context_tokens) == ("catalog", 32768)


# ── The helper: context_tokens = 0 is a NORMAL card, both directions ───────────


@pytest.mark.asyncio
async def test_context_tokens_zero_falls_back_to_the_window_table(clean_registry):
    """``0`` means "unknown", never "a window of zero".

    The fallback is today's constant, said out loud: the shared window table's
    conservative default for an unknown id, and :data:`DEFAULT_OUTPUT_TOKENS` (4096 — the
    number ``llm/anthropic.py`` and the anthropic app factory each hardcoded) for the
    output cap.
    """
    _register(
        clean_registry,
        _FakeLocalProvider(
            "FakeLocal",
            [LocalModel(name="undeclared", context_tokens=0, output_tokens=0)],
        ),
    )

    assert await catalog_window("FakeLocal:undeclared") == (0, 0)
    budget = await model_budget("FakeLocal:undeclared")

    assert budget.source == "window-table"
    assert budget.context_tokens == DEFAULT_CONTEXT_WINDOW
    assert budget.output_tokens == DEFAULT_OUTPUT_TOKENS
    assert budget.input_tokens == DEFAULT_CONTEXT_WINDOW - DEFAULT_OUTPUT_TOKENS
    # The two directions differ — the declared case must not collapse onto the fallback.
    assert budget.output_tokens != 1024


@pytest.mark.asyncio
async def test_zero_window_never_divides_by_zero_or_goes_negative(clean_registry):
    """A pathological card (window of 1) still yields strictly positive budgets."""
    _register(
        clean_registry,
        _FakeLocalProvider("FakeLocal", [LocalModel(name="degenerate", context_tokens=1)]),
    )
    budget = await model_budget("FakeLocal:degenerate")
    assert budget.context_tokens >= 1
    assert budget.output_tokens >= 1
    assert budget.input_tokens >= 1
    assert budget.output_tokens <= budget.context_tokens


@pytest.mark.asyncio
async def test_no_local_provider_registered_uses_the_window_table():
    """The hosted path is untouched: no local catalog, so the table answers."""
    budget = await model_budget("Anthropic:claude-opus-4-1")
    assert budget.source == "window-table"
    assert budget.output_tokens == DEFAULT_OUTPUT_TOKENS
    assert budget.input_tokens > 0


@pytest.mark.asyncio
async def test_empty_ref_is_the_conservative_default():
    budget = await model_budget("")
    assert budget.source == "window-table"
    assert budget.context_tokens == DEFAULT_CONTEXT_WINDOW


# ── The helper: the output cap can never eat the whole window ──────────────────


@pytest.mark.asyncio
async def test_default_output_cap_is_clamped_to_half_a_small_window(clean_registry):
    """A 4k local model must not be handed the 4096-token cap: it would leave no prompt."""
    _register(
        clean_registry,
        _FakeLocalProvider("FakeLocal", [LocalModel(name="small", context_tokens=4096)]),
    )
    budget = await model_budget("FakeLocal:small")
    assert budget.output_tokens == int(4096 * MAX_OUTPUT_FRACTION)
    assert budget.input_tokens == 4096 - budget.output_tokens


@pytest.mark.asyncio
async def test_declared_output_larger_than_the_window_is_clamped(clean_registry):
    _register(
        clean_registry,
        _FakeLocalProvider(
            "FakeLocal",
            [LocalModel(name="overclaim", context_tokens=4096, output_tokens=999_999)],
        ),
    )
    budget = await model_budget("FakeLocal:overclaim")
    assert budget.output_tokens <= int(4096 * MAX_OUTPUT_FRACTION)
    assert budget.input_tokens > 0


@pytest.mark.asyncio
async def test_a_raising_provider_does_not_break_the_budget(clean_registry):
    """``list_models`` reaches a filesystem / localhost — a fault degrades to the table."""

    class _Broken(_FakeLocalProvider):
        async def list_models(self):
            raise RuntimeError("catalog unavailable")

    _register(clean_registry, _Broken("FakeLocal", []))
    budget = await model_budget("FakeLocal:anything")
    assert budget.source == "window-table"
    assert budget.output_tokens == DEFAULT_OUTPUT_TOKENS


@pytest.mark.asyncio
async def test_output_budget_accessor_matches_the_dataclass(clean_registry):
    _register(
        clean_registry,
        _FakeLocalProvider(
            "FakeLocal", [LocalModel(name="tiny-chat", context_tokens=8192, output_tokens=777)]
        ),
    )
    assert await output_budget("FakeLocal:tiny-chat") == 777


# ── The CONSUMER: the budget that reaches the call moves with the catalog ──────


class _StubProvider:
    """The minimum ``one_shot_completion`` drives: start / stream / shutdown."""

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield LLMEvent(kind=EVENT_COMPLETE)


@pytest.fixture
def captured_resolve(monkeypatch):
    """Patch the bridge seam and record the kwargs every resolution is called with."""
    calls: list[dict] = []

    def _fake_resolve(use_case, **kwargs):
        calls.append({"use_case": use_case, **kwargs})
        return _StubProvider()

    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
        _fake_resolve,
    )
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("declared_context", "declared_output", "expected_max_tokens"),
    [
        (8192, 1024, 1024),  # both declared → the card's own cap
        (8192, 0, DEFAULT_OUTPUT_TOKENS),  # window declared, cap not → the named floor
        (4096, 0, 2048),  # small window → the floor is clamped to half
    ],
)
async def test_catalog_context_tokens_moves_the_budget_that_reaches_the_call(
    clean_registry,
    captured_resolve,
    declared_context,
    declared_output,
    expected_max_tokens,
):
    """The anti-inertness assertion: change the CATALOG, the CALL's budget changes.

    Driven through the pinned-model path (``model=``), which is the one resolution branch
    that needs no ``active_models.json`` state — the derivation is identical in all three.
    """
    from personalclaw.llm_helpers import one_shot_completion

    _register(
        clean_registry,
        _FakeLocalProvider(
            "FakeLocal",
            [
                LocalModel(
                    name="tiny-chat",
                    context_tokens=declared_context,
                    output_tokens=declared_output,
                )
            ],
        ),
    )

    text = await one_shot_completion("hello", use_case="reasoning", model="FakeLocal:tiny-chat")

    assert text == "ok"
    assert len(captured_resolve) == 1
    assert captured_resolve[0]["max_tokens"] == expected_max_tokens


@pytest.mark.asyncio
async def test_an_unknown_model_still_carries_the_fallback_budget(captured_resolve):
    """No catalog entry: the call still carries a budget, and it is the named constant.

    This is the ``context_tokens = 0`` direction AT THE CONSUMER — the path a hosted
    model takes today. The value equals the constant the adapters hardcoded, so hosted
    behaviour is unchanged in value while the number is now derived in one place.
    """
    from personalclaw.llm_helpers import one_shot_completion

    await one_shot_completion("hello", use_case="reasoning", model="Anthropic:claude-opus-4-1")

    assert captured_resolve[0]["max_tokens"] == DEFAULT_OUTPUT_TOKENS


@pytest.mark.asyncio
async def test_the_budget_rides_alongside_a_pinned_temperature(clean_registry, captured_resolve):
    """The budget must not displace the existing ``temperature`` build kwarg."""
    from personalclaw.llm_helpers import one_shot_completion

    _register(
        clean_registry,
        _FakeLocalProvider(
            "FakeLocal", [LocalModel(name="tiny-chat", context_tokens=8192, output_tokens=512)]
        ),
    )

    await one_shot_completion(
        "hello", use_case="reasoning", model="FakeLocal:tiny-chat", temperature=0.7
    )

    assert captured_resolve[0]["temperature"] == 0.7
    assert captured_resolve[0]["max_tokens"] == 512
