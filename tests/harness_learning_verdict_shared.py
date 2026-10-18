"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_harness_learning_verdict.py``

Its checks:
``tests/rails/test_harness_learning_verdict_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

from harness import learning_verdict

MODULE = Path(learning_verdict.__file__)
