"""A knowledge library opens its documents after it moves to another home.

An item keeps its document as a path into the library's files folder under the home that wrote it,
and the file route serves only from this library's folder. A library that came from another home —
an archive imported here, a snapshot restored on another machine, a home folder moved — named the
other home's folder, so each document it brought read "not found" though its file was there. The
library now points such a path at its own copy when it opens, and only at a file of its own folder
that no other item keeps.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from personalclaw.dashboard.handlers.knowledge import _serve_item_path
from personalclaw.knowledge.store import KnowledgeStore

LIBRARY = "workspace/knowledge/knowledge.db"
FILES = "workspace/knowledge/files"


def _with_document(home: Path, name: str, body: bytes) -> tuple[str, Path]:
    """A library holding one document, kept in its files folder as an upload keeps one."""
    (home / FILES).mkdir(parents=True, exist_ok=True)
    store = KnowledgeStore(str(home / LIBRARY))
    try:
        item = store.create_typed_item(item_type="document", title=name)
        assert item
        dest = home / FILES / f"{item}.txt"
        dest.write_bytes(body)
        store.update_item(item, file_path=str(dest), mime_type="text/plain")
        store.db.commit()
        return item, dest
    finally:
        store.close()


def _naming(home: Path, title: str, path: str) -> str:
    """An item whose document path another home's library wrote."""
    store = KnowledgeStore(str(home / LIBRARY))
    try:
        item = store.create_typed_item(item_type="document", title=title)
        assert item
        store.update_item(item, file_path=path, mime_type="text/plain")
        store.db.commit()
        return item
    finally:
        store.close()


def _opened(home: Path, item: str) -> tuple[Path | None, dict]:
    store = KnowledgeStore(str(home / LIBRARY))
    try:
        path, _mime = _serve_item_path(store, item, thumbnail=False)
        return path, store.get_item(item)
    finally:
        store.close()


def test_a_library_moved_to_another_home_opens_its_documents_there(tmp_path, monkeypatch):
    there, here = tmp_path / "there", tmp_path / "here"
    item, _ = _with_document(there, "planting-calendar.txt", b"sow beans in May")
    store = KnowledgeStore(str(there / LIBRARY))
    try:
        written = store.get_item(item)["updated_at"]
    finally:
        store.close()
    shutil.copytree(there / "workspace", here / "workspace")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(here))

    path, row = _opened(here, item)

    assert path == (here / FILES / f"{item}.txt").resolve()
    assert path.read_bytes() == b"sow beans in May"
    assert row["file_path"] == str(here / FILES / f"{item}.txt")
    assert row["updated_at"] == written, "pointing it here is not an edit"


def test_a_document_this_library_keeps_is_never_taken_over(tmp_path, monkeypatch):
    """Two items never share one file: deleting either would delete the other's document."""
    here = tmp_path / "here"
    mine, dest = _with_document(here, "planting-calendar.txt", b"sow beans in May")
    stranger_path = f"/home/user/elsewhere/{FILES}/{dest.name}"
    stranger = _naming(here, "another calendar", stranger_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(here))

    path, row = _opened(here, stranger)

    assert path is None and row["file_path"] == stranger_path
    assert _opened(here, mine)[0] == dest.resolve()


def test_a_path_is_only_ever_pointed_inside_the_library_folder(tmp_path, monkeypatch):
    here = tmp_path / "here"
    _with_document(here, "planting-calendar.txt", b"sow beans in May")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not the library's")
    (here / FILES / "linked.txt").symlink_to(outside)
    linked = _naming(here, "linked", f"/home/user/elsewhere/{FILES}/linked.txt")
    climbing = _naming(here, "climbing", f"/home/user/elsewhere/{FILES}/../../outside.txt")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(here))

    for item, path in (
        (linked, f"/home/user/elsewhere/{FILES}/linked.txt"),
        (climbing, f"/home/user/elsewhere/{FILES}/../../outside.txt"),
    ):
        served, row = _opened(here, item)
        assert served is None and row["file_path"] == path
