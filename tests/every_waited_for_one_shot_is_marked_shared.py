"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_every_waited_for_one_shot_is_marked.py``

Its checks:
``tests/rails/test_every_waited_for_one_shot_is_marked_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
