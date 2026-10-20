"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_api_version_one_origin.py``

Its checks:
``tests/rails/test_api_version_one_origin_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


SRC = REPO / "src" / "personalclaw"


WEB = REPO / "web" / "src"


ORIGIN_TS = WEB / "lib" / "apiVersion.ts"
