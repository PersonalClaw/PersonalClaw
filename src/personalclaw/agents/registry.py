"""AgentProvider registry — maps a provider id to its runtime class.

Mirrors the model ``ProviderRegistry`` pattern: provider classes register their
id at import time; the bridge resolves a class by id and instantiates it per
session. The ``native`` and ``acp:*`` provider families register here.
"""

from __future__ import annotations

from personalclaw.agents.provider import AgentProvider

_providers: dict[str, type[AgentProvider]] = {}


def register_agent_provider(provider_id: str, cls: type[AgentProvider]) -> None:
    _providers[provider_id] = cls


def get_agent_provider_class(provider_id: str) -> type[AgentProvider] | None:
    """Resolve a provider class. Exact match first, then a ``prefix:*`` family
    match (so ``acp:claude-code`` resolves the registered ``acp`` family)."""
    if provider_id in _providers:
        return _providers[provider_id]
    family = provider_id.split(":", 1)[0]
    return _providers.get(family)


def list_agent_providers() -> list[str]:
    return list(_providers.keys())
