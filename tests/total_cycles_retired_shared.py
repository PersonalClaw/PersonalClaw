"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_total_cycles_retired.py``

Its checks:
``tests/rails/test_total_cycles_retired_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: The retired column/field name. One spelling, used by every rail below.
_RETIRED = "total_cycles"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rail here writes a loop row + a ledger. Keep both out of the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
