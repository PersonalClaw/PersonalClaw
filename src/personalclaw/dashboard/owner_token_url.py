"""The owner-token link's browser half — the one script the gateway inlines into its doors.

A fresh browser has no session cookie, so the owner token has to arrive in the first request
(``/?token=…``). The token middleware exchanges it for the HttpOnly ``pc_token_<port>`` cookie on
that response — and the token then stayed in the address bar and the history entry, where a
copied link, a screenshot or the Back button carries it on. And the paste-token Connect gate
rebuilt its target as ``origin + '?token='``, dropping the route the user had opened, so every
deep link landed on the dashboard instead.

``owner_token_url.js`` (package data beside this module) handles both, once: it scrubs the token
with ``history.replaceState``, and it builds the gate's same-origin target keeping only a
validated hash route. It is read ONCE at import, like ``baseline_denylist.json``: a wheel that
lost the file fails to start, instead of serving a Connect gate whose button does nothing.
"""

from __future__ import annotations

from pathlib import Path

#: Present on the inlined tag when the script should scrub the current URL as it runs.
SCRUB_ATTR = "data-owner-token-scrub"

_SOURCE = Path(__file__).with_name("owner_token_url.js").read_text(encoding="utf-8")
if "</script" in _SOURCE.lower():  # pragma: no cover — a packaging guard, not a runtime branch
    raise RuntimeError("owner_token_url.js must not contain a closing script tag")


#: The opening tag of the scrubbing script — what marks a document as already carrying it. The
#: bare attribute name is not enough: the script's own source spells it.
_SCRUB_OPEN = f"<script {SCRUB_ATTR}>"


def script_tag(*, scrub: bool) -> str:
    """The inline ``<script>`` carrying the owner-token helpers (and, with *scrub*, running it)."""
    return f"{_SCRUB_OPEN if scrub else '<script>'}{_SOURCE}</script>"


def script_source() -> str:
    """The bare script body, for a page that composes its own ``<script>`` element."""
    return _SOURCE


def inject_scrub(html: str) -> str:
    """Make the scrubbing script the FIRST thing in ``html``'s ``<head>``.

    First, so the token leaves the address bar before the parser reaches a single resource
    declaration. Idempotent, and a no-op for a document without ``<head>`` — served unchanged
    rather than mangled, like ``surface_layers.inject_safe_meta``.
    """
    if _SCRUB_OPEN in html:
        return html
    head = html.find("<head>")
    if head < 0:
        return html
    at = head + len("<head>")
    return html[:at] + script_tag(scrub=True) + html[at:]
