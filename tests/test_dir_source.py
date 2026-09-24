"""WATCHED-SOURCES WS-5 — dir-source: signature-diff observer, debounce, archive-on-delete.

Covers the atom's done_when as COUNTING claims, not liveness ones:

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
        daily_request_budget=288,
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


def _items(store, sid):
    return store.db.execute(
        "SELECT * FROM items WHERE source_id = ? ORDER BY guid", (sid,)
    ).fetchall()


# ── the contract itself ────────────────────────────────────────────────────────


def test_change_vocabulary_is_closed_and_default_is_created():
    assert SOURCE_CHANGES == {CHANGE_CREATED, CHANGE_MODIFIED, CHANGE_DELETED}
    # An append-only feed provider (WS-3/WS-4) must keep working unchanged.
    assert SourceItem(guid="g", title="t").change == CHANGE_CREATED


# ── seeding: the first pass must not storm ──────────────────────────────────────


@pytest.mark.asyncio
async def test_first_pass_seeds_only_no_ingestion_storm(store, watched):
    clock = _Clock()
    for i in range(5):
        _write(watched / f"n{i}.md", f"note {i}", mtime=clock.t)
    sid, _prov, engine, queue = _setup(store, watched, clock)

    assert await _poll(engine, store, sid) == 0
    assert queue.enqueued == []
    assert _items(store, sid) == []
    cursor = json.loads(store.get_source_cursor(sid))
    assert cursor["seeded"] is True
    # The baseline knows every existing file, so only LATER changes are library events.
    assert set(cursor["sigs"]) == {f"n{i}.md" for i in range(5)}


@pytest.mark.asyncio
async def test_seeded_cursor_survives_and_second_pass_is_quiet(store, watched):
    clock = _Clock()
    _write(watched / "a.md", "a", mtime=clock.t)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)
    clock.advance(3600)
    assert await _poll(engine, store, sid) == 0
    assert queue.enqueued == []


# ── exactly-once: three files in one window → three re-indexes ──────────────────


@pytest.mark.asyncio
async def test_three_files_in_one_window_reindex_exactly_once_each(store, watched):
    clock = _Clock()
    _write(watched / "a.md", "a v1", mtime=clock.t)
    _write(watched / "b.md", "b v1", mtime=clock.t)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)  # seed

    # Three files touched inside the window: two edits + one creation.
    clock.advance(1)
    _write(watched / "a.md", "a v2", mtime=clock.t)
    _write(watched / "b.md", "b v2", mtime=clock.t)
    _write(watched / "c.md", "c v1", mtime=clock.t)

    # Inside the window nothing is indexed yet — a half-written file must not be ingested.
    assert await _poll(engine, store, sid) == 0
    assert queue.enqueued == []

    # Poll again mid-window: still nothing, and crucially no double-count later.
    clock.advance(2)
    assert await _poll(engine, store, sid) == 0
    assert queue.enqueued == []

    # Window elapses → each of the three is re-indexed EXACTLY once: 3, not 4, not 2.
    clock.advance(10)
    assert await _poll(engine, store, sid) == 3
    assert len(queue.enqueued) == 3
    assert len(set(queue.enqueued)) == 3
    assert {r["guid"] for r in _items(store, sid)} == {"a.md", "b.md", "c.md"}

    # And the settled files do not re-fire on the next quiet poll.
    clock.advance(100)
    assert await _poll(engine, store, sid) == 0
    assert len(queue.enqueued) == 3


@pytest.mark.asyncio
async def test_repeated_edits_to_one_file_collapse_to_one_reindex(store, watched):
    clock = _Clock()
    _write(watched / "a.md", "v1", mtime=clock.t)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)  # seed

    # Three saves of the SAME file, each observed by its own poll, all inside the window:
    # every one restarts the quiet timer, so none of them emits.
    for n, text in enumerate(("v2", "v3", "v4"), start=1):
        clock.advance(2)
        _write(watched / "a.md", text, mtime=clock.t)
        assert await _poll(engine, store, sid) == 0

    clock.advance(10)
    assert await _poll(engine, store, sid) == 1
    assert len(queue.enqueued) == 1
    rows = _items(store, sid)
    assert len(rows) == 1
    # The content indexed is the LAST state, not an intermediate one.
    assert rows[0]["content"] == "v4"


# ── create vs modify: a new item vs the SAME item re-enqueued ───────────────────


@pytest.mark.asyncio
async def test_create_makes_new_item_then_modify_reenqueues_the_same_item(store, watched):
    clock = _Clock()
    _write(watched / "keep.md", "keep", mtime=clock.t)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)  # seed

    # create → a NEW item
    clock.advance(1)
    _write(watched / "new.md", "first", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    rows = _items(store, sid)
    assert len(rows) == 1
    first_id = rows[0]["id"]
    assert rows[0]["item_type"] == "note"
    assert queue.enqueued == [first_id]

    # modify → the EXISTING item, re-enqueued, no second row
    clock.advance(1)
    _write(watched / "new.md", "second", mtime=clock.t)
    clock.advance(11)
    assert await _poll(engine, store, sid) == 1
    rows = _items(store, sid)
    assert len(rows) == 1, "a modify must not mint a duplicate row"
    assert rows[0]["id"] == first_id
    assert rows[0]["content"] == "second"
    assert rows[0]["processing_status"] == "queued", "re-index means back on the ingest path"
    assert queue.enqueued == [first_id, first_id]


@pytest.mark.asyncio
async def test_modify_of_a_seeded_file_creates_its_item_once(store, watched):
    """A file that only ever SEEDED has no item; its first edit must create one (and only
    one), rather than being dropped because the guid looked already-seen."""
    clock = _Clock()
    _write(watched / "old.md", "v1", mtime=clock.t)
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)

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
    assert rows[0]["content"] == "two"
    revived = store.get_item(item_id)
    assert not revived["is_archived"]
    assert "source_deleted_at" not in revived["file_metadata"]


def test_neither_provider_nor_engine_can_hard_delete_an_item():
    """Structural rail: the dangerous direction must be UNREACHABLE, not merely unused.

    An archive-on-delete implementation that also carried a `DELETE FROM items` one branch
    away would pass every behavioural test above and still be one edit from data loss.
    """
    from pathlib import Path

    import personalclaw.knowledge.source_engine as engine_mod
    import personalclaw.knowledge_providers.dir_source as dir_mod

    for mod in (dir_mod, engine_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "DELETE FROM items" not in src, f"{mod.__name__} must never hard-delete an item"
        assert "delete_item" not in src, f"{mod.__name__} must not reach a delete path"


# ── fail-open + guards ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_unreadable_file_does_not_abort_the_cycle(store, watched, monkeypatch):
    clock = _Clock()
    sid, _prov, engine, queue = _setup(store, watched, clock)
    await _poll(engine, store, sid)

    clock.advance(1)
    for name in ("good1.md", "bad.md", "good2.md"):
        _write(watched / name, name, mtime=clock.t)

    real_open = open

    def _boom(path, *a, **kw):
        if str(path).endswith("bad.md"):
            raise PermissionError("nope")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", _boom)
    clock.advance(11)
    # The two readable files still index; the unreadable one is skipped, not fatal.
    assert await _poll(engine, store, sid) == 2
    assert {r["guid"] for r in _items(store, sid)} == {"good1.md", "good2.md"}
    monkeypatch.undo()

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

    sigs, errors = prov.scan({"path": str(watched)})
    assert set(sigs) == {"top.md", "deep/n.md"}
    assert errors == 0
    flat, _ = prov.scan({"path": str(watched), "recursive": False})
    assert set(flat) == {"top.md"}
    capped, _ = prov.scan({"path": str(watched), "max_files": 1})
    assert len(capped) == 1
    widened, _ = prov.scan({"path": str(watched), "include": ["*.bin"]})
    assert set(widened) == {"skip.bin"}


def test_default_debounce_is_a_real_window():
    assert DEFAULT_DEBOUNCE_SECS > 0
