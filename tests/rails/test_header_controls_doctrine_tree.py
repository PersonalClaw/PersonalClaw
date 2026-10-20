"""The source-tree checks split from
``tests/test_header_controls_doctrine.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from header_controls_doctrine_shared import _WEB


def _tsx_files() -> list[Path]:
    """Every ``.tsx`` under ``web/src``, from the suite's one source index."""
    return [p for p in source_index.web_files() if p.suffix == ".tsx"]


def test_no_overflowmenu_primitive():
    """The standalone OverflowMenu primitive stays deleted — the HeaderActions
    cluster's internal auto-`…` is the single overflow mechanism for headers."""
    assert not (_WEB / "ui" / "OverflowMenu.tsx").exists(), (
        "web/src/ui/OverflowMenu.tsx is back — headers must use the HeaderActions "
        "cluster's built-in `…` (a hand-built OverflowMenu is the anti-pattern this "
        "plan removed)."
    )
    offenders = [
        str(f.relative_to(source_index.REPO))
        for f in _tsx_files()
        if "OverflowMenu" in source_index.text(f)
    ]
    assert not offenders, (
        "`OverflowMenu` referenced again — use HeaderControl with priority='low' in a "
        "HeaderActions cluster; the container collapses low-priority controls into its "
        "own `…` menu:\n  " + "\n  ".join(offenders)
    )


def test_no_headerbutton_alias():
    """`HeaderButton` (the old 2-tier control) is gone — use `HeaderControl`."""
    offenders = [
        str(f.relative_to(source_index.REPO))
        for f in _tsx_files()
        if "HeaderButton" in source_index.text(f)
    ]
    assert not offenders, (
        "`HeaderButton` found — it was replaced by `HeaderControl` (adds priority / "
        "danger / `…`-menu participation). Import + use HeaderControl:\n  " + "\n  ".join(offenders)
    )
