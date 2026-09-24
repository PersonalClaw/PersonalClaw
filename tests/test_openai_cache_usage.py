"""PCS-9: the OpenAI adapter is the producer for the prompt-cache usage fields.

`PCS-6` built the producer for Anthropic (`llm/anthropic.py::_read_cache_usage`). The
OpenAI-dialect half was never built, because "OpenAI needs no cache MARKER" was read as "there
is no cache NUMBER to read". There is: OpenAI-family caching is AUTOMATIC, and the vendor
reports the hit on `usage.prompt_tokens_details`. The adapter read only `prompt_tokens` /
`completion_tokens`, so every automatic hit — on `openai-models`, `deepseek-models`,
`google-models`, `openrouter-models` and every other app riding core's `OpenAIProvider` — was
dropped before it could reach `LLMEvent`, and the turn reported a flat `0`.

THE PART THAT IS NOT A FIELD COPY, and the reason this file is long. `LLMEvent`'s three prompt
buckets are contractually DISJOINT — `stats.cache_hit_pct` ADDS all three to recover the whole
prompt (`stats.py:160-162`) and `pricing.estimate_cost` bills them additively
(`pricing.py:106-113`). Anthropic's wire satisfies that natively. OpenAI's does not:
`prompt_tokens_details.cached_tokens` is a BREAKDOWN of `prompt_tokens`, so the same tokens are
counted in both. Copying the field straight across therefore double-bills the cached span and
halves the reported hit rate — corrupting the exact two numbers this atom exists to prove. The
adapter resolves the overlap at the edge; `TestTheDisjointnessContract` pins it, with the naive
overlapping copy as the firing positive control.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from personalclaw.llm.openai import _read_cache_usage, _uncached_prompt_tokens
from personalclaw.pricing import cache_savings_usd, estimate_cost
from personalclaw.stats import cache_hit_pct

# The measured vendor pair this file is built on, recorded live against api.openai.com on
# gpt-4o-mini with a byte-identical prefix sent twice and NO cache marker in either request:
# turn 1 -> prompt_tokens=5218, cached_tokens=0; turn 2 -> prompt_tokens=5218,
# cached_tokens=5120. `prompt_tokens` is IDENTICAL across the two turns while `cached_tokens`
# goes 0 -> 5120, which is itself the proof that `cached_tokens` is a subset of `prompt_tokens`
# and not a sibling of it: were they disjoint populations, turn 2's `prompt_tokens` would have
# fallen to the ~98-token remainder.
_PROMPT_TOKENS = 5218
_CACHED_TOKENS = 5120
_UNCACHED_REMAINDER = _PROMPT_TOKENS - _CACHED_TOKENS  # 98
_MODEL = "gpt-4o-mini"  # priced: in 0.15 / cache_read 0.075 / cache_write 0.0


# ── the reader ─────────────────────────────────────────────────────────────────────────


def test_a_cached_read_is_read_off_the_nested_details() -> None:
    """A cache HIT: the vendor reports it one level down, and we surface it."""
    usage = types.SimpleNamespace(
        prompt_tokens=_PROMPT_TOKENS,
        completion_tokens=7,
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=_CACHED_TOKENS),
    )
    creation, read = _read_cache_usage(usage)
    assert read == _CACHED_TOKENS
    assert creation == 0


def test_a_cache_write_is_read_off_the_nested_details() -> None:
    """`cache_write_tokens` is the creation counterpart the SDK carries beside `cached_tokens`."""
    usage = types.SimpleNamespace(
        prompt_tokens_details=types.SimpleNamespace(cache_write_tokens=4096, cached_tokens=0)
    )
    creation, read = _read_cache_usage(usage)
    assert creation == 4096
    assert read == 0


def test_a_usage_without_the_details_object_yields_zero_and_does_not_raise() -> None:
    """An endpoint that does not cache (or an older SDK) has no nested object at all.

    This is the shape an undeclared / Ollama-style endpoint produces, so it is also the
    honest-zero leg of the atom's "runs byte-identical with zeros" clause.
    """
    usage = types.SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    assert _read_cache_usage(usage) == (0, 0)


def test_an_explicitly_null_details_object_yields_zero() -> None:
    """The SDK types both nested fields Optional, so `None` is a real wire value."""
    usage = types.SimpleNamespace(prompt_tokens=100, prompt_tokens_details=None)
    assert _read_cache_usage(usage) == (0, 0)


def test_none_usage_yields_zero() -> None:
    """A chunk carrying no usage object at all — 0, no crash."""
    assert _read_cache_usage(None) == (0, 0)


def test_non_int_cache_values_are_ignored() -> None:
    """A malformed value is treated as 0 rather than propagated onto the event."""
    usage = types.SimpleNamespace(
        prompt_tokens_details=types.SimpleNamespace(cached_tokens=None, cache_write_tokens="lots")
    )
    assert _read_cache_usage(usage) == (0, 0)


def test_the_reader_matches_the_real_sdk_shape() -> None:
    """PREMISE FLOOR: the two field names are the installed SDK's, not invented ones.

    Every other test here drives a hand-built namespace, which cannot catch a typo in a
    vendor field name — a misspelled attr yields 0 through the same defensive path a
    non-caching endpoint takes, so the suite would stay green while the feature was inert.
    This reads the names off `openai`'s own model.
    """
    openai_types = pytest.importorskip("openai.types.completion_usage")
    fields = set(openai_types.PromptTokensDetails.model_fields)
    assert {"cached_tokens", "cache_write_tokens"} <= fields


# ── the disjointness normaliser ────────────────────────────────────────────────────────


def test_the_cached_span_is_subtracted_out_of_the_prompt_total() -> None:
    assert _uncached_prompt_tokens(_PROMPT_TOKENS, 0, _CACHED_TOKENS) == _UNCACHED_REMAINDER


def test_an_uncached_turn_is_the_prompt_total_unchanged() -> None:
    """With both buckets 0 the normaliser is the identity — today's value, exactly."""
    assert _uncached_prompt_tokens(_PROMPT_TOKENS, 0, 0) == _PROMPT_TOKENS


def test_both_buckets_are_subtracted() -> None:
    assert _uncached_prompt_tokens(1000, 400, 500) == 100


def test_an_over_wide_cached_span_clamps_to_zero_rather_than_going_negative() -> None:
    """A defensive floor: a negative token count would propagate into every consumer."""
    assert _uncached_prompt_tokens(100, 0, 4096) == 0


# ── driving the adapter ────────────────────────────────────────────────────────────────


class _Stream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "_Stream":
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _Completions:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _Stream:
        self.calls.append(kwargs)
        return _Stream(self._chunks)


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class _AsyncOpenAI:
    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self.chat = _Chat(_Completions([]))

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake ``openai`` module so the lazy SDK import resolves."""
    import sys

    fake = types.ModuleType("openai")
    fake.AsyncOpenAI = _AsyncOpenAI  # type: ignore[attr-defined]

    class _BadRequestError(Exception):
        pass

    fake.BadRequestError = _BadRequestError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake)


def _usage(prompt_tokens: int, completion_tokens: int, cached: int | None = None) -> Any:
    details = None if cached is None else types.SimpleNamespace(cached_tokens=cached)
    return types.SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=details,
    )


def _chunks(usage: Any) -> list[Any]:
    """A minimal two-chunk stream: one text delta, then the terminal usage chunk."""
    return [
        types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    delta=types.SimpleNamespace(content="ACK", tool_calls=None),
                    finish_reason=None,
                )
            ],
            usage=None,
        ),
        types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    delta=types.SimpleNamespace(content=None, tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=usage,
        ),
    ]


def _provider(chunks: list[Any]) -> Any:
    from personalclaw.llm.credentials import Credential
    from personalclaw.llm.openai import OpenAIProvider

    provider = OpenAIProvider(
        model=_MODEL,
        credential=Credential(name="x", kind="api_key", secret="sk-test", source="env"),
    )
    provider._client.chat = _Chat(_Completions(chunks))
    return provider


async def _terminal_event(provider: Any, *, stateful: bool = False) -> Any:
    from personalclaw.llm.base import EVENT_COMPLETE

    stream = (
        provider.stream("hello")
        if stateful
        else provider.complete([{"role": "user", "content": "hello"}])
    )
    events = [e async for e in stream]
    terminal = [e for e in events if e.kind == EVENT_COMPLETE]
    assert len(terminal) == 1, f"expected exactly one terminal event, got {len(terminal)}"
    return terminal[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("stateful", [False, True], ids=["complete", "stream"])
async def test_both_usage_sites_surface_the_vendor_cache_read(
    fake_openai: None, stateful: bool
) -> None:
    """THE PRODUCER, on both of the adapter's two usage sites.

    `stream()` and `complete()` each accumulate usage independently, exactly as the Anthropic
    adapter's twin sites do (`anthropic.py:548` / `:739`) — a fix applied to one only would
    leave the other reporting 0, so both are driven.
    """
    provider = _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=_CACHED_TOKENS)))
    event = await _terminal_event(provider, stateful=stateful)

    assert event.cache_read_tokens == _CACHED_TOKENS
    assert event.cache_creation_tokens == 0
    assert event.input_tokens == _UNCACHED_REMAINDER


@pytest.mark.asyncio
@pytest.mark.parametrize("stateful", [False, True], ids=["complete", "stream"])
async def test_an_uncached_response_reports_zeros_with_the_token_count_unchanged(
    fake_openai: None, stateful: bool
) -> None:
    """The undeclared / non-caching endpoint: zeros, and `input_tokens` is what it always was.

    This is the atom's "an undeclared/Ollama model runs byte-identical with zeros" clause on
    the read side: no nested details object means nothing is subtracted, so the number the
    turn reports is unchanged from before the producer existed.
    """
    provider = _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=None)))
    event = await _terminal_event(provider, stateful=stateful)

    assert event.cache_read_tokens == 0
    assert event.cache_creation_tokens == 0
    assert event.input_tokens == _PROMPT_TOKENS


@pytest.mark.asyncio
async def test_turn_one_creates_and_turn_two_reads(fake_openai: None) -> None:
    """The atom's turn1-creation / turns2+-read shape, on the vendor's own measured numbers.

    Turn 1's `cached_tokens=0` is the positive control for turn 2's 5120: a reader stuck high
    would report a read on the creation turn, and a dead one would report 0 on both.
    """
    first = await _terminal_event(_provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=0))))
    second = await _terminal_event(
        _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=_CACHED_TOKENS)))
    )

    assert first.cache_read_tokens == 0
    assert second.cache_read_tokens == _CACHED_TOKENS
    assert second.cache_read_tokens > first.cache_read_tokens

    # And the hit rate RISES, which is the number the atom names.
    def _pct(event: Any) -> float | None:
        return cache_hit_pct(
            cache_read_tokens=event.cache_read_tokens,
            cache_creation_tokens=event.cache_creation_tokens,
            input_tokens=event.input_tokens,
        )

    assert _pct(first) == 0.0
    assert _pct(second) == pytest.approx(_CACHED_TOKENS / _PROMPT_TOKENS * 100)


@pytest.mark.asyncio
async def test_no_cache_marker_of_any_kind_reaches_the_wire(fake_openai: None) -> None:
    """The other half of the atom's clause: the read is reported with NO marker sent.

    OpenAI's posture is AUTOMATIC, so surfacing the number must not have smuggled a request
    key in to get it. Pinned by exact kwarg set rather than by substring, so a future key
    named anything at all has to be reviewed here.
    """
    provider = _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=_CACHED_TOKENS)))
    await _terminal_event(provider)

    sent = provider._client.chat.completions.calls
    assert len(sent) == 1
    assert set(sent[0]) == {"model", "messages", "stream", "stream_options"}
    body = repr(sent[0])
    for vendor_token in ("cache_control", "ephemeral", "cachePoint", "cache"):
        assert vendor_token not in body, f"{vendor_token!r} reached the OpenAI wire"


@pytest.mark.asyncio
@pytest.mark.parametrize("stateful", [False, True], ids=["complete", "stream"])
async def test_the_context_gauge_still_measures_the_whole_served_prompt(
    fake_openai: None, stateful: bool
) -> None:
    """REGRESSION FLOOR on the subtraction: the model still SAW every cached token.

    `input_tokens` is now the uncached remainder, so a gauge reading it directly would show a
    5218-token prompt as 98 tokens — the context meter would collapse on exactly the turns the
    cache works best. The gauge reconstructs the whole prompt from the three buckets instead.
    """
    cached = await _terminal_event(
        _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=_CACHED_TOKENS))), stateful=stateful
    )
    uncached = await _terminal_event(
        _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=None))), stateful=stateful
    )

    assert cached.context_usage_pct == pytest.approx(uncached.context_usage_pct)
    assert cached.context_usage_pct is not None
    assert cached.context_usage_pct > 0


# ── the contract the normaliser exists to keep ─────────────────────────────────────────


class TestTheDisjointnessContract:
    """The three buckets that reach `LLMEvent` must partition the vendor's prompt exactly.

    Each test pairs the adapter's answer with the NAIVE overlapping copy — `input_tokens =
    prompt_tokens` alongside `cache_read_tokens = cached_tokens`, which is the literal shape a
    straight field copy produces. The naive leg is a firing positive control: it shows the
    consumer really does misreport when the contract is broken, so these are not assertions
    that hold for any input.
    """

    @pytest.mark.asyncio
    async def test_the_three_buckets_sum_to_the_vendor_prompt_total(
        self, fake_openai: None
    ) -> None:
        event = await _terminal_event(
            _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=_CACHED_TOKENS)))
        )
        assert (
            event.input_tokens + event.cache_read_tokens + event.cache_creation_tokens
            == _PROMPT_TOKENS
        )

    @pytest.mark.asyncio
    async def test_the_turn_is_not_billed_for_the_cached_span_twice(
        self, fake_openai: None
    ) -> None:
        """`estimate_cost` bills the buckets additively, so an overlap inflates the turn."""
        event = await _terminal_event(
            _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=_CACHED_TOKENS)))
        )
        actual = estimate_cost(
            _MODEL,
            event.input_tokens,
            event.output_tokens,
            event.cache_read_tokens,
            event.cache_creation_tokens,
        )
        naive = estimate_cost(_MODEL, _PROMPT_TOKENS, event.output_tokens, _CACHED_TOKENS, 0)

        # The honest bill: the uncached remainder at the input rate + the cached span at the
        # discounted read rate. Nothing counted twice. Rounded to 6dp because
        # `estimate_cost` rounds its result there (`pricing.py:115`).
        expected = round(
            (_UNCACHED_REMAINDER * 0.15 + 7 * 0.6 + _CACHED_TOKENS * 0.075) / 1_000_000, 6
        )
        assert actual == expected
        assert naive > actual, "the naive overlapping copy must overstate the bill"

    @pytest.mark.asyncio
    async def test_the_hit_rate_is_not_halved_by_a_double_counted_denominator(
        self, fake_openai: None
    ) -> None:
        """`cache_hit_pct`'s denominator is the sum of the three, so an overlap dilutes it."""
        event = await _terminal_event(
            _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=_CACHED_TOKENS)))
        )
        actual = cache_hit_pct(
            cache_read_tokens=event.cache_read_tokens,
            cache_creation_tokens=event.cache_creation_tokens,
            input_tokens=event.input_tokens,
        )
        naive = cache_hit_pct(
            cache_read_tokens=_CACHED_TOKENS, cache_creation_tokens=0, input_tokens=_PROMPT_TOKENS
        )

        assert actual == pytest.approx(_CACHED_TOKENS / _PROMPT_TOKENS * 100)
        assert actual is not None and naive is not None
        assert actual > naive, "the naive overlapping copy must understate the hit rate"

    @pytest.mark.asyncio
    async def test_the_saving_is_reported_and_positive_on_a_cache_hit(
        self, fake_openai: None
    ) -> None:
        """The number the atom exists to prove: a real, provider-reported, non-zero saving.

        `cache_savings_usd` returns `None` for an unpriced model, so a non-`None` here also
        pins that a priced OpenAI row is reachable from an event this adapter produced.
        """
        event = await _terminal_event(
            _provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=_CACHED_TOKENS)))
        )
        saved = cache_savings_usd(
            _MODEL,
            cache_read_tokens=event.cache_read_tokens,
            cache_creation_tokens=event.cache_creation_tokens,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
        )
        assert saved is not None
        assert saved > 0
        # read * (in_rate - cache_read_rate): the discount the vendor actually applied.
        assert saved == pytest.approx(_CACHED_TOKENS * (0.15 - 0.075) / 1_000_000, rel=1e-6)

    @pytest.mark.asyncio
    async def test_an_uncached_turn_saves_a_measured_zero_not_a_none(
        self, fake_openai: None
    ) -> None:
        """Honest-zero: a priced model with no cache activity saved 0.0, which is a real answer."""
        event = await _terminal_event(_provider(_chunks(_usage(_PROMPT_TOKENS, 7, cached=None))))
        saved = cache_savings_usd(
            _MODEL,
            cache_read_tokens=event.cache_read_tokens,
            cache_creation_tokens=event.cache_creation_tokens,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
        )
        assert saved == 0.0
