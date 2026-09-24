"""Security floors every scanned byte passes — reusing ours, not new ones.

Reading another tool's config is reading a directory full of things the user never
meant to hand over: an OAuth token cache, a `.env`, an API key in an MCP server's
`env` block. Three floors apply, in this order, and all three are counting floors —
the user is told how much was withheld, never what it was:

1. :func:`refuses` — a credential-bearing PATH is never opened. ``is_sensitive_path``
   (the same predicate that blocks the agent's file reads) plus a filename denylist,
   because a fixture/foreign root outside ``$HOME`` doesn't match the home-relative
   rules.
2. :func:`strip_secrets` — a secret-NAMED key inside a config we DO read is dropped
   before the value is ever copied into an :class:`~.model.ImportItem`.
3. :func:`safe_text` — free text keeps its body but loses embedded credentials and
   exfiltration URLs (``redact_credentials`` / ``redact_exfiltration_urls``).

Nothing here returns a secret value. Every function returns a count, so a caller
*cannot* accidentally log one.

This module never writes: the foreign root is strictly read-only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from personalclaw.security import is_sensitive_path, redact_credentials, redact_exfiltration_urls

#: Filenames that are credential stores by convention. Checked in addition to
#: ``is_sensitive_path`` because that predicate anchors on the real ``$HOME`` and a
#: foreign/fixture root can live anywhere.
_SECRET_FILE_RE = re.compile(
    r"(^\.env($|\.)|credential|\.pem$|\.key$|id_[rd]sa|(^|[._-])secrets?($|[._-])"
    r"|(^|[._-])tokens?($|[._-])|\.netrc$|\.htpasswd$)",
    re.IGNORECASE,
)

#: Key names whose VALUE is a secret. Matched as a substring on the key, so
#: ``ANTHROPIC_API_KEY``, ``githubToken`` and ``db_password`` all hit.
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|bearer|private[_-]?key"
    r"|access[_-]?key|auth)",
    re.IGNORECASE,
)

#: A dropped value is replaced by nothing at all — the key is removed too, so a
#: downstream writer can't resurrect a placeholder into a real config.


def refuses(path: Path | str) -> bool:
    """True when this path must not be opened at all (floor 1)."""
    p = Path(path)
    if _SECRET_FILE_RE.search(p.name):
        return True
    return is_sensitive_path(str(p))


def strip_secrets(value: Any) -> tuple[Any, int]:
    """Drop every secret-named key, recursively. Returns ``(clean, dropped_count)``.

    Dicts lose the whole key/value pair; lists are walked. Scalars pass through —
    a bare string is redacted by :func:`safe_text`, not here, because at scalar
    depth there is no key name to judge by.
    """
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        dropped = 0
        for key, val in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                dropped += 1
                continue
            sub, sub_dropped = strip_secrets(val)
            clean[key] = sub
            dropped += sub_dropped
        return clean, dropped
    if isinstance(value, list):
        out: list[Any] = []
        dropped = 0
        for entry in value:
            sub, sub_dropped = strip_secrets(entry)
            out.append(sub)
            dropped += sub_dropped
        return out, dropped
    return value, 0


def safe_text(text: str) -> tuple[str, int]:
    """Redact credentials + exfiltration URLs from free text (floor 3).

    Returns ``(redacted_text, redaction_count)``. The matched values are discarded
    here on purpose: the count is the only thing a caller can propagate.
    """
    cleaned, creds = redact_credentials(text)
    cleaned, urls = redact_exfiltration_urls(cleaned)
    return cleaned, len(creds) + len(urls)


def read_text_safely(path: Path) -> tuple[str, int, int]:
    """Read a text file through floors 1 and 3.

    Returns ``(text, redactions, secrets_skipped)``. A refused path yields
    ``("", 0, 1)`` — counted as withheld and never opened.
    """
    if refuses(path):
        return "", 0, 1
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", 0, 0
    cleaned, redactions = safe_text(raw)
    return cleaned, redactions, 0


def read_json_safely(path: Path) -> tuple[Any, int]:
    """Read a JSON file through floors 1 and 2.

    Returns ``(secret_free_object_or_None, secrets_skipped)``. Malformed JSON is
    treated as "nothing to import" (a foreign tool's half-written config is not our
    problem to repair), and a refused path is counted, not parsed.
    """
    if refuses(path):
        return None, 1
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, 0
    clean, dropped = strip_secrets(parsed)
    return clean, dropped
