"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_computer_use_live_view.py``

Its checks:
``tests/rails/test_computer_use_live_view_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pathlib

import pytest

from personalclaw.computer_use import enable_state, overlay, service

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "personalclaw"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Keystone → tmp, home → tmp, and every view/snapshot/trail store cleared, every test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    enable_state.reset_enable_state()
    service.reset_snapshots()
    overlay.reset_trail()
    yield
    enable_state.reset_enable_state()
    service.reset_snapshots()
    overlay.reset_trail()
