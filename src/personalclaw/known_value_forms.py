"""The forms a known value takes in text beside the value as written: as an address carries it
and as HTML escapes it. The mask by value in :mod:`personalclaw.security` matches each one.
"""

from __future__ import annotations

import re

#: What a browser percent-encodes of a value it writes into an address, beyond control characters
#: and anything past ``~`` (the WHATWG URL standard's percent-encode sets): in an http(s) query,
#: and in a path.
_BROWSER_QUERY_ENCODES = frozenset(" \"#<>'")
_BROWSER_PATH_ENCODES = frozenset(' "#<>?^`{}')


def _as_a_browser_writes(value: str, encodes: frozenset[str]) -> str:
    """*value* percent-encoded as a browser writes it into an address: each character in
    *encodes*, each control character and each past ``~``, as its UTF-8 bytes."""
    return "".join(
        (
            "".join(f"%{b:02X}" for b in ch.encode("utf-8", "surrogatepass"))
            if ch in encodes or not " " <= ch <= "~"
            else ch
        )
        for ch in value
    )


def _encoded_forms(value: str) -> set[str]:
    """*value* as a URL carries it (percent-encoded as Python's ``quote`` and as a browser writes a
    path or a query, a query's ``+`` for a space, ``/`` kept or not) and as HTML escapes it, so a
    page or an address that echoes it is masked too. A form that can't be built (a value holding
    an unpaired surrogate) is built from its escaped bytes, so this never raises."""
    from html import escape
    from urllib.parse import quote, quote_plus

    return {
        quote(value, errors="surrogatepass"),
        quote(value, safe="", errors="surrogatepass"),
        quote_plus(value, errors="surrogatepass"),
        _as_a_browser_writes(value, _BROWSER_QUERY_ENCODES),
        _as_a_browser_writes(value, _BROWSER_PATH_ENCODES),
        escape(value),
        escape(value, False),
    }


def _form_pattern(form: str) -> str:
    """The pattern one masked form matches: as written, but each percent escape in either case."""
    return "".join(
        (
            re.sub(r"[A-Fa-f]", lambda m: f"[{m.group(0).upper()}{m.group(0).lower()}]", piece)
            if re.fullmatch(r"%[0-9A-Fa-f]{2}", piece)
            else re.escape(piece)
        )
        for piece in re.split(r"(%[0-9A-Fa-f]{2})", form)
    )
