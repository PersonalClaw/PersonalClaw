"""Chat-attachment content extraction — knowledge EXTRACTION graph only, used to
inject an uploaded file's text into the chat prompt (no store / enrichment)."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.dashboard.attachment_extract import AttachmentExtractor, display_name
from personalclaw.knowledge.extract import Extracted, extract_file


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestExtractFileContent:
    def test_plain_text_file(self, tmp_path):
        f = tmp_path / "report.txt"
        f.write_text("Revenue grew 42% to $3.1M.\nKey risk: API cost.")
        got = _run(extract_file(str(f), "text/plain", surface="attachment"))
        assert "Revenue grew 42%" in got.text
        assert "$3.1M" in got.text
        assert got.read is True

    def test_markdown_file(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text("# Heading\n\nBody text here.")
        got = _run(extract_file(str(f), "text/markdown", surface="attachment"))
        assert "Body text here" in got.text

    def test_missing_file_returns_empty(self):
        assert _run(
            extract_file("/no/such/file.txt", "text/plain", surface="attachment")
        ) == Extracted("", False)

    def test_image_no_ocr_yields_structural_descriptor(self, tmp_path):
        # A tiny PNG with no text → no OCR/vision configured → graceful structural
        # descriptor (dimensions/format/size) instead of a content-less blank.
        png = tmp_path / "pic.png"
        # 1×1 transparent PNG
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        got = _run(extract_file(str(png), "image/png", surface="attachment"))
        # Either real OCR text (if a model is configured) or the structural fallback;
        # on a no-OCR box it must be the descriptor, never empty — and SAID to be one.
        assert got.text != ""
        assert "pic.png" in got.text or "Image" in got.text
        if "no extractable text content" in got.text:
            assert got.read is False, "a structural descriptor was reported as read content"

    def test_empty_path_returns_empty(self):
        assert _run(extract_file("", None, surface="attachment")) == Extracted("", False)


class TestDisplayName:
    def test_strips_uuid_prefix(self):
        assert display_name("/x/uploads/" + "a" * 32 + "_report.txt") == "report.txt"

    def test_keeps_plain_name(self):
        assert display_name("/x/uploads/report.txt") == "report.txt"


class TestAttachmentExtractor:
    @pytest.mark.asyncio
    async def test_get_extracts_and_caches(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("hello attachment world")
        ex = AttachmentExtractor()
        ex.start(str(f), "text/plain")
        got = await ex.get(str(f), "text/plain")
        assert "hello attachment world" in got.text
        # second get returns the same cached task result
        assert await ex.get(str(f), "text/plain") == got

    @pytest.mark.asyncio
    async def test_get_without_prior_start(self, tmp_path):
        f = tmp_path / "doc2.txt"
        f.write_text("late start content")
        ex = AttachmentExtractor()
        got = await ex.get(str(f), "text/plain")  # no start() first
        assert "late start content" in got.text


class TestAttachmentInjectionRoots:
    """Which attached paths reach the model as CONTENT.

    A screen capture takes one of two routes to the same attachment chip: the browser
    snip uploads a PNG into ``uploads/`` like any other file, while the macOS native
    ``screencapture -i`` writes into ``screenshots/`` and threads the path straight
    into the send. Both must reach the model — as the image itself for a model that takes
    images, as its extracted text otherwise — or the chip claims an attachment the model
    was never told about (CHAT-CRAFT CC-4 finding).
    """

    class _Session:
        def __init__(self, files):
            self.messages = [{"role": "user", "content": "look", "meta": {"files": files}}]

    def _home(self, monkeypatch, tmp_path, texts):
        class _FakeExtractor:
            async def get(self, path, mime=None):
                return Extracted(texts.get(path, ""), path in texts)

        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.attachment_extract.get_extractor", lambda: _FakeExtractor()
        )

    def _inject(self, monkeypatch, tmp_path, files, texts):
        from personalclaw.dashboard import chat_runner

        self._home(monkeypatch, tmp_path, texts)
        return _run(chat_runner._inject_attachment_content(self._Session(files), "look"))

    def _images(self, monkeypatch, tmp_path, files, texts, *, accepted):
        from unittest.mock import AsyncMock

        from personalclaw.dashboard import chat_runner
        from personalclaw.providers.image_input import ImageInput

        self._home(monkeypatch, tmp_path, texts)
        monkeypatch.setattr(
            chat_runner,
            "_turn_image_input",
            AsyncMock(
                return_value=ImageInput(accepted, "" if accepted else "m can't take images.")
            ),
        )
        session = self._Session(files)
        return session, _run(chat_runner._prepare_image_attachments(session, object(), "look"))

    def _captures(self, tmp_path):
        from PIL import Image

        (tmp_path / "uploads").mkdir()
        (tmp_path / "screenshots").mkdir()
        up = tmp_path / "uploads" / "snip.png"
        shot = tmp_path / "screenshots" / "screenshot_1.png"
        for p in (up, shot):
            Image.new("RGB", (2, 2)).save(p, format="PNG")
        return str(up), str(shot)

    def test_upload_and_native_screenshot_are_both_inlined_for_a_text_model(
        self, monkeypatch, tmp_path
    ):
        up, shot = self._captures(tmp_path)
        session, (out, pixels) = self._images(
            monkeypatch,
            tmp_path,
            [up, shot],
            {up: "BROWSER SNIP TEXT", shot: "NATIVE SNIP TEXT"},
            accepted=False,
        )
        assert pixels == []
        assert "BROWSER SNIP TEXT" in out
        assert "NATIVE SNIP TEXT" in out
        assert out.endswith("look")
        assert session.messages[0]["meta"]["image_delivery"] == {up: "text", shot: "text"}

    def test_upload_and_native_screenshot_both_ride_as_images_for_a_vision_model(
        self, monkeypatch, tmp_path
    ):
        up, shot = self._captures(tmp_path)
        session, (out, pixels) = self._images(monkeypatch, tmp_path, [up, shot], {}, accepted=True)
        assert [p for p, _url in pixels] == [up, shot]
        assert all(url.startswith("data:image/png;base64,") for _p, url in pixels)
        assert out == "look"
        assert session.messages[0]["meta"]["image_delivery"] == {up: "image", shot: "image"}

    def test_workspace_mention_is_not_inlined(self, monkeypatch, tmp_path):
        # @-mentioned workspace files stay for the agent's own file tools — inlining
        # them here would duplicate content the model can already fetch on demand.
        (tmp_path / "uploads").mkdir()
        ws = str(tmp_path / "workspace" / "notes.md")
        out = self._inject(monkeypatch, tmp_path, [ws], {ws: "WORKSPACE TEXT"})
        assert out == "look"

    def test_sibling_dir_is_not_an_attachment_root(self, monkeypatch, tmp_path):
        # A prefix match without the separator would treat `uploads-old/` as `uploads/`.
        (tmp_path / "uploads").mkdir()
        (tmp_path / "uploads-old").mkdir()
        stray = str(tmp_path / "uploads-old" / "x.png")
        out = self._inject(monkeypatch, tmp_path, [stray], {stray: "STRAY TEXT"})
        assert out == "look"

    def test_native_screenshot_runs_the_real_extraction_graph(self, monkeypatch, tmp_path):
        """End-to-end through the REAL extractor: a PNG in screenshots/ reaches a turn
        whose model takes no images as its text. With no vision/OCR model bound the graph
        yields the structural descriptor rather than OCR text — so "OCR'd content" is a
        property of the configured model, not of this wiring."""
        from unittest.mock import AsyncMock

        from personalclaw.dashboard import chat_runner
        from personalclaw.providers.image_input import ImageInput

        monkeypatch.setattr(
            chat_runner,
            "_turn_image_input",
            AsyncMock(return_value=ImageInput(False, "m can't take images.")),
        )
        shots = tmp_path / "screenshots"
        shots.mkdir()
        png = shots / "screenshot_1.png"
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        out, pixels = _run(
            chat_runner._prepare_image_attachments(
                self._Session([str(png)]), object(), "what is this"
            )
        )
        assert pixels == []
        assert "screenshot_1.png" in out
        assert "(No extractable text content.)" not in out
