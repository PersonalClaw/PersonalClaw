"""A file dropped in the memory vault's ``raw/`` folder is read and scanned as an upload is.

The vault's ``raw/`` folder is a drop box: the next sync files what it holds under Knowledge. The
sweep made every file a note. A note's graph hands on the note's content and reads no file, so
anything but a ``.md`` or a ``.txt`` (a script, a PDF, a picture) became a note with nothing read
from it but its name, and the text it did read went into the note without the content scan every
upload gets. Each test drops files in a scratch vault, syncs it through the route Settings → Memory
uses (or the mirror that follows a chat's end), and reads what Knowledge made of them, ingesting
each item it queued as the gateway's queue does.

The refused text is the scanner's own example of content it refuses: a shopping list with an
invisible right-to-left override character in it (the character makes text read differently from
what it says). The same list without the character is ordinary content, and passes.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import memory_vault
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.memory_service import MemoryService
from personalclaw.sel import sel
from personalclaw.vector_memory import VectorMemoryStore

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


class _Queue:
    """The gateway's ingest queue, as far as the sync uses it: what it was handed, in order."""

    def __init__(self) -> None:
        self.ids: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.ids.append(item_id)

    enqueue_background = enqueue


@pytest.fixture(autouse=True)
def _no_settle_wait(monkeypatch):
    """A file still being written waits out a settle window before it is taken. These tests are
    about what the drop box makes of a file once it is there, so the window is not waited out
    here: settling has tests of its own
    (``test_a_watched_folders_files_are_taken_as_uploads_are.py``)."""
    from personalclaw.knowledge import file_items

    monkeypatch.setattr(file_items, "SETTLE_SECS", 0.0)


@pytest.fixture
def vault_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "vault"
    (root / "raw").mkdir(parents=True)
    monkeypatch.setattr(memory_vault, "vault_path_from_config", lambda: root)
    monkeypatch.setattr(memory_vault, "vault_mode_from_config", lambda: "mirror")
    return root


@pytest.fixture
def service(tmp_path) -> MemoryService:
    vs = VectorMemoryStore(db_path=tmp_path / "mem.db")
    vs.init()
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return MemoryService.over_vector_store(vs)


@pytest.fixture
def store(tmp_path) -> KnowledgeStore:
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


async def _ingest(store: KnowledgeStore, ids: list[str]) -> None:
    """Read each queued item as the gateway's ingest queue does."""
    from personalclaw.knowledge.pipeline.runner import ingest_item

    for item_id in ids:
        await ingest_item(store, item_id)


async def _sync(service, store, monkeypatch) -> tuple[dict, _Queue]:
    """``POST /api/memory/vault/sync``, then each item it queued ingested."""
    from personalclaw.dashboard.handlers import memory as handlers

    monkeypatch.setattr(handlers, "_global_service", lambda _state: service)
    queue = _Queue()
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store, knowledge_ingest_queue=lambda: queue)
    app.router.add_post("/api/memory/vault/sync", handlers.api_memory_vault_sync)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/memory/vault/sync")
        assert resp.status == 200, await resp.text()
        body = await resp.json()
    await _ingest(store, queue.ids)
    return body, queue


def _items(store: KnowledgeStore) -> list[dict]:
    rows = store.db.execute("SELECT id FROM items ORDER BY created_at, rowid").fetchall()
    return [store.get_item(r["id"]) for r in rows]


def _drop(vault_dir: Path, name: str, data: bytes | str) -> Path:
    path = vault_dir / "raw" / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def _files_dir() -> Path:
    from personalclaw.knowledge import knowledge_files_dir

    return Path(knowledge_files_dir()).resolve()


# ── each file is read by the reader for its kind ─────────────────────────────


@pytest.mark.asyncio
async def test_a_script_is_kept_as_its_code(vault_dir, service, store, monkeypatch):
    _drop(vault_dir, "pantry.py", CODE)

    body, _queue = await _sync(service, store, monkeypatch)

    [item] = _items(store)
    assert item["type"] == "gist" and item["gist_language"] == "python"
    assert item["content"] == CODE
    assert item["title"] == "pantry.py"
    assert body["raw_ingested"] == 1


@pytest.mark.asyncio
async def test_a_pdf_is_read_by_the_pdf_reader(vault_dir, service, store, monkeypatch):
    _drop(vault_dir, "minutes.pdf", _pdf("Garden committee minutes"))

    await _sync(service, store, monkeypatch)

    [item] = _items(store)
    assert item["type"] == "pdf"
    assert "Garden committee minutes" in item["content"]
    # Kept as an upload is: in the library's own files, which is where its preview is served from.
    assert Path(item["file_path"]).resolve().is_relative_to(_files_dir())
    assert item["mime_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_a_picture_is_read_by_the_image_graph(vault_dir, service, store, monkeypatch):
    _drop(vault_dir, "porch.png", _png())

    await _sync(service, store, monkeypatch)

    [item] = _items(store)
    assert item["type"] == "image"
    meta = item["file_metadata"] or {}
    assert (meta.get("width"), meta.get("height")) == (32, 24)
    assert item["thumbnail_path"] and Path(item["thumbnail_path"]).is_file()


@pytest.mark.asyncio
async def test_an_ordinary_text_file_is_read(vault_dir, service, store, monkeypatch):
    _drop(vault_dir, "list.md", CLEAN_LIST)

    await _sync(service, store, monkeypatch)

    [item] = _items(store)
    assert item["content"].strip() == CLEAN_LIST.strip()
    assert item["processing_status"] != "failed"


# ── what the scan refuses is refused, and keeps nothing ──────────────────────


@pytest.mark.asyncio
async def test_a_text_the_scan_refuses_is_refused_and_keeps_no_text(
    vault_dir, service, store, monkeypatch
):
    _drop(vault_dir, "list.md", OVERRIDE_LIST)

    body, queue = await _sync(service, store, monkeypatch)

    [item] = _items(store)
    assert item["processing_status"] == "failed"
    assert item["processing_error"] == REFUSED
    assert item["content"] == "" and not item["file_path"]
    assert not store.get_extracted_contents(item["id"])
    assert queue.ids == []  # nothing is handed on to be read
    assert body["raw_refused"] == 1 and body["raw_ingested"] == 0
    # The owner's file is not lost: it is parked where every swept file goes.
    parked = vault_dir / "raw" / ".ingested" / "list.md"
    assert parked.read_text(encoding="utf-8") == OVERRIDE_LIST
    rows = [e for e in sel().recent(limit=200) if e.get("operation") == "upload_scan"]
    assert [(r["caller_identity"], r["outcome"], r["resources"]) for r in rows] == [
        ("uploads.content_scan:memory_vault", "rejected", "category=document")
    ]
    # No step of it runs, and each says why, rather than reading as still to come.
    phases = item["file_metadata"]["node_phases"]
    assert phases and {(p["status"], p["reason"]) for p in phases.values()} == {
        ("not_applicable", REFUSED)
    }


@pytest.mark.asyncio
async def test_a_refused_file_says_why_again_when_it_is_read_again(
    vault_dir, service, store, monkeypatch
):
    """Retry, or a regenerate of the whole library, ingests the item again: it kept nothing of the
    file to read, so it says again why, rather than that it holds no text."""
    _drop(vault_dir, "list.md", OVERRIDE_LIST)
    await _sync(service, store, monkeypatch)
    [item] = _items(store)

    await _ingest(store, [item["id"]])

    again = store.get_item(item["id"])
    assert (again["processing_status"], again["processing_error"]) == ("failed", REFUSED)
    assert again["content"] == "" and not again["file_path"]


# ── a file no reader reads is never an empty note that looks read ────────────


@pytest.mark.asyncio
async def test_a_binary_file_is_not_an_empty_note(vault_dir, service, store, monkeypatch):
    _drop(vault_dir, "backup.bin", b"\x00\x01\x02binary\x00" * 64)

    body, queue = await _sync(service, store, monkeypatch)

    [item] = _items(store)
    assert item["processing_status"] == "failed"
    assert item["processing_error"] == (
        "Knowledge does not take this kind of file, so nothing was made from it."
    )
    assert item["content"] == "" and not item["file_path"]
    assert queue.ids == [] and body["raw_refused"] == 1


@pytest.mark.asyncio
async def test_a_binary_file_named_as_code_is_not_an_empty_note(
    vault_dir, service, store, monkeypatch
):
    _drop(vault_dir, "tool.py", b"\x7fELF\x00\x00\x00binary" * 32)

    await _sync(service, store, monkeypatch)

    [item] = _items(store)
    assert item["processing_status"] == "failed"
    assert item["processing_error"] == "tool.py is not a text file, so it cannot be kept as code."
    assert item["content"] == ""


@pytest.mark.asyncio
async def test_a_binary_document_says_it_could_not_be_read(vault_dir, service, store, monkeypatch):
    """A kind the library takes, whose reader finds no text in it: the item says so, as an upload
    of the same file does (its content is no more than the file's kind and size)."""
    _drop(vault_dir, "old.doc", b"\xd0\xcf\x11\xe0\x00\x00binary" * 32)

    await _sync(service, store, monkeypatch)

    [item] = _items(store)
    assert item["type"] == "document"
    assert item["processing_status"] == "failed"
    assert "not a text file" in (item["processing_error"] or "")
    assert "binary" not in item["content"]


# ── the owner's files are moved, never lost ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_second_file_of_the_same_name_does_not_replace_the_first(
    vault_dir, service, store, monkeypatch
):
    _drop(vault_dir, "notes.md", "First draft of the plan.\n")
    await _sync(service, store, monkeypatch)
    _drop(vault_dir, "notes.md", "Second draft of the plan.\n")
    await _sync(service, store, monkeypatch)

    parked = sorted(
        p.read_text(encoding="utf-8") for p in (vault_dir / "raw" / ".ingested").iterdir()
    )
    assert parked == ["First draft of the plan.\n", "Second draft of the plan.\n"]
    assert sorted(i["content"].strip() for i in _items(store)) == [
        "First draft of the plan.",
        "Second draft of the plan.",
    ]


@pytest.mark.asyncio
async def test_an_empty_file_is_left_until_it_holds_something(
    vault_dir, service, store, monkeypatch
):
    """An upload of an empty file is refused as having nothing to store; one dropped in raw/
    stays where it is (an editor's new, still empty, page), and is taken once it is written."""
    page = _drop(vault_dir, "Untitled.md", "")

    await _sync(service, store, monkeypatch)

    assert _items(store) == [] and page.exists()


@pytest.mark.asyncio
async def test_a_note_an_earlier_sweep_made_is_left_as_it_was(
    vault_dir, service, store, monkeypatch
):
    """What an earlier version made of a dropped file stays as it was: the owner may have edited,
    tagged or filed it, and nothing in the vault or the library changes behind her back."""
    parked = vault_dir / "raw" / ".ingested" / "old.md"
    parked.parent.mkdir(parents=True)
    parked.write_text("Notes from before.\n", encoding="utf-8")
    old_id = store.create_typed_item(
        item_type="note",
        title="old",
        content="Notes from before.\n",
        tags=["vault-raw"],
        extra={"file_path": str(parked), "processing_status": "done"},
    )
    before = store.get_item(old_id)

    await _sync(service, store, monkeypatch)

    after = store.get_item(old_id)
    assert {k: after[k] for k in ("type", "content", "file_path", "processing_status")} == {
        k: before[k] for k in ("type", "content", "file_path", "processing_status")
    }
    assert parked.read_text(encoding="utf-8") == "Notes from before.\n"


# ── the mirror after a chat ends takes them too ──────────────────────────────


@pytest.mark.asyncio
async def test_the_mirror_after_a_chat_ends_takes_dropped_files_the_same_way(
    vault_dir, service, store, monkeypatch
):
    from personalclaw.action_providers import services as action_services

    queue = _Queue()
    monkeypatch.setattr("personalclaw.knowledge.get_knowledge_store", lambda: store)
    monkeypatch.setattr(
        action_services,
        "_services",
        SimpleNamespace(state=SimpleNamespace(knowledge_ingest_queue=lambda: queue)),
    )
    _drop(vault_dir, "minutes.pdf", _pdf("Garden committee minutes"))

    sweep = memory_vault.mirror_after_consolidation(service)
    if sweep is not None:  # the sweep runs beside the mirror, on the gateway's loop
        await sweep

    [item] = _items(store)
    assert queue.ids == [item["id"]]  # handed to the gateway's queue, not left for a restart
    await _ingest(store, queue.ids)
    item = store.get_item(item["id"])
    assert item["type"] == "pdf" and "Garden committee minutes" in item["content"]
