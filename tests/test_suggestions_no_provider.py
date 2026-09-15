"""A pre-onboarding instance with no provider bound must degrade quietly, like every other
"can't generate yet" path in ``generate_suggestions`` — not spam ``gateway.log`` with a
``ProviderResolutionError`` traceback on every ``/api/suggestions`` poll.

The bug (issue #2866): ``generate_suggestions`` acquired the background session
(``state.sessions.get_or_create(BACKGROUND_KEY)``) OUTSIDE the try-block that returns the
fallback list on its other degradation paths (empty context, unresolved prompt, stream
timeout). On a fresh install with no model bound that acquire raises
``ProviderResolutionError``, which

  1. reached ``refresh_suggestions``' broad ``except Exception`` and logged a full ``exc_info``
     traceback on EVERY poll; and
  2. left ``cache.generated_at == 0`` (the timestamp is only set AFTER ``generate_suggestions``
     returns), so ``api_suggestions``' ``generated_at == 0`` branch re-ran generation on EVERY
     poll rather than once per 30-minute interval — and the empty-chat SPA polls continuously.

🪤 Two distinct classes carry the "no model configured" signal (the LLM registry's and the
provider bridge's ``ProviderResolutionError``), so every case here is parametrised over both:
catching only one is a fix with a hole, exactly as ``session.py`` and ``cli.py`` note.
"""

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from personalclaw import suggestions
from personalclaw.llm.registry import ProviderResolutionError as LLMResolveErr
from personalclaw.providers.provider_bridge import ProviderResolutionError as BridgeResolveErr

_ERROR_CLASSES = [
    pytest.param(LLMResolveErr, id="llm-registry"),
    pytest.param(BridgeResolveErr, id="provider-bridge"),
]


@pytest.fixture
def state_with_context(tmp_path, monkeypatch):
    """A DashboardState stand-in that DOES have enough context to earn an LLM turn.

    ``_build_context`` returns a non-empty ``## User Preferences`` section (preferences that
    differ from the pristine template), so ``generate_suggestions`` gets past the
    ``len(context) < 50`` guard and reaches ``state.sessions.get_or_create`` — the line under
    test. ``config_dir`` is pinned at ``tmp_path`` so the automations read cannot touch the real
    ``~/.personalclaw``. ``get_or_create`` is left unset here; each test installs the raising one.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    memory = SimpleNamespace(
        read_preferences=lambda: (
            "# User Preferences\n\nI prefer concise, direct answers and I work mostly in "
            "Python and Rust. Skip preamble."
        ),
        read_projects=lambda: "# Active Projects\n\n<!-- Current work context -->",
        read_recent_history=lambda days=2: "",
    )
    with patch("personalclaw.context.ContextBuilder.get_memory_for", return_value=memory):
        yield SimpleNamespace(
            conversation_log=None,
            sessions=SimpleNamespace(get_or_create=None, release=lambda *a, **k: None),
            _background_tasks=set(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", _ERROR_CLASSES)
async def test_context_earns_a_turn_but_no_provider_bound(state_with_context, exc_cls):
    """Sanity: the context here is long enough to reach ``get_or_create`` (not the empty-context
    fast path). If this ever stops holding, the tests below would pass vacuously."""
    with patch("personalclaw.suggestions.datetime") as dt:
        dt.now.return_value = __import__("datetime").datetime(2026, 5, 1, 7, 30)
        ctx = suggestions._build_context(state_with_context)
    assert (
        len(ctx) >= 50
    ), f"fixture context is {len(ctx)} chars — it would hit the empty-context path"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", _ERROR_CLASSES)
async def test_no_provider_returns_fallback_instead_of_raising(state_with_context, exc_cls):
    """``generate_suggestions`` must swallow the unbound-provider signal and return the fallback,
    exactly like its empty-context / unresolved-prompt / timeout paths."""

    async def _raise(*a, **k):
        raise exc_cls("no chat model configured yet")

    state_with_context.sessions.get_or_create = _raise
    with patch(
        "personalclaw.prompt_providers.runtime.render_use_case_prompt", return_value="PROMPT"
    ):
        got = await suggestions.generate_suggestions(state_with_context)
    assert got == suggestions._FALLBACK_SUGGESTIONS


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", _ERROR_CLASSES)
async def test_refresh_logs_no_traceback_and_advances_generated_at(
    state_with_context, exc_cls, caplog
):
    """The two symptoms in one place: no WARNING traceback in the log, and ``generated_at`` is
    advanced off zero so the endpoint stops re-generating on every poll."""

    async def _raise(*a, **k):
        raise exc_cls("no chat model configured yet")

    state_with_context.sessions.get_or_create = _raise
    cache = suggestions.SuggestionsCache()
    assert cache.generated_at == 0.0

    with (
        patch(
            "personalclaw.prompt_providers.runtime.render_use_case_prompt", return_value="PROMPT"
        ),
        caplog.at_level(logging.WARNING, logger="personalclaw.suggestions"),
    ):
        await suggestions.refresh_suggestions(state_with_context, cache)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings, (
        "an unbound provider logged a WARNING on the suggestions refresh path — this is the "
        f"traceback that spams gateway.log on every poll: {[r.getMessage() for r in warnings]}"
    )
    assert "ProviderResolutionError" not in caplog.text
    assert cache.suggestions == suggestions._FALLBACK_SUGGESTIONS
    assert cache.generated_at > 0.0, (
        "generated_at stayed at 0 after a fallback refresh, so api_suggestions' "
        "`generated_at == 0` branch will re-run generation on every single poll"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", _ERROR_CLASSES)
async def test_endpoint_does_not_regenerate_on_every_poll(state_with_context, exc_cls):
    """End to end through ``api_suggestions``: with no provider bound, generation runs at most
    once across two consecutive polls — not once per poll. Counted via ``get_or_create``."""
    calls = {"n": 0}

    async def _raise(*a, **k):
        calls["n"] += 1
        raise exc_cls("no chat model configured yet")

    state_with_context.sessions.get_or_create = _raise
    request = SimpleNamespace(app={"state": state_with_context}, query={})

    with patch(
        "personalclaw.prompt_providers.runtime.render_use_case_prompt", return_value="PROMPT"
    ):
        await suggestions.api_suggestions(request)  # first poll — generation may run once
        await suggestions.api_suggestions(request)  # second poll — must NOT re-generate

    assert calls["n"] == 1, (
        f"generation ran {calls['n']} times across two polls; the second poll re-ran it because "
        "generated_at was never advanced off zero on the fallback path"
    )
    cache = suggestions.get_suggestions_cache(state_with_context)
    assert cache.suggestions == suggestions._FALLBACK_SUGGESTIONS
