"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_ci_e2e_path_gate.py``

Its checks:
``tests/rails/test_ci_e2e_path_gate_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
