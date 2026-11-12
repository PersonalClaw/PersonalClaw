"""What the knowledge store keeps for the library (`dashboard/handlers/library.py`): its revision
counter, a knowledge item's move between projects and a findings log's own state.

Mixed into :class:`~personalclaw.knowledge.store.KnowledgeStore`, which opens them after its own
schema and migrations, so the store module keeps to its size and the library's parts sit
together. ``IF NOT EXISTS`` is each one's migration for a library written before it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from personalclaw.instants import utc_now_iso


class LibraryTables:
    """The library's tables, and their reads and writes, over the store's connection."""

    db: Any
    #: The store's tag names for one item, which a move writes back to the search index.
    _tags_for_item: Callable[[str], list[str]]

    #: The tables whose rows the library's read serves, which its revision counter follows.
    LIBRARY_REVISION_TABLES: tuple[str, ...] = (
        "items",
        "item_tags",
        "tags",
        "collections",
        "collection_items",
        "intent_outcomes",
        "intent_log_state",
        "sources",
    )

    _LIBRARY_SQL = """
            -- A findings log's own state, beside its intent rather than on the intent's record:
            -- reading, pinning or archiving the log then never makes an open Edit intent's save
            -- stale. Deleting the intent deletes its row; clearing its findings leaves it.
            CREATE TABLE IF NOT EXISTS intent_log_state (
                intent_id TEXT PRIMARY KEY,
                archived INTEGER NOT NULL DEFAULT 0,
                read INTEGER NOT NULL DEFAULT 0,
                pinned INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
    """

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
        # The library's own tables first, so the triggers below have them to follow.
        self.db.executescript(self._LIBRARY_SQL)
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

    def move_item_project(self, item_id: str, project_id: str, *, policy: Any = None) -> bool:
        """File a knowledge item under *project_id*, taking it out of the project it was in, in
        one write: its ``file_metadata`` project and sharing policy (private unless *policy*
        says shared), and its project tag, the one the project's brief reads it by. ``""``
        takes it out of its project: the project, the policy and the tag all go. Only the project
        tag moves, so every other tag keeps the source that wrote it. A filing, not an edit:
        ``updated_at`` stays, so the library keeps its order. False when there is no such item."""
        from personalclaw.knowledge import project_scope
        from personalclaw.knowledge.session_brief import project_tag
        from personalclaw.knowledge.store import _fts_tags

        row = self.db.execute(
            "SELECT rowid, title, content, file_metadata FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return False
        meta = project_scope.as_metadata_dict(row["file_metadata"])
        old_tag = project_tag(str(meta.get(project_scope.PROJECT_ID_KEY, "") or ""))
        meta.pop(project_scope.PROJECT_ID_KEY, None)
        meta.pop(project_scope.SHARING_POLICY_KEY, None)
        pid = (project_id or "").strip()
        if pid:
            meta.update(project_scope.write_scope(project_id=pid, requested_policy=policy))
        new_tag = project_tag(pid)
        old_fts_tags = _fts_tags(self._tags_for_item(item_id))
        now = utc_now_iso()
        with self.db.held():
            self.db.execute("BEGIN")
            try:
                self.db.execute(
                    "UPDATE items SET file_metadata = ? WHERE id = ?", (json.dumps(meta), item_id)
                )
                if old_tag and old_tag != new_tag:
                    self.db.execute(
                        "DELETE FROM item_tags WHERE item_id = ? AND tag_id IN "
                        "(SELECT id FROM tags WHERE name = ?)",
                        (item_id, old_tag),
                    )
                if new_tag:
                    self.db.execute(
                        "INSERT OR IGNORE INTO tags (name, created_at) VALUES (?, ?)",
                        (new_tag, now),
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO item_tags (item_id, tag_id, source, added_at) "
                        "SELECT ?, id, 'user', ? FROM tags WHERE name = ?",
                        (item_id, now, new_tag),
                    )
                self.db.execute(
                    "INSERT INTO items_fts (items_fts, rowid, title, content, tags) "
                    "VALUES ('delete', ?, ?, ?, ?)",
                    (row["rowid"], row["title"], row["content"], old_fts_tags),
                )
                self.db.execute(
                    "INSERT INTO items_fts (rowid, title, content, tags) VALUES (?, ?, ?, ?)",
                    (
                        row["rowid"],
                        row["title"],
                        row["content"],
                        _fts_tags(self._tags_for_item(item_id)),
                    ),
                )
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        from personalclaw.knowledge import maintenance

        maintenance.mark_dirty(reason="update tags")
        return True

    #: A findings log's flags, as :meth:`intent_log_states` answers them.
    LOG_FLAGS: tuple[str, ...] = ("archived", "read", "pinned")

    def intent_log_states(self) -> dict[str, dict[str, bool]]:
        """Map of intent_id → its findings log's ``archived``, ``read`` and ``pinned``. An intent
        with no row has all three off."""
        rows = self.db.execute("SELECT intent_id, archived, read, pinned FROM intent_log_state")
        return {r["intent_id"]: {k: bool(r[k]) for k in self.LOG_FLAGS} for r in rows}

    def set_intent_log_state(self, intent_id: str, **flags: bool) -> dict[str, bool]:
        """Set the findings log flags *flags* names (any of :data:`LOG_FLAGS`) for *intent_id*,
        leaving the others. Answers the log's state as it now is."""
        unknown = set(flags) - set(self.LOG_FLAGS)
        if unknown:
            raise ValueError(f"unknown findings log flags: {sorted(unknown)}")
        state = {
            **dict.fromkeys(self.LOG_FLAGS, False),
            **self.intent_log_states().get(intent_id, {}),
        }
        state.update({k: bool(v) for k, v in flags.items()})
        self.db.execute(
            "INSERT INTO intent_log_state (intent_id, archived, read, pinned, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(intent_id) DO UPDATE SET "
            "archived = excluded.archived, read = excluded.read, pinned = excluded.pinned, "
            "updated_at = excluded.updated_at",
            (
                intent_id,
                int(state["archived"]),
                int(state["read"]),
                int(state["pinned"]),
                utc_now_iso(),
            ),
        )
        self.db.commit()
        return state

    def delete_intent_log_state(self, intent_id: str) -> None:
        """Drop an intent's findings log state (when the intent itself is deleted)."""
        self.db.execute("DELETE FROM intent_log_state WHERE intent_id = ?", (intent_id,))
        self.db.commit()
