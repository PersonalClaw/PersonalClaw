"""Knowledge provider registry."""

from typing import Any

from personalclaw.knowledge_providers.base import KnowledgeItem, KnowledgeProvider

_providers: dict[str, KnowledgeProvider] = {}


def register_provider(provider: KnowledgeProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def list_providers() -> list[KnowledgeProvider]:
    return list(_providers.values())


def list_provider_info() -> list[dict[str, Any]]:
    """Provider attribution list (S4 pattern): the native always-on provider plus
    any registered external providers. The native provider owns every native
    knowledge type + the cross-cutting intelligence (extraction/insights) that
    runs over all items regardless of origin."""
    out: list[dict[str, Any]] = [
        {
            "name": "native",
            "display_name": "PersonalClaw Knowledge",
            "always_on": True,
            "kind": "native",
        }
    ]
    for prov in _providers.values():
        if prov.name == "native":
            continue
        out.append(
            {
                "name": prov.name,
                "display_name": getattr(prov, "display_name", prov.name),
                "always_on": False,
                "kind": "external",
            }
        )
    return out


async def search_all(query: str, limit: int = 10) -> list[KnowledgeItem]:
    results: list[KnowledgeItem] = []
    for prov in _providers.values():
        try:
            items = await prov.search(query, limit=limit)
            results.extend(items)
        except Exception:
            pass
    return results[:limit]


def create_native_provider(config=None):
    """Manifest factory for the bundled ``native-knowledge`` extension.

    Returns ``None`` by design: the native provider needs the ``DashboardState``
    knowledge store + ingest queue, which the manifest-factory call site does not
    have, so it self-registers via ``state.knowledge_provider()`` (the single
    source of truth). ``ProviderRegistry._enable_one`` skips ``register`` for a
    ``None`` instance, so enabling ``native-knowledge`` through the real
    ``KnowledgeTypeHandler`` does not double-register it. An EXTERNAL provider's
    factory returns a real instance and IS registered here.
    """
    return None
