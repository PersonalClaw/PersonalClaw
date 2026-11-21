"""A memory lint only reads unless its caller asks for the purge.

``MemoryService.lint()`` purged by default: it deleted every memory superseded more than 90 days
ago, the sweep's one auto-fix. ``GET /api/memory/lint`` only reads now, and its POST waits for the
owner's word when someone else asked, but an app calling the SDK's ``lint()`` as a health check
deleted those memories with nothing said.

Now ``lint()`` only reads, and counts what the purge would remove under ``fixable``. A caller that
deletes says so with ``purge=True``: the route's POST, and the maintenance pass, the owner's
standing retention on its own cadence.
"""

from __future__ import annotations

import pytest

from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore

OLD = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def vs(tmp_path):
    store = VectorMemoryStore(db_path=tmp_path / "m.db")
    store.init()
    store.set_semantic("pref.gone", "old", 1.0, "user_explicit")
    store.set_semantic("pref.new", "new", 1.0, "user_explicit")
    store.supersede_semantic("pref.gone", "pref.new", "user_explicit")
    store.db.execute("UPDATE semantic_memory SET invalidated_at=? WHERE key='pref.gone'", (OLD,))
    store.db.commit()
    return store


def _kept(vs) -> int:
    return vs.db.execute("SELECT COUNT(*) FROM semantic_memory WHERE key='pref.gone'").fetchone()[0]


def test_a_lint_only_reads_and_counts_what_the_purge_would_remove(vs):
    """🔴 Red before: the lint deleted the long-superseded memory."""
    report = MemoryService.over_vector_store(vs).lint()
    assert _kept(vs) == 1
    assert report["fixable"] == {"superseded_purged": 1}


def test_a_lint_asked_to_purge_deletes_it(vs):
    report = MemoryService.over_vector_store(vs).lint(purge=True)
    assert _kept(vs) == 0
    assert report["auto_fixed"]["superseded_purged"] == 1


def test_the_maintenance_pass_asks_for_the_purge(monkeypatch):
    """The owner's standing retention still runs on its cadence: the pass says so."""
    from personalclaw import memory_service
    from personalclaw.knowledge import maintenance_passes

    asked: list[dict] = []

    class _Service:
        def lint(self, **kwargs):
            asked.append(kwargs)
            return {"flags": []}

    monkeypatch.setattr(memory_service, "service_for", lambda provider: _Service())
    maintenance_passes._memory_lint_pass()
    assert asked == [{"purge": True}]
