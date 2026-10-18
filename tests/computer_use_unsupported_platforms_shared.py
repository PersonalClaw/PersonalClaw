"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_computer_use_unsupported_platforms.py``

Its checks:
``tests/rails/test_computer_use_unsupported_platforms_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.computer_use import enable_state, linux_driver, service, windows_driver

#: ``(module, platform.system() value, the API its refusal must name)``.
PENDING = (
    (windows_driver, "Windows", "UI Automation"),
    (linux_driver, "Linux", "AT-SPI"),
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Redirect the keystone to ``tmp_path`` and clear the snapshot store, every test.

    Unconditional, like the dispatch suite's: a test that resolved the real keystone would read
    the developer's own arming state, and the refusal messages quote the resolved path.

    A home of its own too, named in the environment, because the dispatch runs the driver in a
    real child process: a PersonalClaw interpreter, which resolves its home from what it inherits.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    enable_state.reset_enable_state()
    service.reset_snapshots()
    yield
    enable_state.reset_enable_state()
    service.reset_snapshots()
