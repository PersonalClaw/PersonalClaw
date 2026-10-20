"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_triggers_status_vocabulary.py``

Its checks:
``tests/rails/test_triggers_status_vocabulary_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"
