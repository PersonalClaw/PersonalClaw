"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_browse_vision_grounding.py``

Its checks:
``tests/rails/test_browse_vision_grounding_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
