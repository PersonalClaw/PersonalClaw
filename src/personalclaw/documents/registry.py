"""Format → writer lookup.

Mirrors the shape of `artifacts/registry.py`: a dict keyed by name, one `register()` per
format. ``available_formats`` reports what is USABLE right now rather than what is
declared, because a tool that offers a format and then fails mid-generation is worse than
one that says up front it can't.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: A writer takes a model and returns file bytes. PURE — no I/O, no store access — so it
#: is unit-testable without a store and cannot half-write on failure.
Writer = Callable[[object], bytes]

_writers: dict[str, Writer] = {}


def register_writer(fmt: str, fn: Writer) -> None:
    _writers[fmt] = fn


def get_writer(fmt: str) -> Writer | None:
    """The writer for *fmt*, or None. Never raises: an unknown format is a caller-facing
    refusal, not an exception to catch at every call site."""
    _ensure_registered()
    return _writers.get((fmt or "").strip().lower())


def available_formats() -> list[str]:
    """Formats usable in this process, sorted.

    Registration itself is the availability check — a writer whose library is missing
    never registers, so this cannot claim a format that would fail on use.
    """
    _ensure_registered()
    return sorted(_writers)


_registered = False


def _ensure_registered() -> None:
    """Import the bundled writers once, on first use.

    Lazy so importing this module never costs the docx/xlsx parse libraries, and so a
    writer whose dependency is unavailable degrades to "format not offered" instead of
    breaking the import of everything else.
    """
    global _registered
    if _registered:
        return
    _registered = True
    for module, fmt in (("docx_writer", "docx"), ("xlsx_writer", "xlsx")):
        try:
            __import__(f"personalclaw.documents.writers.{module}")
        except Exception:  # noqa: BLE001 — a missing optional lib must not break the rest
            logger.info("document writer %s unavailable; %s will not be offered", module, fmt)
