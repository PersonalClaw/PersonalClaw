"""What the knowledge store keeps for the library (`dashboard/handlers/library.py`): its revision
counter.

Mixed into :class:`~personalclaw.knowledge.store.KnowledgeStore`, which opens them after its own
schema and migrations, so the store module keeps to its size and the library's parts sit
together. ``IF NOT EXISTS`` is each one's migration for a library written before it.
"""

from __future__ import annotations

from typing import Any


class LibraryTables:
    """The library's tables, and their reads and writes, over the store's connection."""

    db: Any

    #: The tables whose rows the library's read serves, which its revision counter follows.
    LIBRARY_REVISION_TABLES: tuple[str, ...] = (
        "items",
        "item_tags",
        "tags",
        "collections",
        "collection_items",
        "intent_outcomes",
        "sources",
    )

    def _init_library_revision(self) -> None:
        """A one-row counter that SQLite triggers move after any insert, update or delete on the
        tables the library's read serves (:data:`LIBRARY_REVISION_TABLES`), so the read can say
        whether anything changed without reading a row. Every writer is seen, those that leave
        ``updated_at`` alone included (read state and the star), and a write from another
        connection too: the triggers run inside the writing statement. ``PRAGMA data_version``
        would not do, since in WAL it moves only for other connections' commits. ``IF NOT
        EXISTS`` is the migration for a library written before it. Its one row is keyed by name,
        not numbered, so a merge of another home's library keeps this home's counter (and the
        rows the merge brings in move it, through the same triggers)."""
        statements = [
            "CREATE TABLE IF NOT EXISTS library_revision (name TEXT PRIMARY KEY,"
            " n INTEGER NOT NULL)",
            "INSERT OR IGNORE INTO library_revision (name, n) VALUES ('library', 0)",
        ]
        for table in self.LIBRARY_REVISION_TABLES:
            for event in ("INSERT", "UPDATE", "DELETE"):
                statements.append(
                    f"CREATE TRIGGER IF NOT EXISTS library_revision_{table}_{event.lower()} "
                    f"AFTER {event} ON {table} BEGIN "
                    "UPDATE library_revision SET n = n + 1 WHERE name = 'library'; END"
                )
        self.db.executescript(";\n".join(statements) + ";")

    def library_revision(self) -> int:
        """The library's revision counter: it moves whenever a row the library serves does."""
        row = self.db.execute("SELECT n FROM library_revision WHERE name = 'library'").fetchone()
        return int(row[0]) if row else 0
