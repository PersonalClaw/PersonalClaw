"""The app-contributed message-source registry (INU-8).

Mirrors ``trigger_sources/registry.py`` and ``sync_transports/registry.py``: the
``inbox`` provider-type handler (``providers/registry.py::InboxTypeHandler``)
registers an installed app's source here on enable and removes it on disable.
:func:`personalclaw.inbox_providers.get_default_provider` reads it first, so an
app-declared source resolves the way every other app provider type resolves.

**This registry holds INSTANCES, not classes** — the one shape difference from
the ``personalclaw.message_source_providers`` entry-point group
(``get_message_providers() -> dict[str, type]``, instantiated as ``cls()``). A
manifest factory has ALREADY run by the time the handler sees its result, and it
may close over the app's own config/credentials, so re-instantiating it is not
possible. Keeping the two registries separate is deliberate: normalising an
instance into a fake "class" via a lambda would make ``dict[str, type]`` a lie
and push the shape confusion into every future reader of either path.

Keyed by the provider's own ``source_name`` (not the app name), because that is
what an inbox item records (``inbox.py``'s ``source`` field) and what a caller
asks :func:`get_default_provider` for. This module deliberately imports nothing
from ``providers/`` — the dependency runs one way, handler → registry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.inbox_providers.base import MessageSourceProvider

logger = logging.getLogger(__name__)

_sources: dict[str, "MessageSourceProvider"] = {}


def register_source(provider: "MessageSourceProvider") -> str:
    """Register an app-contributed source under its own ``source_name``.

    Returns the name it was registered under so the caller can log/deregister by
    the same key it actually used.
    """
    name = str(provider.source_name)
    if not name:
        raise ValueError("an app-contributed inbox source must expose a non-empty source_name")
    _sources[name] = provider
    return name


def unregister_source(name: str) -> None:
    """Remove a source. A disabled/uninstalled app must leave NO phantom source
    still answering :func:`get_default_provider` — see ``InboxTypeHandler``."""
    _sources.pop(name, None)


def get_source(name: str) -> "MessageSourceProvider | None":
    """The app-contributed source registered under *name*, or None."""
    return _sources.get(name)


def list_source_names() -> list[str]:
    """Every app-contributed source name, for debug/doctor surfaces."""
    return sorted(_sources)
