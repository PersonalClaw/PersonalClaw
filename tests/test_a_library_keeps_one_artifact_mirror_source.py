"""A library keeps one source row for its artifact mirror, whatever a merge brought in.

Each home made that row under an id it minted, so a merge restore, an archive import or a folder
sync of another home's library brought the other home's row in beside this one's: Sources listed
Artifacts twice, the older row answered for the mirror from then on, and this home's mirrors were
no longer found under it, so the next save of an artifact mirrored it a second time. The row now
has the same id in every home, and a library holding more than one such row (an earlier merge's,
or one written before) folds them into it when it opens.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.knowledge.artifact_ingest import (
    ARTIFACT_ITEM_TYPE,
    ARTIFACT_SOURCE_PROVIDER,
    ensure_source,
    find_source,
)
from personalclaw.knowledge.store import KnowledgeStore


def _library(root: Path) -> KnowledgeStore:
    db = root / "workspace" / "knowledge" / "knowledge.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return KnowledgeStore(str(db))


def _row_made_elsewhere(store: KnowledgeStore, sid: str, created_at: str) -> None:
    """An artifact mirror's row under an id one home minted for it."""
    store.db.execute(
        "INSERT INTO sources (id, name, provider, kind, spec, enrichment, poll_interval_secs, "
        "item_type, created_by, created_at, updated_at) VALUES (?, 'Artifacts', ?, 'artifact', "
        "'{\"uri\": \"artifact://\"}', 'raw', 0, ?, 'system', ?, ?)",
        (sid, ARTIFACT_SOURCE_PROVIDER, ARTIFACT_ITEM_TYPE, created_at, created_at),
    )


def _mirror(store: KnowledgeStore, sid: str, slug: str, title: str, written: str) -> None:
    item = store.create_typed_item(
        item_type=ARTIFACT_ITEM_TYPE,
        title=title,
        content=f"{title} body",
        provider=ARTIFACT_SOURCE_PROVIDER,
        source_id=sid,
        guid=slug,
    )
    assert item
    store.db.execute("UPDATE items SET updated_at = ? WHERE id = ?", (written, item))


def _mirrors(store: KnowledgeStore) -> dict[str, str]:
    """Each mirrored artifact's title, as the mirror's own row finds it."""
    source = find_source(store)
    assert source is not None
    rows = store.db.execute(
        "SELECT guid FROM items WHERE item_type = ? ORDER BY guid", (ARTIFACT_ITEM_TYPE,)
    ).fetchall()
    found = {r[0]: store.find_source_item(str(source["id"]), r[0]) for r in rows}
    return {slug: (item or {}).get("title", "") for slug, item in found.items()}


def test_every_home_gives_its_artifact_mirror_the_same_row(tmp_path):
    here, there = _library(tmp_path / "here"), _library(tmp_path / "there")
    try:
        assert ensure_source(here)[0] == ensure_source(there)[0]
    finally:
        here.close()
        there.close()


def test_a_library_holding_two_mirror_rows_folds_them_into_one(tmp_path):
    store = _library(tmp_path)
    try:
        _row_made_elsewhere(store, "src-1a2b3c4d", "2026-09-01T08:00:00+00:00")
        _row_made_elsewhere(store, "src-5e6f7a8b", "2026-09-02T08:00:00+00:00")
        _mirror(store, "src-1a2b3c4d", "seed-list", "Seed list", "2026-09-03T08:00:00+00:00")
        _mirror(store, "src-1a2b3c4d", "plan", "Plan as it was", "2026-09-03T08:00:00+00:00")
        _mirror(store, "src-5e6f7a8b", "plan", "Plan as it is", "2026-09-05T08:00:00+00:00")
        _mirror(
            store, "src-5e6f7a8b", "shopping-list", "Shopping list", "2026-09-04T08:00:00+00:00"
        )
    finally:
        store.close()

    for _ in range(2):  # opened again, a folded library stays as it is
        store = _library(tmp_path)
        try:
            rows = [s for s in store.list_sources() if s["provider"] == ARTIFACT_SOURCE_PROVIDER]
            assert len(rows) == 1, rows
            # Of two mirrors of one artifact, the one the mirror's row holds stays: a row folded
            # in never replaces it.
            assert _mirrors(store) == {
                "plan": "Plan as it was",
                "seed-list": "Seed list",
                "shopping-list": "Shopping list",
            }
            seen = store.db.execute("SELECT source_id, guid FROM source_seen").fetchall()
            assert sorted((r[0], r[1]) for r in seen) == [
                (rows[0]["id"], "plan"),
                (rows[0]["id"], "seed-list"),
                (rows[0]["id"], "shopping-list"),
            ]
        finally:
            store.close()
