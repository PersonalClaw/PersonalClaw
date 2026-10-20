"""The ONE ``Content-Disposition`` emitter, and the one sanitiser for a download name.

**Why this module exists.** Five routes serve a download and every one of them composed the
header itself, in three mutually incompatible conventions:

* ``tasks/hierarchy_handlers.py`` interpolated ``archive_filename``'s output straight into
  ``attachment; filename="{…}"`` with **no redaction and no ASCII guard**, so a credential typed
  into a project name reached the wire verbatim — into proxy logs and browser download history,
  neither of which the user consented to and neither of which a later deletion reaches. The
  archive BODY carrying secrets is deliberate (the response ships
  ``X-PersonalClaw-Secrets-Expected``); the header was not.
* ``dashboard/session_export.py`` did redact, then folded the name to ASCII because the plain
  ``filename="…"`` form it emitted cannot carry anything else — so a chat titled ``日本語のチャット``
  downloaded as ``chat.json``, losing the name entirely for anyone whose titles are not Latin.
* ``dashboard/handlers/files.py`` used the RFC 6266 ``filename*=UTF-8''…`` form, which carries
  non-ASCII correctly, but ran no redaction at all and emitted no ASCII fallback for the clients
  that predate the parameter.
* ``dashboard/handlers/durability.py`` and ``dashboard/handlers/memory.py`` emitted a fourth and
  fifth copy of the plain form over names they compose themselves.

Three conventions over one header is the shape where a fix lands on one site and the other two
keep leaking, and where the SIXTH download route picks whichever neighbour it read first. So the
header value is composed in exactly one place, and the two rules that make it safe are properties
of that place rather than of each caller's memory:

1. **Redact before anything else.** :func:`safe_download_stem` runs
   :func:`~personalclaw.security.redact_field` *before* collapsing punctuation, because
   sanitising first breaks a credential shape into something the redactor no longer recognises
   while leaving it perfectly identifiable. :func:`attachment_disposition` redacts again at the
   wire, which is deliberate defence in depth and the only pass a name that never went through a
   stem builder (an on-disk outbox filename) ever gets.
2. **Both parameter forms, always.** ``filename="<ascii>"; filename*=UTF-8''<percent-encoded>``.
   A client that understands RFC 6266 reads ``filename*`` and gets the user's own characters; one
   that does not reads the ASCII fallback and still gets a usable name. Nothing has to be folded
   to ASCII to keep the header legal, so nothing is.

Header injection is closed by construction, not by validation: the ASCII fallback is rebuilt from
an allowlist (ASCII alphanumerics plus ``-_.``) so it cannot contain the ``"`` that would close
the quoted string, the ``\\`` that would escape it, or the CR/LF that would start a new header;
and ``filename*`` is percent-encoded with an empty safe set, so the same characters survive only
as ``%xx``.

This is a sibling of :mod:`personalclaw.http_errors` and exists for the same reason: it is a top
level module, not a ``dashboard/`` one, because ``workflows/`` and ``tasks/`` are domain code and
the ``core-must-not-import-the-http-surface`` direction ratchet refuses that upward edge.
"""

from __future__ import annotations

import urllib.parse

from personalclaw.security import redact_field

__all__ = ["DEFAULT_STEM_LIMIT", "attachment_disposition", "safe_download_stem"]

#: Characters kept verbatim in the ASCII fallback, beyond ASCII alphanumerics. Deliberately
#: excludes ``"``, ``\``, CR and LF — see the module docstring on injection.
_ASCII_KEEP = frozenset("-_.")

#: Cap on a derived stem. Long enough that a real title survives, short enough that the name
#: stays readable in a downloads folder and well inside every filesystem's limit.
DEFAULT_STEM_LIMIT = 60

#: Last resort when a name redacts or sanitises away to nothing. A download must still have a
#: name; an empty ``filename=""`` makes the browser invent one from the URL.
_UNNAMED = "download"


def safe_download_stem(
    user_text: str, *, fallback: str = "", limit: int = DEFAULT_STEM_LIMIT
) -> str:
    """A download-name stem derived from user-controlled text, redacted first.

    Tries *user_text* then *fallback*, returning the first that survives redaction and
    sanitisation; returns ``""`` when neither does, so the caller supplies its own last-resort
    literal and this function never invents a domain word. The fallback is sanitised on exactly
    the same path as the primary — a session key or a project id is no more trustworthy than a
    title just because the machine generated it.

    Carries non-ASCII through: ``str.isalnum()`` is true for CJK and accented letters and they
    are kept, because :func:`attachment_disposition` emits the RFC 6266 form that can express
    them. A project called ``Café`` downloads as ``Café`` and not as ``Caf``.

    Path traversal is closed as a side effect of the allowlist rather than by a separate check:
    ``.`` and ``/`` are not alphanumeric, so ``../../etc`` collapses to ``etc``.
    """
    for candidate in (user_text, fallback):
        source = redact_field(candidate or "")
        stem = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in source)
        stem = "-".join(part for part in stem.split("-") if part)[:limit].strip("-")
        if stem:
            return stem
    return ""


def attachment_disposition(filename: str) -> str:
    """The complete ``Content-Disposition`` value for an attachment named *filename*.

    Takes a WHOLE filename (extension included) rather than a stem, so the extension survives
    into both parameter forms and a client falling back to ``filename=`` still gets
    ``report.md`` rather than ``report-md``.

    Redacts *filename* as the last act before the wire. For a name already built by
    :func:`safe_download_stem` that pass is a no-op; for one taken from disk it is the only
    redaction the header ever gets.
    """
    name = redact_field(filename or "") or _UNNAMED
    ascii_name = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in _ASCII_KEEP)) else "-" for ch in name
    )
    ascii_name = "-".join(part for part in ascii_name.split("-") if part).strip("-")
    if not ascii_name or ascii_name.startswith("."):
        # A name that folds away entirely (``日本語.md``) keeps its extension and gains a stem,
        # rather than shipping ``filename=".md"`` and asking the browser to save a dotfile.
        ascii_name = _UNNAMED + ascii_name
    encoded = urllib.parse.quote(name, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"
