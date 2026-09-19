"""SDK: the OCR-engine provider ABC + the true-type gate its input must pass.

Stable re-export of :mod:`personalclaw.ocr` — an OCR app (RapidOCR, Tesseract,
PaddleOCR, docTR, a cloud vision API) implements :class:`OcrProvider` and imports these,
never the core modules directly. Provider-agnostic by construction: core holds the
contract and the gate, the app holds the engine.

:func:`assert_image` is re-exported deliberately, not left to each bundle. The ARCC
"Secure File Uploads" rule an OCR app has to satisfy — trust the BYTES, not the
extension, and cap the size — is one magic-number table, and a table copied per bundle is
a table that drifts per bundle. Call it before handing anything to a decoder.

``register_provider`` is NOT promoted: registration is core's job through the manifest's
``ocr`` type handler, so an app's provider appears exactly while the app is enabled.
"""

from personalclaw.ocr.filetype import (  # noqa: F401
    MAX_IMAGE_BYTES,
    TrueTypeRejected,
    assert_image,
    detect_image_type,
)
from personalclaw.ocr.provider import (  # noqa: F401
    OcrError,
    OcrProvider,
    OcrRejected,
    OcrResult,
)
from personalclaw.ocr.registry import active_ocr, ocr_available  # noqa: F401

__all__ = [
    "OcrProvider",
    "OcrResult",
    "OcrError",
    "OcrRejected",
    "active_ocr",
    "ocr_available",
    "assert_image",
    "detect_image_type",
    "TrueTypeRejected",
    "MAX_IMAGE_BYTES",
]
