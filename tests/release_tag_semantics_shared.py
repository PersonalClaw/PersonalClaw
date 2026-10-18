"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_release_tag_semantics.py``

Its checks:
``tests/rails/test_release_tag_semantics_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
