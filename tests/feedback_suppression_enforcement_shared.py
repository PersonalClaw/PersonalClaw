"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_feedback_suppression_enforcement.py``

Its checks:
``tests/rails/test_feedback_suppression_enforcement_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib

from personalclaw import feedback as fb

SRC = pathlib.Path(fb.__file__).resolve().parent
