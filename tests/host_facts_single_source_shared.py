"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_host_facts_single_source.py``

Its checks:
``tests/rails/test_host_facts_single_source_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
