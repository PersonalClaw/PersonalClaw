"""``personalclaw.sdk.vector_store`` — the external vector-store seam on the app boundary.

A BYO-vector-store app (Qdrant, pgvector, Chroma, Weaviate, …) implements
:class:`VectorStoreProvider` and reaches core only through this facade. Three properties, and
one deliberate withholding:

* every promoted name resolves to the SAME object core uses — a facade that re-declared the
  ABC would let an app satisfy the copy while core checked the original, and the failure would
  show up as "my provider is enabled but search never calls it";
* the four abstract methods are pinned by NAME, because that set is the app contract: an
  addition breaks every installed app at instantiation, which is a compatibility decision and
  not a refactor;
* ``register_provider`` / ``unregister_provider`` / ``active_provider`` are deliberately NOT
  promoted. Registration is core's job through the manifest's ``vector_store`` type handler, so
  a backend is bound exactly while its app is enabled. An app reaching the registry directly
  could bind itself without being enabled — or bind a SECOND backend and trip the ambiguity
  refusal — and "which store is knowledge searching" would stop being a property of what the
  user turned on.

The import block is also what keeps these exports off the inert-surface baseline: that gate
counts only ``from personalclaw.sdk... import NAME`` outside ``sdk/``, so a promoted name with
no importer reads as declared-but-inert.
"""

from __future__ import annotations

import pytest

import personalclaw.sdk.vector_store as facade
from personalclaw.sdk.vector_store import (
    VectorHit,
    VectorRecord,
    VectorStoreInfo,
    VectorStoreProvider,
)
from personalclaw.vector_stores.base import VectorHit as core_vector_hit
from personalclaw.vector_stores.base import VectorRecord as core_vector_record
from personalclaw.vector_stores.base import VectorStoreInfo as core_vector_store_info
from personalclaw.vector_stores.base import VectorStoreProvider as core_vector_store_provider

#: The identity table. Nothing asserts a table like this covers ``__all__``, so the
#: completeness check below is derived from ``__all__`` rather than from this list.
_PROMOTED = (
    ("VectorStoreProvider", VectorStoreProvider, core_vector_store_provider),
    ("VectorRecord", VectorRecord, core_vector_record),
    ("VectorHit", VectorHit, core_vector_hit),
    ("VectorStoreInfo", VectorStoreInfo, core_vector_store_info),
)


def test_every_promoted_name_is_the_core_object_not_a_copy() -> None:
    for name, promoted, core in _PROMOTED:
        assert promoted is core, f"sdk.vector_store.{name} is a fork, not a re-export"


def test_the_identity_table_covers_every_export() -> None:
    """The table above is hand-kept, so its completeness is derived from ``__all__``."""
    assert {name for name, _, _ in _PROMOTED} == set(facade.__all__)


def test_the_app_contract_is_exactly_four_methods() -> None:
    """Pinned by name: adding a fifth abstract method breaks every installed app at
    instantiation, which is a compatibility decision, not a refactor."""
    assert VectorStoreProvider.__abstractmethods__ == frozenset(
        {"upsert", "delete_item", "query", "describe"}
    )


def test_registration_is_not_promoted_to_the_sdk() -> None:
    """An app must not be able to bind itself: enablement is the binding."""
    for withheld in ("register_provider", "unregister_provider", "active_provider", "registry"):
        assert not hasattr(facade, withheld), (
            f"sdk.vector_store.{withheld} would let an app bind a backend without being "
            "enabled, or bind a second one — the registry is core's, reached through the "
            "vector_store type handler."
        )


def test_an_app_can_implement_the_contract_through_the_facade_alone() -> None:
    """The point of the seam, executed: a provider written against ``personalclaw.sdk.*`` and
    nothing else is a usable backend."""

    class AppSideStore(VectorStoreProvider):
        name = "app-side"

        def __init__(self) -> None:
            self.rows: dict[str, VectorRecord] = {}

        def upsert(self, records):
            for r in records:
                self.rows[r.chunk_id] = r
            return len(self.rows)

        def delete_item(self, item_id):
            gone = [c for c, r in self.rows.items() if r.item_id == item_id]
            for c in gone:
                del self.rows[c]
            return len(gone)

        def query(self, vector, *, k):
            return [
                VectorHit(chunk_id=r.chunk_id, item_id=r.item_id, similarity=1.0)
                for r in list(self.rows.values())[:k]
            ]

        def describe(self):
            return VectorStoreInfo(backend="app-side", collection="c", reachable=True)

    s = AppSideStore()
    assert s.upsert([VectorRecord(chunk_id="c1", item_id="i1", chunk_index=0, vector=[1.0])]) == 1
    assert s.query([1.0], k=5)[0].item_id == "i1"
    assert s.describe().reachable is True
    assert s.delete_item("i1") == 1


def test_an_incomplete_provider_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):

        class Missing(VectorStoreProvider):
            def upsert(self, records):
                return 0

        Missing()  # type: ignore[abstract]
