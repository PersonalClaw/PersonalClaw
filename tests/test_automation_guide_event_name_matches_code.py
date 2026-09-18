"""The failure event `docs/guides/automations.md` PROMISES must be the one the code SENDS (DL-12).

Same shape as ``test_forwarded_header_docs_match_code.py`` (RUA-6), and for the same reason. DL-12's
guide tells a user that a failing automation reaches their inbox *even when delivery is* ``none``,
and it names the event that carries it: ``automation.run.failed``. That name is the one thing in the
guide a reader might go looking for by string — in a notification row, in a log line, in a filter —
so it is the one claim that becomes actively misleading if the constant is renamed and the prose is
not.

**Why a rail and not just prose.** Every other claim in that guide is a ``file:line`` pointing at
code, which a reader re-resolves by opening the file. The event NAME has no such anchor: a reader
cannot tell from the guide alone whether ``automation.run.failed`` is still what gets emitted. Prose
cannot notice a rename; a test can. This is deliberately narrow — it pins the identifier that
crosses the docs/code boundary, not the behaviour the guide's recipe is for.

**Direction of the fix when this reds.** The CODE is authoritative. If ``EVENT_FAILED`` is renamed
on purpose, update the guide to the new value in the same change — do not rename the constant back
to satisfy this test, and do not delete the promise from the guide to make it green, because a
user's inbox row will still carry whatever the code sends.
"""

from __future__ import annotations

import pathlib
import re

import personalclaw
from personalclaw.triggers import delivery

SRC = pathlib.Path(personalclaw.__file__).resolve().parent
REPO = SRC.parent.parent
GUIDE = REPO / "docs" / "guides" / "automations.md"
DELIVERY = SRC / "triggers" / "delivery.py"

#: A dotted automation event identifier as the guide writes it, in backticks.
_EVENT_RE = re.compile(r"`(automation\.run\.[a-z_]+)`")


def test_the_guide_exists_and_names_the_failure_event():
    """DL-12's first `done_when`: the guide is the surface a prospect and a user both meet."""
    assert GUIDE.is_file(), f"{GUIDE} is missing — DL-12's guide is the deliverable"
    names = set(_EVENT_RE.findall(GUIDE.read_text(encoding="utf-8")))
    assert names, (
        "docs/guides/automations.md names no `automation.run.*` event, so this rail would be "
        "vacuous — the guide must state the event that carries a failure to the inbox"
    )
    assert delivery.EVENT_FAILED in names, (
        f"the guide promises {sorted(names)} but `triggers/delivery.py` sends "
        f"{delivery.EVENT_FAILED!r}. The code is authoritative: update the guide."
    )


def test_the_constant_still_carries_the_value_the_guide_quotes():
    """The pin itself. Asserted against the imported constant, so a rename reds here even if the
    literal string survives somewhere else in the module."""
    assert delivery.EVENT_FAILED == "automation.run.failed", (
        f"`EVENT_FAILED` is now {delivery.EVENT_FAILED!r}; docs/guides/automations.md still "
        f"promises 'automation.run.failed' to the user. Update the guide in this change."
    )
    # The constant must also be the one the fire path actually routes a FAILURE through — a
    # correctly-named constant with no reader would satisfy a string check and nothing else.
    text = DELIVERY.read_text(encoding="utf-8")
    assert "EVENT_SUCCEEDED if ok else EVENT_FAILED" in text, (
        "`EVENT_FAILED` is no longer selected by the ok/failure branch in triggers/delivery.py; "
        "the guide's guarantee 1 describes that branch, so re-verify it before changing this"
    )


def test_the_failure_route_defaults_to_the_inbox():
    """Guarantee 1 is only true because `failure_delivery` DEFAULTS to the inbox — the guide says so
    in those words ("The default is what makes this true without configuring anything"). A default
    flipped to `none` would make the guide's central promise false while every event name still
    matched, so the name check alone cannot carry this."""
    from personalclaw.triggers.models import Trigger

    field = Trigger.__dataclass_fields__["failure_delivery"]
    assert field.default == "inbox", (
        f"`Trigger.failure_delivery` now defaults to {field.default!r}. "
        f"docs/guides/automations.md promises a failure reaches the inbox with nothing configured."
    )
    # And a failure must not be routed through the silent channel: `route_for` is the decision the
    # guide cites, so assert the behaviour rather than the source text.
    quiet = Trigger(
        id="t",
        name="quiet",
        kind="clock",
        delivery="none",
        spec={"kind": "interval", "interval_secs": 600},
    )
    assert delivery.route_for(quiet, ok=False) == "inbox"
    assert delivery.is_muted(delivery.route_for(quiet, ok=False)) is False
    # The other half of the same rule: a SUCCESS on a quiet automation stays quiet.
    assert delivery.is_muted(delivery.route_for(quiet, ok=True)) is True
