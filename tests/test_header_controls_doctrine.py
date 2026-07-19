"""Guardrail tests for the responsive-header-controls doctrine
(responsive-header-controls.md).

ONE responsive cluster owns every TopBar page's controls: `HeaderActions` +
`HeaderControl`/`HeaderSegmented` (4-tier FULL→TEXT→ICON→OVERFLOW, whole-cluster
degradation, an internal auto-`…` menu). Two invariants worth pinning so a future
header can't regress:

  1. No hand-built `OverflowMenu` in a header — the cluster owns the `…`. The old
     `OverflowMenu.tsx` primitive was deleted once every header folded into the
     cluster; this guard keeps it gone (re-introducing it is the regression).
  2. No `HeaderButton` — it was the 2-tier primitive, replaced by `HeaderControl`
     (+priority/danger/menu participation). The alias was removed; banning the name
     stops a stale import from resurrecting the old contract.

Project FE-source-guard idiom (web has no ESLint), mirroring
test_url_navigation_doctrine.py / test_transport_doctrine.py.
"""

from __future__ import annotations

from pathlib import Path

_WEB = Path("web/src")


def test_no_overflowmenu_primitive():
    """The standalone OverflowMenu primitive stays deleted — the HeaderActions
    cluster's internal auto-`…` is the single overflow mechanism for headers."""
    assert not (_WEB / "ui" / "OverflowMenu.tsx").exists(), (
        "web/src/ui/OverflowMenu.tsx is back — headers must use the HeaderActions "
        "cluster's built-in `…` (a hand-built OverflowMenu is the anti-pattern this "
        "plan removed)."
    )
    offenders = [
        str(f) for f in _WEB.rglob("*.tsx")
        if "OverflowMenu" in f.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "`OverflowMenu` referenced again — use HeaderControl with priority='low' in a "
        "HeaderActions cluster; the container collapses low-priority controls into its "
        "own `…` menu:\n  " + "\n  ".join(offenders)
    )


def test_no_headerbutton_alias():
    """`HeaderButton` (the old 2-tier control) is gone — use `HeaderControl`."""
    offenders = [
        str(f) for f in _WEB.rglob("*.tsx")
        if "HeaderButton" in f.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "`HeaderButton` found — it was replaced by `HeaderControl` (adds priority / "
        "danger / `…`-menu participation). Import + use HeaderControl:\n  "
        + "\n  ".join(offenders)
    )


def test_headeractions_is_four_tier():
    """Sanity: the HeaderActions primitive still implements the 4-tier ladder + the
    controlled children (so the guards above ban *bypasses*, not the mechanism)."""
    src = (_WEB / "ui" / "HeaderActions.tsx").read_text(encoding="utf-8")
    for token in ("'overflow'", "HeaderControl", "HeaderSegmented", "ResizeObserver"):
        assert token in src, f"HeaderActions.tsx should contain {token}"
