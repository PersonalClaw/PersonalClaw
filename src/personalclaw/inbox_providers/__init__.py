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
    """Resolve a message-source provider by name.

    PRECEDENCE (INU-8), in order:

    1. **App-contributed** — an instance registered by ``InboxTypeHandler`` from an
       app's ``{"type": "inbox", "implementation": "mod:factory"}`` manifest at
       enable-time (``inbox_providers.registry``). Wins, so an installed app
       actually takes its ``source_name``.
    2. **Entry-point group** — a ``MessageSourceProvider`` CLASS discovered in
       ``personalclaw.message_source_providers`` and instantiated here.
    3. **native** then 4. **filesystem** — the terminal in-process fallbacks,
       resolved through the same entry-point group (with a direct import of
       ``FilesystemSourceProvider`` as the last resort if discovery is empty).

    The two registries hold different SHAPES and are kept separate on purpose: the
    app path yields an already-built instance (its factory has run and may close
    over app config, so it cannot be re-instantiated), the entry-point path yields a
    class this function calls. ``cls()`` is therefore reserved for the entry-point
    path — see ``inbox_providers/registry.py`` for why not normalising the two.

    The default name is "native" (the always-present in-process push source).
    """
    from personalclaw.inbox_providers.registry import get_source

    app_source = get_source(name)
    if app_source is not None:
        return app_source
    providers = get_message_providers()
    cls = providers.get(name) or providers.get("native") or providers.get("filesystem")
    if cls is None:
        from personalclaw.inbox_providers.filesystem_source import FilesystemSourceProvider

        cls = FilesystemSourceProvider
    return cls()
