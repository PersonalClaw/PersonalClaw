"""Chat-attachment content extraction — knowledge EXTRACTION graph only, used to
inject an uploaded file's text into the chat prompt (no store / enrichment)."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.dashboard.attachment_extract import AttachmentExtractor, display_name
from personalclaw.knowledge.extract import extract_file_content


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestExtractFileContent:
    def test_plain_text_file(self, tmp_path):
        f = tmp_path / "report.txt"
        f.write_text("Revenue grew 42% to $3.1M.\nKey risk: API cost.")
        text = _run(extract_file_content(str(f), "text/plain"))
        assert "Revenue grew 42%" in text
        assert "$3.1M" in text

    def test_markdown_file(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text("# Heading\n\nBody text here.")
        text = _run(extract_file_content(str(f), "text/markdown"))
        assert "Body text here" in text

    def test_missing_file_returns_empty(self):
        assert _run(extract_file_content("/no/such/file.txt", "text/plain")) == ""

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
        text = _run(extract_file_content(str(png), "image/png"))
        # Either real OCR text (if a model is configured) or the structural fallback;
        # on a no-OCR box it must be the descriptor, never empty.
        assert text != ""
        assert "pic.png" in text or "Image" in text

    def test_empty_path_returns_empty(self):
        assert _run(extract_file_content("", None)) == ""


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
        text = await ex.get(str(f), "text/plain")
        assert "hello attachment world" in text
        # second get returns the same cached task result
        assert await ex.get(str(f), "text/plain") == text

    @pytest.mark.asyncio
    async def test_get_without_prior_start(self, tmp_path):
        f = tmp_path / "doc2.txt"
        f.write_text("late start content")
        ex = AttachmentExtractor()
        text = await ex.get(str(f), "text/plain")  # no start() first
        assert "late start content" in text


class TestAttachmentInjectionRoots:
    """Which attached paths reach the model as CONTENT.

    A screen capture takes one of two routes to the same attachment chip: the browser
    snip uploads a PNG into ``uploads/`` like any other file, while the macOS native
    ``screencapture -i`` writes into ``screenshots/`` and threads the path straight
    into the send. Both must be extracted and inlined, or the chip claims an
    attachment the model was never told about (CHAT-CRAFT CC-4 finding).
    """

    class _Session:
        def __init__(self, files):
            self.messages = [{"role": "user", "content": "look", "meta": {"files": files}}]

    def _inject(self, monkeypatch, tmp_path, files, texts):
        from personalclaw.dashboard import chat_runner

        class _FakeExtractor:
            async def get(self, path, mime=None):
                return texts.get(path, "")

        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(
            "personalclaw.dashboard.attachment_extract.get_extractor", lambda: _FakeExtractor()
        )
        return _run(chat_runner._inject_attachment_content(self._Session(files), "look"))

    def test_upload_and_native_screenshot_are_both_inlined(self, monkeypatch, tmp_path):
        (tmp_path / "uploads").mkdir()
        (tmp_path / "screenshots").mkdir()
        up = str(tmp_path / "uploads" / "snip.png")
        shot = str(tmp_path / "screenshots" / "screenshot_1.png")
        out = self._inject(
            monkeypatch,
            tmp_path,
            [up, shot],
            {up: "BROWSER SNIP TEXT", shot: "NATIVE SNIP TEXT"},
        )
        assert "BROWSER SNIP TEXT" in out
        assert "NATIVE SNIP TEXT" in out
        assert out.endswith("look")

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
        """End-to-end through the REAL extractor: a PNG in screenshots/ reaches the
        turn. With no vision/OCR model bound the graph yields the structural
        descriptor rather than OCR text — so "OCR'd content" is a property of the
        configured model, not of this wiring."""
        from personalclaw.dashboard import chat_runner

        shots = tmp_path / "screenshots"
        shots.mkdir()
        png = shots / "screenshot_1.png"
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        out = _run(
            chat_runner._inject_attachment_content(self._Session([str(png)]), "what is this")
        )
        assert "screenshot_1.png" in out
        assert "(No extractable text content.)" not in out
