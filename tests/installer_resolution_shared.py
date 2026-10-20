"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_installer_resolution.py``

Its checks:
``tests/rails/test_installer_resolution_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw import _installer


@pytest.fixture
def env(monkeypatch, environment_made_by):
    """``env(uv=..., pip=..., made_by="uv")``: which tools exist, and which one made the
    environment."""

    def _set(*, uv: bool, pip: bool, made_by: str = "uv"):
        environment_made_by(made_by)
        monkeypatch.setattr(_installer, "_have_uv", lambda: uv)
        monkeypatch.setattr(_installer, "_have_pip", lambda: pip)

    return _set
