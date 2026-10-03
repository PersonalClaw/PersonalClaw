"""An archive import in merge mode brings another home's knowledge library into this one.

Settings → Import / Export merged an archive into a home that already had a knowledge library by
copying what the home lacked file by file. The library is one database file, which the home had, so
none of the archive's items, watched sources or tags came in, and the summary still read
"workspace (merged)". The import now merges the library as a merge restore does, one record at a
time, and its summary says what became of each store the archive held: merged, copied, or left
unchanged and why.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest

from personalclaw import snapshot
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.portability import apply_import_zip, create_export_zip, validate_import_zip

LIBRARY = "workspace/knowledge/knowledge.db"


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    monkeypatch.setattr("personalclaw.snapshot._running_gateway", lambda: None)


def _home(root: Path, monkeypatch) -> Path:
    """A home holding only its settings, made the active one."""
    root.mkdir(parents=True)
    (root / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(root))
    return root


def _open(home: Path) -> KnowledgeStore:
    (home / LIBRARY).parent.mkdir(parents=True, exist_ok=True)
    return KnowledgeStore(str(home / LIBRARY))


def _note(home: Path, title: str, *tags: str) -> str:
    store = _open(home)
    try:
        item = store.create_typed_item(
            item_type="note", title=title, content=f"{title} notes", tags=list(tags)
        )
        assert item
        return item
    finally:
        store.close()


def _document(home: Path, name: str, body: bytes) -> str:
    """A document kept in the library's own files folder, as an upload keeps one."""
    files = home / "workspace" / "knowledge" / "files"
    files.mkdir(parents=True, exist_ok=True)
    store = _open(home)
    try:
        item = store.create_typed_item(item_type="document", title=name)
        assert item
        dest = files / f"{item}.txt"
        dest.write_bytes(body)
        store.update_item(item, file_path=str(dest), mime_type="text/plain")
        store.db.commit()
        return item
    finally:
        store.close()


def _source(home: Path, name: str, url: str) -> None:
    store = _open(home)
    try:
        store.create_source(name=name, provider="web", kind="rss", spec={"url": url})
    finally:
        store.close()


def _library(home: Path) -> dict:
    """What the library holds, read as the Knowledge page reads it: each item's tags by name,
    the watched sources, and what a full-text search for "notes" finds."""
    store = _open(home)
    try:
        ids = [r[0] for r in store.db.execute("SELECT id FROM items ORDER BY title")]
        items = {store.get_item(i)["title"]: sorted(store.get_item(i)["tags"]) for i in ids}
        return {
            "items": items,
            "sources": sorted(s["name"] for s in store.list_sources()),
            "found": sorted(r["title"] for r in store.search_items_fts("notes")),
        }
    finally:
        store.close()


def _export(home: Path, out: Path, monkeypatch) -> Path:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    data, _ = create_export_zip()
    out.write_bytes(data)
    ok, why, _ = validate_import_zip(out)
    assert ok, why
    return out


def _another_homes_archive(tmp_path: Path, monkeypatch) -> Path:
    """The home an archive comes from: a note filed under "garden", a document, a feed."""
    there = _home(tmp_path / "there", monkeypatch)
    _note(there, "Garden plan", "garden")
    _document(there, "planting-calendar.txt", b"sow beans in May")
    _source(there, "Seed catalog", "https://feeds.example.com/seeds.xml")
    return _export(there, tmp_path / "there.zip", monkeypatch)


def test_an_import_brings_another_homes_library_into_this_ones(tmp_path, monkeypatch):
    archive = _another_homes_archive(tmp_path, monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)
    # This home's first tag takes the same number the archive's first tag has.
    _note(here, "Kitchen quotes", "kitchen")
    _source(here, "Cooking blog", "https://blog.example.org/feed")

    summary = apply_import_zip(archive, "merge")

    library = _library(here)
    assert library["items"] == {
        "Garden plan": ["garden"],
        "Kitchen quotes": ["kitchen"],
        "planting-calendar.txt": [],
    }, "each item keeps the tags it was filed under, the archive's and this home's"
    assert library["sources"] == ["Cooking blog", "Seed catalog"]
    assert library["found"] == ["Garden plan", "Kitchen quotes"]
    assert "knowledge library (merged)" in summary["items"]
    assert summary["left_unchanged"] == []


def _kept_at(home: Path, item: str) -> str:
    """The document path the library holds for *item*, read without opening the store."""
    conn = sqlite3.connect(str(home / LIBRARY))
    try:
        return conn.execute("SELECT file_path FROM items WHERE id = ?", (item,)).fetchone()[0]
    finally:
        conn.close()


def test_the_archives_documents_open_in_this_home(tmp_path, monkeypatch):
    """The library keeps each document's path in this home's files folder. An archive's document
    arrives in this home's folder, and its item opens it there, not where the other home kept it,
    at once: the gateway running the import has its library open already."""
    from personalclaw.dashboard.handlers.knowledge import _serve_item_path

    there = _home(tmp_path / "there", monkeypatch)
    item = _document(there, "planting-calendar.txt", b"sow beans in May")
    archive = _export(there, tmp_path / "there.zip", monkeypatch)
    for here in (tmp_path / "with-a-library", tmp_path / "without-one"):
        _home(here, monkeypatch)
        if here.name == "with-a-library":
            _note(here, "Kitchen quotes", "kitchen")

        apply_import_zip(archive, "merge")

        files = here / "workspace" / "knowledge" / "files"
        assert _kept_at(here, item) == str(files / f"{item}.txt"), here.name
        store = _open(here)
        try:
            path, mime = _serve_item_path(store, item, thumbnail=False)
        finally:
            store.close()
        assert path is not None, f"{here.name}: the document does not open"
        assert path.is_relative_to((here / "workspace" / "knowledge" / "files").resolve())
        assert path.read_bytes() == b"sow beans in May" and mime == "text/plain"


def test_a_home_with_no_library_takes_the_archives_whole(tmp_path, monkeypatch):
    archive = _another_homes_archive(tmp_path, monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)

    summary = apply_import_zip(archive, "merge")

    library = _library(here)
    assert library["items"] == {"Garden plan": ["garden"], "planting-calendar.txt": []}
    assert library["sources"] == ["Seed catalog"]
    assert "knowledge library (copied)" in summary["items"]
    assert summary["left_unchanged"] == []


def _with_unreadable(data: bytes, rel: str, out: Path) -> Path:
    """The same archive with *rel* replaced by bytes that are no database, its manifest declaring
    them, so it passes the checks an import makes before it writes anything."""
    with zipfile.ZipFile(io.BytesIO(data)) as zin:
        manifest_name = next(n for n in zin.namelist() if n.endswith("MANIFEST.json"))
        manifest = json.loads(zin.read(manifest_name))
        for member in manifest["members"]:
            if member["path"] == rel:
                member.update(bytes=len(b"not a database"), sha256=_sha(b"not a database"))
        with zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                if info.filename == manifest_name:
                    zout.writestr(info, json.dumps(manifest))
                elif info.filename.endswith("/" + rel):
                    zout.writestr(info, b"not a database")
                else:
                    zout.writestr(info, zin.read(info))
    ok, why, _ = validate_import_zip(out)
    assert ok, why
    return out


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _with_unreadable_library(archive: Path, tmp_path: Path) -> Path:
    return _with_unreadable(archive.read_bytes(), LIBRARY, tmp_path / "damaged.zip")


def test_an_import_that_could_not_merge_the_library_says_so(tmp_path, monkeypatch):
    damaged = _with_unreadable_library(_another_homes_archive(tmp_path, monkeypatch), tmp_path)
    here = _home(tmp_path / "here", monkeypatch)
    _note(here, "Kitchen quotes", "kitchen")

    summary = apply_import_zip(damaged, "merge")

    assert _library(here)["items"] == {"Kitchen quotes": ["kitchen"]}
    assert (
        "knowledge library (left unchanged: the archive's copy could not be merged)"
        in summary["items"]
    )
    assert "knowledge library (merged)" not in summary["items"]
    assert summary["left_unchanged"] == [LIBRARY]


def test_the_terminal_import_says_what_it_left_and_fails(tmp_path, monkeypatch, capsys):
    """`personalclaw restore <export.zip>` applies the dashboard's import, and ends as a merge
    restore ends when a part stayed as it was."""
    damaged = _with_unreadable_library(_another_homes_archive(tmp_path, monkeypatch), tmp_path)
    here = _home(tmp_path / "here", monkeypatch)
    _note(here, "Kitchen quotes", "kitchen")

    assert snapshot.restore_main([str(damaged), "--mode", "merge"]) == 1

    out = capsys.readouterr().out
    assert f"⚠️  Merge finished, but 1 part was left unchanged: {LIBRARY}." in out
    assert _library(here)["items"] == {"Kitchen quotes": ["kitchen"]}


def _memories(db: Path) -> list[str]:
    conn = sqlite3.connect(str(db))
    try:
        return sorted(r[0] for r in conn.execute("SELECT key FROM semantic_memory"))
    finally:
        conn.close()


def _memory_db(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE semantic_memory (key TEXT PRIMARY KEY, value_json TEXT, confidence REAL, "
        "source TEXT, created_at TEXT, updated_at TEXT, embedding BLOB, is_deleted INTEGER "
        "DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO semantic_memory (key, value_json, confidence, source, created_at, "
        "updated_at, is_deleted) VALUES (?, '\"v\"', 0.9, 'agent', '2026-01-01', '2026-01-01', 0)",
        (key,),
    )
    conn.commit()
    conn.close()


def test_a_projects_memories_merge_into_this_homes_copy_of_that_project(tmp_path, monkeypatch):
    """A project's memories live in its own database under the workspace. The import merged the
    home's main memory database and copied the project's only into a home without it."""
    project = "workspace/_ext/garden-site/memory_index.db"
    there = _home(tmp_path / "there", monkeypatch)
    _memory_db(there / project, "beds.layout")
    archive = _export(there, tmp_path / "there.zip", monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)
    _memory_db(here / project, "soil.ph")

    summary = apply_import_zip(archive, "merge")

    assert _memories(here / project) == ["beds.layout", "soil.ph"]
    assert "project memories (merged)" in summary["items"]


def test_what_this_home_keeps_of_its_own_is_named(tmp_path, monkeypatch):
    """A store an import takes only into a home without one is named when this home has it, with
    the reason, instead of passing in silence."""
    there = _home(tmp_path / "there", monkeypatch)
    sqlite3.connect(str(there / "learning.db")).close()
    (there / "feedback.jsonl").write_text('{"id": "f-1"}\n', encoding="utf-8")
    (there / "tool_prefs.json").write_text('{"from": "there"}', encoding="utf-8")
    (there / "routing_policy.json").write_text('{"from": "there"}', encoding="utf-8")
    archive = _export(there, tmp_path / "there.zip", monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)
    sqlite3.connect(str(here / "learning.db")).close()
    (here / "feedback.jsonl").write_text('{"id": "f-2"}\n', encoding="utf-8")
    (here / "tool_prefs.json").write_text('{"from": "here"}', encoding="utf-8")
    (here / "routing_policy.json").write_text('{"from": "here"}', encoding="utf-8")

    items = apply_import_zip(archive, "merge")["items"]

    kept = "left unchanged: this home keeps its own"
    assert f"config ({kept})" in items
    assert f"learning log ({kept})" in items
    assert f"feedback ({kept})" in items
    assert f"2 stores ({kept}: routing_policy.json, tool_prefs.json)" in items
    assert (here / "tool_prefs.json").read_text(encoding="utf-8") == '{"from": "here"}'


def test_a_store_with_nothing_new_is_not_counted_as_merged(tmp_path, monkeypatch):
    """A folder of files whose every file this home already has took nothing in."""
    there = _home(tmp_path / "there", monkeypatch)
    (there / "artifacts").mkdir()
    (there / "artifacts" / "plan.md").write_text("the plan", encoding="utf-8")
    archive = _export(there, tmp_path / "there.zip", monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)
    (here / "artifacts").mkdir()
    (here / "artifacts" / "plan.md").write_text("the plan", encoding="utf-8")

    items = apply_import_zip(archive, "merge")["items"]

    assert not any(re.fullmatch(r"\d+ stores? \(merged\)", i) for i in items), items


def _mirror(home: Path, slug: str, title: str) -> None:
    """An artifact's mirror in the library, written the way the artifact indexer writes one."""
    from personalclaw.knowledge.artifact_ingest import (
        ARTIFACT_ITEM_TYPE,
        ARTIFACT_SOURCE_PROVIDER,
        ensure_source,
    )

    store = _open(home)
    try:
        source_id, _ = ensure_source(store)
        assert store.create_typed_item(
            item_type=ARTIFACT_ITEM_TYPE,
            title=title,
            content=f"{title} body",
            provider=ARTIFACT_SOURCE_PROVIDER,
            source_id=source_id,
            guid=slug,
        )
    finally:
        store.close()


def test_the_artifact_mirror_stays_one_after_an_import(tmp_path, monkeypatch):
    """Each home keeps one source row for its artifact mirror. Merged by minted ids, the other
    home's came in beside this one's, the older of the two answered for the mirror from then on,
    and this home's mirrors were no longer found under it, so the next save of an artifact
    mirrored it a second time."""
    from personalclaw.knowledge.artifact_ingest import ARTIFACT_SOURCE_PROVIDER, find_source

    there = _home(tmp_path / "there", monkeypatch)
    _mirror(there, "seed-list", "Seed list")
    archive = _export(there, tmp_path / "there.zip", monkeypatch)
    here = _home(tmp_path / "here", monkeypatch)
    _mirror(here, "shopping-list", "Shopping list")

    apply_import_zip(archive, "merge")

    store = _open(here)
    try:
        mirrors = [s for s in store.list_sources() if s["provider"] == ARTIFACT_SOURCE_PROVIDER]
        assert len(mirrors) == 1, mirrors
        source = find_source(store)
        assert source is not None
        for slug in ("seed-list", "shopping-list"):
            assert store.find_source_item(str(source["id"]), slug), slug
    finally:
        store.close()
