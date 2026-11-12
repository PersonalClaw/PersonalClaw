"""The library tells the page when to read again: a ``knowledge`` hint is pushed when a knowledge
item is written, when an intent keeps a finding, and when a report's run starts and ends, and not
when nothing changed. The watch reads the knowledge store's revision counter, which every writer
moves, and the reports a run holds."""

from __future__ import annotations

import pytest
from library_shared import intent, isolate, knowledge_store, note, report


@pytest.fixture
def home(tmp_path, monkeypatch):
    return isolate(tmp_path, monkeypatch)


def test_a_write_a_finding_and_a_report_run_each_push_one_hint(home):
    from personalclaw.action_providers.knowledge_report_provider import (
        _hold_claim,
        _release_claim,
    )
    from personalclaw.dashboard.handlers.library_writes import LibraryWatch

    pushed: list[str] = []
    intent("homelab", "Anything that could improve my homelab")
    weekly = report("Homelab weekly")
    watch = LibraryWatch(knowledge_store, pushed.append)
    watch.check()
    assert pushed == [], "the first look is the baseline"

    note("Seed list")
    watch.check()
    knowledge_store().record_intent_outcome("homelab", item_id="x", takeaway="Use ZFS")
    watch.check()
    assert _hold_claim(weekly.id)
    watch.check()
    _release_claim(weekly.id)
    watch.check()
    watch.check()
    assert pushed == ["knowledge"] * 4
