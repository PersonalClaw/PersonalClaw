"""Inline-media outbox support.

api_outbox_notify must accept media (audio/video/image/pdf) with a derived
content_type and skip the UTF-8 gate, while still requiring UTF-8 for text and
honoring the outbox-path containment. api_outbox_download serves media via
FileResponse (correct Content-Type + Range) and keeps the redaction-gated text
path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.files import api_outbox_download, api_outbox_notify


@pytest.fixture
def mock_sel():
    with patch("personalclaw.sel.sel") as m:
        m.return_value = MagicMock()
        yield m.return_value


@pytest.fixture
def outbox(tmp_path):
    d = tmp_path / "outbox"
    d.mkdir()
    with patch("personalclaw.config.loader.outbox_dir", return_value=d):
        yield d


async def _client(outbox) -> TestClient:
    app = web.Application()
    state = MagicMock()
    state._sessions = {}  # no active session → notify skips the append, still 200
    state.broadcast_ws = MagicMock()
    app["state"] = state
    app.router.add_post("/api/outbox/notify", api_outbox_notify)
    app.router.add_get("/api/outbox/{filename}", api_outbox_download)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_notify_accepts_mp3_with_content_type(outbox, mock_sel) -> None:
    f = outbox / "clip.mp3"
    f.write_bytes(b"\xff\xfb\x90\x00not-utf8-binary\x00\x01")  # non-UTF-8 bytes
    client = await _client(outbox)
    try:
        resp = await client.post(
            "/api/outbox/notify", json={"filename": "clip.mp3", "path": str(f)}
        )
        assert resp.status == 200
        # The broadcast carries the derived content_type.
        state = client.app["state"]
        args = state.broadcast_ws.call_args
        assert args[0][0] == "file_ready"
        assert args[0][1]["content_type"] == "audio/mpeg"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_notify_still_rejects_non_utf8_text(outbox, mock_sel) -> None:
    f = outbox / "data.txt"
    f.write_bytes(b"\xff\xfe\x00binary-but-text-ext")
    client = await _client(outbox)
    try:
        resp = await client.post(
            "/api/outbox/notify", json={"filename": "data.txt", "path": str(f)}
        )
        assert resp.status == 400  # text/* must be UTF-8
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_notify_accepts_utf8_text(outbox, mock_sel) -> None:
    f = outbox / "note.txt"
    f.write_text("hello world")
    client = await _client(outbox)
    try:
        resp = await client.post(
            "/api/outbox/notify", json={"filename": "note.txt", "path": str(f)}
        )
        assert resp.status == 200
        ct = client.app["state"].broadcast_ws.call_args[0][1]["content_type"]
        assert ct == "text/plain"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_notify_rejects_path_outside_outbox(outbox, mock_sel, tmp_path) -> None:
    outside = tmp_path / "evil.mp3"
    outside.write_bytes(b"\x00\x01")
    client = await _client(outbox)
    try:
        resp = await client.post(
            "/api/outbox/notify", json={"filename": "evil.mp3", "path": str(outside)}
        )
        assert resp.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_download_serves_media_with_content_type(outbox, mock_sel) -> None:
    f = outbox / "clip.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42binarydata")
    client = await _client(outbox)
    try:
        resp = await client.get("/api/outbox/clip.mp4")
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "video/mp4"
        # FileResponse advertises range support.
        assert resp.headers.get("Accept-Ranges") == "bytes"
        body = await resp.read()
        assert body == f.read_bytes()  # served verbatim (no redaction mangling)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_download_text_still_redaction_gated(outbox, mock_sel) -> None:
    f = outbox / "note.txt"
    f.write_text("plain text body")
    client = await _client(outbox)
    try:
        resp = await client.get("/api/outbox/note.txt")
        assert resp.status == 200
        assert (await resp.text()) == "plain text body"
    finally:
        await client.close()
