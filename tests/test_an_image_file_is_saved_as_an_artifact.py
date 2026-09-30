"""An image or PDF opened in Files is saved as a versioned artifact: a copy of its bytes.

Files offered "Save as a versioned artifact" for text only, while Artifacts keeps images and PDFs
as versioned artifacts of their own. ``POST /api/artifacts`` with an image or PDF kind now saves
the file it names as that artifact's first version. A JSON body carries no bytes, so the gateway
reads the file itself, and judges it by what its bytes are: only a PNG, JPEG, GIF, WebP or PDF is
kept, its name has to agree with its bytes, its size is decided before it is read, and the file
must be in a place an artifact may point. The file is never written. Saving the same file again
adds its next version when its bytes changed, and answers the artifact as it is when they did not.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import file_copy, registry
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.native import NativeArtifactProvider

_PNG = b"\x89PNG\r\n\x1a\n" + b"a receipt" * 8
_PDF = b"%PDF-1.7\n" + b"a slide deck" * 8


@pytest.fixture
def places(tmp_path, monkeypatch):
    """A home, a workspace outside it (a place Files opens), and a folder that is neither."""
    home, ws, outside = tmp_path / "home", tmp_path / "ws", tmp_path / "outside"
    for d in (home, ws, outside):
        d.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    return home, ws, outside


@pytest.fixture
def provider(places):
    home, _ws, _outside = places
    prov = NativeArtifactProvider(root=home / "artifacts")
    with patch.object(registry, "get_provider", return_value=prov):
        yield prov


def _save(body: dict) -> tuple[int, dict]:
    async def drive() -> tuple[int, dict]:
        app = web.Application()
        state = MagicMock()
        state._restricted_keys = set()
        state._sessions = {}
        app["state"] = state
        register_artifact_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.post("/api/artifacts", json={"source": "manual", **body})
            return resp.status, await resp.json()
        finally:
            await client.close()

    return asyncio.run(drive())


def _file(folder: Path, name: str, data: bytes) -> Path:
    path = folder / name
    path.write_bytes(data)
    return path


class TestAnImageOrPdfFileBecomesItsOwnArtifact:
    def test_an_image_is_saved_as_an_image_artifact_with_its_bytes(self, places, provider) -> None:
        _home, ws, _ = places
        receipt = _file(ws, "receipt-hardware-store.png", _PNG)
        status, body = _save(
            {"name": "Hardware receipt", "kind": "image", "source_path": str(receipt)}
        )
        assert status == 201, body
        assert (body["kind"], body["mime"], body["version"]) == ("image", "image/png", 1)
        assert body["source_path"] == str(receipt.resolve())
        assert provider.raw_bytes(body["slug"]) == (_PNG, "image/png")
        assert receipt.read_bytes() == _PNG, "the file is never written"

    def test_a_pdf_is_saved_as_a_pdf_artifact(self, places, provider) -> None:
        _home, ws, _ = places
        deck = _file(ws, "talk.pdf", _PDF)
        status, body = _save({"name": "Talk", "kind": "pdf", "source_path": str(deck)})
        assert status == 201, body
        assert (body["kind"], body["mime"]) == ("pdf", "application/pdf")
        assert provider.raw_bytes(body["slug"]) == (_PDF, "application/pdf")

    def test_saving_it_again_adds_a_version_only_when_its_bytes_changed(
        self, places, provider
    ) -> None:
        _home, ws, _ = places
        receipt = _file(ws, "receipt.png", _PNG)
        first = _save({"name": "Receipt", "kind": "image", "source_path": str(receipt)})[1]

        status, same = _save({"name": "Receipt", "kind": "image", "source_path": str(receipt)})
        assert status == 200 and (same["slug"], same["version"]) == (first["slug"], 1)

        cropped = b"\x89PNG\r\n\x1a\n" + b"a cropped receipt" * 8
        receipt.write_bytes(cropped)
        status, bumped = _save({"name": "Receipt", "kind": "image", "source_path": str(receipt)})
        assert status == 200 and (bumped["slug"], bumped["version"]) == (first["slug"], 2)
        assert provider.raw_bytes(first["slug"]) == (cropped, "image/png")
        assert provider.raw_bytes(first["slug"], version=1) == (_PNG, "image/png")
        assert len(provider.list()) == 1, "one artifact per file, as a text file's re-save"


class TestAFileIsJudgedByItsBytes:
    def test_text_named_as_an_image_is_refused(self, places, provider) -> None:
        _home, ws, _ = places
        fake = _file(ws, "notes.png", b"just some notes, not a picture\n")
        status, body = _save({"name": "Notes", "kind": "image", "source_path": str(fake)})
        assert status == 415 and body["error"]["code"] == "file_type_unsupported", body
        assert provider.list() == []

    def test_a_name_that_disagrees_with_the_bytes_is_refused(self, places, provider) -> None:
        _home, ws, _ = places
        misnamed = _file(ws, "photo.jpg", _PNG)
        status, body = _save({"name": "Photo", "kind": "image", "source_path": str(misnamed)})
        assert status == 415 and body["error"]["code"] == "file_type_unsupported", body
        assert "Rename it to end in .png" in body["error"]["message"]
        assert provider.list() == []

    def test_a_pdf_asked_for_as_an_image_is_refused(self, places, provider) -> None:
        _home, ws, _ = places
        deck = _file(ws, "talk.pdf", _PDF)
        status, body = _save({"name": "Talk", "kind": "image", "source_path": str(deck)})
        assert status == 415 and body["error"]["code"] == "file_type_unsupported", body
        assert provider.list() == []

    def test_a_file_over_the_cap_is_refused_before_it_is_read(
        self, places, provider, monkeypatch
    ) -> None:
        _home, ws, _ = places
        big = _file(ws, "poster.png", _PNG)
        monkeypatch.setattr(file_copy, "MAX_BINARY_CONTENT_BYTES", len(_PNG) - 1)

        def _never(*_a, **_k):
            raise AssertionError("an over-cap file was read")

        monkeypatch.setattr(file_copy, "_read_bounded", _never)
        status, body = _save({"name": "Poster", "kind": "image", "source_path": str(big)})
        assert status == 413 and body["error"]["code"] == "file_too_large", body
        assert provider.list() == []


class TestOnlyAFileInAPlaceFilesOpensIsRead:
    def test_a_file_outside_those_places_is_refused(self, places, provider) -> None:
        _home, _ws, outside = places
        elsewhere = _file(outside, "elsewhere.png", _PNG)
        status, body = _save({"name": "Elsewhere", "kind": "image", "source_path": str(elsewhere)})
        assert status == 400, body
        assert "can't be an artifact's source" in body["error"]["message"]
        assert provider.list() == []

    def test_a_save_carrying_a_body_or_naming_no_file_is_refused(self, places, provider) -> None:
        _home, ws, _ = places
        receipt = _file(ws, "receipt.png", _PNG)
        status, _ = _save(
            {"name": "R", "kind": "image", "source_path": str(receipt), "content": "text"}
        )
        assert status == 400
        status, _ = _save({"name": "R", "kind": "image"})
        assert status == 400
        assert provider.list() == []

    def test_a_text_file_still_saves_as_its_live_pointer(self, places, provider) -> None:
        """The floor: the text path is the one it was, If-Match and all."""
        from personalclaw.file_view import read_head, whole_text
        from personalclaw.stale_write import revision_of

        _home, ws, _ = places
        notes = ws / "notes.md"
        notes.write_text("# Notes\n", encoding="utf-8")

        async def drive() -> tuple[int, dict]:
            app = web.Application()
            state = MagicMock()
            state._restricted_keys = set()
            state._sessions = {}
            app["state"] = state
            register_artifact_routes(app)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                resp = await client.post(
                    "/api/artifacts",
                    json={"name": "Notes", "kind": "markdown", "source_path": str(notes)},
                    headers={"If-Match": revision_of(whole_text(read_head(str(notes))))},
                )
                return resp.status, await resp.json()
            finally:
                await client.close()

        status, body = asyncio.run(drive())
        assert status == 201, body
        assert (body["kind"], body["content"]) == ("markdown", "# Notes\n")
