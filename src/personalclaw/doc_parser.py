"""Document text extraction for .docx, .pdf, and .pptx files.

Uses only Python stdlib (zipfile + xml.etree.ElementTree) for .docx and
.pptx since these are ZIP archives containing XML.  A PDF's text is read from its
pages by pdfplumber, the reader the knowledge library uses.

All functions accept a file path and return extracted text as a string.
They never raise — on failure they return an empty string and log a warning.
"""

import logging
import re
import xml.etree.ElementTree as ETree
import zipfile
from pathlib import Path

from personalclaw.security import is_sensitive_path
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

# ── Size limits ──

_MAX_ZIP_ENTRY = 50 * 1024 * 1024  # 50 MB per ZIP entry (decompressed)
_MAX_PDF_PAGES = 1000  # pages read from one PDF

# ── Public API ──

# Mimetypes that map to document parsers
DOC_MIMETYPES: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/pdf": "pdf",
    "application/msword": "docx",
    "application/vnd.ms-powerpoint": "pptx",
}

# File extensions that map to document parsers
DOC_EXTENSIONS: set[str] = {".docx", ".pdf", ".pptx"}


def is_parseable_document(mimetype: str = "", filename: str = "") -> bool:
    """Return True if the file can be parsed by this module."""
    if mimetype in DOC_MIMETYPES:
        return True
    ext = Path(filename).suffix.lower() if filename else ""
    return ext in DOC_EXTENSIONS


def extract_text(path: str, mimetype: str = "", filename: str = "") -> str:
    """Extract readable text from a document file.

    Detects format from *mimetype* first, then falls back to file extension.
    Returns empty string on any failure.
    """
    if is_sensitive_path(path):
        logger.warning("Refusing to read sensitive path: %s", path)
        sel().log_api_access(
            caller="doc_parser",
            operation="extract_text",
            outcome="denied",
            source="local",
            resources=path,
            error="sensitive_path_rejected",
        )
        return ""
    fmt = DOC_MIMETYPES.get(mimetype, "")
    if not fmt:
        ext = Path(filename or path).suffix.lower()
        fmt = {".docx": "docx", ".pptx": "pptx", ".pdf": "pdf"}.get(ext, "")
    if not fmt:
        return ""
    try:
        if fmt == "docx":
            return _extract_docx(path)
        if fmt == "pptx":
            return _extract_pptx(path)
        if fmt == "pdf":
            return _extract_pdf(path)
    except Exception:
        logger.warning("Failed to extract text from %s", path, exc_info=True)
    return ""


# ── ZIP entry safety ──


def _read_zip_entry(
    zf: zipfile.ZipFile,
    name: str,
    max_size: int | None = None,
) -> bytes | None:
    """Read a ZIP entry with an *actual* decompressed-size limit.

    Returns ``None`` if the real decompressed output exceeds *max_size*,
    regardless of what the ZIP header declares.
    """
    if max_size is None:
        max_size = _MAX_ZIP_ENTRY
    with zf.open(name) as f:
        data = f.read(max_size + 1)
        if len(data) > max_size:
            logger.warning("ZIP entry too large (actual): %s", name)
            return None
        return data


# ── DOCX parser (Office Open XML) ──

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _extract_docx(path: str) -> str:
    """Extract text from a .docx file (ZIP containing word/document.xml).

    Must only be called from extract_text() which enforces is_sensitive_path().
    """
    if is_sensitive_path(path):
        return ""
    paragraphs: list[str] = []
    with zipfile.ZipFile(path, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            return ""
        data = _read_zip_entry(zf, "word/document.xml")
        if data is None:
            return ""
        root = ETree.fromstring(data)  # noqa: S314
        for para in root.iter(f"{_W_NS}p"):
            texts: list[str] = []
            for t_elem in para.iter(f"{_W_NS}t"):
                if t_elem.text:
                    texts.append(t_elem.text)
            if texts:
                paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


# ── PPTX parser (Office Open XML) ──

_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def _extract_pptx(path: str) -> str:
    """Extract text from a .pptx file (ZIP containing ppt/slides/*.xml).

    Must only be called from extract_text() which enforces is_sensitive_path().
    """
    if is_sensitive_path(path):
        return ""
    slides: list[tuple[int, str]] = []
    with zipfile.ZipFile(path, "r") as zf:
        slide_names = sorted(
            (n for n in zf.namelist() if _SLIDE_RE.match(n)),
            key=lambda n: int(_SLIDE_RE.match(n).group(1)),  # type: ignore[union-attr]
        )
        for slide_name in slide_names:
            data = _read_zip_entry(zf, slide_name)
            if data is None:
                continue
            num = int(_SLIDE_RE.match(slide_name).group(1))  # type: ignore[union-attr]
            root = ETree.fromstring(data)  # noqa: S314
            texts: list[str] = []
            for t_elem in root.iter(f"{_A_NS}t"):
                if t_elem.text:
                    texts.append(t_elem.text)
            if texts:
                slides.append((num, "\n".join(texts)))
    parts: list[str] = []
    for num, text in slides:
        parts.append(f"--- Slide {num} ---\n{text}")
    return "\n\n".join(parts)


# ── PDF parser ──


def _extract_pdf(path: str) -> str:
    """The text a PDF's pages show, read by pdfplumber.

    Only the pages: a PDF's other streams hold its embedded fonts and images, which are not
    text, and a scan that read every stream for string literals returned the font bytes as
    if they were the document.

    Must only be called from extract_text() which enforces is_sensitive_path().
    """
    if is_sensitive_path(path):
        return ""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages[:_MAX_PDF_PAGES]]
    return "\n".join(page for page in pages if page.strip()).strip()
