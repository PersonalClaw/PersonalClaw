"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_general_kind_as_run.py``

Its checks:
``tests/rails/test_general_kind_as_run_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every write under `tmp_path`. Nothing here may touch the real home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home
