"""P1 regression: an acp_agent entry (acp:<cli>) resolves through the registry's
own factory (registry.build).

Caught live originally: with a real acp:claude-code entry registered, chat
resolution fell into a model-provider shim path (_build_llm_provider →
load_factory(shim)) → AttributeError ('_Cfg' has no 'implementation'), because the
shim was a model-type duck-type with no manifest. That divergent shim path has
since been removed entirely — model AND agent-runtime entries now build through the
single registry.build path — so the failure mode can no longer occur. This test
keeps the guarantee: an acp_agent candidate builds via registry.build(name, ...).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import personalclaw.providers.provider_bridge as pb


def test_acp_agent_entry_builds_via_registry():
    """An acp_agent candidate is built with registry.build(name, ...)."""
    candidate = MagicMock()
    candidate.name = "acp:claude-code"
    candidate.type = "acp_agent"
    candidate.model = "auto"
    candidate.options = {"command": ["claude-code-acp"], "dialect": "claude-code"}
    candidate.declared_capabilities = frozenset()

    from personalclaw.llm.capabilities import Capability

    registry = MagicMock()
    registry.list_entries.return_value = [candidate]
    # Entry has no declared caps → resolution falls back to capability_of(type);
    # make CHAT present so the candidate matches.
    registry.capability_of.return_value = MagicMock(capabilities=frozenset({Capability.CHAT}))
    built = MagicMock(name="AcpAgentProvider")
    registry.build.return_value = built

    # _resolve_from_config_registry imports get_default_registry locally from
    # personalclaw.llm.registry — patch that path only.
    with patch("personalclaw.llm.registry.get_default_registry", return_value=registry):
        result = pb._resolve_from_config_registry("chat", session_key="s", agent="A")

    assert result is built                       # built via the registry path
    registry.build.assert_called_once()          # registry factory used
    assert registry.build.call_args.args[0] == "acp:claude-code"
