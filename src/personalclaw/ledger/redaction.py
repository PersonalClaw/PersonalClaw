"""What must never reach an append-only file, and what must never sit inline in one.

Two screens, both applied on the way IN because a ledger is append-only: a credential written to
`journal.jsonl` is a credential in every bug report and every flywheel read from then on, and there
is no line to go back and edit. Same for a binary blob — a reader that has already parsed it has
already paid for it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def redact(value: Any) -> Any:
    """Strip credentials from anything bound for the journal.

    Delegates to the platform's existing redactors rather than re-deriving patterns:
    they are already maintained, already cover the exfiltration-URL case, and a second
    private copy of the rules would drift out of date exactly when it mattered.
    """
    if isinstance(value, str):
        try:
            from personalclaw.security import redact_credentials, redact_exfiltration_urls

            text, _ = redact_exfiltration_urls(value)
            text, _ = redact_credentials(text)
            return text
        except Exception:  # pragma: no cover — redaction must never break a write
            logger.debug("redaction unavailable", exc_info=True)
            return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


# ── binary detection ─────────────────────────────────────────────────────────

#: Magic prefixes for the formats a node output plausibly picks up — an action provider
#: reading a file, a screenshot tool, a fetched asset. Not exhaustive by design: this is a
#: cheap "is this obviously not text" check, and the size boundary catches whatever slips
#: through. Bytes rather than str because that is what a magic number IS.
_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
    b"\x1f\x8b",  # gzip
    b"PK\x03\x04",  # zip / docx / xlsx / jar
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"\x7fELF",
    b"OggS",
    b"RIFF",  # wav / avi / webp container
)

#: The SAME formats as they arrive base64-encoded. This is the realistic carrier: a node
#: output is JSON, and JSON cannot hold arbitrary bytes — so a screenshot tool or a fetched
#: asset reaches the journal base64'd, and a raw-byte check alone would miss every one of
#: them. Prefixes are long enough (7+ chars of a fixed header) that a false positive on prose
#: is not a practical concern.
_BASE64_PREFIXES: tuple[str, ...] = (
    "iVBORw0KGgo",  # PNG
    "/9j/",  # JPEG
    "R0lGODdh",  # GIF87a
    "R0lGODlh",  # GIF89a
    "JVBERi0",  # %PDF-
    "H4sI",  # gzip
    "UEsDBB",  # zip
    "f0VMRg",  # ELF
)


def is_binary_payload(value: Any) -> bool:
    """True when ``value`` is a string whose leading bytes match a known binary format.

    Content-based, so it catches a small binary an inline-size check never would: a 400-byte
    PNG is under every threshold and still meaningless inline — mojibake in the widget, a
    poisoned `{{nodes.x.output}}` binding, wasted context if it reaches a model.

    Both carriers are checked. Raw bytes decoded into a `str` are recovered with latin-1
    (which maps codepoints 0-255 back to the identical bytes) rather than UTF-8 — a PNG's
    leading `\\x89` UTF-8-encodes to TWO bytes, so a UTF-8 round-trip silently fails to match
    any magic number, which is exactly the bug this comment exists to prevent. Base64 is the
    other carrier, and in practice the more common one.

    Only strings are inspected. A dict or list is structure the engine created, and treating
    a container as binary because one leaf looked like a PNG would spill a whole useful
    output over one field.
    """
    if not isinstance(value, str) or not value:
        return False
    head = value[:16]
    try:
        raw = head.encode("latin-1")
    except UnicodeEncodeError:
        # Codepoints above 255: genuinely text (or surrogate-escaped bytes, which latin-1
        # cannot hold either). Fall back so a lone astral character cannot mask a match.
        raw = head.encode("utf-8", errors="surrogateescape")
    if any(raw.startswith(m) for m in _MAGIC_PREFIXES):
        return True
    return value[:16].startswith(_BASE64_PREFIXES)
