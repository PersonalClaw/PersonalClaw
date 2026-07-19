"""X2: the background session defers QUIETLY when no chat model resolves.

On a fresh install (no model configured) the lite background agent can't resolve a
ModelProvider, so the factory raises ProviderResolutionError. Before X2 this logged
a noisy WARNING traceback every startup; now it's a single INFO and the spawn is
skipped (self-healing on the next reload_provider_factory). A genuine error still
WARNs. The factory remains the single source of truth — no separate predictor.
"""

from __future__ import annotations

import logging

import pytest

from personalclaw.config import AppConfig
from personalclaw.providers.provider_bridge import ProviderResolutionError
from personalclaw.session import BACKGROUND_KEY, SessionManager


def _cfg() -> AppConfig:
    return AppConfig.load()


@pytest.mark.asyncio
async def test_defers_quietly_when_no_model_resolves(caplog):
    """Factory raises ProviderResolutionError ⇒ no background session, INFO not
    WARNING, and _ensure_background does NOT raise."""
    def factory(*_a, **_k):
        raise ProviderResolutionError("No provider configured for use case 'chat'.")

    mgr = SessionManager(_cfg(), provider_factory=factory)
    # Target the EMITTING logger explicitly (not root): under xdist/worksteal, a bare
    # caplog.at_level(INFO) attaches at root and can miss records when concurrent tests
    # disrupt propagation → an empty caplog.records flake. Pinning the logger captures
    # deterministically regardless of worker.
    with caplog.at_level(logging.INFO, logger="personalclaw.session"):
        await mgr._ensure_background()

    assert BACKGROUND_KEY not in mgr._sessions  # spawn skipped
    msgs = [r.getMessage() for r in caplog.records]
    assert any("deferred" in m.lower() for m in msgs), msgs
    # Must NOT have logged a WARNING-level "Failed to create" traceback.
    assert not any(
        r.levelno >= logging.WARNING and "Failed to create background session" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_genuine_error_still_warns(caplog):
    """A non-resolution failure (e.g. the provider's start() blows up) keeps the
    existing WARNING path — we only special-case the 'no model yet' signal."""
    class _BoomProvider:
        async def start(self):
            raise RuntimeError("socket exploded")

        async def shutdown(self):
            return None

    def factory(*_a, **_k):
        return _BoomProvider()

    mgr = SessionManager(_cfg(), provider_factory=factory)
    with caplog.at_level(logging.INFO, logger="personalclaw.session"):
        await mgr._ensure_background()

    assert BACKGROUND_KEY not in mgr._sessions
    assert any(
        r.levelno >= logging.WARNING and "Failed to create background session" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_creates_session_when_factory_succeeds(caplog):
    """Happy path unchanged: a working factory ⇒ background session created."""
    class _OkProvider:
        async def start(self):
            return None

        async def shutdown(self):
            return None

    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _OkProvider())
    await mgr._ensure_background()
    assert BACKGROUND_KEY in mgr._sessions
