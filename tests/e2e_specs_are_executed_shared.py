"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_e2e_specs_are_executed.py``

Its checks:
``tests/rails/test_e2e_specs_are_executed_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


_E2E = _ROOT / "web" / "e2e"


#: Specs deliberately NOT run in CI, each with the mechanical reason. A spec absent from
#: both this map and the workflows fails the sweep — "we'll wire it later" has to be a
#: sentence someone typed, not a file nobody noticed.
LOCAL_ONLY: dict[str, str] = {
    "visual.spec.ts": (
        "Its baselines are made in the pinned Playwright image's browser alone "
        "(`web/e2e/visual-image.json`, #3943), and no CI leg serves that browser yet: merge.yml's "
        "`e2e-pinned` leg, which compares them through `web/e2e/run-pinned.mjs`, lands with the "
        "merge-queue flip. `test_no_exemption_is_also_wired` fails the moment a workflow runs "
        "this spec while it is still listed here."
    ),
}


def _specs() -> list[str]:
    return sorted(p.name for p in _E2E.glob("*.spec.ts"))
