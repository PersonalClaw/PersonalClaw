"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_gate_aggregate_runs_in_ci.py``

Its checks:
``tests/rails/test_gate_aggregate_runs_in_ci_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[1]
