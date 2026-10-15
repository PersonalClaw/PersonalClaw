"""A deleted artifact leaves Knowledge even while another connection writes the store.

Measured on a CI run: an artifact deleted outside the gateway stayed searchable in Knowledge. The
gateway heard of the delete and asked its store to forget the artifact's copy, and SQLite refused
the first delete at once with "database is locked". The forget's transaction began deferred: it
read the item, and asked for the database's write lock only at its first delete. In between,
another connection had written the store (the gateway compacts its stores once a day, from
a connection of its own), and SQLite refuses a write from a transaction whose first read is older
than the newest commit, without waiting. Nothing asks again, so the copy outlived its artifact.

The forget now takes the write lock before it reads: another connection's write waits for it.
"""

from __future__ import annotations

from personalclaw.knowledge.artifact_ingest import (
    ARTIFACT_ITEM_TYPE,
    ARTIFACT_SOURCE_PROVIDER,
    ensure_source,
)
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.sqlite_compat import sqlite3


def test_a_write_from_another_connection_waits_for_the_forget_and_never_refuses_it(
    tmp_path, monkeypatch
):
    path = tmp_path / "knowledge.db"
    store = KnowledgeStore(str(path))
    source_id, _created = ensure_source(store)
    store.create_typed_item(
        item_type=ARTIFACT_ITEM_TYPE,
        title="Buoy log",
        content="Red buoy relit at dawn.",
        provider=ARTIFACT_SOURCE_PROVIDER,
        source_id=source_id,
        guid="buoy-log",
    )
    assert [item["title"] for item in store.search_items_fts("relit")] == ["Buoy log"]

    # The same driver as the store's: two copies of SQLite on one file do not see each other's
    # locks (personalclaw.sqlite_compat).
    elsewhere = sqlite3.connect(str(path), timeout=0.2, isolation_level=None)
    tried: list[str] = []
    tags_for_item = store._tags_for_item

    def then_another_connection_writes(item_id: str) -> list[str]:
        """The forget has read the item and not yet deleted anything: another connection writes
        the store now, once."""
        tags = tags_for_item(item_id)
        if not tried:
            try:
                elsewhere.execute(
                    "INSERT INTO source_seen (source_id, guid, first_seen_at) VALUES (?, ?, ?)",
                    (source_id, "tide-table", "2026-10-14T06:00:00+00:00"),
                )
                tried.append("written")
            except sqlite3.OperationalError:
                tried.append("waited")
        return tags

    monkeypatch.setattr(store, "_tags_for_item", then_another_connection_writes)
    try:
        assert store.forget_source_item(source_id, "buoy-log") is True
    finally:
        elsewhere.close()

    assert tried == ["waited"], "the other connection wrote in the middle of the forget"
    assert store.find_source_item(source_id, "buoy-log") is None
    assert store.search_items_fts("relit") == [], "the deleted artifact is still found"
