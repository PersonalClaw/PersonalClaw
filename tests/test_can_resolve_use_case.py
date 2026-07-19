"""X2/X3: ``can_resolve_use_case`` — the single dry-run resolvability probe behind
both the onboarding ``needs_model`` signal and the background-session spawn guard.

It must AGREE with what ``resolve_provider_for_use_case`` would actually do, so the
"add a model" nudge and the bridge never disagree (the coarse capability-only
heuristic they used before could diverge — the F1 class of bug). It must also be
side-effect free (no provider instantiation): it runs on a hot onboarding GET.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import personalclaw.llm.openai  # noqa: F401 — registers the "openai" type
import personalclaw.providers.provider_bridge as pb
from personalclaw.llm.registry import ProviderEntry, get_default_registry


def _ensure_registry_entry(name: str, *, type_: str = "ollama") -> None:
    # Model provider TYPES (openai/anthropic/bedrock) register from their apps now, so
    # they aren't in the registry in a bare unit test. This probe only needs an entry
    # that DECLARES a capability — which is exactly the fail-soft path the resolver
    # takes for a not-yet-registered type — so declare CHAT on the entry directly
    # instead of reading it from a (possibly-unregistered) type descriptor.
    from personalclaw.llm.capabilities import Capability
    reg = get_default_registry()
    if any(e.name == name for e in reg.list_entries()):
        return
    reg.register_entry(
        ProviderEntry(
            name=name,
            type=type_,
            model="gpt-x",
            options={"endpoint": "https://x/v1", "api_key": "k"},
            declared_capabilities=frozenset({Capability.CHAT}),
        )
    )


def test_true_when_active_model_selected():
    """An active model selected for the use case ⇒ resolvable, regardless of
    registry state (the Settings → Models selection the bridge resolves first)."""
    with patch("personalclaw.providers.use_cases.active_model_refs", return_value=["MyCloud:gpt-x"]):
        assert pb.can_resolve_use_case("chat") is True


def test_true_when_registry_entry_declares_capability():
    """No active selection, but a configured provider declares CHAT ⇒ resolvable
    (the implicit-fallback path the bridge actually takes)."""
    _ensure_registry_entry("MyCloud")
    with patch("personalclaw.providers.use_cases.active_model_refs", return_value=[]):
        assert pb.can_resolve_use_case("chat") is True


def test_false_when_no_selection_and_no_capable_entry():
    """No active selection and an EMPTY registry ⇒ not resolvable ⇒ needs_model /
    defer bg. The probe imports ``get_default_registry`` locally from
    ``llm.registry``, so that module path is the patch hook."""
    with patch("personalclaw.providers.use_cases.active_model_refs", return_value=[]), \
        patch("personalclaw.llm.registry.get_default_registry") as gdr:
        reg = MagicMock()
        reg.list_entries.return_value = []
        gdr.return_value = reg
        assert pb.can_resolve_use_case("chat") is False


def test_false_when_only_acp_agent_entry_present():
    """An agent-runtime entry (``acp_agent``) is NOT a model provider — a registry
    holding only it must report needs_model=true. This is the subtle case the old
    onboarding heuristic had to special-case; the probe owns it now."""
    with patch("personalclaw.providers.use_cases.active_model_refs", return_value=[]), \
        patch("personalclaw.llm.registry.get_default_registry") as gdr:
        entry = MagicMock()
        entry.type = "acp_agent"
        entry.declared_capabilities = frozenset()
        reg = MagicMock()
        reg.list_entries.return_value = [entry]
        gdr.return_value = reg
        assert pb.can_resolve_use_case("chat") is False


def test_false_for_unknown_use_case():
    assert pb.can_resolve_use_case("not-a-use-case") is False


def test_chat_subcategory_falls_back_to_chat_selection():
    """A chat sub-category (reasoning) with no model of its own borrows the
    parent ``chat`` selection — active_model_refs handles the fallback, so the
    probe reports resolvable."""
    with patch("personalclaw.providers.use_cases.load_active_models",
               return_value={"chat": ["MyCloud:gpt-x"]}):
        assert pb.can_resolve_use_case("reasoning") is True


def test_does_not_build_a_provider():
    """Side-effect freedom: the probe must never instantiate a provider (no
    subprocess/socket) — it runs on a hot GET. Assert the build paths are untouched:
    the registry's type-factory build (config-registry model providers) and the
    native-runtime build."""
    _ensure_registry_entry("MyCloud2")
    reg = get_default_registry()
    with patch("personalclaw.providers.use_cases.active_model_refs", return_value=[]), \
        patch.object(reg, "build") as build, \
        patch.object(pb, "_build_native_runtime") as build_native:
        pb.can_resolve_use_case("chat")
        build.assert_not_called()
        build_native.assert_not_called()
