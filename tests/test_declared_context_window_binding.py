"""Rail: a binding's declared ``context_window`` is read, and never reaches the wire (#2364).

The conservative local default is a guess about someone else's deployment, so it needs an
escape hatch: an operator who raised Ollama's ``num_ctx`` (or is serving a model with a
smaller window than its architecture allows) declares the served window on the binding, in
the same free-form ``entry.options`` bag that already carries ``embedding_model`` and
``max_tokens``.

That bag has one hazard, and it is why this file exists: **whatever is left in
``_extra_options`` is copied verbatim onto the SDK request kwargs**, so a key that is not a
wire parameter must be POPPED in ``__init__`` or it rides into the request body and dies in
the vendor's validator. ``embedding_model`` and ``max_tokens`` are popped for exactly this
reason; ``context_window`` is the third, and the assertions below are on the captured
request dict rather than on the attribute alone, because the attribute being right is not
the part that breaks.

Coercion is `model_windows.declared_context_window` — one reader, so a provider cannot
disagree with the resolver about whether ``0`` means "a window of zero" (it means
undeclared; a zero would reach a caller's ``chars / window`` and divide by it).
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from personalclaw.llm.credentials import Credential

pytestmark = pytest.mark.asyncio


def _cred() -> Credential:
    return Credential(name="x", kind="api_key", secret="sk-test", source="env")


# ── openai: request kwargs come from chat.completions.create ───────────────────


class _FakeStream:
    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _FakeChatCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeStream:
        self.calls.append(kwargs)
        return _FakeStream()


class _FakeChat:
    def __init__(self, completions: _FakeChatCompletions) -> None:
        self.completions = completions


class _FakeAsyncOpenAI:
    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url


@pytest.fixture
def fake_openai_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Stand in for the OPTIONAL ``openai`` SDK so ``OpenAIProvider`` can be built.

    ``[dev]`` does not pull the provider SDKs (and CI's ``uv sync --locked --extra dev``
    does not either), so a test that constructs the provider for real raises
    ``MissingSDKError``. ``__init__`` imports openai lazily and needs only
    ``AsyncOpenAI``; ``complete()`` additionally references ``openai.BadRequestError``
    in its ``stream_options`` fallback. Mirrors ``fake_openai`` in
    test_model_provider_complete.py.
    """
    fake = types.ModuleType("openai")
    fake.AsyncOpenAI = _FakeAsyncOpenAI  # type: ignore[attr-defined]

    class _BadRequestError(Exception):
        pass

    fake.BadRequestError = _BadRequestError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake)
    return fake


# ── anthropic: request kwargs come from messages.stream ───────────────────────


class _FakeStreamIter:
    def __aiter__(self) -> "_FakeStreamIter":
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _FakeStreamCM:
    async def __aenter__(self) -> _FakeStreamIter:
        return _FakeStreamIter()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStreamCM:
        self.calls.append(kwargs)
        return _FakeStreamCM()


class _FakeAsyncAnthropic:
    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self.messages = _FakeMessages()

    async def close(self) -> None:  # pragma: no cover - not driven here
        return None


@pytest.fixture
def fake_anthropic_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = _FakeAsyncAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    return fake


def _openai(options: dict | None):
    from personalclaw.llm.openai import OpenAIProvider

    return OpenAIProvider(model="llama3.1:8b", credential=_cred(), extra_options=options)


def _anthropic(options: dict | None):
    from personalclaw.llm.anthropic import AnthropicProvider

    return AnthropicProvider(model="claude-x", credential=_cred(), extra_options=options)


# ── the attribute the consumers read ──────────────────────────────────────────


@pytest.mark.parametrize("factory", ["openai", "anthropic"])
async def test_a_declared_window_is_exposed(factory, fake_openai_module, fake_anthropic_module):
    provider = (_openai if factory == "openai" else _anthropic)({"context_window": 8192})
    assert provider.context_window == 8192


@pytest.mark.parametrize("factory", ["openai", "anthropic"])
@pytest.mark.parametrize("options", [None, {}, {"embedding_model": "e5"}])
async def test_undeclared_is_None(factory, options, fake_openai_module, fake_anthropic_module):
    """``None`` is the "resolve from the table as usual" signal every consumer branches on."""
    provider = (_openai if factory == "openai" else _anthropic)(options)
    assert provider.context_window is None


@pytest.mark.parametrize("factory", ["openai", "anthropic"])
@pytest.mark.parametrize("raw", [0, -1, True, "8192", "", "auto", None, 3.9])
async def test_junk_is_coerced_by_the_one_shared_reader(
    factory, raw, fake_openai_module, fake_anthropic_module
):
    """Same coercion as the resolver's, because it IS the resolver's: ``0`` and ``True``
    mean undeclared, a float truncates rather than dividing a caller by a non-int, and a
    NUMERIC string declares — that last one is not a convenience, it is the only shape the
    write path produces (Settings builds ``options`` as string→string), so a reader that
    rejected it left this override with no reader on its one user-facing path."""
    from personalclaw.model_windows import declared_context_window

    provider = (_openai if factory == "openai" else _anthropic)({"context_window": raw})
    assert provider.context_window == declared_context_window(raw)


# ── the hazard: it must not reach the request ─────────────────────────────────


async def test_openai_does_not_forward_it_to_the_sdk(fake_openai_module):
    provider = _openai({"context_window": 8192, "temperature": 0.1})
    fake = _FakeChatCompletions()
    provider._client.chat = _FakeChat(fake)
    async for _ in provider.complete([{"role": "user", "content": "hi"}]):
        pass
    assert len(fake.calls) == 1
    kwargs = fake.calls[0]
    assert "context_window" not in kwargs, "a non-wire key rode into the request body"
    # The control: a REAL extra option is still forwarded, so the pop is targeted and this
    # test would catch "stopped forwarding extra_options at all" as well.
    assert kwargs["temperature"] == 0.1


async def test_anthropic_does_not_forward_it_to_the_sdk(fake_anthropic_module):
    provider = _anthropic({"context_window": 8192, "top_k": 5})
    fake = _FakeMessages()
    provider._client.messages = fake
    async for _ in provider.complete([{"role": "user", "content": "hi"}]):
        pass
    assert len(fake.calls) == 1
    kwargs = fake.calls[0]
    # Extra options ride the request as body fields (`extra_body`), which is where to look for
    # the control — `anthropic` 1.x takes no `top_k` keyword.
    body = kwargs.get("extra_body") or {}
    assert "context_window" not in kwargs and "context_window" not in body
    assert body["top_k"] == 5


# ── the half that was missing: it has to reach the MEASURED gauge ─────────────
#
# The attribute was read and the wire was clean, and the override still changed nothing a
# user could see: both measured gauges called ``model_context_window(model, default)``
# POSITIONALLY, and ``local``/``override`` are keyword-only, so no value of the declared
# window could reach the percentage the composer renders or the number the native loop's
# compaction gate compares. The declaration only steered the char ESTIMATE — the path that
# runs when the provider reports nothing — which is precisely the path an endpoint that DOES
# report usage never takes. So for local runtimes like Ollama, which report
# ``prompt_eval_count`` on every turn, the escape hatch was inert exactly where it mattered.
#
# ``override=`` and NOT ``local=`` at these two call sites: an operator's declaration is a
# truth claim and belongs in a measurement, while ``LOCAL_SERVED_CONTEXT_WINDOW`` is a
# conservative floor for an estimate. Substituting the floor into a real token count would
# report 651% for the measured 26682-token turn — a fabrication in the opposite direction.


def _usage_chunks(prompt_tokens: int):
    """An OpenAI-shaped stream whose terminal chunk reports usage."""
    delta = types.SimpleNamespace(content=None, tool_calls=None)
    choice = types.SimpleNamespace(delta=delta, finish_reason="stop")
    usage = types.SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=1)
    return [types.SimpleNamespace(choices=[choice], usage=usage)]


class _UsageStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> "_UsageStream":
        return self

    async def __anext__(self) -> Any:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _UsageCompletions:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _UsageStream:
        self.calls.append(kwargs)
        return _UsageStream(self._chunks)


async def _openai_measured_pct(options: dict | None, prompt_tokens: int) -> float | None:
    """Drive a real ``complete()`` and return the percentage it reported."""
    from personalclaw.llm.base import EVENT_COMPLETE

    provider = _openai(options)
    provider._client.chat = _FakeChat(_UsageCompletions(_usage_chunks(prompt_tokens)))
    events = [e async for e in provider.complete([{"role": "user", "content": "hi"}])]
    terminal = [e for e in events if e.kind == EVENT_COMPLETE]
    assert len(terminal) == 1
    assert terminal[0].input_tokens == prompt_tokens
    return terminal[0].context_usage_pct


#: The measured Ollama turn from #2364: real prompt tokens against the window `/api/ps`
#: reported. The model id is ``llama3.1:8b``, whose TABLE entry is the architectural 128000.
_MEASURED_PROMPT_TOKENS = 26_682
_SERVED_WINDOW = 32_768


async def test_a_declared_window_reaches_the_measured_gauge(fake_openai_module):
    pct = await _openai_measured_pct({"context_window": _SERVED_WINDOW}, _MEASURED_PROMPT_TOKENS)
    assert pct == pytest.approx(_MEASURED_PROMPT_TOKENS / _SERVED_WINDOW * 100)
    assert pct is not None and pct > 70.0, (
        "the declared window has to put this turn OVER the compaction threshold — that "
        "consequence, not the arithmetic, is what the defect suppressed"
    )


async def test_the_string_the_ui_writes_reaches_the_measured_gauge(fake_openai_module):
    """End to end in the shape a user actually produces: the form writes ``"32768"``."""
    pct = await _openai_measured_pct(
        {"context_window": str(_SERVED_WINDOW)}, _MEASURED_PROMPT_TOKENS
    )
    assert pct == pytest.approx(_MEASURED_PROMPT_TOKENS / _SERVED_WINDOW * 100)


@pytest.mark.parametrize("options", [None, {}, {"temperature": 0.1}, {"context_window": 0}])
async def test_an_absent_declaration_leaves_the_gauge_byte_identical(options, fake_openai_module):
    """THE NEGATIVE CONTROL. With nothing declared, the gauge must be exactly the table
    lookup it has always been — a change that moves the default path is a different
    change, and this is the assertion that fails if ``local=`` is ever passed here (it
    would divide 26682 by 4096 and report 651%) or if the floor leaks in some other way.
    Computed from ``model_context_window`` rather than a literal so it tracks the table.
    """
    from personalclaw.model_windows import resolved_context_window

    expected_window = resolved_context_window("llama3.1:8b")
    assert expected_window == 128_000, "the table moved; the control's premise is stale"
    pct = await _openai_measured_pct(options, _MEASURED_PROMPT_TOKENS)
    assert pct == pytest.approx(_MEASURED_PROMPT_TOKENS / expected_window * 100)
    assert pct is not None and pct < 70.0, (
        "unchanged means unchanged: the architectural window still hides this overflow, "
        "which is why the declaration (or a served-window probe) is the fix and not a "
        "bigger numerator"
    )


# ── the override is LIVE-EFFECTIVE on a model the table has never heard of ─────
#
# The gap this section closes. #2364 was closed COMPLETED and then reopened as a false
# success on the finding that "an operator who declares ``context_window: 32768`` changes
# nothing the product reports". The declaration did start reaching the gauge (PR #3323),
# but every test that proved it used ``llama3.1:8b`` — a model the TABLE lists, so the
# undeclared arm had a window either way and the assertions could not tell "the override
# was read" apart from "a window was found". `gemma4`, the model the issue was actually
# measured on, is absent from ``model_tokens.json``, and on that binding the override is
# the ONLY thing that can produce a window at all. So this pair is the live-effect proof:
# same provider, same turn, same token report — one variable.


def _openai_unlisted(options: dict | None):
    from personalclaw.llm.openai import OpenAIProvider

    return OpenAIProvider(model="gemma4:12b", credential=_cred(), extra_options=options)


async def _unlisted_measured_pct(options: dict | None, prompt_tokens: int) -> float | None:
    from personalclaw.llm.base import EVENT_COMPLETE

    provider = _openai_unlisted(options)
    provider._client.chat = _FakeChat(_UsageCompletions(_usage_chunks(prompt_tokens)))
    events = [e async for e in provider.complete([{"role": "user", "content": "hi"}])]
    terminal = [e for e in events if e.kind == EVENT_COMPLETE]
    assert len(terminal) == 1
    return terminal[0].context_usage_pct


async def test_the_model_the_issue_was_measured_on_is_still_unlisted():
    """Premise control. If the table ever lists a ``gemma*`` entry, the two tests below
    stop measuring what they claim to and must move to another unlisted model."""
    from personalclaw.model_windows import resolved_context_window

    assert resolved_context_window("gemma4:12b") is None


async def test_an_unlisted_binding_reports_NOTHING_until_the_window_is_declared(
    fake_openai_module,
):
    """Half one. No table entry and no declaration ⇒ no number — where the shipped code
    divided by ``_DEFAULT_CONTEXT_WINDOW = 128_000`` and rendered a confident 25.19% peak."""
    assert await _unlisted_measured_pct(None, _MEASURED_PROMPT_TOKENS) is None


async def test_the_declaration_ALONE_makes_the_live_gauge_read_and_cross_the_gate(
    fake_openai_module,
):
    """Half two, and the assertion #2364 lacked: the override changes the LIVE value.

    Same provider class, same 26682-token report, one variable — the declaration. The
    inert-control half above proves the number came from the declaration and not from a
    fallback, and the threshold assertion proves the consequence rather than the
    arithmetic: it is the compaction gate, not the percentage, that the operator cares
    about.
    """
    from personalclaw.agents.native.runtime import NativeAgentRuntime

    pct = await _unlisted_measured_pct({"context_window": "32768"}, _MEASURED_PROMPT_TOKENS)
    assert pct == pytest.approx(_MEASURED_PROMPT_TOKENS / _SERVED_WINDOW * 100)
    assert pct is not None and pct >= NativeAgentRuntime._COMPACT_THRESHOLD_PCT
