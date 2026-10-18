"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_channel_inbound_policy_gate.py``

Its checks:
``tests/rails/test_channel_inbound_policy_gate_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the trust store, SEL and the door's record of deliveries at ``tmp_path``; the
    real home is never touched."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield tmp_path
