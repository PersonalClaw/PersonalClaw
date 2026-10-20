"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_verify_wheel_contract.py``

Its checks:
``tests/rails/test_verify_wheel_contract_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


_VERIFY_WHEEL = _REPO_ROOT / "scripts" / "verify_wheel.py"
