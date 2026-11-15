"""A Library item's file is a download unless it is a type shown inline, and never runs script.

``GET /api/knowledge/items/{id}/file`` answers an item's own bytes, which the gateway did not
write. Shown inline only as what the item page draws or plays in place (a raster image, audio,
video) or a PDF whose bytes are a PDF's, it is otherwise a download named for the item. It is never
sniffed, and every answer but a PDF's carries a policy that loads and runs nothing, so even opened
in a tab of its own the file is inert. The dashboard's own headers middleware stands in front of
the route here, as it does in the gateway, since it fills in the policy a handler leaves unset.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import knowledge as H
from personalclaw.dashboard.server import _security_headers_middleware
from personalclaw.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.security

SANDBOX = "default-src 'none'; sandbox"
PAGE = b"<!doctype html><html><body><script>fetch('/api/sessions')</script></body></html>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("/api/sessions")</script></svg>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"


async def _answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, data: bytes, title: str = ""
):
    """The route's answer for an item kept as *name* holding *data*, titled *title* (else *name*):
    (status, headers, body)."""
    files = tmp_path / "kfiles"
    files.mkdir(exist_ok=True)
    monkeypatch.setattr("personalclaw.knowledge.knowledge_files_dir", lambda: str(files))
    store = KnowledgeStore(str(tmp_path / "k.db"))
    kept = files / f"item{Path(name).suffix}"
    kept.write_bytes(data)
    item_id = store.create_typed_item(
        item_type="document", title=title or name, extra={"file_path": str(kept)}
    )
    app = web.Application(middlewares=[_security_headers_middleware])
    app["state"] = SimpleNamespace(knowledge_store=store)
    app.router.add_get("/api/knowledge/items/{id}/file", H.get_item_file)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/knowledge/items/{item_id}/file")
        return resp.status, resp.headers, await resp.read()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, data, mime",
    [
        ("saved page.html", PAGE, "text/html"),
        ("notes.htm", PAGE, "text/html"),
        ("drawing.svg", SVG, "image/svg+xml"),
        ("page.xhtml", PAGE, "application/xhtml+xml"),
        ("readme.txt", b"plain words", "text/plain"),
    ],
    ids=["html", "htm", "svg", "xhtml", "text"],
)
async def test_a_type_not_shown_inline_is_a_download_that_runs_nothing(
    tmp_path, monkeypatch, name, data, mime
):
    status, headers, body = await _answer(tmp_path, monkeypatch, name, data)
    assert status == 200 and body == data
    assert headers["Content-Type"].split(";")[0] == mime
    disposition = headers.get("Content-Disposition", "")
    assert disposition.startswith("attachment;"), disposition
    assert "filename*=UTF-8''" in disposition and name.split(".")[-1] in disposition
    assert headers["Content-Security-Policy"] == SANDBOX
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, data, mime",
    [
        ("photo.png", PNG, "image/png"),
        ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
        ("clip.mp3", b"ID3\x04" + b"\x00" * 16, "audio/mpeg"),
        ("clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8, "video/mp4"),
    ],
    ids=["png", "jpeg", "mp3", "mp4"],
)
async def test_an_image_audio_or_video_is_shown_inline_and_runs_nothing(
    tmp_path, monkeypatch, name, data, mime
):
    status, headers, _ = await _answer(tmp_path, monkeypatch, name, data)
    assert status == 200
    assert headers["Content-Type"].split(";")[0] == mime
    assert "Content-Disposition" not in headers
    assert headers["Content-Security-Policy"] == SANDBOX
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_a_download_is_named_with_the_extension_its_file_has(tmp_path, monkeypatch):
    # A page titled as another type downloads under its title with its own extension added, so
    # the saved file's name says what it is.
    _, headers, _ = await _answer(tmp_path, monkeypatch, "invoice.html", PAGE, title="invoice.pdf")
    disposition = headers["Content-Disposition"]
    assert disposition.startswith("attachment;"), disposition
    assert 'filename="invoice.pdf.html"' in disposition, disposition
    assert "filename*=UTF-8''invoice.pdf.html" in disposition, disposition


@pytest.mark.asyncio
async def test_a_pdf_is_shown_inline_unsandboxed_so_it_still_shows(tmp_path, monkeypatch):
    status, headers, _ = await _answer(tmp_path, monkeypatch, "paper.pdf", PDF)
    assert status == 200 and headers["Content-Type"].split(";")[0] == "application/pdf"
    assert "Content-Disposition" not in headers
    assert "sandbox" not in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_a_page_named_as_a_pdf_is_a_download_that_runs_nothing(tmp_path, monkeypatch):
    status, headers, _ = await _answer(tmp_path, monkeypatch, "paper.pdf", PAGE)
    assert status == 200 and headers["Content-Type"].split(";")[0] == "application/pdf"
    assert headers["Content-Disposition"].startswith("attachment;")
    assert headers["Content-Security-Policy"] == SANDBOX
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_a_page_named_as_an_image_is_never_read_as_a_page(tmp_path, monkeypatch):
    # Its type is the name's, never sniffed, and its policy runs nothing: a browser that opens it
    # shows a broken image, never a page.
    status, headers, _ = await _answer(tmp_path, monkeypatch, "photo.png", PAGE)
    assert status == 200 and headers["Content-Type"].split(";")[0] == "image/png"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == SANDBOX


@pytest.mark.asyncio
async def test_a_missing_item_is_still_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.knowledge.knowledge_files_dir", lambda: str(tmp_path))
    app = web.Application(middlewares=[_security_headers_middleware])
    app["state"] = SimpleNamespace(knowledge_store=KnowledgeStore(str(tmp_path / "k.db")))
    app.router.add_get("/api/knowledge/items/{id}/file", H.get_item_file)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/knowledge/items/nope/file")
        assert resp.status == 404
