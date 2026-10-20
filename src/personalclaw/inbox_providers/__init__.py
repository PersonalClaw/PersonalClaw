from personalclaw.inbox_providers.base import MessageSourceProvider
from personalclaw.provider_registry import discover_providers

_cache: dict[str, type] | None = None
#: One instance per built-in source class, so a source keeps whatever it holds between polls.
_builtin_instances: dict[str, MessageSourceProvider] = {}


def get_message_providers() -> dict[str, type]:
    global _cache
    if _cache is None:
        _cache = discover_providers(
            "personalclaw.message_source_providers",
            MessageSourceProvider,  # type: ignore[type-abstract]
        )
    return _cache


def source_catalog() -> list[tuple[MessageSourceProvider, bool]]:
    """Every poll source the inbox knows, each with whether it is polled now.

    The ONE definition of what the inbox polls, read on every tick so an app enabled or
    disabled since the last one is picked up without a restart. The sources are the ones an
    app registered (``inbox_providers.registry``: an installed inbox app's while it is enabled,
    and the drop folder through the native ``filesystem-inbox`` app), then any
    ``personalclaw.message_source_providers`` entry point no app has taken the name of. Each
    is polled while its own :meth:`~MessageSourceProvider.polling_enabled` says so, which an
    installed app's always does: a second switch that defaulted off is how an installed Mail
    Inbox did nothing at all. The drop folder's reads ``inbox.enabled``.

    The native source is absent: agents push to it, nothing polls it.
    """
    from personalclaw.inbox_providers.registry import list_sources

    sources = list_sources()
    taken = {str(s.source_name) for s in sources}
    for name, cls in sorted(get_message_providers().items()):
        if name == "native":
            continue
        instance = _builtin_instances.get(name)
        if instance is None:
            instance = _builtin_instances[name] = cls()
        if str(instance.source_name) not in taken:
            sources.append(instance)
    return [(source, bool(source.polling_enabled())) for source in sources]


def polled_sources() -> list[MessageSourceProvider]:
    """The sources the inbox polls on this tick (:func:`source_catalog`'s polled ones)."""
    return [source for source, polled in source_catalog() if polled]


def polled_source(name: str) -> MessageSourceProvider | None:
    """The polled source named *name*, or None: where a reply to one of its rows goes."""
    return next((s for s in polled_sources() if str(s.source_name) == name), None)


def source_label(source: MessageSourceProvider) -> str:
    """What a sentence calls *source*: its ``display_name`` when it has one, else its name."""
    return str(getattr(source, "display_name", "") or source.source_name)
