"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_guardrails_rung_routing.py``

Its checks:
``tests/rails/test_guardrails_rung_routing_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home for the rung store, the SEL and the inbox.

    ``PERSONALCLAW_HOME`` as well as a patched ``config_dir``: the SEL singleton resolves
    its directory from the environment at instantiation, so patching ``config_dir`` alone
    would leave audit rows landing in the real home.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    cfg = home / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    from personalclaw import sel as sel_mod

    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    yield home
    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False


@pytest.fixture(autouse=True)
def _clean_provider_registry():
    """Restore the action-provider registry — these tests install fakes into it."""
    from personalclaw.action_providers.registry import _providers

    before = dict(_providers)
    yield
    _providers.clear()
    _providers.update(before)
