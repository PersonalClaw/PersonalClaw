"""ONE loop action-guard vocabulary, backend to frontend.

🔴 WHY THIS RAIL EXISTS. Which statuses a loop action may be invoked FROM is one backend table,
``loop.loop:ACTION_SOURCE_STATES``, enforced in ``dashboard/handlers/loop_routes.py`` — a status
outside the action's set gets a 409 (``Cannot resume a loop in 'complete' state``). Every frontend
surface that renders a Start/Pause/Resume/Stop affordance then re-decided the same question by
hand, and the copies disagreed with the backend AND with each other.

Measured on the tree that introduced ``LOOP_ACTION_SOURCE_STATUSES``, ``resume`` had SIX
hand-written guards spanning three vocabularies:

* ``pages/chat/SdlcProgressCard.tsx``, ``pages/loops/LoopsListPage.tsx`` (twice) — three states.
* ``pages/loops/DesignCockpitPage.tsx``, ``pages/loops/LoopCockpitPage.tsx`` — four (plus
  ``failed``).
* ``pages/code/CodeCockpitPage.tsx`` — all five, but written as a chained ``===`` disjunction
  rather than an array literal, which is why an array-literal search found only five of the six.

Two user-visible defects fell out of that. Five of the six omitted ``blocked``, so a blocked loop
was unresumable through the UI everywhere while the backend accepted the transition; and ``failed``
was resumable on the two cockpits but not on the list or the in-chat card, so one loop offered a
different action set depending on which view you opened it from. ``start`` drifted the same way:
the backend accepts two states, one cockpit offered only one of them.

**Equality, not subset.** Each action's frontend set must EQUAL the backend's. A subset assertion
would have passed happily on all six guards — being a subset is exactly the shape the ``blocked``
omission had, and it is what let one missing state ship on every surface at once.

**Both sides are derived.** The backend side is the imported table, never retyped here; the
frontend side is parsed out of the TypeScript. Neither is a hand-written expectation, so this rail
cannot drift into agreeing with itself.

**Every scan carries a vacuity floor**, and ``test_the_parse_floors_fire`` proves the floors by
feeding the parser stubs that must red. A regex that quietly stops matching turns every equality
assertion below into a statement about the regex; that failure mode is the whole reason this file
exists, so it is asserted rather than trusted.

🪤 The parse is a TEXT scan with no comment awareness, and this repo has been bitten by a docstring
satisfying a ratchet, so comments are blanked before anything is matched (``_strip_comments``).
"""

from __future__ import annotations

import pytest
from loop_action_guard_mirror_shared import _WEB, _strip_comments

from personalclaw.loop.loop import ACTION_SOURCE_STATES

# web is optional in some checkouts (backend-only installs); skip cleanly then.
pytestmark = pytest.mark.skipif(not _WEB.exists(), reason="web sources not present")


def test_no_non_terminal_status_is_actionless():
    """The union of the action rows must cover every non-terminal status.

    The specific gap this closes: `intake` and `planning` were in NO row, so a loop whose classifier
    or planner died offered no action on any surface and `DELETE` was its only exit — which discards
    the record instead of terminating it. Asserted as the general property rather than as
    "intake and planning are in stop", because the next status added to the enum should fail
    this too rather than quietly inheriting the same hole.
    """
    from personalclaw.loop.loop import TERMINAL_STATUSES, LoopStatus

    union: set[LoopStatus] = set().union(*ACTION_SOURCE_STATES.values())
    assert union, "ACTION_SOURCE_STATES is empty — import drift?"
    non_terminal = set(LoopStatus) - set(TERMINAL_STATUSES)
    assert non_terminal, "every status reads as terminal — import drift?"
    stranded = sorted(s.value for s in non_terminal - union)
    assert not stranded, (
        f"these non-terminal statuses have NO available lifecycle action: {stranded}. A loop "
        "that reaches one can only be DELETED, losing its record. Give it a home in "
        "ACTION_SOURCE_STATES (and mirror it in web/src/lib/loopStatus.ts) rather than "
        "leaving the state actionless."
    )


#: The surfaces that host a loop lifecycle control today. Named rather than derived, following
#: `pages/surfaceEntranceAdoption.test.ts`'s reasoning: the property is about construction, and a
#: derived list would silently shrink to nothing the day someone renames a directory.
_LIFECYCLE_SURFACES = (
    "pages/loops/LoopsListPage.tsx",
    "pages/loops/LoopCockpitPage.tsx",
    # A goal loop's Start, Pause or Resume and Stop, in the strip above its composer (#3824).
    "pages/loops/LoopProgressStrip.tsx",
    # The same strip in a loop's detail tile beside the chat that started it, asking the map for
    # that loop's backing (#3824).
    "pages/chat/beside/LoopBeside.tsx",
    "pages/loops/DesignCockpitPage.tsx",
    # The code cockpit's run controls, in its header since the page was split into regions.
    "pages/code/cockpit/header/CockpitHeader.tsx",
)
# `pages/chat/SdlcProgressCard.tsx` left this list when it became a status mirror with no
# controls of its own: its Pause, Stop and Delete live on the loop's page, and its Details opens
# the tile above, where the strip is.


def test_every_lifecycle_surface_actually_reaches_the_mirror():
    """The other half of the census above, which on its own is satisfied by deleting a control.

    "No hand-written guard" is true of a file with no affordances at all, so absence is not
    adoption. Each surface that offers Start/Pause/Resume/Stop must be reached by the one mirror.
    """
    missing = []
    for rel in _LIFECYCLE_SURFACES:
        path = _WEB / rel
        assert path.exists(), f"{rel} is gone — update this list rather than letting it rot"
        text = _strip_comments(path.read_text(encoding="utf-8"))
        # `loopActionSources(loop)` is the mirror too — it answers from one of the two maps by the
        # loop's backing, and it is what a surface listing BOTH kinds of loop must ask.
        if "LOOP_ACTION_SOURCE_STATUSES" not in text and "loopActionSources(" not in text:
            missing.append(rel)
    assert not missing, (
        f"a lifecycle surface no longer reaches the action mirror: {missing}. Either it lost its "
        "controls (update this list and say so) or it went back to deciding for itself."
    )
