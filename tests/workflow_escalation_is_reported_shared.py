"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_workflow_escalation_is_reported.py``

Its checks:
``tests/rails/test_workflow_escalation_is_reported_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home
