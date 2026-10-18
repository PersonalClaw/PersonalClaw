"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_update_checks_follow_the_setting.py``

Its checks:
``tests/rails/test_update_checks_follow_the_setting_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.dashboard.handlers import updates


@pytest.fixture(autouse=True)
def _fresh_update_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A neutral git environment, no check run yet, and the cached answer put back afterwards."""
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
    monkeypatch.delenv("PERSONALCLAW_PROJECT_DIR", raising=False)
    monkeypatch.setattr(updates, "_last_update_check", 0.0)
    monkeypatch.setattr(updates, "_local_version", "0.2.0")
    saved = dict(updates._update_info)
    updates._update_info.update({"available": False, "changes": "", "checked": False})
    yield
    updates._update_info.clear()
    updates._update_info.update(saved)
