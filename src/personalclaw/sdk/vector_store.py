"""SDK: the external vector-store provider ABC + data types (KBVS-1).

Stable re-export of ``personalclaw.vector_stores.base`` — an app imports these, not the
core module directly, so the core path can move without breaking installed apps.

An app subclasses :class:`VectorStoreProvider` to point knowledge chunk-vector search at
a store the user runs (Qdrant, pgvector, Chroma). The vendor client belongs to the app and
only to the app: core carries no vector-store client, by the same judgment
``docs/architecture/provider-boundary.md`` applies to every other vendor integration.

Two conventions the seam depends on, restated because getting them wrong degrades recall
silently rather than loudly:

* :meth:`VectorStoreProvider.query` returns hits sorted by DESCENDING cosine similarity —
  core stops at the first hit under its floor.
* the connection host/port/collection round-trip through the app's own
  ``settingsSchema``/``ProviderSettings`` config; an api key or password is resolved from
  ``personalclaw.sdk.credentials`` and must never be written into that config.
"""

from personalclaw.vector_stores.base import (  # noqa: F401
    VectorHit,
    VectorRecord,
    VectorStoreInfo,
    VectorStoreProvider,
)

__all__ = [
    "VectorStoreProvider",
    "VectorRecord",
    "VectorHit",
    "VectorStoreInfo",
]
