"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_triggers_web_poll.py``

Its checks:
``tests/rails/test_triggers_web_poll_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_knowledge(tmp_path_factory, monkeypatch):
    """Point the DEFAULT knowledge-digest routing at a per-test tmp DB.

    A fire now writes fresh items to the knowledge store, and a test that fires without an injected
    `knowledge_store` would otherwise reach `get_knowledge_store()` → the real `~/.personalclaw`
    knowledge.db (config_dir() resolves there when PERSONALCLAW_HOME is unset). Same real-home
    hazard and remedy as conftest's trigger-store fixture. Returned so a test can assert on it; a
    test that passes its own `knowledge_store` is unaffected."""
    from personalclaw.knowledge.store import KnowledgeStore

    kdb = tmp_path_factory.mktemp("pclaw-knowledge") / "k.db"
    kstore = KnowledgeStore(str(kdb))
    monkeypatch.setattr("personalclaw.knowledge.get_knowledge_store", lambda: kstore)
    return kstore
