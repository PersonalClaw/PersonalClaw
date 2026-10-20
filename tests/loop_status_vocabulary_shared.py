"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_loop_status_vocabulary.py``

Its checks:
``tests/rails/test_loop_status_vocabulary_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


_WEB = _REPO / "web" / "src"


_REGISTRY = _WEB / "lib" / "loopStatus.ts"
