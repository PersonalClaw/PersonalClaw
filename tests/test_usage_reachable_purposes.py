"""``reachable_purposes()`` must match the writers that actually exist.

A spend chart that renders a row nothing can ever fill is not showing "no spend" — it is
showing a bucket with no writer, and the two read identically. ``PURPOSE_BY_SOURCE`` declares
five purposes; only some have a live turn-ledger writer, so :data:`UNWRITTEN_PURPOSES` names
the rest and this file keeps that list honest in BOTH directions:

* a purpose excluded as unwritten that in fact gains a writer  → the exclusion is now a LIE
  hiding real spend, which is the worse failure and the one this file exists to catch;
* a purpose advertised as reachable with no writer             → a permanent zero row.

The census reads the real ``record_from_event`` call sites out of the source, because the thing
under test is *which sources the code passes* — importing the modules would prove only that
they import.
"""

from __future__ import annotations

from personalclaw.routing.usage import (
    APP_PURPOSE,
    PURPOSE_BY_SOURCE,
    UNWRITTEN_PURPOSES,
    reachable_purposes,
)


def test_reachable_purposes_omits_the_unwritten_and_keeps_the_rest() -> None:
    got = set(reachable_purposes())
    assert not (got & UNWRITTEN_PURPOSES), f"{got & UNWRITTEN_PURPOSES} cannot be filled"
    # The ones with live writers are all present, so the exclusion is narrow, not a blanket.
    assert {"interactive", "background", APP_PURPOSE} <= got, got
    assert got, "reachable_purposes() must never be empty — a chart with no rows at all"


def test_an_app_source_still_reaches_the_app_purpose() -> None:
    """``chat_runner`` passes ``session._app or "chat"``, so an unrecognized source is an app
    name by design. APP_PURPOSE must therefore stay reachable even though no writer passes the
    literal string "app"."""
    assert APP_PURPOSE not in PURPOSE_BY_SOURCE.values()
    assert APP_PURPOSE in reachable_purposes()
