"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_task_collection_completeness.py``

Its checks:
``tests/rails/test_task_collection_completeness_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """Never the real home: `_ensure_native` would otherwise read the owner's tasks."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield
