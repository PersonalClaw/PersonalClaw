"""What a fire its automation's own rules held tells whoever asked for it.

A program's post to a webhook (`inbound.webhook`) and a run someone other than you asked for by name
(`dashboard.handlers.trigger_runs`) are each admitted as every fire is (`service.admit_fire`). A
fire the admission holds runs nothing: its caller is told so in these words, with the HTTP status
its gate answers (``429`` when the automation has fired as often as its owner allows, ``409`` for
every other rule), under the one code ``fire_held`` (`http_errors`), which each door passes as
the literal, and the automation's history keeps the typed row that says exactly why.
"""

from __future__ import annotations

#: The status and the sentence for each gate a held fire can be held at (`triggers.firepath`).
_HELD: dict[str, tuple[int, str]] = {
    **dict.fromkeys(
        ("spacing", "rate"),
        (
            429,
            "This automation has fired as often as its owner allows for now, so this request "
            "fired nothing. Try again later.",
        ),
    ),
    **dict.fromkeys(
        ("quiet", "duty"),
        (
            409,
            "This automation is set not to fire at this time, so this request fired nothing. Try "
            "again later.",
        ),
    ),
    "budget": (
        409,
        "This automation has fired as many times as its owner allows, so this request fired "
        "nothing.",
    ),
    "claim": (
        409,
        "This automation is still running from an earlier request, so this one fired nothing. "
        "Send it again once that run ends.",
    ),
    **dict.fromkeys(
        ("slot", "active", "yield"),
        (
            409,
            "What this automation works with is busy, so this request fired nothing. Try again "
            "shortly.",
        ),
    ),
}
_HELD_OTHERWISE = (409, "This automation's own rules held this request, so it fired nothing.")


def held(gate: str) -> tuple[int, str]:
    """The HTTP status and the sentence for a fire its automation's admission held at *gate*."""
    return _HELD.get(gate, _HELD_OTHERWISE)
