"""Registry for app-contributed external vector-store backends (KBVS-1).

Binding is by ENABLEMENT, deliberately: the one enabled ``vector_store`` provider is the
one knowledge vector search uses. There is no ``knowledge.vector_store_provider`` config
field, because a name in ``config.json`` plus a set of enabled apps is two places to say
one thing, and the two would drift the first time a user disabled an app without editing
config — leaving a name pointing at nothing, or worse, at a store that was re-enabled
later.

The zero and the many are both handled as "not bound", for different reasons:

* **none registered** — the default, and every install that has not opted in. Retrieval
  keeps the bundled ``sqlite-vec``/``vec0`` + FTS5 + graph path, unchanged.
* **more than one registered** — refused, logged at WARNING, once. Picking one of two
  stores the user bound would answer from an index they did not choose and give them no
  way to see it; that is the silent-wrong-recall class. Refusing degrades to the local
  path with a message that names both, which is repairable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from personalclaw.vector_stores.base import VectorStoreProvider

logger = logging.getLogger(__name__)

_providers: dict[str, VectorStoreProvider] = {}
#: The ambiguity warning is emitted once per offending provider set, not once per query —
#: `active_provider()` is called on every knowledge search.
_warned_sets: set[tuple[str, ...]] = set()


def register_provider(name: str, provider: VectorStoreProvider) -> None:
    _providers[name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> VectorStoreProvider | None:
    return _providers.get(name)


def list_providers() -> list[str]:
    return sorted(_providers)


def active_provider() -> VectorStoreProvider | None:
    """The single bound external backend, or None.

    None means "use the bundled local path", which is the only other possibility — so
    every caller is a two-branch read and there is no third state to forget.
    """
    if len(_providers) == 1:
        return next(iter(_providers.values()))
    if len(_providers) > 1:
        names = tuple(sorted(_providers))
        if names not in _warned_sets:
            _warned_sets.add(names)
            logger.warning(
                "knowledge vector search: %d external vector stores are enabled (%s) — "
                "refusing to pick one and using the built-in local index instead. "
                "Disable all but one in the Store.",
                len(names),
                ", ".join(names),
            )
    return None
