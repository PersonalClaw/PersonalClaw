"""Safe-surfaces mode — the server half of the layered surface overlay.

Lives in CORE, not under ``dashboard/``: the gateway entry point (`cli_server._gateway`) has
to latch it before anything serves a page, and core importing the HTTP surface is the upward
dependency `scripts/gate_report.py`'s import-direction gate refuses. The HTTP surface imports
DOWN into this module instead (the status handler, the index handler) — which is the inversion
that gate asks for rather than a raised violation count.

AMBIENT-SURFACES §6. The SPA resolves three surface layers: L0 core (the shipped
bundle), L1 app (app-contributed pages and genui components), L2 user/agent overlays.
Safe mode forces ``maxLayer = 0`` — pure L0 — and there are deliberately TWO ways in:

* ``#/dashboard?safe=1`` — the URL a user can be read over the phone. Owned by the SPA.
* ``--safe-surfaces`` — this module. The lever that works when the client cannot render
  its own address bar, and the one an operator can set once for the whole process.

The flag is a PROCESS latch, not config: it is a recovery decision about this run of the
gateway, and persisting it would mean a user who recovered once boots into safe mode
forever. It is also one-way within a process — a later ``set_safe_surfaces(False)``
cannot clear it, because nothing in a running gateway has the authority to decide that
the operator's recovery flag is over.

How the SPA learns it: a ``<meta>`` tag injected into ``index.html`` at serve time. NOT
a fetch — the layer ceiling has to be known before the first app module loads, and a
bootstrap request that lands after that decision would make the flag advisory. It is
also reported on ``/api/status`` so the CLI/doctor can say the gateway is in safe mode.
"""

from __future__ import annotations

_safe = False

#: The meta tag `index.html` carries when this process serves safe surfaces. The FE reads
#: this name; changing it is a wire-contract change on both sides.
SAFE_META_NAME = "personalclaw-safe-surfaces"


def set_safe_surfaces(on: bool) -> None:
    """Latch safe-surfaces mode on. A falsy value is a no-op (one-way by design)."""
    global _safe
    if on:
        _safe = True


def safe_surfaces() -> bool:
    """Whether this gateway process serves surfaces in safe mode."""
    return _safe


def reset_for_tests() -> None:
    """Clear the latch. Tests only — the flag is one-way in a real process."""
    global _safe
    _safe = False


def inject_safe_meta(html: str) -> str:
    """Add the safe-surfaces meta tag to `html` when the latch is on.

    Idempotent (a second call cannot double-inject) and a no-op when the document has no
    ``<head>`` — a malformed index is served unchanged rather than mangled, because the
    recovery route is worth more intact than annotated.
    """
    if not _safe or SAFE_META_NAME in html:
        return html
    head = html.find("<head>")
    if head < 0:
        return html
    at = head + len("<head>")
    return f'{html[:at]}<meta name="{SAFE_META_NAME}" content="1">{html[at:]}'
