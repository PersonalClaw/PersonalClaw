from personalclaw.inbox_providers.base import MessageSourceProvider
from personalclaw.provider_registry import discover_providers

_cache: dict[str, type] | None = None


def get_message_providers() -> dict[str, type]:
    global _cache
    if _cache is None:
        _cache = discover_providers(
            "personalclaw.message_source_providers",
            MessageSourceProvider,  # type: ignore[type-abstract]
        )
    return _cache


def get_default_provider(name: str = "native") -> "MessageSourceProvider":
    """Resolve and instantiate a message-source provider by name.

    Falls back through: requested name → native → filesystem.
    The default is "native" (always-present in-process source); channel-specific
    sources are contributed by their app bundle at enable-time, each registering
    its own ``source_name`` through the ``personalclaw.message_source_providers``
    entry-point group.

    SEAM LIMIT (do not mistake this for a working path): resolution reads ONLY that
    entry-point group. An app that declares an ``inbox`` provider in its ``app.json``
    is NOT reachable here — the install pipeline pip-installs an app's declared
    dependencies but never makes the app itself an installed distribution, so it can
    contribute no entry point; and ``discover_providers`` binds a module-level
    ``Provider``/``<Name>Provider`` CLASS, so a manifest's ``create_provider``
    factory would be invisible to it even then. Bridging the two (resolving an
    app-contributed source through the app registry's manifest factory, the way every
    other app provider type resolves) is an open provider-seam contract change owned
    by this seam, not by the contributing app.
    """
    providers = get_message_providers()
    cls = providers.get(name) or providers.get("native") or providers.get("filesystem")
    if cls is None:
        from personalclaw.inbox_providers.filesystem_source import FilesystemSourceProvider

        cls = FilesystemSourceProvider
    return cls()
