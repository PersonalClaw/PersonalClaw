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
