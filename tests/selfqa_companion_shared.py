"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_selfqa_companion.py``

Its checks:
``tests/rails/test_selfqa_companion_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.selfqa import watch as watch_mod


@pytest.fixture(autouse=True)
def watch_state(tmp_path, monkeypatch):
    """Isolate the watcher's state file per test.

    `watch.state_path()` derives from `config_dir()`, which conftest redirects once per
    SESSION — shared state across tests in this file would make "first sight" true exactly
    once and every later test's quiet/fire verdicts depend on ordering.
    """
    path = tmp_path / "selfqa-state" / "commit_watch.state.json"
    monkeypatch.setattr(watch_mod, "state_path", lambda: path)
    return path
