"""Tests for document text extraction (doc_parser.py)."""

import logging
import os
import tempfile
import zipfile

from personalclaw.doc_parser import (
    extract_text,
    is_parseable_document,
)

# ── Helpers ──


def _make_docx(paragraphs: list[str]) -> str:
    """Create a minimal .docx file and return its path."""
    body = "\n".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return path


def _make_pptx(slides: list[list[str]]) -> str:
    """Create a minimal .pptx file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".pptx")
    os.close(fd)
    with zipfile.ZipFile(path, "w") as zf:
        for i, texts in enumerate(slides, 1):
            shapes = "\n".join(
                f"<p:sp><p:txBody><a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:txBody></p:sp>"
                for t in texts
            )
            xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
                ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                f"<p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>"
            )
            zf.writestr(f"ppt/slides/slide{i}.xml", xml)
    return path


# ── is_parseable_document ──


class TestIsParseableDocument:
    def test_docx_mimetype(self):
        mt = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert is_parseable_document(mimetype=mt)

    def test_pptx_mimetype(self):
        mt = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        assert is_parseable_document(mimetype=mt)

    def test_pdf_mimetype(self):
        assert is_parseable_document(mimetype="application/pdf")

    def test_extension_docx(self):
        assert is_parseable_document(filename="report.docx")

    def test_extension_pptx(self):
        assert is_parseable_document(filename="deck.pptx")

    def test_extension_pdf(self):
        assert is_parseable_document(filename="paper.pdf")

    def test_text_not_parseable(self):
        assert not is_parseable_document(mimetype="text/plain")

    def test_image_not_parseable(self):
        assert not is_parseable_document(mimetype="image/png")

    def test_empty_not_parseable(self):
        assert not is_parseable_document()


# ── DOCX extraction ──


class TestExtractDocx:
    def test_basic_paragraphs(self):
        path = _make_docx(["Hello World", "Second paragraph"])
        try:
            result = extract_text(path, filename="test.docx")
            assert "Hello World" in result
            assert "Second paragraph" in result
        finally:
            os.unlink(path)

    def test_empty_docx(self):
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'  # noqa: E501
                "<w:body></w:body></w:document>",
            )
        try:
            result = extract_text(path, filename="empty.docx")
            assert result == ""
        finally:
            os.unlink(path)

    def test_missing_document_xml(self):
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("other.xml", "<root/>")
        try:
            result = extract_text(path, filename="bad.docx")
            assert result == ""
        finally:
            os.unlink(path)

    def test_mimetype_detection(self):
        path = _make_docx(["Via mimetype"])
        try:
            mt = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            result = extract_text(path, mimetype=mt)
            assert "Via mimetype" in result
        finally:
            os.unlink(path)


# ── PPTX extraction ──


class TestExtractPptx:
    def test_single_slide(self):
        path = _make_pptx([["Title", "Body text"]])
        try:
            result = extract_text(path, filename="deck.pptx")
            assert "Slide 1" in result
            assert "Title" in result
            assert "Body text" in result
        finally:
            os.unlink(path)

    def test_multiple_slides(self):
        path = _make_pptx([["Slide One"], ["Slide Two"]])
        try:
            result = extract_text(path, filename="multi.pptx")
            assert "Slide 1" in result
            assert "Slide 2" in result
            assert "Slide One" in result
            assert "Slide Two" in result
        finally:
            os.unlink(path)

    def test_empty_pptx(self):
        fd, path = tempfile.mkstemp(suffix=".pptx")
        os.close(fd)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
        try:
            result = extract_text(path, filename="empty.pptx")
            assert result == ""
        finally:
            os.unlink(path)


# ── PDF extraction ──


def _pdf(text: str, *, other_stream: bytes = b"") -> bytes:
    """A real one-page PDF showing *text* in Helvetica, and optionally carrying *other_stream*
    as an object no page shows (where a PDF keeps an embedded font's bytes)."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if other_stream:
        objects.append(
            b"<< /Length %d >>\nstream\n" % len(other_stream) + other_stream + b"\nendstream"
        )
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


class TestExtractPdf:
    def test_a_pages_text_is_read(self, tmp_path):
        path = tmp_path / "test.pdf"
        path.write_bytes(_pdf("Hello from PDF"))
        assert extract_text(str(path), filename="test.pdf") == "Hello from PDF"

    def test_what_no_page_shows_is_not_read_as_text(self, tmp_path):
        """A PDF's embedded fonts sit in streams no page shows. Their bytes hold string-shaped
        runs, and a reader that took every stream's runs returned them as the document."""
        font_like = b"(\x01\x02 binary glyph run) (\x7f\x10 another) (kern table)"
        path = tmp_path / "quote.pdf"
        path.write_bytes(_pdf("Subtotal 31080", other_stream=font_like))
        text = extract_text(str(path), mimetype="application/pdf", filename="quote.pdf")
        assert text == "Subtotal 31080"
        assert "glyph" not in text and "kern" not in text

    def test_empty_pdf(self, tmp_path):
        path = tmp_path / "empty.pdf"
        path.write_bytes(b"%PDF-1.0\n%%EOF")
        assert extract_text(str(path), filename="empty.pdf") == ""


# ── Error handling ──


class TestErrorHandling:
    def test_nonexistent_file(self):
        result = extract_text("/nonexistent/file.docx", filename="file.docx")
        assert result == ""

    def test_corrupt_zip(self):
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(b"not a zip file")
            result = extract_text(path, filename="corrupt.docx")
            assert result == ""
        finally:
            os.unlink(path)

    def test_unknown_extension(self):
        result = extract_text("/tmp/file.xyz", filename="file.xyz")
        assert result == ""

    def test_unknown_mimetype(self):
        result = extract_text("/tmp/file", mimetype="application/octet-stream")
        assert result == ""

    def test_sensitive_path_rejected(self, caplog):
        """extract_text refuses to read sensitive paths."""
        with caplog.at_level(logging.WARNING):
            result = extract_text(
                os.path.expanduser("~/.aws/credentials"), filename="credentials.docx"
            )
        assert result == ""
        assert "Refusing to read sensitive path" in caplog.text


# ── Decompression bomb guards ──


class TestDecompressionGuards:
    def test_oversized_zip_entry_skipped(self, caplog):
        """A ZIP entry whose actual decompressed content exceeds the limit is skipped."""
        from unittest.mock import patch

        import personalclaw.doc_parser as dp

        path = _make_docx(["Normal text"])
        try:
            # Temporarily lower the limit so the real entry exceeds it
            with patch.object(dp, "_MAX_ZIP_ENTRY", 5):
                with caplog.at_level(logging.WARNING):
                    result = extract_text(path, filename="bomb.docx")
            assert result == ""
            assert "ZIP entry too large" in caplog.text
        finally:
            os.unlink(path)
