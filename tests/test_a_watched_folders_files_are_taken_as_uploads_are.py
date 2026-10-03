"""A watched folder's file is taken into Knowledge as an upload is, and only once it is written.

A folder added under Knowledge → Sources was read by decoding each matched file as UTF-8, binary
included, into a note that held that text before anything scanned it: a script, a PDF or a
picture became a note of its bytes read as text, and a note's text reached the library unscanned.
Now a watched file comes in through the door uploads come through: typed by its kind, a private
copy of its bytes scanned, then read by the reader for its kind (a script's code, a PDF's text, a
picture by its graph), and what the scan refuses is a failed item that keeps no text. And a file
still being written (a large file copied in) is left for the next pass, in a watched folder and in
the memory vault's ``raw/`` drop box alike, rather than taken as the part copied in so far.

Each test drives a real folder, a real knowledge store and the real content scan, polls the folder
as the source engine does, and ingests what it queued as the gateway's queue does. The refused
text is the scanner's own example of content it refuses: a shopping list with an invisible
right-to-left override character in it. The same list without it is ordinary content.
"""

from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw.knowledge import file_items
from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers.dir_source import DirSourceProvider
from personalclaw.sel import sel

#: The scanner's own example of content it refuses: a list with a right-to-left override in it.
OVERRIDE_LIST = "Shopping list for Saturday: ‮eggs‬, flour, apples.\n"
#: The same list without the override: ordinary content.
CLEAN_LIST = "Shopping list for Saturday: eggs, flour, apples.\n"
#: The status line of an item whose file the content scan refused.
REFUSED = "Its text failed the content safety scan, so nothing was made from it."
CODE = "def total(prices):\n    return sum(prices)\n"


def _pdf(text: str) -> bytes:
    """A real one-page PDF showing *text* in Helvetica, with no metadata."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (32, 24), (200, 120, 40)).save(out, format="PNG")
    return out.getvalue()


class _Clock:
    """A hand-driven clock for the folder's quiet window: advanced, never slept."""

    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs


class _Queue:
    """The gateway's ingest queue, as far as a poll uses it: what it was handed, in order."""

    def __init__(self) -> None:
        self.ids: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.ids.append(item_id)

    enqueue_background = enqueue

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


class _Folder:
    """A folder watched under Knowledge → Sources, with the engine that polls it."""

    def __init__(self, store: KnowledgeStore, path: Path, *, include: list[str], **source) -> None:
        self.store, self.path, self.clock, self.queue = store, path, _Clock(), _Queue()
        self.sid = store.create_source(
            name="Garden",
            provider="watched-dir",
            kind="dir",
            spec={"path": str(path), "debounce_secs": 10.0, "include": include},
            item_type="note",
            **source,
        )
        provider = DirSourceProvider(store, now_fn=self.clock)
        self.engine = SourceEngine(
            store,
            self.queue,
            providers_lister=lambda: [provider],
            config_loader=_cfg,
            now_fn=self.clock,
        )

    def write(self, name: str, data: bytes | str, *, ago: float = 3600.0) -> Path:
        """A file saved *ago* seconds before the folder's clock, so it is past its quiet window."""
        target = self.path / name
        if isinstance(data, str):
            target.write_text(data, encoding="utf-8")
        else:
            target.write_bytes(data)
        os.utime(target, (self.clock.t - ago, self.clock.t - ago))
        return target

    async def poll(self) -> int:
        """One poll of the folder, after its quiet window has passed."""
        self.clock.advance(11)
        return await self.engine.poll_source(self.store.get_source(self.sid), _cfg())

    async def ingest(self) -> None:
        """Read each item the polls queued, as the gateway's ingest queue does."""
        from personalclaw.knowledge.pipeline.runner import ingest_item

        for item_id in self.queue.ids:
            await ingest_item(self.store, item_id)

    def items(self) -> dict[str, dict]:
        rows = self.store.db.execute(
            "SELECT id FROM items WHERE source_id = ? ORDER BY guid", (self.sid,)
        ).fetchall()
        return {item["guid"]: item for item in (self.store.get_item(r["id"]) for r in rows)}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture(autouse=True)
def _no_settle_wait(monkeypatch):
    """The files these tests write are done before the poll; the tests of a file still being
    written say what happens within the settle window themselves."""

    async def _done() -> None:
        return None

    monkeypatch.setattr(file_items, "_settle", _done, raising=False)


@pytest.fixture
def store(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


@pytest.fixture
def garden(store, tmp_path) -> _Folder:
    path = tmp_path / "garden"
    path.mkdir()
    return _Folder(store, path, include=["*.md", "*.py", "*.pdf", "*.png", "*.bin"])


def _files_dir() -> Path:
    from personalclaw.knowledge import knowledge_files_dir

    return Path(knowledge_files_dir()).resolve()


# ── each file is read by the reader for its kind ─────────────────────────────


@pytest.mark.asyncio
async def test_a_watched_script_is_kept_as_its_code(garden):
    garden.write("planting.py", CODE)

    assert await garden.poll() == 1

    item = garden.items()["planting.py"]
    assert item["type"] == "gist" and item["gist_language"] == "python"
    assert item["content"] == CODE
    assert item["title"] == "planting.py"


@pytest.mark.asyncio
async def test_a_watched_pdf_is_read_by_the_pdf_reader(garden):
    garden.write("minutes.pdf", _pdf("Garden committee minutes"))

    await garden.poll()
    await garden.ingest()

    item = garden.items()["minutes.pdf"]
    assert item["type"] == "pdf"
    assert "Garden committee minutes" in item["content"]
    # Kept as an upload is: a copy in the library's own files, where its preview is served from.
    assert Path(item["file_path"]).resolve().is_relative_to(_files_dir())
    assert item["mime_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_a_watched_picture_is_read_by_the_image_graph(garden):
    garden.write("porch.png", _png())

    await garden.poll()
    await garden.ingest()

    item = garden.items()["porch.png"]
    assert item["type"] == "image"
    meta = item["file_metadata"] or {}
    assert (meta.get("width"), meta.get("height")) == (32, 24)
    assert item["thumbnail_path"] and Path(item["thumbnail_path"]).is_file()


@pytest.mark.asyncio
async def test_an_ordinary_note_is_read_as_written(garden):
    garden.write("list.md", CLEAN_LIST)

    await garden.poll()
    await garden.ingest()

    item = garden.items()["list.md"]
    assert item["type"] == "document"
    assert item["content"].strip() == CLEAN_LIST.strip()
    assert item["processing_status"] != "failed"


# ── what the scan refuses is refused, and keeps nothing ──────────────────────


@pytest.mark.asyncio
async def test_a_watched_note_the_scan_refuses_is_refused_and_keeps_no_text(garden, caplog):
    garden.write("list.md", OVERRIDE_LIST)

    with caplog.at_level(logging.WARNING, logger="personalclaw.knowledge.source_engine"):
        await garden.poll()

    item = garden.items()["list.md"]
    assert item["processing_status"] == "failed"
    assert item["processing_error"] == REFUSED
    assert item["content"] == "" and not item["file_path"]
    assert not garden.store.get_extracted_contents(item["id"])
    assert garden.queue.ids == []  # nothing is handed on to be read
    rows = [e for e in sel().recent(limit=200) if e.get("operation") == "upload_scan"]
    assert [(r["caller_identity"], r["outcome"], r["resources"]) for r in rows] == [
        ("uploads.content_scan:watched_folder", "rejected", "category=document")
    ]
    # No step of it runs, and each says why, rather than reading as still to come.
    phases = item["file_metadata"]["node_phases"]
    assert phases and {(p["status"], p["reason"]) for p in phases.values()} == {
        ("not_applicable", REFUSED)
    }
    # Said in the gateway's log too, since no one was there to be told.
    assert any("list.md" in r.getMessage() and REFUSED in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_change_the_scan_refuses_leaves_the_item_holding_nothing(garden):
    """A note the folder read, saved again with text the scan refuses: the same item keeps none of
    what it held. Saved clean once more, it is read again."""
    note = garden.write("list.md", CLEAN_LIST)
    await garden.poll()
    await garden.ingest()
    before = garden.items()["list.md"]
    assert before["content"].strip()
    kept = Path(before["file_path"] or "")
    kept_before = bool(before["file_path"]) and kept.is_file()

    note.write_text(OVERRIDE_LIST, encoding="utf-8")
    os.utime(note, (garden.clock.t, garden.clock.t))
    assert await garden.poll() == 1

    refused = garden.items()["list.md"]
    assert refused["id"] == before["id"]
    assert (refused["processing_status"], refused["processing_error"]) == ("failed", REFUSED)
    assert refused["content"] == "" and not refused["file_path"]
    assert not garden.store.get_extracted_contents(refused["id"])
    assert kept_before and not kept.exists(), "the copy it kept of the file before is not kept"

    note.write_text(CLEAN_LIST + "And seed potatoes.\n", encoding="utf-8")
    os.utime(note, (garden.clock.t, garden.clock.t))
    assert await garden.poll() == 1
    await garden.ingest()
    again = garden.items()["list.md"]
    assert again["id"] == before["id"]
    assert "seed potatoes" in again["content"]
    assert again["processing_status"] != "failed"


# ── a file no reader reads is never a note of its bytes ──────────────────────


@pytest.mark.asyncio
async def test_a_binary_file_is_not_a_note_of_its_bytes(garden):
    garden.write("backup.bin", b"\x00\x01\x02binary\x00" * 64)

    await garden.poll()

    item = garden.items()["backup.bin"]
    assert item["processing_status"] == "failed"
    assert item["processing_error"] == (
        "Knowledge does not take this kind of file, so nothing was made from it."
    )
    assert item["content"] == "" and not item["file_path"]
    assert garden.queue.ids == []


@pytest.mark.asyncio
async def test_a_binary_file_named_as_a_note_says_it_could_not_be_read(garden):
    garden.write("notes.md", b"\x7fELF\x00\x00\x00binary" * 32)

    await garden.poll()
    await garden.ingest()

    item = garden.items()["notes.md"]
    assert item["type"] == "document"
    assert item["processing_status"] == "failed"
    assert "not a text file" in (item["processing_error"] or "")
    assert "binary" not in item["content"] and "ELF" not in item["content"]


# ── a file still being written is taken once it is done ──────────────────────


@pytest.mark.asyncio
async def test_a_file_still_being_written_is_not_taken_until_it_settles(garden, monkeypatch):
    """A copy that keeps the original's date writes on under an old modified time: the file is
    left while it grows, and taken whole once it holds still."""
    minutes = garden.write("minutes.md", "Garden committee minutes, part one.\n")

    async def still_copying() -> None:
        with open(minutes, "a", encoding="utf-8") as fh:
            fh.write("Part two, still arriving.\n")

    monkeypatch.setattr(file_items, "_settle", still_copying, raising=False)
    assert await garden.poll() == 0
    assert garden.items() == {}, "a file caught while it is copied in is not taken part-way"

    # The copy is done: it puts back the original's date, and the file holds still.
    os.utime(minutes, (garden.clock.t - 3600, garden.clock.t - 3600))

    async def done() -> None:
        return None

    monkeypatch.setattr(file_items, "_settle", done)
    assert await garden.poll() == 1
    await garden.ingest()
    item = garden.items()["minutes.md"]
    assert "part one" in item["content"] and "Part two, still arriving." in item["content"]


@pytest.mark.asyncio
async def test_a_file_dropped_in_the_vault_while_still_written_waits_for_the_next_sync(
    store, tmp_path, monkeypatch
):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw import memory_vault
    from personalclaw.dashboard.handlers import memory as handlers
    from personalclaw.memory_service import MemoryService
    from personalclaw.vector_memory import VectorMemoryStore

    vault = tmp_path / "vault"
    (vault / "raw").mkdir(parents=True)
    monkeypatch.setattr(memory_vault, "vault_path_from_config", lambda: vault)
    monkeypatch.setattr(memory_vault, "vault_mode_from_config", lambda: "mirror")
    memories = VectorMemoryStore(db_path=tmp_path / "mem.db")
    memories.init()
    memories.embed_fn = lambda t: [1.0, 0.0, 0.0]
    monkeypatch.setattr(
        handlers, "_global_service", lambda _state: MemoryService.over_vector_store(memories)
    )
    queue = _Queue()
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store, knowledge_ingest_queue=lambda: queue)
    app.router.add_post("/api/memory/vault/sync", handlers.api_memory_vault_sync)

    async def sync() -> dict:
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/memory/vault/sync")
            assert resp.status == 200, await resp.text()
            return await resp.json()

    dropped = vault / "raw" / "minutes.md"
    dropped.write_text("Garden committee minutes, part one.\n", encoding="utf-8")

    async def still_copying() -> None:
        with open(dropped, "a", encoding="utf-8") as fh:
            fh.write("Part two, still arriving.\n")

    monkeypatch.setattr(file_items, "_settle", still_copying, raising=False)
    body = await sync()
    assert store.db.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"] == 0
    assert body.get("raw_waiting") == 1 and body["raw_ingested"] == 0
    assert dropped.exists(), "it stays in raw/ until it is taken"

    async def done() -> None:
        return None

    monkeypatch.setattr(file_items, "_settle", done)
    body = await sync()
    assert body["raw_ingested"] == 1 and body["raw_waiting"] == 0
    from personalclaw.knowledge.pipeline.runner import ingest_item

    for item_id in queue.ids:
        await ingest_item(store, item_id)
    [item_id] = queue.ids
    content = store.get_item(item_id)["content"]
    assert "part one" in content and "Part two, still arriving." in content


# ── one item per file: a change remakes it, a file unchanged is not read again ──


@pytest.mark.asyncio
async def test_an_unchanged_file_is_not_read_again_and_a_change_updates_its_item(garden):
    note = garden.write("plan.md", "Sow the beans in May.\n")
    assert await garden.poll() == 1
    await garden.ingest()
    [first] = garden.items().values()

    # Its time moves (a sync client touching it) but not its bytes: nothing to read again.
    os.utime(note, (garden.clock.t, garden.clock.t))
    assert await garden.poll() == 0
    assert garden.queue.ids == [first["id"]]

    note.write_text("Sow the beans in June.\n", encoding="utf-8")
    os.utime(note, (garden.clock.t, garden.clock.t))
    assert await garden.poll() == 1
    await garden.ingest()
    [after] = garden.items().values()
    assert after["id"] == first["id"], "a change remakes the item, never a second one"
    assert "June" in after["content"]
    assert len(list(_files_dir().glob("*.md"))) == 1, "the copy it kept before goes"


@pytest.mark.asyncio
async def test_a_note_an_earlier_version_made_is_left_as_it_was_until_its_file_changes(garden):
    """What an earlier version made of a watched file stays as it was: nothing in the library
    changes behind her back. When the file changes, the change is taken as an upload is."""
    note = garden.write("plan.md", "Notes from before.\n")
    old_id = garden.store.create_typed_item(
        item_type="note",
        title="plan.md",
        content="Notes from before.\n",
        provider="watched-dir",
        source_id=garden.sid,
        guid="plan.md",
        extra={"processing_status": "done"},
    )
    sig = [note.stat().st_mtime, note.stat().st_size]
    garden.store.record_poll(
        garden.sid,
        cursor=json.dumps({"first_scan": {"found": 1, "left_out": 0}, "sigs": {"plan.md": sig}}),
        new_count=0,
        health_status="ok",
    )
    before = garden.store.get_item(old_id)

    assert await garden.poll() == 0
    after = garden.store.get_item(old_id)
    fields = ("type", "content", "file_path", "processing_status")
    assert {k: after[k] for k in fields} == {k: before[k] for k in fields}

    note.write_text("Notes from today.\n", encoding="utf-8")
    os.utime(note, (garden.clock.t, garden.clock.t))
    assert await garden.poll() == 1
    await garden.ingest()
    taken = garden.store.get_item(old_id)
    assert taken["type"] == "document"
    assert Path(taken["file_path"]).resolve().is_relative_to(_files_dir())
    assert taken["content"].strip() == "Notes from today."


# ── a folder set to no AI still has its files read, by no model ──────────────


@pytest.mark.asyncio
async def test_a_raw_folders_note_is_read_with_no_model(store, tmp_path, monkeypatch):
    import personalclaw.knowledge.pipeline.runner as runner_mod

    ran: list[str] = []

    def _forbidden(name):
        async def _stage(*a, **kw):
            ran.append(name)
            raise AssertionError(f"a raw source must not reach the {name} stage")

        return _stage

    for stage in ("_run_insights", "_run_entities_stage", "_run_intents_stage"):
        monkeypatch.setattr(runner_mod, stage, _forbidden(stage))
    path = tmp_path / "plain"
    path.mkdir()
    folder = _Folder(store, path, include=["*.md"], enrichment="raw")
    folder.write("list.md", CLEAN_LIST)

    await folder.poll()
    await folder.ingest()

    item = folder.items()["list.md"]
    assert ran == []
    assert item["type"] == "document"
    assert item["content"].strip() == CLEAN_LIST.strip()
