"""An attached image as an image part: the one place a chat attachment becomes pixels.

A chat attachment that is an image reaches the model one of two ways, and the turn decides
which from the platform's record (``providers.image_input``): as PIXELS, when the model serving
the turn takes images, or as the text the knowledge extraction graph read from it (OCR and a
vision description), when it does not. This module is the pixel half.

What an image part must be, whichever wire carries it:

* **Checked by its bytes.** :func:`~personalclaw.ocr.filetype.assert_image` gates it first —
  extension on the allowlist, true type from the magic number, the two agreeing, and a size
  ceiling (ARCC ``cnt_eMkU5kkpTaEk65`` "Secure File Uploads": never trust the name or the
  Content-Type; enforce size limits). Nothing is decoded before that passes.
* **One of the closed wire types** — the same set a screen frame is held to
  (``screen_context.ALLOWED_MEDIA_TYPES``). A GIF, BMP or TIFF is re-encoded to PNG; an
  animation keeps its first frame.
* **Fit for every wire.** The long edge is capped at :data:`MAX_EDGE_PX` (beyond it the vendors
  downscale anyway, and bill for the pixels first) and the encoded part at
  :data:`MAX_PART_BYTES`, the smallest per-image ceiling among the wires the platform speaks
  (Bedrock Converse). An image that cannot be brought under it is not sent as pixels at all;
  the caller then sends its text instead of an image the provider would refuse.
"""

from __future__ import annotations

import base64
import io
import logging

from personalclaw.dashboard.screen_context import ALLOWED_MEDIA_TYPES

logger = logging.getLogger(__name__)

#: Longest edge an image part keeps, in pixels.
MAX_EDGE_PX = 1568
#: Largest encoded image part, in bytes (Bedrock Converse takes 3.75 MB per image).
MAX_PART_BYTES = 3_750_000

_FORMAT_MEDIA_TYPE = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def is_image_attachment(path: str) -> bool:
    """True when *path* is sent down the image path (pixels, or its extracted text)."""
    from personalclaw.ocr.filetype import has_image_extension

    return has_image_extension(path)


def image_part_url(path: str) -> str:
    """A ``data:`` URL for *path* fit to ride a turn as an image part, or ``""`` when it cannot.

    ``""`` covers every refusal — a file the byte gate rejects, one Pillow cannot decode, one
    that stays over :data:`MAX_PART_BYTES` — and the reason is logged, never raised: the caller
    falls back to the image's text, so a refused image is still described to the model.
    """
    from personalclaw.ocr.filetype import TrueTypeRejected, assert_image

    try:
        assert_image(path)
    except TrueTypeRejected as exc:
        logger.info("image part refused for %s: %s", path, exc)
        return ""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        media_type, payload = _fit(raw)
    except Exception:  # noqa: BLE001 — an undecodable image is sent as its text instead
        logger.info("image part: could not prepare %s", path, exc_info=True)
        return ""
    if not payload:
        logger.info("image part: %s stays over %d bytes after downscaling", path, MAX_PART_BYTES)
        return ""
    return f"data:{media_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _fit(raw: bytes) -> tuple[str, bytes]:
    """``(media_type, bytes)`` for an image part; ``bytes`` is empty when it cannot fit.

    An image already on the wire list, within both limits, is sent byte-for-byte. Anything else
    is decoded once and re-encoded: downscaled to :data:`MAX_EDGE_PX`, kept as PNG when it has
    transparency or came in as a lossless format, JPEG otherwise, and retried as JPEG when the
    PNG is still over the byte ceiling.
    """
    from PIL import Image

    with Image.open(io.BytesIO(raw)) as im:
        fmt = (im.format or "").upper()
        media_type = _FORMAT_MEDIA_TYPE.get(fmt, "")
        fits = max(im.size) <= MAX_EDGE_PX and len(raw) <= MAX_PART_BYTES
        if media_type in ALLOWED_MEDIA_TYPES and fits and not getattr(im, "is_animated", False):
            return media_type, raw
        im.seek(0)
        frame = im.copy()
    frame.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
    has_alpha = frame.mode in ("RGBA", "LA") or (frame.mode == "P" and "transparency" in frame.info)
    if has_alpha or fmt in ("PNG", "GIF", "BMP", "TIFF"):
        png = _encode(frame.convert("RGBA" if has_alpha else "RGB"), "PNG")
        if len(png) <= MAX_PART_BYTES:
            return "image/png", png
    jpeg = _encode(frame.convert("RGB"), "JPEG", quality=85)
    return "image/jpeg", (jpeg if len(jpeg) <= MAX_PART_BYTES else b"")


def _encode(frame, fmt: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    frame.save(buf, format=fmt, **kwargs)
    return buf.getvalue()
