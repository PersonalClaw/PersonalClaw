"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_ci_audit.py``

Its checks:
``tests/rails/test_ci_audit_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_summary(monkeypatch, tmp_path: Path) -> Path:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return summary
