"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_connector_pack.py``

Its checks:
``tests/rails/test_connector_pack_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

# Reached through the SDK facade on purpose: this is the exact import a pack's own
# ``provider.py`` writes, so exercising it here means the published boundary is what the
# tests drive rather than the core module behind it.


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
