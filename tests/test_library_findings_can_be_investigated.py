"""A findings log can be taken into a chat with Ask about this: the ``findings`` kind resolves an
intent's log to its goal and what it found, newest first, and an intent with no findings is no
log to ask about."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from library_shared import intent, isolate, knowledge_store


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_a_findings_log_resolves_to_its_goal_and_findings(home):
    from personalclaw import investigate

    intent("homelab", "Anything that could improve my homelab", "Use ZFS snapshots", "Add a UPS")
    intent("garden", "Anything about the garden")
    state = SimpleNamespace(knowledge_store=knowledge_store())
    found = await investigate.resolve("findings", "homelab", state)
    empty = await investigate.resolve("findings", "garden", state)
    unknown = await investigate.resolve("findings", "nope", state)
    assert found is not None
    assert found.kind == "findings" and found.id == "homelab"
    assert "Anything that could improve my homelab" in found.title
    assert found.snapshot.index("Add a UPS") < found.snapshot.index("Use ZFS snapshots")
    assert empty is None and unknown is None
