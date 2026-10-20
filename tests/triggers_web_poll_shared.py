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
def _isolate_knowledge(tmp_path, monkeypatch):
    """Point the DEFAULT knowledge-digest routing at a knowledge store of this test's own.

    A fire now writes fresh items to the knowledge store, and a test that fires without an injected
    `knowledge_store` would otherwise reach `get_knowledge_store()`: the process-wide store, which
    another test may have left behind, or the real `~/.personalclaw` knowledge.db when nothing
    redirects the home. Same real-home hazard and remedy as conftest's trigger-store fixture. A test
    that passes its own `knowledge_store` is unaffected.

    The store is built the first time something asks for it, in this test's own folder, and handed
    out again after that. Building one costs about 20 ms, and most tests here never ask: only the
    few that fire without a store of their own do, and none of the tree checks."""
    built = []

    def _this_tests_store():
        if not built:
            from personalclaw.knowledge.store import KnowledgeStore

            folder = tmp_path / "pclaw-knowledge"
            folder.mkdir()
            built.append(KnowledgeStore(str(folder / "k.db")))
        return built[0]

    monkeypatch.setattr("personalclaw.knowledge.get_knowledge_store", _this_tests_store)
