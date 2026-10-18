"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_npm_advisory_floors.py``

Its checks:
``tests/rails/test_npm_advisory_floors_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_LOCKFILE = Path(__file__).resolve().parents[1] / "package-lock.json"


#: Set where the root workspace is installed on purpose (CI's `rails` job): there, an absent tree
#: is a failure, never a skip, because a skip reads like a pass.
_REQUIRE_TREE = "PERSONALCLAW_REQUIRE_NPM_TREE"
