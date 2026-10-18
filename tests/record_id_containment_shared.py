"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_record_id_containment.py``

Its checks:
``tests/rails/test_record_id_containment_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "personalclaw"
