"""Shared pytest configuration and fixtures."""

import asyncio
import os
import shutil

import pytest
from hypothesis import HealthCheck, settings

# NOTE: this suite is standalone — it must collect + pass on a clone of this
# package alone, with NO sibling apps/ directory. Channel/provider seams are
# exercised against in-tree fakes (tests/fakes.py); tests of app-INTERNAL
# behavior (slack_runtime, the ollama provider module) live with their apps
# (apps/slack-channel/tests/, apps/ollama-models/tests/). Workspace-layout
# tests (apps import-boundary lint, ACP bundles, web-tools app wiring) skip
# themselves when apps/ is absent.

# ── Hypothesis profiles ─────────────────────────────────────────────────
# Default (CI): fast iteration.  Run ``HYPOTHESIS_PROFILE=thorough make build test``
# for deeper coverage.
settings.register_profile("default", max_examples=20, suppress_health_check=[HealthCheck.too_slow], deadline=None)
settings.register_profile("thorough", max_examples=100)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))

_HAS_GIT = shutil.which("git") is not None

requires_git = pytest.mark.skipif(not _HAS_GIT, reason="git not available")


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Ensure a current event loop exists for code that constructs asyncio
    primitives (e.g. Semaphore) at import/init time outside a running loop."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def _isolate_session_map(tmp_path_factory, monkeypatch):
    """Point the SESSION MAP at a per-test tmp dir so nothing touches the real
    ~/.personalclaw/session_map.json. SessionManager.__init__ builds a SessionMap()
    that reads/prunes/REWRITES config_dir()/session_map.json at construction time — so
    any test that does SessionManager(cfg) without its own home patch mutates the USER's
    real session map (observed: a SessionMap key migration ran against the live file
    during a rename). Scoped to session_map.config_dir only (NOT a global Path.home
    patch, which breaks tests that assert real-home safety rails — seed/loop-validation).
    A test that patches session_map.config_dir itself still overrides this (last wins)."""
    map_home = tmp_path_factory.mktemp("pclaw-sessmap")
    monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: map_home)


@pytest.fixture(autouse=True)
def _reset_trust_mode():
    """Reset the process-global YOLO/auto-approve trust state around every test.

    ``personalclaw.trust_mode`` is a deliberate process singleton (one auto-approve
    posture per gateway). Tests that flip it must not leak into the next test, so we
    force it OFF before and after each test.
    """
    import personalclaw.trust_mode as _tm
    _tm._TRUST.disable()
    yield
    _tm._TRUST.disable()


@pytest.fixture(autouse=True)
def _no_acp_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never auto-provision (npm-install) ACP adapters during tests — provisioning
    is a real network + filesystem side effect (writes to the managed prefix under
    the user's home). Bundles that would otherwise install an adapter fall back to
    the npx-fallback argv, which is exactly what the resolution tests assert on."""
    monkeypatch.setenv("PERSONALCLAW_ACP_NO_PROVISION", "1")


@pytest.fixture(autouse=True)
def _no_app_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spawn (or orphan-reap) the user's REAL app backends from a test.
    Any test that reaches load_all_extensions() → start_enabled_app_backends()
    against the real config dir would otherwise launch backends for the user's
    installed apps — and its reaper killed the live gateway's backends once.
    Tests that exercise the backend lifecycle explicitly (test_app_api) call
    the supervisor directly and are unaffected by this flag."""
    monkeypatch.setenv("PERSONALCLAW_SKIP_APP_BACKENDS", "1")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure git commits succeed in environments without a global git identity."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")


# (The slack-suite autouse fixtures — enterprise bypass, emoji reset, allowlist
# reset — moved to apps/slack-channel/tests/conftest.py with the slack tests.)
