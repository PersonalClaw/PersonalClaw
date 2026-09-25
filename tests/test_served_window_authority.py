"""ONE authority answers "what window will this turn be served with" — and it sees the fallback.

Three pieces of code used to answer that question for the same turn, and they disagreed:

* the ASSEMBLER (``model_windows.active_chat_model_window``) read only the chat binding. With
  nothing bound — every out-of-the-box user, answered by the bundled floor model — it said
  200,000; with a local binding it said a fixed 4,096 however much the runtime really served;
* the BUDGET CHECK (``context_headroom.check_for_model``) resolved the same binding, found none
  for the fallback (the floor is an in-memory registry entry, not a binding) and read the window
  as UNKNOWN, so every prompt passed — including a 40,000-character paste that OOM-killed a
  6 GB-capped gateway;
* the GAUGE divided by a third number: Ollama's ``/api/ps`` probe (32,768 on the measured host),
  or for the bundled model the WEIGHT's 8,192.

Measured with host Ollama serving 32,768: every turn told the user their model had "a 4,096-token
context window" and dropped the widget instructions — false on both counts.

The fix is one resolver (``context_headroom.resolve_window``) that asks the provider actually
serving the turn, and one ``Window`` every consumer reads. These tests pin that the four
consumers — the assembler, the budget check, the warning, and the gauge — agree, for the
unbound fallback and for a probed local runtime.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    load_bundle_module,
    namespaced_module_name,
)
from personalclaw.context import USER_REQUEST_MARKER, ContextBuilder
from personalclaw.context_headroom import HeadroomState, Window, check, resolve_window
from personalclaw.llm.registry import ProviderRegistry

_BUNDLED = "bundled-chat"
_OLLAMA = "ollama-models"


# ── fixtures ────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def bundled(tmp_path, monkeypatch):
    """The bundled app's module, a tiny weight, and a home where it is FETCHED and nothing is bound.

    Settings pin a 48-token window with an 8-token reply reserve, because the fixture weight
    itself only declares 64 positions — so every number below is the configured one, not a clamp.
    """
    from test_bundled_chat_provider import sign_off, tiny_gguf

    rail = load_bundle_module(NATIVE_DIR / _BUNDLED, _BUNDLED, "provider")
    rail.reset_loaded_model()
    rail.reset_declaration_cache()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(rail, "config_dir", lambda: home)
    weight, _ = tiny_gguf(tmp_path / "weights")
    sign_off(rail, monkeypatch, home, weight)
    settings = {"context_tokens": 48, "max_output_tokens": 8}
    monkeypatch.setattr(rail.ProviderSettings, "load", staticmethod(lambda _name: dict(settings)))
    yield rail
    rail.reset_loaded_model()
    rail.reset_declaration_cache()


@pytest.fixture()
def floor_registry(bundled, monkeypatch):
    """A default registry holding ONLY the bundled floor entry — the fresh-install shape."""
    registry = ProviderRegistry()
    monkeypatch.setattr(bundled, "get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    assert bundled.register() is True
    from personalclaw.local_models import registry as local_registry

    local_registry.register_provider(
        bundled.create_provider(bundled.ProviderSettings.load(_BUNDLED)), ["chat"], name=_BUNDLED
    )
    yield registry
    local_registry.unregister_provider(_BUNDLED)


@pytest.fixture()
def ollama():
    name = namespaced_module_name(_OLLAMA, "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / _OLLAMA, _OLLAMA, "provider")
    finally:
        sys.modules.pop(name, None)


def _fake_ps(served: int):
    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"models": [{"name": "gemma4:12b", "context_length": served}]}

    async def _get(url: str, *a, **k):
        assert url == "/api/ps", url
        return _Resp()

    return _get


def _runtime(model_provider, model: str = "") -> NativeAgentRuntime:
    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="t", provider="native", model=model),
        model_provider=model_provider,
    )


def _native_runtime_for_unbound_chat():
    """The native runtime the chat runner gets on a fresh install — through the REAL resolution."""
    from personalclaw.providers import provider_bridge

    return provider_bridge._build_native_runtime(
        use_case="chat", session_key="dashboard:fresh", agent=None, model_override=None, cwd=None
    )


def _spy_assembler_windows(monkeypatch) -> list:
    """Every window the ASSEMBLER budgets by, as it asks for it."""
    import personalclaw.context as ctx

    seen: list = []
    real_caps, real_affordable = ctx._memory_caps, ctx._widget_guidance_affordable

    def caps(window):  # noqa: ANN001, ANN202
        seen.append(window)
        return real_caps(window)

    def affordable(window):  # noqa: ANN001, ANN202
        seen.append(window)
        return real_affordable(window)

    monkeypatch.setattr(ctx, "_memory_caps", caps)
    monkeypatch.setattr(ctx, "_widget_guidance_affordable", affordable)
    return seen


# ── the fallback is visible ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_unbound_fallback_is_the_model_whose_window_governs_the_turn(floor_registry):
    """Nothing is bound and the bundled floor answers: the resolver must name IT, with ITS window.

    Before this, the fallback was not a binding, so the budget check read the window as unknown
    and passed every prompt — which is how an ordinary paste reached a quadratic prefill.
    """
    runtime = _native_runtime_for_unbound_chat()
    window = await resolve_window("auto", serving=runtime)
    assert window.ref == "bundled-chat:tiny"
    assert window.tokens == 48, window
    assert window.output_reserve_tokens == 8
    assert window.input_tokens == 40
    assert window.request_only is True
    assert window.measured


def test_the_build_stamps_the_ref_it_was_built_for(floor_registry):
    """The one place that knows both halves of the served ref — entry and model — records them.

    Everything downstream (the label a refusal prints, the catalog card the reserve comes from)
    reads this instead of re-deriving the resolution, which is a chain walk with breakers and
    routing in it and would drift the moment it was copied.
    """
    from personalclaw.providers import provider_bridge

    built = provider_bridge._resolve_from_config_registry("chat")
    assert built.served_ref == "bundled-chat:tiny"


# ── a probed served window reaches the prompt builder (bug 6) ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("served, keeps_widgets", [(32_768, True), (4_096, False)])
async def test_a_probed_served_window_reaches_the_prompt_builder(ollama, served, keeps_widgets):
    """Ollama serving 32,768 was told it had 4,096 — the probe fed only the gauge."""
    provider = ollama.OllamaProvider(model="gemma4:12b", endpoint="http://127.0.0.1:11434")
    provider._client.get = _fake_ps(served)
    provider.served_ref = "Ollama:gemma4:12b"
    window = await resolve_window("auto", serving=_runtime(provider, "gemma4:12b"))
    assert window.tokens == served and window.source == "served"
    assert window.ref == "Ollama:gemma4:12b"
    assert window.request_only is False

    notices: list[str] = []
    message, _ = ContextBuilder().build_message(
        "hello", False, "dashboard:t", window=window, notices_out=notices
    )
    assert ("[WIDGETS]" in message) is keeps_widgets
    widget_notes = [n for n in notices if "widget" in n.lower()]
    if keeps_widgets:
        assert widget_notes == [], widget_notes
    else:
        assert len(widget_notes) == 1
        assert f"{served:,}-token" in widget_notes[0]
        assert "gemma4:12b" in widget_notes[0]
        assert "the bound model" not in widget_notes[0]


# ── the four consumers agree ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("served", [6_000, 32_768])
async def test_the_four_consumers_agree_on_a_probed_local_runtime(ollama, monkeypatch, served):
    """Assembler, budget check, warning and gauge all read the one resolved window."""
    provider = ollama.OllamaProvider(model="gemma4:12b", endpoint="http://127.0.0.1:11434")
    provider._client.get = _fake_ps(served)
    provider.served_ref = "Ollama:gemma4:12b"
    window = await resolve_window("auto", serving=_runtime(provider, "gemma4:12b"))

    assembled_at = _spy_assembler_windows(monkeypatch)
    components, notices = [], []
    ContextBuilder().build_message(
        "hello",
        True,
        "dashboard:t",
        window=window,
        components_out=components,
        notices_out=notices,
    )
    # 1. the assembler
    assert assembled_at and set(assembled_at) == {served}, assembled_at
    # 2. the budget check
    verdict = check(components, window=window)
    assert verdict.window.tokens == served
    # 3. the warning — every number a notice prints about the window is this one
    for note in [*notices, verdict.notice()]:
        for figure in ("4,096", "8,192" if served > 8_192 else "\0"):
            if f"{figure}-token" in note:
                assert figure == f"{served:,}", note
    # 4. the gauge
    pct = await provider._context_pct("gemma4:12b", served // 2, [{"role": "user", "content": "x"}])
    assert pct == pytest.approx(50.0, abs=0.01)


@pytest.mark.asyncio
async def test_the_four_consumers_agree_on_the_unbound_bundled_floor(floor_registry, monkeypatch):
    """The out-of-the-box path, end to end on the real bundled provider."""
    runtime = _native_runtime_for_unbound_chat()
    window = await resolve_window("auto", serving=runtime)

    assembled_at = _spy_assembler_windows(monkeypatch)
    components, notices = [], []
    message, _ = ContextBuilder().build_message(
        "abc abc",
        True,
        "dashboard:t",
        window=window,
        components_out=components,
        notices_out=notices,
    )
    # 1. the assembler: a request-only model is sent the request and nothing to budget
    assert assembled_at == [], "context was budgeted for a model that is sent none of it"
    assert message == f"{USER_REQUEST_MARKER}\nabc abc"
    # 2. the budget check measures exactly what the provider will be handed
    verdict = check(components, window=window)
    assert verdict.state is HeadroomState.FITS
    assert verdict.window.input_tokens == 40
    # 3. no warning claims a window the model does not have
    assert notices == []
    # 4. the gauge: the provider divides the prompt it prefilled by the SAME window
    model_provider = runtime.model_provider
    events = [e async for e in model_provider.complete([{"role": "user", "content": message}])]
    prefilled = events[-1].input_tokens
    assert prefilled > 0
    assert model_provider.context_usage_pct() == pytest.approx(prefilled / window.tokens * 100)


# ── the request is always framed, and a request-only model gets only the request ─────────


def test_the_marker_frames_the_request_on_a_follow_up_turn_too():
    """Follow-up turns carried no marker when nothing else was injected, so the dashboard's
    widget guidance reached the small model unframed and it answered THAT: "capital of France"
    → Paris on turn 1, Tailwind HTML on turn 2."""
    message, _ = ContextBuilder().build_message("capital of France?", False, "dashboard:t")
    assert message.startswith(f"{USER_REQUEST_MARKER}\ncapital of France?"), message


def test_a_small_window_gets_no_widget_guidance_on_a_follow_up_either():
    window = Window(
        tokens=4096,
        output_reserve_tokens=320,
        input_tokens=3776,
        source="served",
        ref="Ollama:gemma4:12b",
    )
    message, _ = ContextBuilder().build_message("hi", False, "dashboard:t", window=window)
    assert "[WIDGETS]" not in message
    assert message == f"{USER_REQUEST_MARKER}\nhi"


def test_a_request_only_model_is_sent_the_request_and_nothing_else(monkeypatch):
    """What is assembled, measured and recorded is what the model receives — so no skill is
    recorded as "used" by a model that was never shown it (item 4)."""
    builder = ContextBuilder()
    monkeypatch.setattr(builder.skills, "get_surfaced_skills", lambda _text: ["git-review"])
    monkeypatch.setattr(builder.skills, "load_skill", lambda _name: "# git review\nsteps")
    window = Window(
        tokens=4096,
        output_reserve_tokens=320,
        input_tokens=3776,
        source="served",
        ref="bundled-chat:SmolLM2-135M-Instruct-Q8_0",
        request_only=True,
    )
    components, decisions, notices = [], [], []
    message, _ = builder.build_message(
        "how do I review this diff?",
        True,
        "dashboard:t",
        window=window,
        components_out=components,
        skill_decisions_out=decisions,
        notices_out=notices,
    )
    assert message == f"{USER_REQUEST_MARKER}\nhow do I review this diff?"
    assert [c.name for c in components] == ["request header", "the user's request"]
    assert decisions == []
    assert notices == []


# ── the refusal says what the user can do ──────────────────────────────────────────────


def test_a_message_that_cannot_fit_is_refused_with_the_model_its_limit_and_the_fix():
    """The error IS the product here: it replaced a 4-minute silence, a dead gateway, and the
    raw numpy text "Unable to allocate 26.0 GiB for an array with shape (9, 27862, 27862)"."""
    paste = "The quick brown fox jumps over the lazy dog. " * 900  # ~40,000 characters
    components: list = []
    ContextBuilder().build_message(paste, False, "dashboard:t", components_out=components)
    window = Window(
        tokens=4096,
        output_reserve_tokens=320,
        input_tokens=3776,
        source="served",
        ref="bundled-chat:SmolLM2-135M-Instruct-Q8_0",
    )
    verdict = check(components, window=window)
    assert verdict.state is HeadroomState.CANNOT_FIT
    text = verdict.notice()
    assert "SmolLM2-135M-Instruct-Q8_0" in text, text
    assert "tokens" in text and "characters" in text, text
    assert f"{len(paste):,} characters" in text, text
    assert "Shorten it" in text and "Settings → Models" in text, text


# ── the resolver never costs a turn ─────────────────────────────────────────────────────


class _Stuck:
    request_only = False
    served_ref = "Somewhere:slow-model"

    async def served_context_window(self) -> int | None:
        await asyncio.sleep(3600)
        return 1

    async def complete(self, messages, **_kw):  # pragma: no cover - never driven
        yield None


class _Broken(_Stuck):
    async def served_context_window(self) -> int | None:
        raise RuntimeError("probe exploded")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_cls", [_Stuck, _Broken])
async def test_a_stuck_or_broken_probe_degrades_to_the_static_answer(provider_cls, monkeypatch):
    import personalclaw.context_headroom as headroom

    monkeypatch.setattr(headroom, "SERVED_WINDOW_PROBE_TIMEOUT_SECS", 0.05)
    window = await asyncio.wait_for(
        resolve_window("auto", serving=_runtime(provider_cls())), timeout=5
    )
    assert window.ref == "Somewhere:slow-model"
    assert window.source != "served"


def test_a_mock_that_answers_everything_is_not_mistaken_for_a_request_only_model():
    """Chat-runner tests drive turns with ``AsyncMock`` clients, which answer ANY attribute with a
    truthy mock. The resolver must take ``request_only`` only from a real ``True``."""
    from unittest.mock import AsyncMock

    window = asyncio.run(resolve_window("auto", serving=AsyncMock()))
    assert window.request_only is False


def test_the_old_resolution_paths_are_gone():
    """Clean break: the three answers are one answer."""
    import personalclaw.context_headroom as headroom
    import personalclaw.model_windows as windows

    assert not hasattr(windows, "active_chat_model_window")
    assert not hasattr(headroom, "check_for_model")
    assert not hasattr(headroom, "bound_model_ref")


def test_the_base_contract_defaults_keep_every_other_provider_unchanged():
    """``request_only`` defaults to False and ``served_context_window`` to "cannot say", so every
    existing provider keeps its full context and its table-derived window."""
    from personalclaw.llm.base import ModelProvider

    assert ModelProvider.request_only is False
    assert ModelProvider.served_ref == ""
    unbound = ModelProvider.served_context_window
    assert asyncio.run(unbound(object())) is None  # type: ignore[arg-type]
