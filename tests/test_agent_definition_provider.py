"""F4: the three agent shapes are intentional layers, all carrying the
per-agent ``provider`` field (E2-P5) so a marketplace-imported agent is a
first-class peer of a config-defined one.

  - config.loader.AgentProfile      — the persisted agent in config.json agents{}
  - agents.provider.AgentRuntimeDefinition — runtime DTO the bridge builds
  - agents.marketplace.AgentDefinition     — marketplace transport/catalog shape

These are NOT a dual-source-of-truth bug (the FINDINGS note was overstated):
AgentProfile is the single persisted truth; the other two are derived/transport
layers. This test pins that all three expose ``provider`` consistently.
"""

from __future__ import annotations

import dataclasses


def test_all_three_agent_shapes_carry_provider():
    from personalclaw.agents.marketplace import AgentDefinition
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.config.loader import AgentProfile

    # Default: empty provider → inherits the global agent.provider default.
    assert AgentProfile().provider == ""
    assert AgentRuntimeDefinition(name="x").provider == "native"  # runtime default
    assert AgentDefinition(name="x").provider == ""

    # Explicit per-agent provider is honored + round-trips for the persisted/
    # transport shapes.
    prof = AgentProfile(provider="acp:claude-code")
    assert prof.provider == "acp:claude-code"

    defn = AgentDefinition(name="y", provider="acp:claude-code")
    assert dataclasses.asdict(defn)["provider"] == "acp:claude-code"


def test_marketplace_definition_provider_distinct_from_provider_entry():
    """`provider` (agent-runtime axis) is distinct from `provider_entry`
    (a ModelProvider entry name) — both can be set independently."""
    from personalclaw.agents.marketplace import AgentDefinition

    d = AgentDefinition(name="z", provider="native", provider_entry="MyCloud")
    assert d.provider == "native"
    assert d.provider_entry == "MyCloud"


def test_marketplace_from_dict_preserves_provider():
    """Regression: AgentDefinition.from_dict MUST read `provider`. It used to
    drop it, so an agent bound to an ACP runtime silently
    lost the binding on every load → the connected ACP provider was never used
    and no ACP agents were chattable."""
    from personalclaw.agents.marketplace import AgentDefinition

    d = AgentDefinition.from_dict({"name": "cc", "provider": "acp:claude-code"})
    assert d.provider == "acp:claude-code"
    # Full round-trip through serialization is stable.
    assert AgentDefinition.from_dict(d.to_dict()).provider == "acp:claude-code"


def test_local_marketplace_update_can_rebind_provider(tmp_path):
    """Regression: `provider` must be in the update allowlist so an agent can be
    (re)bound to an ACP runtime via the editor."""
    from personalclaw.agents.marketplace import AgentDefinition, LocalAgentMarketplace

    market = LocalAgentMarketplace(base_dir=tmp_path / "agents")
    market.create(AgentDefinition(name="rebind", provider="native"))
    updated = market.update("rebind", {"provider": "acp:test-cli"})
    assert updated.provider == "acp:test-cli"
    # Persisted + reloaded keeps the rebind.
    assert market.get("rebind").provider == "acp:test-cli"
