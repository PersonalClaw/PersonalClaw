"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_header_controls_doctrine.py``

Its checks:
``tests/rails/test_header_controls_doctrine_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_WEB = Path("web/src")
