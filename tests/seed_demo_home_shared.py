"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_seed_demo_home.py``

Its checks:
``tests/rails/test_seed_demo_home_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from pathlib import Path

FIXTURE_NAME = "demo-home"


_REPO_ROOT = Path(__file__).resolve().parents[1]


_FIXTURES_DIR = _REPO_ROOT / "src" / "personalclaw" / "tests_fixtures"
