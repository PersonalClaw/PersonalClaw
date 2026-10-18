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
        "All 40 baselines are platform-qualified `-darwin` (playwright.config.ts's "
        "`snapshotPathTemplate` includes `{platform}`), so a Linux runner has no baseline to "
        "compare against and would fail every route on its first run. Wiring this job "
        "requires committing `-linux` baselines, which requires a Linux run — see "
        "`test_the_visual_exemption_retires_itself`, which fails the moment one appears."
    ),
}


def _specs() -> list[str]:
    return sorted(p.name for p in _E2E.glob("*.spec.ts"))
