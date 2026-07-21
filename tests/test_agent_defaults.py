"""E2-P4.5: native default agent seed + default-name resolution."""

from __future__ import annotations

from personalclaw.agents.defaults import (
    DEFAULT_NATIVE_AGENT_NAME,
    default_agent_name,
    make_default_native_profile,
    normalize_agent_name,
)


def test_default_native_profile_is_native():
    from personalclaw.config.loader import AgentProfile

    prof = make_default_native_profile(AgentProfile)
    assert prof.provider == "native"
    assert prof.source == "builtin"
    assert prof.system_prompt  # has a persona
    assert prof.model == ""  # inherits the chat use-case binding


def test_global_agent_provider_defaults_native():
    from personalclaw.config.loader import AgentConfig

    assert AgentConfig().provider == "native"


def test_agent_profile_has_provider_field():
    from personalclaw.config.loader import AgentProfile

    # Per-agent provider override (P5 field, brought forward in P4.5).
    assert AgentProfile().provider == ""  # empty inherits the global default
    assert AgentProfile(provider="acp:claude").provider == "acp:claude"


class _Cfg:
    def __init__(self, default_agent=""):
        self.default_agent = default_agent


def test_default_agent_name_falls_back_to_native():
    # default_agent is a single top-level field (the nested agent.default_agent
    # was removed); empty → the native default, set → that name.
    assert default_agent_name(_Cfg()) == DEFAULT_NATIVE_AGENT_NAME
    assert default_agent_name(_Cfg(default_agent="MyAgent")) == "MyAgent"


def test_normalize_agent_name_canonicalizes_default():
    # All the spellings of "the default agent" collapse to one scope key, so
    # agent-scoped memory (persona, commitments) writes + reads agree (M5e).
    assert normalize_agent_name(None) == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("") == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("personalclaw") == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("PersonalClaw") == DEFAULT_NATIVE_AGENT_NAME
    assert normalize_agent_name("  personalclaw  ") == DEFAULT_NATIVE_AGENT_NAME
    # a real custom agent passes through unchanged
    assert normalize_agent_name("MyCustomAgent") == "MyCustomAgent"


# ── PUT /api/config/default-agent — set-time agent-name validation ──
# Regression: the setter wrote ANY name to config.json → the write "succeeded"
# (ok:true) but the next AppConfig.load() re-migration reconciled the dangling name
# back to the real default, so the change silently didn't stick. It now rejects an
# unknown agent up-front (same set-time-validation principle as models #16 / search
# #17). Empty string is allowed (reset to system default).

import json as _json  # noqa: E402

import pytest  # noqa: E402
from aiohttp.test_utils import make_mocked_request  # noqa: E402

import personalclaw.config.loader as _loader  # noqa: E402
import personalclaw.dashboard.handlers as _handlers  # noqa: E402
import personalclaw.dashboard.handlers.agents as _agents_h  # noqa: E402


def _acfg(monkeypatch, tmp_path, agents: dict):
    """A config.json with the given agents dict; point both config_path seams at it."""
    cfg = tmp_path / "config.json"
    cfg.write_text(_json.dumps({"agents": {k: {} for k in agents}, "default_agent": "default"}))
    monkeypatch.setattr(_loader, "config_path", lambda: cfg)
    monkeypatch.setattr(_loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(_handlers, "config_path", lambda: cfg, raising=False)
    return cfg


def _put(body):
    req = make_mocked_request("PUT", "/api/config/default-agent")

    async def _j():
        return body

    req.json = _j
    return req


@pytest.mark.asyncio
async def test_default_agent_rejects_unknown(monkeypatch, tmp_path):
    cfg = _acfg(monkeypatch, tmp_path, {"default": {}, "my-writer": {}})
    resp = await _agents_h.api_default_agent(_put({"agent": "no-such-agent-xyz"}))
    assert resp.status == 400
    assert "Unknown agent" in _json.loads(resp.body.decode())["error"]
    # config.json's default_agent must be UNCHANGED (write rejected before disk).
    assert _json.loads(cfg.read_text())["default_agent"] == "default"


@pytest.mark.asyncio
async def test_default_agent_accepts_known(monkeypatch, tmp_path):
    cfg = _acfg(monkeypatch, tmp_path, {"default": {}, "my-writer": {}})
    resp = await _agents_h.api_default_agent(_put({"agent": "my-writer"}))
    assert resp.status == 200
    assert _json.loads(resp.body.decode())["default_agent"] == "my-writer"
    assert _json.loads(cfg.read_text())["default_agent"] == "my-writer"


@pytest.mark.asyncio
async def test_default_agent_empty_allowed(monkeypatch, tmp_path):
    _acfg(monkeypatch, tmp_path, {"default": {}})
    resp = await _agents_h.api_default_agent(_put({"agent": ""}))
    assert resp.status == 200  # empty = reset to system default, never rejected


@pytest.mark.asyncio
async def test_default_agent_missing_key_is_400_not_silent_clear(monkeypatch, tmp_path):
    """S05 C11 regression: a body WITHOUT 'agent' (typo'd key, wrong contract —
    e.g. {"name": "..."}) used to coerce to "" and silently CLEAR the default
    agent while returning ok:true. Missing key must be a 400; reset stays the
    explicit {"agent": ""}."""
    cfg = _acfg(monkeypatch, tmp_path, {"default": {}, "my-writer": {}})
    resp = await _agents_h.api_default_agent(_put({"name": "my-writer"}))
    assert resp.status == 400
    # default_agent untouched on disk
    assert _json.loads(cfg.read_text())["default_agent"] == "default"
