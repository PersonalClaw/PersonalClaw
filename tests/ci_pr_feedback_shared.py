"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_ci_pr_feedback.py``

Its checks:
``tests/rails/test_ci_pr_feedback_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
