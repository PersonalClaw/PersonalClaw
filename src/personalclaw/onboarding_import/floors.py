"""Security floors every scanned byte passes — the platform's, not new ones.

Reading another tool's config is reading a directory full of things the user never
meant to hand over: an OAuth token cache, a `.env`, an API key in an MCP server's
`env` block. One secret policy applies, the same on the onboarding step and on the Tools
page's Import, and it never returns a value — only a count, so a caller *cannot*
accidentally log one:

1. :func:`refuses` — a credential-bearing PATH is never opened. ``is_sensitive_path``
   (the same predicate that blocks the agent's file reads) plus a filename denylist,
   because a fixture/foreign root outside ``$HOME`` doesn't match the home-relative
   rules.
2. :func:`safe_text` — free text keeps its body but loses every credential and
   exfiltration URL the platform's ONE detector finds (``security.redact_credentials`` /
   ``redact_exfiltration_urls``). A credential in text is dropped and counted.
3. An MCP server's ``env`` and ``headers`` values are not judged by name here: the MCP
   writer keeps every one in the credential store (``secret_refs.write_mcp_document``) and
   the file holds a reference. Another tool's own settings are never copied, so no value in
   them needs judging.

This module never writes: the foreign root is strictly read-only.
"""

from __future__ import annotations

import re
from pathlib import Path

from personalclaw.security import is_sensitive_path, redact_credentials, redact_exfiltration_urls

#: Filenames that are credential stores by convention. Checked in addition to
#: ``is_sensitive_path`` because that predicate anchors on the real ``$HOME`` and a
#: foreign/fixture root can live anywhere. ``auth.json`` is Codex's login (and Composer's
#: registry tokens): nothing in its name says "secret", so it is named here.
_SECRET_FILE_RE = re.compile(
    r"(^\.env($|\.)|credential|\.pem$|\.key$|id_[rd]sa|(^|[._-])secrets?($|[._-])"
    r"|(^|[._-])tokens?($|[._-])|\.netrc$|\.htpasswd$|^auth\.json$)",
    re.IGNORECASE,
)


def refuses(path: Path | str) -> bool:
    """True when this path must not be opened at all (floor 1)."""
    p = Path(path)
    if _SECRET_FILE_RE.search(p.name):
        return True
    return is_sensitive_path(str(p))


def safe_text(text: str) -> tuple[str, int]:
    """Redact credentials + exfiltration URLs from free text (floor 2).

    Returns ``(redacted_text, redaction_count)``. The matched values are discarded
    here on purpose: the count is the only thing a caller can propagate.
    """
    cleaned, creds = redact_credentials(text)
    cleaned, urls = redact_exfiltration_urls(cleaned)
    return cleaned, len(creds) + len(urls)


def read_text_safely(path: Path) -> tuple[str, int, int]:
    """Read a text file through floors 1 and 2.

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
