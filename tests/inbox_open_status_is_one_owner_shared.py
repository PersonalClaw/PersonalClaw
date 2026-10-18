"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_inbox_open_status_is_one_owner.py``

Its checks:
``tests/rails/test_inbox_open_status_is_one_owner_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
