"""Every route that stores an uploaded file gives it the content scan first, one request or many.

Before, only a resumable upload's complete scanned what it stored. A file sent in one request,
which is every file under the 50 MB chunk threshold, reached the agent and the library unread on
every route that takes one: a chat attachment, a file uploaded to a folder, a Knowledge file, a
file dropped into a workflow run, a binary artifact's bytes, a project archive and a backup
import. And the scan read only the first 256 KB of a file between 256 KB and 512 KB.

Each route here is sent the same two files. The first is a shopping list with an invisible
right-to-left override character in it, the scanner's own example of content it refuses (the
character makes text read differently from what it says). It is answered 422
``upload_content_refused``, and nothing is made from it. The second is the same list without the
character, and it is stored as it was sent. A scan that cannot run refuses the upload (503
``upload_content_unchecked``) and never passes it.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.http_errors import HTTP_ERROR_CODES
from personalclaw.sel import sel
from personalclaw.uploads import content_scan
from personalclaw.uploads.content_scan import (
    REFUSED_CODE,
    SCAN_WINDOW,
    UNCHECKED_CODE,
    WHOLE_FILE_BYTES,
    ContentRefused,
    read_window,
    scan_upload,
    window_of,
)

#: A shopping list with a right-to-left override in it: the scanner refuses it.
REFUSED_TEXT = "Shopping list for Saturday: \u202eeggs\u202c, flour, apples.\n".encode()
#: The same list without the override: ordinary content, which the scanner passes.
CLEAN_TEXT = "Shopping list for Saturday: eggs, flour, apples.\n".encode()
_KB = 1024


def _form(data: bytes, *, filename: str = "shopping.txt", mime: str = "text/plain") -> FormData:
    form = FormData()
    form.add_field("file", data, filename=filename, content_type=mime)
    return form


def _files_in(folder: Path) -> list[bytes]:
    if not folder.is_dir():
        return []
    return [p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()]


def _scan_rows() -> list[dict]:
    return [e for e in sel().recent(limit=500) if e.get("operation") == "upload_scan"]


# ── the routes, each with what it stored ─────────────────────────────────────────────────────


def _chat_attachment(tmp_path, monkeypatch) -> SimpleNamespace:
    uploads = tmp_path / "uploads"
    started: list[str] = []
    monkeypatch.setattr("personalclaw.dashboard.handlers.files._upload_dir", lambda: uploads)
    monkeypatch.setattr(
        "personalclaw.dashboard.attachment_extract.get_extractor",
        lambda: SimpleNamespace(start=lambda path, mime=None: started.append(path)),
    )
    from personalclaw.dashboard.handlers.files import api_upload_file

    app = web.Application()
    app.router.add_post("/api/upload/file", api_upload_file)

    async def send(client, data: bytes):
        r = await client.post("/api/upload/file", data=_form(data))
        return r.status, await r.json(), data

    return SimpleNamespace(app=app, send=send, stored=lambda: _files_in(uploads), started=started)


def _folder_upload(tmp_path, monkeypatch) -> SimpleNamespace:
    folder = tmp_path / "folder"
    folder.mkdir()
    folder = folder.resolve()
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.files._dashboard_roots", lambda: [("Test", str(folder))]
    )
    from personalclaw.dashboard.handlers.files import api_file_upload

    app = web.Application()
    app.router.add_post("/api/file-upload", api_file_upload)

    async def send(client, data: bytes):
        r = await client.post(f"/api/file-upload?path={folder}", data=_form(data))
        return r.status, await r.json(), data

    return SimpleNamespace(app=app, send=send, stored=lambda: _files_in(folder))


def _knowledge(tmp_path, monkeypatch) -> SimpleNamespace:
    from personalclaw.dashboard.handlers.knowledge import ingest_file
    from personalclaw.knowledge.pipeline import ensure_nodes_registered
    from personalclaw.knowledge.store import KnowledgeStore

    ensure_nodes_registered()
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    queued: list[str] = []
    app = web.Application()
    app["state"] = SimpleNamespace(
        knowledge_store=store,
        knowledge_ingest_queue=lambda: SimpleNamespace(enqueue=queued.append),
    )
    app.router.add_post("/api/knowledge/ingest", ingest_file)

    async def send(client, data: bytes):
        r = await client.post("/api/knowledge/ingest", data=_form(data))
        return r.status, await r.json(), data

    def stored() -> list[bytes]:
        rows = store.db.execute("SELECT id FROM items").fetchall()
        return [Path(store.get_item(row["id"])["file_path"]).read_bytes() for row in rows]

    return SimpleNamespace(app=app, send=send, stored=stored)


def _workflow_drop(tmp_path, monkeypatch) -> SimpleNamespace:
    from personalclaw.workflows import filedrop
    from personalclaw.workflows import handlers as workflow_handlers
    from personalclaw.workflows import store
    from personalclaw.workflows.models import RunStatus, WorkflowRun

    home = tmp_path / "workflows-home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    run = WorkflowRun(id=store.new_run_id(), workflow_name="drop-probe", status=RunStatus.RUNNING)
    store.create(run)
    store.write_spec(
        run.id,
        {
            "root": {"kind": "sequence", "id": "main"},
            "file_drop": {"auto_accept_mimes": ["text/plain"]},
        },
    )
    app = web.Application()
    workflow_handlers.register_workflow_routes(app)

    async def send(client, data: bytes):
        r = await client.post(f"/api/workflows/runs/{run.id}/drop", data=_form(data))
        return r.status, await r.json(), data

    def stored() -> list[bytes]:
        named = [e["filename"] for e in filedrop.read_manifest(run.id)]
        loose = [
            p.name
            for p in (
                filedrop.drop_dir(run.id).iterdir() if filedrop.drop_dir(run.id).is_dir() else []
            )
            if p.is_file() and p.name != filedrop.DROP_MANIFEST
        ]
        assert sorted(named) == sorted(loose), "every file in the drop zone is in its manifest"
        return [(filedrop.drop_dir(run.id) / name).read_bytes() for name in named]

    return SimpleNamespace(app=app, send=send, stored=stored)


def _artifact(tmp_path, monkeypatch) -> SimpleNamespace:
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.handlers import register_artifact_routes
    from personalclaw.artifacts.models import mime_for_ext
    from personalclaw.artifacts.native import NativeArtifactProvider

    pdf = mime_for_ext("pdf")
    provider = NativeArtifactProvider(root=tmp_path / "artifacts")
    monkeypatch.setattr(registry, "get_provider", lambda *a, **k: provider)
    first = b"%PDF-1.4\n% the list as it was\n%%EOF\n"
    art = provider.create_binary(name="Shopping", data=first, mime=pdf, kind="pdf", actor="agent")
    state = MagicMock()
    state._sessions = {}
    app = web.Application()
    app["state"] = state
    register_artifact_routes(app)

    async def send(client, data: bytes):
        body = b"%PDF-1.4\n% " + data + b"%%EOF\n"
        r = await client.put(
            f"/api/artifacts/{art.slug}/raw",
            data=body,
            headers={"Content-Type": pdf, "If-Match": "1"},
        )
        return r.status, await r.json(), body

    def stored() -> list[bytes]:
        current = provider.raw_bytes(art.slug)[0]
        return [] if current == first else [current]

    return SimpleNamespace(app=app, send=send, stored=stored)


def _resumable(tmp_path, monkeypatch) -> SimpleNamespace:
    from personalclaw.dashboard.routes import _register_upload_routes
    from personalclaw.uploads.store import UploadStore

    uploads = tmp_path / "uploads"
    monkeypatch.setattr("personalclaw.dashboard.handlers.files._upload_dir", lambda: uploads)
    monkeypatch.setattr(
        "personalclaw.dashboard.attachment_extract.get_extractor",
        lambda: SimpleNamespace(start=lambda *a, **k: None),
    )
    app = web.Application()
    app["upload_store"] = UploadStore(uploads / ".parts")
    _register_upload_routes(app)

    async def send(client, data: bytes):
        r = await client.post(
            "/api/uploads/init",
            json={
                "filename": "shopping.txt",
                "size": len(data),
                "mime": "text/plain",
                "target": "attachment",
            },
        )
        info = await r.json()
        assert r.status == 200, info
        part = info["partSize"]
        for i in range(info["totalParts"]):
            r = await client.put(
                f"/api/uploads/{info['uploadId']}/part?index={i}",
                data=data[i * part : (i + 1) * part],
                headers={"Content-Type": "application/octet-stream"},
            )
            assert r.status == 200, await r.text()
        r = await client.post(f"/api/uploads/{info['uploadId']}/complete", json={})
        return r.status, await r.json(), data

    return SimpleNamespace(app=app, send=send, stored=lambda: _files_in(uploads))


ROUTES = {
    "chat attachment": _chat_attachment,
    "file uploaded to a folder": _folder_upload,
    "Knowledge file": _knowledge,
    "file dropped into a workflow run": _workflow_drop,
    "binary artifact's new bytes": _artifact,
    "resumable upload": _resumable,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_content_the_scan_refuses_is_refused_and_nothing_is_kept(
    route, tmp_path, monkeypatch
):
    """🔴 Red before on every route but the resumable one: the file was stored unscanned."""
    upload = ROUTES[route](tmp_path, monkeypatch)
    async with TestClient(TestServer(upload.app)) as client:
        status, body, _sent = await upload.send(client, REFUSED_TEXT)

    assert status == 422, body
    assert body["error"] == {"code": REFUSED_CODE, "message": content_scan.REFUSED}
    assert upload.stored() == [], "nothing was made from refused content"
    assert getattr(upload, "started", []) == [], "nothing read the refused file"
    assert [r["outcome"] for r in _scan_rows()] == ["rejected"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", sorted(ROUTES))
async def test_clean_content_is_stored_as_it_was_sent(route, tmp_path, monkeypatch):
    """The vacuity arm: the same route keeps the same file without the override in it."""
    upload = ROUTES[route](tmp_path, monkeypatch)
    async with TestClient(TestServer(upload.app)) as client:
        status, body, sent = await upload.send(client, CLEAN_TEXT)

    assert status == 200, body
    assert upload.stored() == [sent]
    assert _scan_rows() == [], "a pass is not a security event"


@pytest.mark.asyncio
async def test_one_refused_attachment_refuses_the_request_and_keeps_none_of_its_files(
    tmp_path, monkeypatch
):
    """Attachments sent together are kept together or not at all, as with a file over its cap."""
    upload = _chat_attachment(tmp_path, monkeypatch)
    form = FormData()
    form.add_field("file", CLEAN_TEXT, filename="list.txt", content_type="text/plain")
    form.add_field("file", REFUSED_TEXT, filename="shopping.txt", content_type="text/plain")
    async with TestClient(TestServer(upload.app)) as client:
        r = await client.post("/api/upload/file", data=form)
        body = await r.json()

    assert r.status == 422, body
    assert upload.stored() == [] and upload.started == []


# ── the archives: scanned as the files they are ──────────────────────────────────────────────


def _project_import_patches(tmp_path):
    from contextlib import ExitStack

    stack = ExitStack()
    for module in (
        "personalclaw.tasks.native",
        "personalclaw.tasks.hierarchy",
        "personalclaw.config.loader",
        "personalclaw.workflows.store",
        "personalclaw.workflows.leases",
        "personalclaw.concurrency",
        "personalclaw.loop.files",
    ):
        stack.enter_context(patch(f"{module}.config_dir", return_value=tmp_path))
    return stack


@pytest.mark.asyncio
async def test_a_project_archive_the_scan_refuses_is_not_imported(tmp_path):
    """🔴 Red before: an upload to the project import was read as an archive unscanned. A real
    archive is binary to the scan and is imported (the vacuity arm)."""
    from personalclaw.dashboard.request_boundary import request_boundary_middleware
    from personalclaw.tasks import registry
    from personalclaw.tasks.handlers import register_task_routes
    from personalclaw.workflows import project_archive as pa
    from personalclaw.workflows.project_export import plan_export

    files = {"project.json": b"{}", "context/overview.md": b"# Garden plans"}
    archive = pa.write_archive(plan_export("p-src", project_name="Garden", files=files), files)
    registry._providers.clear()
    try:
        with _project_import_patches(tmp_path):
            app = web.Application(middlewares=[request_boundary_middleware()])
            register_task_routes(app)
            async with TestClient(TestServer(app)) as client:
                refused = await client.post(
                    "/api/projects/import",
                    data=_form(REFUSED_TEXT, filename="garden.zip", mime="application/zip"),
                )
                refused_body = await refused.json()
                imported = await client.post(
                    "/api/projects/import",
                    data=_form(archive, filename="garden.zip", mime="application/zip"),
                )
                imported_body = await imported.json()
    finally:
        registry._providers.clear()

    assert refused.status == 422, refused_body
    assert refused_body["error"]["code"] == REFUSED_CODE
    assert imported.status == 201, imported_body
    assert sorted(imported_body["accepted"]) == ["context/overview.md", "project.json"]


@pytest.mark.asyncio
async def test_a_backup_the_scan_refuses_is_not_imported(tmp_path, monkeypatch):
    """🔴 Red before: an upload to the backup import was read as an archive unscanned. A real
    backup is binary to the scan and is read (the vacuity arm)."""
    from personalclaw.dashboard.handlers import durability

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"theme": "dark"}))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.portability.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    app = web.Application(middlewares=[owner])
    app.router.add_post("/api/durability/import", durability.api_durability_import)
    backup = io.BytesIO()
    with zipfile.ZipFile(backup, "w") as zf:
        root = "personalclaw-export-20260101T000000Z"
        zf.writestr(f"{root}/config.json", json.dumps({"theme": "imported"}))
        zf.writestr(f"{root}/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))

    async with TestClient(TestServer(app)) as client:
        refused = await client.post(
            "/api/durability/import",
            data=_form(REFUSED_TEXT, filename="backup.zip", mime="application/zip"),
        )
        refused_body = await refused.json()
        read = await client.post(
            "/api/durability/import",
            data=_form(backup.getvalue(), filename="backup.zip", mime="application/zip"),
        )
        read_body = await read.json()

    assert refused.status == 422, refused_body
    assert refused_body["error"]["code"] == REFUSED_CODE
    assert read.status == 200 and read_body["ok"] is True, read_body
    assert (
        json.loads((home / "config.json").read_text())["theme"] == "dark"
    ), "a plan writes nothing"


# ── the window: no byte of a file up to 512 KB goes unread ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["a stored file", "bytes held in memory"])
async def test_a_300_kb_file_is_scanned_to_its_last_byte(form, tmp_path):
    """🔴 Red before: a file between 256 KB and 512 KB was read only to 256 KB, so what its tail
    carried was never scanned."""
    data = CLEAN_TEXT * (300 * _KB // len(CLEAN_TEXT)) + REFUSED_TEXT
    assert 300 * _KB <= len(data) < WHOLE_FILE_BYTES
    path = tmp_path / "shopping.txt"
    path.write_bytes(data)

    with pytest.raises(ContentRefused) as refused:
        await scan_upload(path if form == "a stored file" else data, "document", surface="files")

    assert (refused.value.code, refused.value.status) == (REFUSED_CODE, 422)


@pytest.mark.parametrize(
    "size", [1, SCAN_WINDOW, SCAN_WINDOW + 1, 300 * _KB, WHOLE_FILE_BYTES - 1, WHOLE_FILE_BYTES]
)
def test_a_file_up_to_512_kb_is_read_whole(size, tmp_path):
    data = bytes(i % 251 + 1 for i in range(size))  # no NUL: text-like
    path = tmp_path / "f.txt"
    path.write_bytes(data)

    assert read_window(path) == data
    assert window_of(data) == data


@pytest.mark.parametrize("size", [WHOLE_FILE_BYTES + 1, 600 * _KB, 3 * WHOLE_FILE_BYTES])
def test_a_larger_file_gets_its_first_and_last_256_kb(size, tmp_path):
    """The large-file policy, exactly: the two windows and the line between them."""
    data = bytes(i % 251 + 1 for i in range(size))
    path = tmp_path / "f.txt"
    path.write_bytes(data)
    expected = data[:SCAN_WINDOW] + b"\n" + data[-SCAN_WINDOW:]

    assert read_window(path) == expected
    assert window_of(data) == expected
    assert len(expected) == content_scan.MAX_WINDOW_BYTES


def _text(size: int) -> bytes:
    """*size* bytes of the clean list, over and over."""
    return (CLEAN_TEXT * (size // len(CLEAN_TEXT) + 1))[:size]


@pytest.mark.parametrize(
    "name, data, read",
    [
        ("a small binary file", _text(4 * _KB) + b"\x00", None),
        (
            "a 300 KB file binary in its first 256 KB",
            b"\x00" + _text(SCAN_WINDOW - 1) + _text(44 * _KB),
            _text(44 * _KB),
        ),
        (
            "a 300 KB file binary past its first 256 KB",
            _text(SCAN_WINDOW) + _text(40 * _KB) + b"\x00" + _text(4 * _KB),
            _text(SCAN_WINDOW),
        ),
        (
            "a 600 KB file binary in its last 256 KB",
            _text(SCAN_WINDOW) + _text(88 * _KB) + b"\x00" + _text(SCAN_WINDOW - 1),
            _text(SCAN_WINDOW),
        ),
    ],
)
def test_a_window_that_holds_a_nul_byte_is_not_read_and_the_other_still_is(
    name, data, read, tmp_path
):
    """A NUL byte marks the window it is in as binary, not the file: random runs of binary bytes
    read as false alarms, and the file's other window is read whatever this one holds. A NUL
    byte past 256 KB once left a 300 KB file's first 256 KB read; it still does."""
    path = tmp_path / "f.txt"
    path.write_bytes(data)

    assert read_window(path) == read, name
    assert window_of(data) == read, name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "where",
    ["refused tail, binary first window", "refused first window, binary past 256 KB"],
)
async def test_refused_content_in_a_text_window_is_refused_whatever_the_other_holds(where):
    """🔴 Red before, the first case: one NUL byte in a 300 KB file's first 256 KB made the scan
    pass the whole file. The second case held before, and still does."""
    if where.startswith("refused tail"):
        data = b"\x00" + _text(SCAN_WINDOW - 1) + _text(40 * _KB) + REFUSED_TEXT
    else:
        data = REFUSED_TEXT + _text(SCAN_WINDOW) + b"\x00" + _text(40 * _KB)

    with pytest.raises(ContentRefused) as refused:
        await scan_upload(data, "document", surface="files")

    assert refused.value.code == REFUSED_CODE


@pytest.mark.asyncio
async def test_content_straddling_the_two_windows_of_a_300_kb_file_is_read_as_one(tmp_path):
    """A file of up to 512 KB is read on from its first window into its last, so content cut by
    the line between them is read as it is written: here the override's three bytes."""
    override = "\u202e".encode()
    data = _text(SCAN_WINDOW - 1) + override + _text(44 * _KB)
    assert data[SCAN_WINDOW - 1 : SCAN_WINDOW + 2] == override

    with pytest.raises(ContentRefused) as refused:
        await scan_upload(data, "document", surface="files")

    assert refused.value.code == REFUSED_CODE


# ── a scan that did not run is never a pass ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_chat_attachment_whose_scan_cannot_run_is_refused_not_passed(tmp_path, monkeypatch):
    """🔴 Red before: a single-request attachment was never scanned, so a scan that could not
    run passed it as surely as one that did. The same upload is kept when the scan runs."""
    upload = _chat_attachment(tmp_path, monkeypatch)
    async with TestClient(TestServer(upload.app)) as client:
        with patch(
            "personalclaw.uploads.content_scan.scan_argv",
            return_value=["/nonexistent/personalclaw-content-scan"],
        ):
            refused, said, _sent = await upload.send(client, CLEAN_TEXT)
        assert upload.stored() == [], "nothing was kept from the unchecked upload"
        kept, body, sent = await upload.send(client, CLEAN_TEXT)

    assert refused == 503, said
    assert said["error"] == {"code": UNCHECKED_CODE, "message": content_scan.NOT_CHECKED}
    assert kept == 200, body
    assert upload.stored() == [sent]
    rows = _scan_rows()
    assert [(r["outcome"], r["caller_identity"]) for r in rows] == [
        ("error", "uploads.content_scan:attachment")
    ], rows


@pytest.mark.asyncio
async def test_a_scan_that_ends_without_an_answer_refuses_the_upload(tmp_path):
    """A child that runs and exits with no answer is a check that did not complete."""
    path = tmp_path / "shopping.txt"
    path.write_bytes(CLEAN_TEXT)
    silent = [sys.executable, "-c", "import sys; sys.stdin.buffer.read(); sys.exit(3)"]

    with patch("personalclaw.uploads.content_scan.scan_argv", return_value=silent):
        with pytest.raises(ContentRefused) as refused:
            await scan_upload(path, "document", surface="files")

    assert (refused.value.code, refused.value.status) == (UNCHECKED_CODE, 503)


@pytest.mark.asyncio
async def test_a_window_that_cannot_be_read_refuses_the_upload(tmp_path):
    """🔴 Red before: a window the scan could not read was let through unchecked."""
    gone = tmp_path / "never-written.txt"

    with pytest.raises(ContentRefused) as refused:
        await scan_upload(gone, "document", surface="knowledge")

    assert (refused.value.code, refused.value.status) == (UNCHECKED_CODE, 503)


def test_content_the_scanner_raises_on_gets_no_answer(monkeypatch, capsys):
    """🔴 Red before: the child answered "not dangerous" for content its scanner raised on, so
    the gateway passed content nothing had checked. Now the child ends with no answer, which the
    gateway reads as unchecked (the test above)."""

    def raises(_window: bytes) -> bool:
        raise RecursionError("the scanner could not finish")

    monkeypatch.setattr(content_scan, "is_dangerous", raises)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(CLEAN_TEXT)))

    with pytest.raises(RecursionError):
        content_scan.main()

    assert capsys.readouterr().out == "", "no answer, so no pass"


def test_the_scan_answers_are_registered_wire_codes():
    assert REFUSED_CODE in HTTP_ERROR_CODES and UNCHECKED_CODE in HTTP_ERROR_CODES
