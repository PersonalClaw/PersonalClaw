"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_computer_use_approval_ladder.py``

Its checks:
``tests/rails/test_computer_use_approval_ladder_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.computer_use import enable_state, service


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A throwaway home for the keystone, the ladder store and the inbox store.

    All four redirects are load-bearing. The keystone env var keeps the grant off the real
    governance directory; ``config_dir``/``config_path``/``PERSONALCLAW_HOME`` are what the rung
    store, the governance ceiling and the inbox store resolve through, and several of them bind
    at import — so patching one and not the others is how a suite writes into the developer's
    own ``~/.personalclaw``.
    """
    home = tmp_path / "home"
    (home / "governance").mkdir(parents=True)
    cfg = home / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(home / "governance" / "enable.json"))
    enable_state.reset_enable_state()
    service.reset_snapshots()
    yield home
    enable_state.reset_enable_state()
    service.reset_snapshots()
