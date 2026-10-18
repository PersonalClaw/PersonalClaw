"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_onboarding_state.py``

Its checks:
``tests/rails/test_onboarding_state_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    """Point the whole config home at a tmp dir so the real home is never touched.

    ``PERSONALCLAW_HOME`` rather than a ``config_dir`` patch: the store resolves its path
    through ``entity_routes._entity_settings_path`` -> ``config_dir()`` on every call, and
    the env var is the one lever every such caller honours.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path
