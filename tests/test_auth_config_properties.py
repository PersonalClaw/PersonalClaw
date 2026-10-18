"""Hypothesis property tests for auth modes and internal-tool residue.

Properties tested:
    * The loopback invariant (NONE forces 127.0.0.1; other modes respect the configured
      bind_host)
    * No residue of deleted code paths (banned symbols) in the source tree
"""

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from personalclaw.auth.modes import (  # noqa: E402
    AuthConfig,
    AuthMode,
    effective_bind,
)

# ── Loopback invariant ────────────────────────────────────────────────────────


@given(
    bind_host=st.text(min_size=1, max_size=64),
)
@settings(max_examples=100)
def test_loopback_invariant_none_mode(bind_host):
    """AuthMode.NONE always forces effective_bind to 127.0.0.1."""
    cfg = AuthConfig(mode=AuthMode.NONE, bind_host=bind_host)
    result = effective_bind(cfg)
    assert (
        result == "127.0.0.1"
    ), f"loopback invariant violated: AuthMode.NONE with bind_host={bind_host!r} → {result!r}"


@given(
    mode=st.sampled_from([AuthMode.LOCAL_TOKEN]),
    bind_host=st.sampled_from(["0.0.0.0", "127.0.0.1", "10.0.0.1", "::1"]),
)
@settings(max_examples=50)
def test_loopback_invariant_non_none_mode_respects_bind_host(mode, bind_host):
    """The one non-NONE mode returns the configured bind_host unchanged."""
    cfg = AuthConfig(mode=mode, bind_host=bind_host)
    result = effective_bind(cfg)
    assert (
        result == bind_host
    ), f"unexpected rewrite: mode={mode} bind_host={bind_host!r} → {result!r}"


# ── from_env: PERSONALCLAW_AUTH_MODE dev override ─────────────────────────────


def test_from_env_default_is_local_token(monkeypatch):
    monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)
    assert AuthConfig.from_env().mode == AuthMode.LOCAL_TOKEN


def test_from_env_none_selects_none_mode(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "none")
    cfg = AuthConfig.from_env()
    assert cfg.mode == AuthMode.NONE
    # The loopback invariant still holds for the selected mode.
    assert effective_bind(cfg) == "127.0.0.1"


def test_from_env_unknown_value_falls_back_to_local_token(monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "banana")
    assert AuthConfig.from_env().mode == AuthMode.LOCAL_TOKEN


# ── No residue of deleted code paths ──────────────────────────────────────────
