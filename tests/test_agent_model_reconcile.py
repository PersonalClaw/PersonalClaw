"""Built-in / pinned agent model: auto-reconcile + editable (#53).

Two behaviors:
  * Runtime auto-reconcile — an agent pinning a model that's no longer in the
    active chat set falls back to the chat binding instead of handing a dead id
    to the client (``provider_bridge._reconcile_agent_model``).
  * Editable — reserved system agents stay locked EXCEPT their ``model`` field,
    so a user can swap which model a built-in agent runs on.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.providers import provider_bridge as pb


# ── Runtime reconcile ──

def test_reconcile_keeps_active_pin(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-5", "native:glm-5"})
    assert pb._reconcile_agent_model("glm-5") == "glm-5"


def test_reconcile_drops_stale_pin(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-6"})
    # glm-5 no longer active → reconcile to "" (caller falls back to chat binding)
    assert pb._reconcile_agent_model("glm-5") == ""


def test_reconcile_empty_passes_through(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-6"})
    assert pb._reconcile_agent_model("") == ""


def test_reconcile_noop_when_no_active_models(monkeypatch):
    # Nothing configured yet → don't second-guess the pin.
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: set())
    assert pb._reconcile_agent_model("glm-5") == "glm-5"


def test_reconcile_accepts_qualified_pin(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-5", "myprov:glm-5"})
    assert pb._reconcile_agent_model("myprov:glm-5") == "myprov:glm-5"


# ── Fallback chat model must AGREE with the resolved inner provider ──
#
# Regression for the background-suggestions failure: a model-less native agent
# (personalclaw-lite) resolves its inner ModelProvider from the FIRST active chat
# ref (e.g. Bedrock), but the definition model came from _fallback_chat_model()
# which preferred the DEFAULT AGENT's pin (e.g. Alibaba:glm-5.2). The Alibaba id
# was then sent to the Bedrock client → "The provided model identifier is invalid"
# every ~30s (suggestions/title/consolidation turns). The fix threads the resolved
# provider's name as a hint so the fallback picks that provider's active model.

def _use_cases_refs(monkeypatch, refs, known):
    """Patch the use_cases module that provider_bridge imports lazily."""
    import personalclaw.providers.use_cases as uc

    monkeypatch.setattr(uc, "active_model_refs", lambda use_case="chat": list(refs))
    monkeypatch.setattr(uc, "_known_provider_names", lambda: set(known))


def test_fallback_model_agrees_with_hinted_provider(monkeypatch):
    # chat binding leads with Bedrock; default agent (below) pins Alibaba.
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2"],
        {"Bedrock", "Alibaba"},
    )
    # With the Bedrock hint, the fallback MUST return the Bedrock model, never the
    # Alibaba one — even though the default-agent pin (step 2) is Alibaba.
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {
        "global.anthropic.claude-opus-4-8", "Bedrock:global.anthropic.claude-opus-4-8",
        "glm-5.2", "Alibaba:glm-5.2",
    })
    assert pb._fallback_chat_model(provider_hint="Bedrock") == "global.anthropic.claude-opus-4-8"


def test_fallback_model_hint_discriminates_per_provider(monkeypatch):
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2", "Anthropic:claude-opus-4-8"],
        {"Bedrock", "Alibaba", "Anthropic"},
    )
    assert pb._fallback_chat_model(provider_hint="Alibaba") == "glm-5.2"
    assert pb._fallback_chat_model(provider_hint="Anthropic") == "claude-opus-4-8"


def test_provider_entry_name_is_first_resolvable_ref(monkeypatch):
    # Mirrors the inner resolver: the first ref whose provider is configured.
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2"],
        {"Bedrock", "Alibaba"},
    )
    assert pb._provider_entry_name(None) == "Bedrock"


def test_provider_entry_name_skips_uninstalled_first_ref(monkeypatch):
    # If the first ref's provider isn't configured, the hint is the first that IS
    # (matches the inner resolver, which builds from the first resolvable ref).
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2"],
        {"Alibaba"},  # Bedrock NOT installed
    )
    assert pb._provider_entry_name(None) == "Alibaba"


def test_fallback_model_default_agent_pin_ignored_when_provider_disagrees(monkeypatch):
    """A default-agent pin naming a DIFFERENT provider than the hint must not be
    returned (it would send that provider's id to the hinted client)."""
    _use_cases_refs(
        monkeypatch,
        ["Bedrock:global.anthropic.claude-opus-4-8", "Alibaba:glm-5.2"],
        {"Bedrock", "Alibaba"},
    )
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {
        "global.anthropic.claude-opus-4-8", "Bedrock:global.anthropic.claude-opus-4-8",
        "glm-5.2", "Alibaba:glm-5.2",
    })

    class _Prof:
        model = "Alibaba:glm-5.2"

    import personalclaw.config.loader as loader

    class _Cfg:
        agents = {"default": _Prof()}

    monkeypatch.setattr(loader.AppConfig, "load", staticmethod(lambda: _Cfg()))
    monkeypatch.setattr(
        "personalclaw.agents.defaults.default_agent_name", lambda cfg: "default"
    )
    # Hint is Bedrock; the Alibaba default-agent pin must be skipped in favor of
    # the Bedrock active ref.
    assert pb._fallback_chat_model(provider_hint="Bedrock") == "global.anthropic.claude-opus-4-8"


# ── Reserved-agent model edit allowance ──

def _put(name: str, body: dict):
    from personalclaw.dashboard.handlers import agents as H

    async def _json():
        return body
    req = make_mocked_request("PUT", f"/api/agents/{name}", match_info={"name": name})
    req.json = _json  # type: ignore[assignment]
    return asyncio.run(H.api_personalclaw_agent_update(req)), H


def test_reserved_agent_rejects_non_model_edit(monkeypatch, tmp_path):
    from personalclaw.agents.defaults import LITE_AGENT_NAME
    resp, _H = _put(LITE_AGENT_NAME, {"system_prompt": "hacked", "model": "x"})
    assert resp.status == 403
    assert "only its model" in json.loads(resp.body)["error"]


def test_reserved_agent_allows_model_only_edit(monkeypatch, tmp_path):
    """A model-only body is NOT rejected by the reserved guard (it proceeds to the
    normal load/update path)."""
    from personalclaw.agents.defaults import LITE_AGENT_NAME
    # The guard is the unit under test; the subsequent AppConfig.load path may
    # 404 if the lite agent isn't seeded in this env — that's fine, we only
    # assert the guard didn't 403 the model-only edit.
    resp, _H = _put(LITE_AGENT_NAME, {"model": "glm-6"})
    assert resp.status != 403
