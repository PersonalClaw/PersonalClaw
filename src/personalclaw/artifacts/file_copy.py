"""The bytes of an image or PDF file, checked, for saving as a versioned binary artifact.

What Files' "Save as artifact" does for a file whose body is bytes rather than text: the artifact
is a COPY, taken when it is saved, and the file is never written. (A text file's artifact is a
live pointer at the file instead; `native.py`.)

The file is judged by what its BYTES are, never by its name or a declared type, and only the
formats the artifact store keeps pass: PNG, JPEG, GIF and WebP images (the true type
``ocr.filetype.detect_image_type`` reads, the one image sniffer in core) and PDF. The name must
agree with the bytes as well, so a ``.png`` holding a PDF is refused rather than stored as either.
Its size is decided from the file's metadata before a byte is read.
"""

from __future__ import annotations

import os
from pathlib import Path

from personalclaw.artifacts.models import MAX_BINARY_CONTENT_BYTES
from personalclaw.ocr.filetype import detect_image_type

#: The true image types the store keeps (a MIME `models.ext_for_mime` names a file for): how each
#: is named to a person, the MIME it is stored as, and the extensions a file of it may carry.
_IMAGE_TYPES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "png": ("PNG", "image/png", (".png",)),
    "jpeg": ("JPEG", "image/jpeg", (".jpg", ".jpeg")),
    "gif": ("GIF", "image/gif", (".gif",)),
    "webp": ("WebP", "image/webp", (".webp",)),
}

_PDF_MAGIC = b"%PDF-"

#: Enough of the head for every signature above (WebP's tag ends at byte 12).
_SNIFF_BYTES = 32

#: What a refusal is, so the route can answer each with its own status.
TOO_LARGE = "too_large"
UNSUPPORTED = "unsupported"
UNREADABLE = "unreadable"


class FileCopyRefused(ValueError):
    """The file cannot be saved as a binary artifact; ``reason`` is one of the three above and
    the message says which fact refused it, in words the person can act on."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _sniffed(head: bytes) -> tuple[str, str, str, tuple[str, ...]] | None:
    """``(kind, label, mime, extensions)`` for *head*'s true type, or ``None`` for a type the
    store keeps no artifact of."""
    image = detect_image_type(head)
    if image in _IMAGE_TYPES:
        label, mime, extensions = _IMAGE_TYPES[image]
        return "image", label, mime, extensions
    if head.startswith(_PDF_MAGIC):
        return "pdf", "PDF", "application/pdf", (".pdf",)
    return None


def _read_bounded(path: Path) -> bytes:
    """At most one byte over the cap, through a descriptor that refuses a symlink: *path* is
    canonical, so a symlink there now is one swapped in since it was admitted."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as fh:
        return fh.read(MAX_BINARY_CONTENT_BYTES + 1)


def read_file_body(path: Path) -> tuple[bytes, str, str]:
    """``(data, kind, mime)`` of the image or PDF at *path* (an admitted, canonical path).

    Raises :class:`FileCopyRefused`: over the artifact size cap (decided from ``stat``, before
    reading), not an image or PDF the store keeps, a name that does not match its bytes, or a
    file that cannot be read.
    """
    name = path.name
    try:
        if not path.is_file():
            raise FileCopyRefused(UNREADABLE, f"{name} is not a file that can be read.")
        size = path.stat().st_size
    except OSError as exc:
        raise FileCopyRefused(UNREADABLE, f"{name} could not be read: {exc.strerror}.") from exc
    cap_mb = MAX_BINARY_CONTENT_BYTES // (1024 * 1024)
    if size > MAX_BINARY_CONTENT_BYTES:
        raise FileCopyRefused(
            TOO_LARGE,
            f"{name} is {size / (1024 * 1024):.1f} MB, and an artifact holds at most {cap_mb} MB.",
        )
    try:
        data = _read_bounded(path)
    except OSError as exc:
        raise FileCopyRefused(UNREADABLE, f"{name} could not be read: {exc.strerror}.") from exc
    if len(data) > MAX_BINARY_CONTENT_BYTES:
        # It grew between the check and the read: still over the cap, and still refused.
        raise FileCopyRefused(TOO_LARGE, f"{name} is over {cap_mb} MB, what an artifact holds.")
    sniffed = _sniffed(data[:_SNIFF_BYTES])
    if sniffed is None:
        raise FileCopyRefused(
            UNSUPPORTED,
            f"{name} is not a PNG, JPEG, GIF, WebP or PDF file, so it cannot be saved as an "
            "image or PDF artifact.",
        )
    kind, label, mime, extensions = sniffed
    if path.suffix.lower() not in extensions:
        raise FileCopyRefused(
            UNSUPPORTED,
            f"{name} is a {label} file, and its name says otherwise, so it is not saved. Rename "
            f"it to end in {' or '.join(extensions)} first.",
        )
    return data, kind, mime
