"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_computer_use_dispatch.py``

Its checks:
``tests/rails/test_computer_use_dispatch_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.computer_use import enable_state, service


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Redirect the keystone to ``tmp_path`` and clear the snapshot store, every test.

    Autouse and unconditional: a dispatch test that resolved the real keystone would read the
    developer's own machine's arming state, so the suite would pass or fail differently on an
    armed laptop than in CI — and the refusal messages quote the resolved path.
    """
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    enable_state.reset_enable_state()
    service.reset_snapshots()
    yield
    enable_state.reset_enable_state()
    service.reset_snapshots()
