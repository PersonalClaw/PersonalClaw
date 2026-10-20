"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_bundled_model_gate.py``

Its checks:
``tests/rails/test_bundled_model_gate_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


_VERIFY_WHEEL = _REPO_ROOT / "scripts" / "verify_wheel.py"
