"""True-type image detection — what the BYTES say, never what the name claims.

ARCC ``cnt_eMkU5kkpTaEk65`` "Secure File Uploads": a service "should not trust
Content-Type ... and must perform checks to verify that only allowlisted file types are
uploaded", must keep "an allow-list of file extensions", and "should implement and
enforce file upload size limits". This module is core's single implementation of the
first two for the OCR path, and :data:`MAX_IMAGE_BYTES` the third.

It is here, in core, rather than in each OCR app because a magic-number table copied per
bundle is a table that drifts per bundle — and the one that drifts is the one that lets a
non-image through. Apps reach it via ``personalclaw.sdk.ocr``.

**Allowlist, not denylist.** ``_SIGNATURES`` enumerates what MAY pass; anything else is
rejected, so a format nobody thought about is refused rather than forwarded to a decoder.
The extension allowlist is checked TOO, and independently: the bytes decide what the file
IS, the extension decides whether the user meant to hand us one at all, and a mismatch
between the two is itself a rejection — a ``.png`` holding a PDF is exactly the shape of
input this gate exists to stop.
"""

from __future__ import annotations

import os

#: Magic-number prefixes for the image types OCR accepts, by canonical type name.
#: Bytes, because that is what a magic number is. Kept deliberately small: every entry
#: is a decoder we are willing to hand untrusted bytes to.
_SIGNATURES: tuple[tuple[str, tuple[bytes, ...]], ...] = (
    ("png", (b"\x89PNG\r\n\x1a\n",)),
    ("jpeg", (b"\xff\xd8\xff",)),
    ("gif", (b"GIF87a", b"GIF89a")),
    ("bmp", (b"BM",)),
    ("tiff", (b"II*\x00", b"MM\x00*")),
)

#: WebP is ``RIFF....WEBP`` — a container magic with the format tag at offset 8, so it
#: cannot be expressed as a single prefix and is checked separately.
_RIFF = b"RIFF"
_WEBP = b"WEBP"

#: Extensions the OCR path accepts at all, mapped to the true type they must hold.
#: A ``.jpg`` whose bytes are PNG is a mismatch and is refused: the two facts have to
#: agree, because a gate that took either one alone would be bypassable by the other.
_EXTENSIONS: dict[str, str] = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".bmp": "bmp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".webp": "webp",
}

#: Byte ceiling for one image handed to an OCR engine (ARCC: enforce upload size limits).
#: 64 MiB is far above a 600-dpi A4 page (~25 MiB uncompressed) and far below what makes
#: a decoder the memory problem. A file over it is refused unread.
MAX_IMAGE_BYTES = 64 * 1024 * 1024

#: How many bytes the sniffer needs. The longest signature is 8 (PNG); WebP's format tag
#: ends at 12. Read a little more so the table can grow without touching the reader.
_SNIFF_BYTES = 32


class TrueTypeRejected(Exception):
    """The file is not an allowlisted image, by its BYTES or by its extension.

    Carries a reason a user can act on — "says .png, bytes are pdf" is actionable,
    "invalid file" is not.
    """


def detect_image_type(data: bytes) -> str | None:
    """The true image type of *data*, or ``None`` if it is not an allowlisted image.

    Pure and byte-only: no filename, no path, no ``Content-Type``. Give it a prefix
    (``_SNIFF_BYTES`` is enough) or the whole file — the answer is the same.
    """
    for name, prefixes in _SIGNATURES:
        if data.startswith(prefixes):
            return name
    if data[:4] == _RIFF and data[8:12] == _WEBP:
        return "webp"
    return None


def assert_image(path: str) -> str:
    """Gate *path* for the OCR engines: allowlisted extension, matching true type, size.

    Returns the detected true type. Raises :class:`TrueTypeRejected` otherwise. Called
    BEFORE any decoder sees the bytes — that ordering is the whole point, so a malformed
    or mislabeled file never reaches an image parser at all.

    The three checks are independent on purpose:

    * extension on the allowlist — the user meant to hand us an image;
    * true type from the bytes — it actually is one, and one we accept;
    * the two agree — neither fact alone can be used to smuggle past the other.
    """
    ext = os.path.splitext(path)[1].lower()
    expected = _EXTENSIONS.get(ext)
    if expected is None:
        raise TrueTypeRejected(
            f"extension {ext or '(none)'!r} is not an accepted image type "
            f"(accepted: {', '.join(sorted(_EXTENSIONS))})"
        )
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise TrueTypeRejected(f"cannot stat {os.path.basename(path)}: {exc}") from exc
    if size > MAX_IMAGE_BYTES:
        raise TrueTypeRejected(
            f"{os.path.basename(path)} is {size} bytes, over the "
            f"{MAX_IMAGE_BYTES}-byte OCR image ceiling"
        )
    if size == 0:
        raise TrueTypeRejected(f"{os.path.basename(path)} is empty")
    try:
        with open(path, "rb") as fh:
            head = fh.read(_SNIFF_BYTES)
    except OSError as exc:
        raise TrueTypeRejected(f"cannot read {os.path.basename(path)}: {exc}") from exc
    actual = detect_image_type(head)
    if actual is None:
        raise TrueTypeRejected(
            f"{os.path.basename(path)} claims {ext} but its bytes are not any accepted "
            "image type — refusing to OCR it"
        )
    if actual != expected:
        raise TrueTypeRejected(
            f"{os.path.basename(path)} claims {ext} ({expected}) but its bytes are "
            f"{actual} — refusing to OCR it"
        )
    return actual
