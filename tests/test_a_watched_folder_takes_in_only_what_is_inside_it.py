"""A watched folder takes in only what is inside it.

A link in a folder she shared names a file somewhere else. The folder's scan opened the link,
read the file it names, and put that file's text in the library: from there it reached search,
recall and the item's embedding, although she shared the folder and not the place the link
points to. The agent's file tools already refused the same link (``file_scope``), so the two
disagreed about what the folder shares.

Now the scan asks the rule the file tools ask (``dir_source.resolve_in`` then ``takes``):

* a file or folder whose real path is outside the folder is left out, and the source row says
  how many and why;
* a link to a file inside the folder is that file, which the folder takes in once under its own
  name;
* a note an earlier scan took in through a link out of the folder is removed at the next scan,
  with its sighting, so a file later saved under that name comes in as new.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.dir_source import DirSourceProvider

_PRIVATE = "The spare key is under the blue pot"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


@pytest.fixture()
def place(tmp_path):
    """Her notes folder, and a folder beside it she shared with nobody."""
    root = Path(os.path.realpath(tmp_path))
    notes = root / "notes"
    (notes / "Garden").mkdir(parents=True)
    private = root / "private"
    private.mkdir()
    (private / "diary.md").write_text(f"# Diary\n{_PRIVATE}\n", encoding="utf-8")
    (private / "more.md").write_text(f"{_PRIVATE}, again\n", encoding="utf-8")
    return notes, private


class _Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs


class _Queue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)

    def enqueue_background(self, item_id: str) -> None:
        self.enqueue(item_id)

    def recover_pending(self) -> int:
        return 0


def _cfg():
    from personalclaw.config.loader import SourcesConfig

    return SourcesConfig(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
    )


def _write(path: Path, text: str, *, mtime: float) -> None:
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _source(store, notes: Path, clock: _Clock):
    sid = store.create_source(
        name="Notes",
        provider="watched-dir",
        kind="dir",
        spec={"path": str(notes), "debounce_secs": 10.0},
        item_type="note",
    )
    provider = DirSourceProvider(store, now_fn=clock)
    engine = SourceEngine(
        store,
        _Queue(),
        providers_lister=lambda: [provider],
        config_loader=_cfg,
        now_fn=clock,
    )
    return sid, provider, engine


async def _poll(engine, store, sid) -> int:
    return await engine.poll_source(store.get_source(sid), _cfg())


def _guids(store, sid) -> set[str]:
    rows = store.db.execute("SELECT guid FROM items WHERE source_id = ?", (sid,)).fetchall()
    return {row["guid"] for row in rows}


def _texts(store) -> str:
    rows = store.db.execute("SELECT COALESCE(content, '') AS c FROM items").fetchall()
    return "\n".join(row["c"] for row in rows)


@pytest.mark.asyncio
async def test_a_link_out_of_the_folder_is_not_taken_in_and_the_row_says_so(store, place):
    """🔴 Before: ``linked.md`` came in with the diary's text."""
    notes, private = place
    clock = _Clock()
    _write(notes / "today.md", "- water the tomatoes\n", mtime=clock.t - 3600)
    (notes / "linked.md").symlink_to(private / "diary.md")
    (notes / "Garden" / "elsewhere").symlink_to(private, target_is_directory=True)
    sid, provider, engine = _source(store, notes, clock)

    assert await _poll(engine, store, sid) == 1
    assert _guids(store, sid) == {"today.md"}
    assert _PRIVATE not in _texts(store)
    assert store.search_items_fts("spare key") == []
    status = provider.links_outside(store.get_source_cursor(sid))
    assert status == 2, "the file and the folder that lead outside are both counted"

    clock.advance(3600)
    assert await _poll(engine, store, sid) == 0
    assert _guids(store, sid) == {"today.md"}
    assert provider.links_outside(store.get_source_cursor(sid)) == 2, "said again at every scan"

    (notes / "linked.md").unlink()
    (notes / "Garden" / "elsewhere").unlink()
    clock.advance(3600)
    await _poll(engine, store, sid)
    assert provider.links_outside(store.get_source_cursor(sid)) == 0, "gone once they are"


@pytest.mark.asyncio
async def test_a_link_to_a_file_inside_the_folder_is_taken_in_once(store, place):
    """🔴 Before: ``alias.md`` came in as a second note with ``today.md``'s text."""
    notes, _private = place
    clock = _Clock()
    _write(notes / "today.md", "- water the tomatoes\n", mtime=clock.t - 3600)
    _write(notes / "Garden" / "beds.md", "raised beds\n", mtime=clock.t - 3600)
    (notes / "alias.md").symlink_to(notes / "today.md")
    (notes / "Beds").symlink_to(notes / "Garden", target_is_directory=True)
    sid, provider, engine = _source(store, notes, clock)

    assert await _poll(engine, store, sid) == 2
    assert _guids(store, sid) == {"today.md", "Garden/beds.md"}
    assert provider.links_outside(store.get_source_cursor(sid)) == 0, "nothing here leads out"


@pytest.mark.asyncio
async def test_a_note_taken_in_through_a_link_out_is_removed_at_the_next_scan(store, place):
    """A library an earlier scan filled through a link out of the folder: its note goes, with its
    sighting, and the removal is a no-op when repeated."""
    notes, private = place
    clock = _Clock()
    _write(notes / "today.md", "- water the tomatoes\n", mtime=clock.t - 3600)
    (notes / "linked.md").symlink_to(private / "diary.md")
    sid, _provider, engine = _source(store, notes, clock)
    # What the scan used to leave behind: the linked file's text as a note, its sighting, and the
    # folder's baseline listing it.
    leaked = store.create_typed_item(
        item_type="note",
        title="linked.md",
        content=(private / "diary.md").read_text(encoding="utf-8"),
        provider="watched-dir",
        source_id=sid,
        guid="linked.md",
    )
    kept = store.create_typed_item(
        item_type="note",
        title="today.md",
        content="- water the tomatoes\n",
        provider="watched-dir",
        source_id=sid,
        guid="today.md",
    )
    entity = store.add_entity("Blue Pot", "thing")
    store.add_mention(leaked, entity)
    stat = (notes / "today.md").stat()
    linked = (private / "diary.md").stat()
    store.record_poll(
        sid,
        cursor=json.dumps(
            {
                "first_scan": {"found": 2, "left_out": 0},
                "sigs": {
                    "today.md": [stat.st_mtime, stat.st_size],
                    "linked.md": [linked.st_mtime, linked.st_size],
                },
                "gone": {},
                "tombstones": {},
                "waiting": 0,
            }
        ),
        new_count=2,
    )
    assert store.search_items_fts("spare key")

    await _poll(engine, store, sid)
    assert store.get_item(leaked) is None, "removed, not archived: it was never hers to keep"
    assert store.get_item(kept) is not None
    assert _PRIVATE not in _texts(store)
    assert store.search_items_fts("spare key") == []
    assert store.db.execute("SELECT 1 FROM entities WHERE id = ?", (entity,)).fetchone() is None
    seen = store.db.execute(
        "SELECT 1 FROM source_seen WHERE source_id = ? AND guid = ?", (sid, "linked.md")
    ).fetchone()
    assert seen is None
    assert "linked.md" not in json.loads(store.get_source_cursor(sid))["sigs"]

    clock.advance(3600)
    await _poll(engine, store, sid)
    assert _guids(store, sid) == {"today.md"}, "the next scan finds nothing left to remove"

    # A file she later saves under that name is hers, and comes in as a new note.
    (notes / "linked.md").unlink()
    _write(notes / "linked.md", "- buy seeds\n", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    assert _guids(store, sid) == {"today.md", "linked.md"}


@pytest.mark.asyncio
async def test_a_file_swapped_for_a_link_out_after_the_scan_is_not_read(store, place):
    """The read checks again: a file replaced by a link between the scan and the read is not
    opened."""
    notes, private = place
    provider = DirSourceProvider(store)
    spec = {"path": str(notes)}
    (notes / "today.md").write_text("- water the tomatoes\n", encoding="utf-8")
    assert provider._read(spec, "today.md") == "- water the tomatoes\n"

    (notes / "today.md").unlink()
    (notes / "today.md").symlink_to(private / "diary.md")
    assert provider._read(spec, "today.md") is None


def test_the_scan_and_the_file_tools_agree_on_what_the_folder_shares(
    store, place, tmp_path, monkeypatch
):
    """One rule: every file the agent's file tools may read in the folder is a file the scan takes
    in, and the other way round."""
    from personalclaw.file_scope import FileScope

    notes, private = place
    (notes / "today.md").write_text("- water the tomatoes\n", encoding="utf-8")
    (notes / "Garden" / "beds.md").write_text("raised beds\n", encoding="utf-8")
    (notes / "scan.txt").write_text("not a note\n", encoding="utf-8")
    (notes / "alias.md").symlink_to(notes / "today.md")
    (notes / "hidden.md").symlink_to(notes / "Garden" / ".drafts.md")
    (notes / "Garden" / ".drafts.md").write_text("half a thought\n", encoding="utf-8")
    (notes / "linked.md").symlink_to(private / "diary.md")
    (notes / "Garden" / "elsewhere").symlink_to(private, target_is_directory=True)

    pc_home = Path(os.path.realpath(tmp_path)) / "pc-home"
    (pc_home / "workspace").mkdir(parents=True)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    from personalclaw.knowledge import get_knowledge_store

    spec = {"path": str(notes), "include": ["*.md"]}
    get_knowledge_store().create_source(
        name="Notes", provider="watched-dir", kind="dir", spec=spec, item_type="note"
    )
    scope = FileScope([pc_home / "workspace"])

    readable = set()
    for dirpath, dirnames, filenames in os.walk(notes, followlinks=True):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            real = scope.admits(path)
            if real and os.path.isfile(real):
                readable.add(real)
    scan = DirSourceProvider(store).scan(spec)
    taken = {str(notes / rel) for rel in scan.sigs}

    assert taken == {str(notes / "today.md"), str(notes / "Garden" / "beds.md")}
    assert readable == taken
