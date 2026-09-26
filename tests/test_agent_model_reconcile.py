"""Built-in / pinned agent model: one availability rule + editable (#53).

Two behaviors:
  * The availability rule — a pin that is no longer in the active chat set is reported as
    unavailable (``provider_bridge.named_model_problem``) instead of being handed to the client as a
    dead id. What serves instead, and how that is said, is
    ``tests/test_a_named_model_is_said_not_swapped.py``.
  * Editable — reserved system agents stay locked EXCEPT their ``model`` field,
    so a user can swap which model a built-in agent runs on.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import make_mocked_request

from personalclaw.providers import provider_bridge as pb

# ── The availability rule ──


def test_an_active_pin_is_available(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-5", "native:glm-5"})
    assert pb.named_model_problem("glm-5") is None


def test_a_stale_pin_is_unavailable_and_says_why(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-6"})
    # glm-5 no longer active: unavailable, with the reason the surfaces show.
    why, fix = pb.named_model_problem("glm-5")
    assert "not one of the chat models set up in Settings → Models" in why
    assert "Settings → Models" in fix


def test_no_pin_has_no_problem(monkeypatch):
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-6"})
    assert pb.named_model_problem("") is None


def test_a_pin_is_not_second_guessed_when_no_chat_model_is_set_up(monkeypatch):
    # Nothing configured yet → there is no list for the pin to be outside of.
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: set())
    assert pb.named_model_problem("glm-5") is None


def test_a_qualified_active_pin_is_available(monkeypatch):
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

    registry = ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type="myprov-type",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=8192,
        ),
        lambda **_kw: object(),
    )
    registry.register_entry(ProviderEntry(name="myprov", type="myprov-type", model="glm-5"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-5", "myprov:glm-5"})
    assert pb.named_model_problem("myprov:glm-5") is None


def test_an_active_pin_whose_provider_is_gone_is_unavailable(monkeypatch):
    """A chat chain entry IS a ref: its provider missing from the registry is why it cannot run."""
    from personalclaw.llm.registry import ProviderRegistry

    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", ProviderRegistry)
    monkeypatch.setattr(pb, "_active_chat_model_ids", lambda: {"glm-5", "myprov:glm-5"})
    why, _fix = pb.named_model_problem("myprov:glm-5")
    assert "myprov" in why


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
    monkeypatch.setattr(
        pb,
        "_active_chat_model_ids",
        lambda: {
            "global.anthropic.claude-opus-4-8",
            "Bedrock:global.anthropic.claude-opus-4-8",
            "glm-5.2",
            "Alibaba:glm-5.2",
        },
    )
    assert pb._fallback_chat_model(provider_hint="Bedrock") == "global.anthropic.claude-opus-4-8"


def test_fallback_model_hint_discriminates_per_provider(monkeypatch):
    _use_cases_refs(
        monkeypatch,
        [
            "Bedrock:global.anthropic.claude-opus-4-8",
            "Alibaba:glm-5.2",
            "Anthropic:claude-opus-4-8",
        ],
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
    monkeypatch.setattr(
        pb,
        "_active_chat_model_ids",
        lambda: {
            "global.anthropic.claude-opus-4-8",
            "Bedrock:global.anthropic.claude-opus-4-8",
            "glm-5.2",
            "Alibaba:glm-5.2",
        },
    )

    class _Prof:
        model = "Alibaba:glm-5.2"

    import personalclaw.config.loader as loader

    class _Cfg:
        agents = {"default": _Prof()}

    monkeypatch.setattr(loader.AppConfig, "load", staticmethod(lambda: _Cfg()))
    monkeypatch.setattr("personalclaw.agents.defaults.default_agent_name", lambda cfg: "default")
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
