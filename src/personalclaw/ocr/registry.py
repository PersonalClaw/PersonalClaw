"""OCR provider registry — resolves the engine that reads pixels, if any.

Name-keyed, mirroring ``sandbox_providers.registry``: an enabled ``ocr`` app registers
its provider through ``OcrTypeHandler``, disabling it unregisters. There is NO in-core
builtin, deliberately — core ships no OCR engine, so an empty registry is the normal
state and :func:`active_ocr` returning ``None`` is what "today's behaviour" means.

:func:`active_ocr` filters on ``available()`` rather than on registration, because a
registered-but-unusable provider (package present, weights missing) must not shadow the
graceful-skip path it would otherwise turn into a failed ingest.
"""

from __future__ import annotations

import logging

from personalclaw.ocr.provider import OcrProvider

logger = logging.getLogger(__name__)

_providers: dict[str, OcrProvider] = {}


def register_provider(provider: OcrProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> OcrProvider | None:
    return _providers.get(name)


def list_providers() -> list[OcrProvider]:
    return list(_providers.values())


def active_ocr() -> OcrProvider | None:
    """The OCR provider that can run right now, or ``None``.

    Registration order decides between two available providers — an install is a user
    action, so the one they added is the one they get; there is no ranking to tune.
    A provider whose ``available()`` raises is treated as unavailable and logged, never
    propagated: this function is called on the ingest path and must not be able to fail it.
    """
    for prov in _providers.values():
        try:
            if prov.available():
                return prov
        except Exception:  # noqa: BLE001 - a broken probe must not fail an ingest
            logger.debug("ocr provider %s availability probe failed", prov.name, exc_info=True)
    return None


def ocr_available() -> bool:
    """Whether any registered OCR engine can run. The predicate the ingestion
    graph asks before it bothers rasterizing a page."""
    return active_ocr() is not None
