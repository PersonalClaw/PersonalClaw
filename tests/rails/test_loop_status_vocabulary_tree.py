"""The source-tree checks split from
``tests/test_loop_status_vocabulary.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import source_index
from loop_status_vocabulary_shared import _REGISTRY, _REPO, _WEB

# web is optional in some checkouts (backend-only installs); skip cleanly then.
pytestmark = pytest.mark.skipif(not _WEB.exists(), reason="web sources not present")


#: A loop-status registry declares a status key against a string or an object literal
#: (``stagnant: { label: 'Stalled', … }``). Deliberately NOT matched: a status keyed to a number
#: (``CodeSection``'s ``stagnant: 2`` attention-sort rank) — a rank order is not a vocabulary.
#:
#: Also deliberately NOT counted: a table that PROJECTS a loop status into a different closed
#: vocabulary. `lib/useAgentActivity.ts`'s `LOOP_STATE` maps the 12 `UnifiedLoopStatus` members
#: onto the 5 `AgentActivityState` members (`working`/`needs_input`/`waiting_approval`/`idle`/
#: `error`) for the ambient worlds — it holds no label and no tone, so it cannot produce the
#: "Stalled vs Stagnant" or two-meanings-of-green drift this census exists to prevent. Same
#: reasoning as the rank-order exemption above: a projection is not a words-and-tones table.
#: (When it was added, the rail matched its `stagnant: 'needs_input'` row, which is a state
#: name, not a display word.)
_REGISTRY_ROW = re.compile(r"\bstagnant:\s*[{'\"]")


#: The active-status set literal, in the order every hand-written copy used.
_ACTIVE_LITERAL = re.compile(r"'running',\s*'paused',\s*'stagnant'")


def _web_sources() -> list[Path]:
    return [
        p
        for p in source_index.web_files()
        if p.suffix in {".ts", ".tsx"} and not p.name.endswith(".test.ts")
    ]


def test_there_is_exactly_one_loop_status_registry():
    # Vacuity floor first: the positive control must still match, or "nothing else matches" is
    # a statement about the regex rather than about the codebase.
    assert _REGISTRY_ROW.search(
        _REGISTRY.read_text(encoding="utf-8")
    ), f"the loop-status row pattern no longer matches {_REGISTRY} — this census is vacuous"
    others = [
        p.relative_to(_REPO)
        for p in _web_sources()
        if p != _REGISTRY
        and p.name != "useAgentActivity.ts"  # a projection, not a vocabulary — see above
        and _REGISTRY_ROW.search(source_index.text(p))
    ]
    assert not others, (
        f"a second loop-status registry reappeared: {[str(p) for p in others]}. The words and "
        "tones live in web/src/lib/loopStatus.ts only — two tables is how 'Stalled' vs 'Stagnant' "
        "and two meanings of green shipped at once."
    )


def test_no_surface_rewrites_the_active_status_set_by_hand():
    assert _ACTIVE_LITERAL.search(
        _REGISTRY.read_text(encoding="utf-8")
    ), f"the active-set literal no longer matches {_REGISTRY} — this census is vacuous"
    others = [
        p.relative_to(_REPO)
        for p in _web_sources()
        if p != _REGISTRY and _ACTIVE_LITERAL.search(source_index.text(p))
    ]
    assert not others, (
        f"a hand-written active-loop-status set reappeared: {[str(p) for p in others]}. Import "
        "ACTIVE_LOOP_STATUSES from lib/loopStatus instead — four copies drifted here once, and the "
        "one that dropped `blocked` made a blocked loop's cockpit read as finished."
    )
