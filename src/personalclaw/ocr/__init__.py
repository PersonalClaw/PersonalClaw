"""OCR — the removable engine seam for reading text out of pixels.

Core declares the contract (:mod:`personalclaw.ocr.provider`), resolves whichever engine
an installed ``ocr`` app registered (:mod:`personalclaw.ocr.registry`), and owns the
true-type gate every engine must pass its input through (:mod:`personalclaw.ocr.filetype`).
It ships NO engine: with no app installed the registry is empty, ``active_ocr()`` is
``None``, and ingestion behaves exactly as it did before this package existed.
"""

from __future__ import annotations

from personalclaw.ocr.filetype import (
    MAX_IMAGE_BYTES,
    TrueTypeRejected,
    assert_image,
    detect_image_type,
)
from personalclaw.ocr.provider import OcrError, OcrProvider, OcrRejected, OcrResult
from personalclaw.ocr.registry import (
    active_ocr,
    get_provider,
    list_providers,
    ocr_available,
    register_provider,
    unregister_provider,
)

__all__ = [
    "OcrProvider",
    "OcrResult",
    "OcrError",
    "OcrRejected",
    "active_ocr",
    "ocr_available",
    "register_provider",
    "unregister_provider",
    "get_provider",
    "list_providers",
    "assert_image",
    "detect_image_type",
    "TrueTypeRejected",
    "MAX_IMAGE_BYTES",
]
