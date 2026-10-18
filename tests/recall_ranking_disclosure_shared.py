"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_recall_ranking_disclosure.py``

Its checks:
``tests/rails/test_recall_ranking_disclosure_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"
