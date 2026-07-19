"""Media classification + thumbnailing for previewable knowledge items.

Binary media (image/audio/video) are stored as single previewable items that
serve their bytes back via the file endpoint, then run through their node-graph
(Image/Audio/Video) for exif, OCR, vision, and transcription extraction. A quick
inline thumbnail is generated on upload for immediate display; the graph's own
nodes enrich the item asynchronously. Thumbnail generation is best-effort
(Pillow, if present); callers degrade to a type icon when no thumbnail exists.
"""

import mimetypes
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]

# Extension → knowledge type. Mirrors the typed-item discriminator + the
# accepted-MIME map the create UI offers.
_EXT_TYPE: dict[str, str] = {
    # images (.heic/.heif = the default iPhone photo format; .tif/.tiff = scans/cameras)
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".bmp": "image", ".svg": "image",
    ".heic": "image", ".heif": "image", ".tiff": "image", ".tif": "image",
    # audio
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".flac": "audio",
    ".m4a": "audio", ".aac": "audio",
    # video (.m4v = the MPEG-4 video container Apple/iTunes uses, sibling of .mp4)
    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
    ".webm": "video", ".m4v": "video",
    # documents (text-extractable via the reader stack → Document graph)
    ".pdf": "pdf", ".docx": "document", ".doc": "document",
    # tabular → sheet (.tsv is the tab-separated sibling of .csv, rendered as a table)
    ".xlsx": "sheet", ".xls": "sheet", ".csv": "sheet", ".tsv": "sheet",
    ".pptx": "slides", ".ppt": "slides",
    # prose / markup → document (one logical doc; read inline). .markdown/.text are the
    # full-word forms of .md/.txt that some editors emit.
    ".md": "document", ".markdown": "document", ".txt": "document", ".text": "document",
    ".rst": "document", ".html": "document", ".htm": "document", ".log": "document",
    # source code → gist (code, with a language for syntax highlighting + the
    # "Gist · <Language>" label). Routed via _CODE_EXT_LANGUAGE, not _EXT_TYPE,
    # so classify() can both pick the gist type AND carry the language.
}

# Code-file extension → gist language. An uploaded source file becomes a ``gist``
# item (not a generic ``document``) so it gets the code-aware treatment: a syntax-
# highlighted preview/editor and a "Gist · <Language>" type label everywhere. The
# language values match the FE's GIST_LANGUAGES (highlight.js identifiers).
_CODE_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".sql": "sql",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml", ".yml": "yaml",
}


def code_language(filename: str) -> str | None:
    """The gist language for a source-code filename, or None if not code.

    Used by the upload path to route code files to the ``gist`` type and stamp
    ``gist_language`` so the item gets syntax highlighting + a "Gist · <Language>"
    label, instead of landing as a generic ``document``."""
    return _CODE_EXT_LANGUAGE.get(Path(filename).suffix.lower())

# The binary media types that have no text to chunk → stored as previewable items.
BINARY_MEDIA_TYPES = {"image", "audio", "video"}

_THUMBNAIL_MAX = (480, 480)


def classify(filename: str, mime: str | None = None) -> str | None:
    """Map a filename to its knowledge type, or None if not a known media/doc kind.

    A ``mime`` hint disambiguates extensions that don't pin the kind — notably
    ``.webm``/``.ogg`` (audio OR video): a browser audio recording is ``audio/webm``
    but the extension alone would classify as video, sending it down the wrong graph.
    The mime's top-level ``audio/``|``video/``|``image/`` wins when it disagrees with
    the extension's media guess; otherwise the extension map is authoritative."""
    by_ext = _EXT_TYPE.get(Path(filename).suffix.lower())
    top = (mime or "").split("/", 1)[0].lower()
    if top in ("audio", "video", "image") and by_ext in BINARY_MEDIA_TYPES and by_ext != top:
        return top
    if by_ext is None and code_language(filename):
        # A source-code file → gist (code), not a generic document.
        return "gist"
    return by_ext


def is_binary_media(filename: str) -> bool:
    return classify(filename) in BINARY_MEDIA_TYPES


# Python's mimetypes returns legacy/nonstandard types for some media that browsers
# won't play in <audio>/<video> (e.g. .m4a → audio/mp4a-latm, .wav → audio/x-wav).
# Override with the canonical web MIME so the inline players work.
_CANONICAL_MIME: dict[str, str] = {
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    # .m4v is an MPEG-4 container; browsers play it as video/mp4 (not the legacy x-m4v).
    ".m4v": "video/mp4",
}


def guess_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _CANONICAL_MIME:
        return _CANONICAL_MIME[ext]
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def make_image_thumbnail(src_path: str, dest_path: str) -> bool:
    """Write a WebP thumbnail of an image to ``dest_path``. Returns True on success.

    Best-effort: requires Pillow and a raster image (SVG is skipped — it scales
    natively in the browser and Pillow can't open it)."""
    if Image is None:
        return False
    if Path(src_path).suffix.lower() == ".svg":
        return False
    try:
        with Image.open(src_path) as im:
            im = im.convert("RGB")
            im.thumbnail(_THUMBNAIL_MAX)
            im.save(dest_path, "WEBP", quality=80)
        return True
    except Exception:
        return False
