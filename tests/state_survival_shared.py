"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_state_survival.py``

Its checks:
``tests/rails/test_state_survival_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_NAME = "six-month-home"


_REPO_ROOT = Path(__file__).resolve().parents[1]


_FIXTURES_DIR = _REPO_ROOT / "src" / "personalclaw" / "tests_fixtures"


_FIXTURE_DIR = _FIXTURES_DIR / FIXTURE_NAME
