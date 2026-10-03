"""Dir-source: signature-diff observer, debounce, archive-on-delete.

Covers the change's acceptance criteria as COUNTING claims, not liveness ones:

* editing three files inside one debounce window re-indexes each **exactly once** — asserted
  as `len(queue.enqueued) == 3`, and three edits to the SAME file collapse to exactly one;
* a create yields a NEW item while a modify re-enqueues the EXISTING item (item count
  unchanged, same id);
* a deleted file ARCHIVES its item with `source_deleted_at` and the row **survives** — plus a
  structural rail that neither the provider nor the engine contains a `DELETE FROM items`,
  so the dangerous direction stays unreachable rather than merely unused;
* the first pass SEEDS only (no startup ingestion storm);
* one unreadable file does not abort the cycle.

Time is injected (`now_fn`) — the debounce window is driven at exact instants, never slept on.
Isolation: tmp_path db + PERSONALCLAW_HOME so nothing reaches the real home.
"""

import json
import os

import pytest

from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.base import (
    CHANGE_CREATED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    SOURCE_CHANGES,
    SourceItem,
)
from personalclaw.knowledge_providers.dir_source import (
    DEFAULT_DEBOUNCE_SECS,
    MAX_FILES_PER_SOURCE,
    DirSourceProvider,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _no_settle_wait(monkeypatch):
    """A file still being written waits out a settle window before it is taken. These tests are
    about what the folder's observer reports and what the engine keeps of it, so the window is not
    waited out here: settling has tests of its own
    (``test_a_watched_folders_files_are_taken_as_uploads_are.py``)."""
    from personalclaw.knowledge import file_items

    monkeypatch.setattr(file_items, "SETTLE_SECS", 0.0)


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


@pytest.fixture()
def watched(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    return d


class _Clock:
    """A hand-driven clock: the debounce window is advanced explicitly, never slept."""

    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


class _FakeQueue:
    def __init__(self):
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)

    def enqueue_background(self, item_id: str) -> None:
        self.enqueue(item_id)

    def recover_pending(self) -> int:
        return 0


def _cfg(**over):
    from personalclaw.config.loader import SourcesConfig

    base = dict(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
    )
    base.update(over)
    return SourcesConfig(**base)


def _write(path, text, *, mtime):
    """Write a file and pin its mtime, so a signature change is deterministic (two writes
    inside one filesystem mtime granularity tick would otherwise look identical)."""
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _setup(store, watched, clock, **spec_over):
    """A dir source + its provider + an engine wired to a recording queue."""
    spec = {"path": str(watched), "debounce_secs": 10.0}
    spec.update(spec_over)
    sid = store.create_source(
        name="notes", provider="watched-dir", kind="dir", spec=spec, item_type="note"
    )
    provider = DirSourceProvider(store, now_fn=clock)
    queue = _FakeQueue()
    engine = SourceEngine(
        store,
        queue,
        providers_lister=lambda: [provider],
        config_loader=lambda: _cfg(),
        now_fn=clock,
    )
    return sid, provider, engine, queue


async def _poll(engine, store, sid):
    return await engine.poll_source(store.get_source(sid), _cfg())


async def _ingest(store, item_id):
    """Read an item the poll queued, as the gateway's ingest queue does: a folder's file is read by
    the reader for its kind when its item is ingested, as an upload is."""
    from personalclaw.knowledge.pipeline.runner import ingest_item

    await ingest_item(store, item_id)


def _items(store, sid):
    return store.db.execute(
        "SELECT * FROM items WHERE source_id = ? ORDER BY guid", (sid,)
    ).fetchall()


# ── the contract itself ────────────────────────────────────────────────────────


def test_change_vocabulary_is_closed_and_default_is_created():
    assert SOURCE_CHANGES == {CHANGE_CREATED, CHANGE_MODIFIED, CHANGE_DELETED}
    # An append-only feed provider must keep working unchanged.
    assert SourceItem(guid="g", title="t").change == CHANGE_CREATED


# ── the first scan: what is already in the folder comes in, within a bound ─────────


@pytest.mark.asyncio
async def test_first_scan_brings_in_the_files_already_there(store, watched):
    """Adding a folder brings in what is in it. The first pass used to record a baseline
    and emit nothing, so seven folders of notes arrived as seven healthy sources with no
    notes in the library, and nothing said why."""
    clock = _Clock()
    for i in range(5):
        _write(watched / f"n{i}.md", f"note {i}", mtime=clock.t - 3600)
    sid, prov, engine, queue = _setup(store, watched, clock)

    assert await _poll(engine, store, sid) == 5
    assert {r["guid"] for r in _items(store, sid)} == {f"n{i}.md" for i in range(5)}
    assert len(queue.enqueued) == 5
    assert prov.first_scan_status(store.get_source_cursor(sid)) == {
        "found": 5,
        "left_out": 0,
        "waiting": 0,
    }


@pytest.mark.asyncio
async def test_after_the_first_scan_later_polls_are_incremental(store, watched):
    clock = _Clock()
    _write(watched / "a.md", "a", mtime=clock.t - 3600)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    assert await _poll(engine, store, sid) == 1

    clock.advance(3600)
    assert await _poll(engine, store, sid) == 0, "a quiet folder brings in nothing new"
    assert len(queue.enqueued) == 1

    _write(watched / "b.md", "b", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1, "a file added later comes in on its own"
    assert {r["guid"] for r in _items(store, sid)} == {"a.md", "b.md"}


@pytest.mark.asyncio
async def test_first_scan_holds_a_file_still_being_written_then_brings_it_in(store, watched):
    """The debounce applies to the first scan too: a note saved a moment ago is read once it
    is quiet, never half-written."""
    clock = _Clock()
    _write(watched / "old.md", "settled", mtime=clock.t - 3600)
    _write(watched / "fresh.md", "being written", mtime=clock.t)
    sid, _prov, engine, _queue = _setup(store, watched, clock)

    assert await _poll(engine, store, sid) == 1
    assert {r["guid"] for r in _items(store, sid)} == {"old.md"}

    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    assert {r["guid"] for r in _items(store, sid)} == {"old.md", "fresh.md"}


@pytest.mark.asyncio
async def test_first_scan_stops_at_its_file_bound_newest_first(store, watched, monkeypatch):
    """The first scan takes the most recently changed files up to its bound and records how
    many it left out, so the page can say so; a file it left out comes in once it changes."""
    import personalclaw.knowledge_providers.dir_source as dir_mod

    monkeypatch.setattr(dir_mod, "FIRST_SCAN_MAX_FILES", 3)
    clock = _Clock()
    for i in range(5):  # n4.md is the newest, n0.md the oldest
        _write(watched / f"n{i}.md", f"note {i}", mtime=clock.t - 1000 + i)
    sid, prov, engine, _queue = _setup(store, watched, clock)

    assert await _poll(engine, store, sid) == 3
    assert {r["guid"] for r in _items(store, sid)} == {"n2.md", "n3.md", "n4.md"}
    assert prov.first_scan_status(store.get_source_cursor(sid)) == {
        "found": 5,
        "left_out": 2,
        "waiting": 0,
    }

    clock.advance(1)
    _write(watched / "n0.md", "note 0, edited", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    assert "n0.md" in {r["guid"] for r in _items(store, sid)}


@pytest.mark.asyncio
async def test_first_scan_stops_at_its_byte_bound(store, watched, monkeypatch):
    import personalclaw.knowledge_providers.dir_source as dir_mod

    monkeypatch.setattr(dir_mod, "FIRST_SCAN_MAX_BYTES", 250)
    clock = _Clock()
    for i in range(4):  # 100 bytes each; n3.md newest
        _write(watched / f"n{i}.md", str(i) * 100, mtime=clock.t - 1000 + i)
    sid, prov, engine, _queue = _setup(store, watched, clock)

    assert await _poll(engine, store, sid) == 2
    assert {r["guid"] for r in _items(store, sid)} == {"n2.md", "n3.md"}
    assert prov.first_scan_status(store.get_source_cursor(sid))["left_out"] == 2


@pytest.mark.asyncio
async def test_a_first_scan_bigger_than_one_poll_arrives_over_several_and_loses_nothing(
    store, watched
):
    """The engine indexes at most ``max_items_per_poll`` sightings of one poll, so a folder
    handing it more used to lose every file past the cap: its baseline had already moved on.
    The folder now stops at the cap and says how many are still to come."""
    clock = _Clock()
    for i in range(7):
        _write(watched / f"n{i}.md", f"note {i}", mtime=clock.t - 3600 + i)
    sid, prov, engine, queue = _setup(store, watched, clock)
    capped = _cfg(max_items_per_poll=3)

    counts, waiting = [], []
    for _ in range(4):
        counts.append(await engine.poll_source(store.get_source(sid), capped))
        waiting.append(prov.first_scan_status(store.get_source_cursor(sid))["waiting"])
        clock.advance(300)

    assert counts == [3, 3, 1, 0]
    assert waiting == [4, 1, 0, 0]
    assert len(_items(store, sid)) == 7
    assert len(queue.enqueued) == 7


@pytest.mark.asyncio
async def test_a_folder_watched_before_the_first_scan_existed_gets_its_files(store, watched):
    """A folder added while the first pass only recorded a baseline has its files brought in
    on its next poll. An item that already exists (its file was edited since) is not doubled:
    the source's novelty gate refuses a second row for a guid it has seen."""
    clock = _Clock()
    for name in ("a.md", "b.md", "c.md"):
        _write(watched / name, name, mtime=clock.t - 3600)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    edited = store.create_typed_item(
        item_type="note",
        title="b.md",
        content="b.md",
        provider="watched-dir",
        source_id=sid,
        guid="b.md",
    )
    sigs = {name: [clock.t - 3600, len(name)] for name in ("a.md", "b.md", "c.md")}
    store.record_poll(
        sid,
        cursor=json.dumps({"seeded": True, "sigs": sigs, "gone": {}, "tombstones": {}}),
        new_count=0,
        health_status="ok",
    )

    assert await _poll(engine, store, sid) == 2
    rows = _items(store, sid)
    assert sorted(r["guid"] for r in rows) == ["a.md", "b.md", "c.md"]
    assert [r["id"] for r in rows if r["guid"] == "b.md"] == [edited]
    assert len(queue.enqueued) == 2


# ── exactly-once: three files in one window → three re-indexes ──────────────────


@pytest.mark.asyncio
async def test_three_files_in_one_window_reindex_exactly_once_each(store, watched):
    clock = _Clock()
    _write(watched / "a.md", "a v1", mtime=clock.t - 3600)
    _write(watched / "b.md", "b v1", mtime=clock.t - 3600)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    assert await _poll(engine, store, sid) == 2  # the first scan brings both in
    before = len(queue.enqueued)

    # Three files touched inside the window: two edits + one creation.
    clock.advance(1)
    _write(watched / "a.md", "a v2", mtime=clock.t)
    _write(watched / "b.md", "b v2", mtime=clock.t)
    _write(watched / "c.md", "c v1", mtime=clock.t)

    # Inside the window nothing is indexed yet — a half-written file must not be ingested.
    assert await _poll(engine, store, sid) == 0
    assert len(queue.enqueued) == before

    # Poll again mid-window: still nothing, and crucially no double-count later.
    clock.advance(2)
    assert await _poll(engine, store, sid) == 0
    assert len(queue.enqueued) == before

    # Window elapses → each of the three is re-indexed EXACTLY once: 3, not 4, not 2.
    clock.advance(10)
    assert await _poll(engine, store, sid) == 3
    reindexed = queue.enqueued[before:]
    assert len(reindexed) == 3
    assert len(set(reindexed)) == 3
    assert {r["guid"] for r in _items(store, sid)} == {"a.md", "b.md", "c.md"}

    # And the settled files do not re-fire on the next quiet poll.
    clock.advance(100)
    assert await _poll(engine, store, sid) == 0
    assert len(queue.enqueued) == before + 3


@pytest.mark.asyncio
async def test_repeated_edits_to_one_file_collapse_to_one_reindex(store, watched):
    clock = _Clock()
    _write(watched / "a.md", "v1", mtime=clock.t - 3600)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    assert await _poll(engine, store, sid) == 1  # the first scan brings it in

    # Three saves of the SAME file, each observed by its own poll, all inside the window:
    # every one restarts the quiet timer, so none of them emits.
    for n, text in enumerate(("v2", "v3", "v4"), start=1):
        clock.advance(2)
        _write(watched / "a.md", text, mtime=clock.t)
        assert await _poll(engine, store, sid) == 0

    clock.advance(10)
    assert await _poll(engine, store, sid) == 1
    assert len(queue.enqueued) == 2, "one for the first scan, ONE for the three edits"
    rows = _items(store, sid)
    assert len(rows) == 1
    # The content indexed is the LAST state, not an intermediate one.
    await _ingest(store, rows[0]["id"])
    assert store.get_item(rows[0]["id"])["content"] == "v4"


# ── create vs modify: a new item vs the SAME item re-enqueued ───────────────────


@pytest.mark.asyncio
async def test_create_makes_new_item_then_modify_reenqueues_the_same_item(store, watched):
    clock = _Clock()
    _write(watched / "keep.md", "keep", mtime=clock.t - 3600)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    assert await _poll(engine, store, sid) == 1  # the first scan brings keep.md in
    before = len(queue.enqueued)

    def _new_rows():
        return [r for r in _items(store, sid) if r["guid"] == "new.md"]

    # create → a NEW item
    clock.advance(1)
    _write(watched / "new.md", "first", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    rows = _new_rows()
    assert len(rows) == 1
    first_id = rows[0]["id"]
    # A markdown file is a document, read by the document reader, as an upload of it is.
    assert rows[0]["item_type"] == "document"
    assert queue.enqueued[before:] == [first_id]

    # modify → the EXISTING item, re-enqueued, no second row
    clock.advance(1)
    _write(watched / "new.md", "second", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    rows = _new_rows()
    assert len(rows) == 1, "a modify must not mint a duplicate row"
    assert rows[0]["id"] == first_id
    assert rows[0]["processing_status"] == "queued", "re-index means back on the ingest path"
    assert queue.enqueued[before:] == [first_id, first_id]
    await _ingest(store, first_id)
    assert store.get_item(first_id)["content"] == "second"


@pytest.mark.asyncio
async def test_modify_of_a_file_the_first_scan_left_out_creates_its_item_once(
    store, watched, monkeypatch
):
    """A file past the first scan's bound is in the baseline and has no item; its first
    edit must create one (and only one), rather than being dropped because the guid looked
    already-seen."""
    import personalclaw.knowledge_providers.dir_source as dir_mod

    monkeypatch.setattr(dir_mod, "FIRST_SCAN_MAX_FILES", 0)
    clock = _Clock()
    _write(watched / "old.md", "v1", mtime=clock.t - 3600)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    assert await _poll(engine, store, sid) == 0

    clock.advance(1)
    _write(watched / "old.md", "v2", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    assert len(_items(store, sid)) == 1
    assert len(queue.enqueued) == 1


# ── delete: archive with a stamp, never a hard delete ───────────────────────────


@pytest.mark.asyncio
async def test_delete_archives_with_source_deleted_at_and_never_hard_deletes(store, watched):
    clock = _Clock()
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)  # seed (empty dir)

    clock.advance(1)
    _write(watched / "doomed.md", "body", mtime=clock.t)
    clock.advance(11)
    await _poll(engine, store, sid)
    rows = _items(store, sid)
    assert len(rows) == 1
    item_id = rows[0]["id"]
    assert not rows[0]["is_archived"]
    await _ingest(store, item_id)

    # The file goes away.
    (watched / "doomed.md").unlink()
    clock.advance(1)
    assert await _poll(engine, store, sid) == 0  # inside the window: nothing yet
    clock.advance(11)
    # An archive is not a re-index, so it enqueues nothing…
    assert await _poll(engine, store, sid) == 0
    assert len(queue.enqueued) == 1

    # …but the row SURVIVES, archived and stamped.
    item = store.get_item(item_id)
    assert item is not None, "a deleted source file must never hard-delete its item"
    assert item["is_archived"]
    assert item["file_metadata"]["source_deleted_at"]
    assert item["content"] == "body", "the last known content is preserved"


@pytest.mark.asyncio
async def test_delete_then_restore_revives_the_same_item(store, watched):
    clock = _Clock()
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)
    clock.advance(1)
    _write(watched / "x.md", "one", mtime=clock.t)
    clock.advance(11)
    await _poll(engine, store, sid)
    item_id = _items(store, sid)[0]["id"]

    # Delete it and let the window elapse so the ARCHIVE actually lands.
    (watched / "x.md").unlink()
    clock.advance(1)
    await _poll(engine, store, sid)  # first missing sighting starts the window
    clock.advance(11)
    await _poll(engine, store, sid)
    assert store.get_item(item_id)["is_archived"]

    # Restore it: the item is revived in place, stamp cleared, no second row.
    _write(watched / "x.md", "two", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    rows = _items(store, sid)
    assert len(rows) == 1, "a restored file revives its item rather than minting a second"
    assert rows[0]["id"] == item_id
    await _ingest(store, item_id)
    assert store.get_item(item_id)["content"] == "two"
    revived = store.get_item(item_id)
    assert not revived["is_archived"]
    assert "source_deleted_at" not in revived["file_metadata"]


def test_neither_provider_nor_engine_can_hard_delete_an_item():
    """Structural rail: the dangerous direction must be UNREACHABLE, not merely unused.

    An archive-on-delete implementation that also carried a `DELETE FROM items` one branch
    away would pass every behavioural test above and still be one edit from data loss.

    The folder's one removal is not a change it observes: `_withdraw` forgets a note an earlier
    scan took in through a link out of the folder, and only the scan's `outside` paths reach it.
    """
    import ast
    from pathlib import Path

    import personalclaw.knowledge.source_engine as engine_mod
    import personalclaw.knowledge_providers.dir_source as dir_mod

    for mod in (dir_mod, engine_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "DELETE FROM items" not in src, f"{mod.__name__} must never hard-delete an item"
        assert "delete_item" not in src, f"{mod.__name__} must not reach a delete path"
    engine_src = Path(engine_mod.__file__).read_text(encoding="utf-8")
    assert "forget_source_item" not in engine_src

    tree = ast.parse(Path(dir_mod.__file__).read_text(encoding="utf-8"))
    forgets, withdraws = [], []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "forget_source_item":
                    forgets.append(fn.name)
                if node.func.attr == "_withdraw":
                    withdraws.append((fn.name, ast.unparse(node.args[1])))
    assert forgets == ["_withdraw"]
    assert withdraws == [("poll", "rel")]
    poll = next(f for f in ast.walk(tree) if getattr(f, "name", "") == "poll")
    loops = [
        ast.unparse(n.iter)
        for n in ast.walk(poll)
        if isinstance(n, ast.For)
        and any(
            isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "_withdraw"
            for c in ast.walk(n)
        )
    ]
    assert loops == ["scan.outside"], "only a path the scan found outside the folder is withdrawn"


# ── fail-open + guards ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_unreadable_file_does_not_abort_the_cycle(store, watched, monkeypatch):
    clock = _Clock()
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)

    clock.advance(1)
    for name in ("good1.md", "bad.md", "good2.md"):
        _write(watched / name, name, mtime=clock.t)

    real_open = os.open

    def _boom(path, *a, **kw):
        if str(path).endswith("bad.md"):
            raise PermissionError("nope")
        return real_open(path, *a, **kw)

    # A context, not `monkeypatch.undo()`, which would also undo the fixtures' and the suite's
    # patches for the rest of the test. The file is opened where it is taken, as an upload's
    # copy is made (`knowledge.file_items`).
    with monkeypatch.context() as unreadable:
        unreadable.setattr("os.open", _boom)
        clock.advance(11)
        # The two readable files still index; the unreadable one is skipped, not fatal.
        assert await _poll(engine, store, sid) == 2
        assert {r["guid"] for r in _items(store, sid)} == {"good1.md", "good2.md"}

    # And the skipped file does not spin forever: its baseline advanced.
    clock.advance(100)
    assert await _poll(engine, store, sid) == 0


@pytest.mark.asyncio
async def test_missing_dir_degrades_health_and_keeps_the_baseline(store, watched):
    clock = _Clock()
    _write(watched / "a.md", "a", mtime=clock.t)
    sid, _prov, engine, _queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)
    before = store.get_source_cursor(sid)

    (watched / "a.md").unlink()
    watched.rmdir()
    clock.advance(11)
    assert await _poll(engine, store, sid) == 0
    src = store.get_source(sid)
    assert src["health_status"] == "degraded"
    # Critically: the baseline is untouched, so remounting the volume does not archive
    # every item at once.
    assert store.get_source_cursor(sid) == before


def test_validate_spec_refuses_missing_nondir_and_bad_cap(store, watched, tmp_path):
    prov = DirSourceProvider(store)
    assert prov.validate_spec({"path": str(watched)})[0] is True
    assert prov.validate_spec({})[0] is False
    assert prov.validate_spec({"path": str(tmp_path / "nope")})[0] is False
    _write(watched / "f.md", "f", mtime=1.0)
    assert prov.validate_spec({"path": str(watched / "f.md")})[0] is False
    ok, err = prov.validate_spec({"path": str(watched), "max_files": MAX_FILES_PER_SOURCE + 1})
    assert ok is False and "max_files" in err


def test_validate_spec_refuses_a_sensitive_path(store, watched, monkeypatch):
    """A credential location is refused even when explicitly configured (decision 7's
    bypass-immune class). ``is_sensitive_path`` keys off the REAL home, so the sensitive
    verdict is injected here — what is under test is that the guard consults it at all."""
    import personalclaw.security as security

    monkeypatch.setattr(security, "is_sensitive_path", lambda p: str(watched) in str(p))
    ok, err = DirSourceProvider(store).validate_spec({"path": str(watched)})
    assert ok is False and "sensitive" in err


def test_real_credential_dirs_are_refused_by_the_shared_guard():
    """The guard's teeth live in ``security.is_sensitive_path``; pin that the paths a dir
    source would most plausibly be pointed at are in its scope, so the refusal above is not
    only true of an injected fake."""
    from personalclaw.security import is_sensitive_path

    assert is_sensitive_path("~/.ssh/id_rsa")
    assert is_sensitive_path("~/.aws/credentials")


@pytest.mark.asyncio
async def test_poll_refuses_a_spec_edited_to_a_sensitive_path(store, watched, monkeypatch):
    """The guard is not save-time-only: the spec is a mutable row, so a poll re-validates."""
    import personalclaw.security as security

    _write(watched / "notes.md", "secret", mtime=1.0)
    sid = store.create_source(
        name="bad", provider="watched-dir", kind="dir", spec={"path": str(watched)}
    )
    monkeypatch.setattr(security, "is_sensitive_path", lambda p: str(watched) in str(p))
    prov = DirSourceProvider(store, now_fn=_Clock())
    result = await prov.poll(sid, "")
    assert result.items == []
    assert "sensitive" in result.error
    # Nothing was recorded either — a refused poll must not seed a baseline it never read.
    assert result.cursor == ""


def test_scan_skips_noise_dirs_and_honours_include_and_cap(store, watched):
    clock = _Clock()
    prov = DirSourceProvider(store, now_fn=clock)
    (watched / ".git").mkdir()
    _write(watched / ".git" / "config.md", "vcs", mtime=clock.t)
    sub = watched / "deep"
    sub.mkdir()
    _write(sub / "n.md", "deep", mtime=clock.t)
    _write(watched / "top.md", "top", mtime=clock.t)
    _write(watched / "skip.bin", "binary", mtime=clock.t)

    scan = prov.scan({"path": str(watched)})
    assert set(scan.sigs) == {"top.md", "deep/n.md"}
    assert scan.unreadable == 0
    assert scan.outside == ()
    flat = prov.scan({"path": str(watched), "recursive": False}).sigs
    assert set(flat) == {"top.md"}
    capped = prov.scan({"path": str(watched), "max_files": 1}).sigs
    assert len(capped) == 1
    widened = prov.scan({"path": str(watched), "include": ["*.bin"]}).sigs
    assert set(widened) == {"skip.bin"}


def test_default_debounce_is_a_real_window():
    assert DEFAULT_DEBOUNCE_SECS > 0
