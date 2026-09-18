"""Abstract base for OCR providers — read text OUT of pixels, with no model bound.

This is the seam that separates "PersonalClaw can read a scanned page" from "which
engine does the reading". Core declares the contract and owns nothing else: every real
engine (RapidOCR, Tesseract, PaddleOCR, docTR, a cloud vision API) lives in a removable
``ocr`` provider app and reaches core only through ``personalclaw.sdk.ocr``.

Why a provider type rather than a model use-case: an OCR engine is not a chat model.
It takes no prompt, has no sampling temperature, and its output must be **byte-stable**
for the same input — a property the model seam deliberately does not promise. Routing an
engine through ``image_modality`` would have meant lying about its shape and then
re-deriving determinism on top of a sampler. So ``recognize`` is the whole contract, and
:attr:`OcrProvider.deterministic` is a claim the provider makes and its own tests hold it to.

``available()`` is a LIVE probe, not a symbol check: an engine whose python package is
importable but whose weights/binary are missing is NOT available, and a seam that
answered otherwise would turn a removable app into a hard ingest failure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


class OcrError(Exception):
    """An engine failed to read an image it accepted. NOT raised for "no engine" —
    that case is a ``None`` from :func:`personalclaw.ocr.registry.active_ocr`, because
    "nothing installed" is a configuration fact and not an error to handle."""


class OcrRejected(OcrError):
    """The input was refused BEFORE recognition — wrong true type, over a ceiling.

    Separate from :class:`OcrError` so a caller can tell "this file is not OCR-able"
    (a permanent, explainable verdict about the input) from "the engine broke on a file
    it accepted" (a retryable fault). A rejection is the expected result of the
    true-type gate, so callers report it; they do not retry it.
    """


@dataclass
class OcrResult:
    """One recognition pass over one or more page/frame images.

    ``text`` is the concatenated recognized text in input order — the single value the
    ingestion pipeline pools. ``engine`` identifies what produced it so a stored item
    records WHICH engine read it (an item OCR'd by two different engines across a
    re-ingest is not the same evidence). ``pages`` carries the per-image text so a
    caller that needs page boundaries does not have to re-split ``text``.
    """

    text: str = ""
    engine: str = ""
    pages: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class OcrProvider(ABC):
    """Provider interface for an OCR engine.

    An implementation lives in an app bundle and is registered by core's
    ``OcrTypeHandler`` when the app is enabled (and unregistered on disable), so a
    user with no OCR app installed sees exactly today's behaviour.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Registry key — the provider/app name (kebab-case), unique per install."""

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """The ENGINE this provider drives, with its version, e.g.
        ``"rapidocr-onnxruntime/1.2.3"``. Recorded on the item alongside the text:
        two engines reading one page are two different pieces of evidence, and a
        stored result with no engine id cannot be re-verified later."""

    @property
    def deterministic(self) -> bool:
        """True when the same bytes always yield the same text (no sampling).

        Declared, not inferred. A provider that cannot promise this returns False, and
        callers that need a stable ingest may then decline it.
        """
        return True

    @abstractmethod
    def available(self) -> bool:
        """Whether this engine can actually run RIGHT NOW in this process.

        A LIVE probe: import the engine, confirm its weights/binary resolve. Must never
        be a truthiness test on a symbol — an importable package with no model files is
        not available, and answering True there converts a missing optional dependency
        into a failed ingest.
        """

    @abstractmethod
    async def recognize(self, image_paths: Sequence[str]) -> OcrResult:
        """Recognize text in the given images, in order.

        Raises :class:`OcrRejected` when an input is refused before recognition (true
        type, ceiling) and :class:`OcrError` when an accepted input fails. Returns an
        :class:`OcrResult` with empty ``text`` when the images genuinely carry no text —
        that is a successful read of a blank page, not a failure.
        """

    def info(self) -> dict[str, Any]:
        """Describe this provider for the settings surface."""
        return {
            "name": self.name,
            "engine_id": self.engine_id,
            "deterministic": self.deterministic,
            "available": self.available(),
        }
