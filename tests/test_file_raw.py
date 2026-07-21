"""Tests for the /api/file-raw serving endpoint — magic bytes, MIME, symlinks.

Serves images (PNG/JPEG/GIF/WebP/BMP/TIFF/ICO/SVG) and PDF, gated by content
magic-byte sniffing plus path, symlink, and sensitive-path checks.
"""

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import api_file_raw


def _make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/file-raw", api_file_raw)
    return app


@pytest.fixture
def mock_sel():
    with (
        patch("personalclaw.sel.sel") as m,
        patch("personalclaw.security.is_sensitive_path", return_value=False),
    ):
        instance = MagicMock()
        m.return_value = instance
        yield instance


# --- Magic bytes: accepted formats ---

_PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 100
_GIF89_HEADER = b"GIF89a" + b"\x00" * 100
_WEBP_HEADER = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100
_BMP_HEADER = b"BM" + b"\x00" * 100
_TIFF_LE_HEADER = b"II\x2a\x00" + b"\x00" * 100
_ICO_HEADER = b"\x00\x00\x01\x00" + b"\x00" * 100
_SVG_CONTENT = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
_SVG_WITH_XML = b"<?xml version='1.0'?><svg></svg>"
_SVG_WITH_BOM = b"\xef\xbb\xbf<svg></svg>"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ext,data",
    [
        ("png", _PNG_HEADER),
        ("jpg", _JPEG_HEADER),
        ("gif", _GIF89_HEADER),
        ("webp", _WEBP_HEADER),
        ("bmp", _BMP_HEADER),
        ("tiff", _TIFF_LE_HEADER),
        ("ico", _ICO_HEADER),
    ],
)
async def test_serves_valid_image_formats(tmp_path, mock_sel, ext, data):
    f = tmp_path / f"test.{ext}"
    f.write_bytes(data)
    with patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(f)):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-raw?path={f}")
            assert resp.status == 200
            assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [_SVG_CONTENT, _SVG_WITH_XML, _SVG_WITH_BOM])
async def test_serves_svg(tmp_path, mock_sel, data):
    f = tmp_path / "test.svg"
    f.write_bytes(data)
    with patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(f)):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-raw?path={f}")
            assert resp.status == 200


@pytest.mark.asyncio
async def test_serves_pdf(tmp_path, mock_sel):
    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4\n" + b"\x00" * 100)
    with patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(f)):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-raw?path={f}")
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "application/pdf"


# --- Rejected cases ---


@pytest.mark.asyncio
async def test_rejects_non_image_mime(tmp_path, mock_sel):
    f = tmp_path / "test.txt"
    f.write_text("not an image")
    with patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(f)):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-raw?path={f}")
            assert resp.status == 403
            assert "not a recognized format" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_rejects_wrong_magic_bytes(tmp_path, mock_sel):
    """File with .png extension but non-image content."""
    f = tmp_path / "fake.png"
    f.write_bytes(b"this is not a png file at all")
    with patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(f)):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-raw?path={f}")
            assert resp.status == 403
            assert "not a recognized format" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_rejects_invalid_path(mock_sel):
    with patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=None):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/api/file-raw?path=../../etc/passwd")
            assert resp.status == 400


@pytest.mark.asyncio
async def test_rejects_missing_file(tmp_path, mock_sel):
    missing = str(tmp_path / "nope.png")
    with patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=missing):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-raw?path={missing}")
            assert resp.status == 404


@pytest.mark.asyncio
async def test_rejects_symlink(tmp_path, mock_sel):
    real = tmp_path / "real.png"
    real.write_bytes(_PNG_HEADER)
    link = tmp_path / "link.png"
    link.symlink_to(real)
    with patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(link)):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-raw?path={link}")
            assert resp.status == 403
            assert "symlink" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_rejects_sensitive_path(tmp_path, mock_sel):
    f = tmp_path / "creds.png"
    f.write_bytes(_PNG_HEADER)
    with (
        patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(f)),
        patch("personalclaw.security.is_sensitive_path", return_value=True),
    ):
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get(f"/api/file-raw?path={f}")
            assert resp.status == 403
            assert "sensitive" in (await resp.json())["error"]


# --- resolve=1: relative chat file-mentions serve raw bytes (regression) ---
# A chat file-mention is often WORKSPACE-RELATIVE (the agent's cwd). file-read
# already resolved these; file-raw did NOT — so a relative-path MEDIA file (image/
# pdf/video/binary) loaded fine as text but 400'd on the raw serve. That was the
# "some files fail to load in the chat side panel" bug: text worked, binaries broke.
@pytest.mark.asyncio
async def test_resolve_serves_relative_workspace_media(tmp_path, mock_sel):
    import os

    from personalclaw.config.loader import workspace_root

    ws = str(workspace_root())
    os.makedirs(os.path.join(ws, "sub"), exist_ok=True)
    img = os.path.join(ws, "sub", "pic_raw_resolve.png")
    with open(img, "wb") as fh:
        fh.write(_PNG_HEADER)
    try:
        # NOT mocking _validate_dashboard_path — exercise the real resolver+allowlist.
        async with TestClient(TestServer(_make_app())) as client:
            # Without resolve=1 a bare relative path is forbidden (not in an allowed root).
            denied = await client.get("/api/file-raw?path=sub/pic_raw_resolve.png")
            assert denied.status == 400
            # With resolve=1 it resolves against the workspace and serves.
            ok = await client.get("/api/file-raw?path=sub/pic_raw_resolve.png&resolve=1")
            assert ok.status == 200
            assert ok.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        os.remove(img)
