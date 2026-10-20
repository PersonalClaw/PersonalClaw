"""Wiring point 5: an allowlisted config path must be reachable from a Settings control.

The config round-trip contract is five points — dataclass + ``_meta``, ``load()``, ``to_dict()``,
the ``_EDITABLE_CONFIG`` PATCH allowlist, and (for a user-facing field) a frontend control.
``tests/test_config_roundtrip.py`` covers points 1-3 and cannot see point 5 at all, so a whole
config SECTION could be editable, bounded, help-texted and read by the backend while no control
anywhere in ``web/`` wrote it. Issues #752 (``feedback.*``) and #2801 (``loops.*``) are two
instances; #2801 measured the class at **nine sections / 67 keys** and named ``workflows`` (21) as
the largest single block.

This module is the rail for that measurement, scoped to exactly those nine sections. It is derived
(the allowlist is IMPORTED, not parsed) rather than a pinned list of keys, so a new key added to any
of the nine reds here on the day it lands.

🪤 COMMENTS ARE STRIPPED FIRST. An unstripped scan would read a disabled example or explanatory
sentence as a control — the failure this repo has now had at least five times (the
primitive-adoption ratchet counting a ``<button>`` in a comment; the load-error scanner counting
a ``.catch`` it was documenting).

🪤 WHAT THIS RAIL DOES *NOT* CLAIM. It proves a WRITE PATH reaches each key, not that the control is
reachable, correctly bounded or correctly labelled. Those are separate claims, and they are made
where they can actually be made: ``web/src/pages/settings/configSectionControls.test.tsx`` renders
the new panels and asserts the exact dotted path each control PATCHes plus its bounds, and
``web/src/design/settingsHubCoverage.test.ts`` proves each panel is reachable from the hub at all.
A text scan alone would pass with every rendered element deleted.
"""

from __future__ import annotations

from settings_control_coverage_shared import INERT_NO_CONTROL, REPO, _allowlist

WEB_SRC = REPO / "web/src"


def test_the_inert_exclusions_are_still_on_the_allowlist() -> None:
    """The mirror direction: a stale exclusion for a path that no longer exists reads as coverage of
    something gone, and hides that the list was never revisited.
    """
    allowlist = _allowlist()
    stale = [k for k in INERT_NO_CONTROL if k not in allowlist]
    assert stale == [], f"INERT_NO_CONTROL names paths that are not on the allowlist: {stale}"


def test_the_two_panels_this_change_added_exist_and_are_registered() -> None:
    """The nineteen `workflows.*` and four `loops.*` controls needed somewhere to live, and a panel
    file that exists but is not in ``SUBPAGES`` is reachable by nothing. The hub/axe-manifest halves
    of that are checked in ``web/src/design/settingsHubCoverage.test.ts`` and
    ``settingsSubpageCoverage.test.ts``; this asserts the registration those two compare against.
    """
    page = (WEB_SRC / "pages/settings/SettingsPage.tsx").read_text(encoding="utf-8")
    for panel, sub in (("WorkflowsPanel", "workflows"), ("LoopsPanel", "loops")):
        assert (WEB_SRC / f"pages/settings/{panel}.tsx").exists(), f"{panel}.tsx is missing"
        assert f"import {{ {panel} }}" in page, f"{panel} is not imported by SettingsPage"
        assert f"id: '{sub}'" in page, f"{sub} is not a SUBPAGES id"
