"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_apps_sdk_contract.py``

Its checks:
``tests/rails/test_apps_sdk_contract_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
