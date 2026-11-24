"""The memory lint's purge changes your memory as any write does.

``POST /api/memory/lint`` purges the rows superseded more than 90 days ago, a permanent delete.
It had neither of the checks the other memory writes make: work that keeps nothing (an Incognito
or Temporary chat's) could purge, and so could a turn someone other than you asked for, with no
word of yours. Now the first is refused and the second is held for your Allow, as ``set_semantic``
is. Your own call, from a chat that keeps memory, purges at once.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.memory_lint import lint_memory
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def vs():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    store.set_semantic("pref.gone", "old", 1.0, "user_explicit")
    store.set_semantic("pref.new", "new", 1.0, "user_explicit")
    store.supersede_semantic("pref.gone", "pref.new", "user_explicit")
    store.db.execute(
        "UPDATE semantic_memory SET invalidated_at=? WHERE key='pref.gone'",
        ("2020-01-01T00:00:00+00:00",),
    )
    store.db.commit()
    return store


def _kept(vs) -> int:
    return vs.db.execute("SELECT COUNT(*) FROM semantic_memory WHERE key='pref.gone'").fetchone()[0]


class _Service:
    def __init__(self, vs) -> None:
        self._vs = vs

    def lint(self, *, purge: bool = True) -> dict:
        return lint_memory(self._vs, purge=purge).to_dict()


@pytest.fixture
def handlers(vs, monkeypatch):
    from personalclaw.dashboard.handlers import memory as memory_handlers

    monkeypatch.setattr(memory_handlers, "_get_service", lambda request: _Service(vs))
    return memory_handlers


def _post():
    app = web.Application()
    app["state"] = None
    return make_mocked_request("POST", "/api/memory/lint", app=app)


@pytest.mark.asyncio
async def test_work_that_keeps_nothing_is_refused_the_purge(vs, handlers, monkeypatch):
    """🔴 Red before: an Incognito or Temporary chat's work purged."""
    monkeypatch.setattr(handlers, "_is_restricted_session", lambda state, request: True)
    resp = await handlers.api_memory_lint(_post())
    assert resp.status == 403
    assert _kept(vs) == 1


@pytest.mark.asyncio
async def test_a_purge_someone_else_asked_for_waits_for_your_allow(vs, handlers, monkeypatch):
    """🔴 Red before: a turn someone else asked for purged with no word of yours."""
    from personalclaw import memory_writes
    from personalclaw.dashboard import memory_holds

    monkeypatch.setattr(handlers, "_is_restricted_session", lambda state, request: False)
    monkeypatch.setattr(memory_writes, "asker", lambda: {"source": "telegram", "name": "Sam"})
    held: list = []

    def _held(request, change, write):
        held.append((change, write))
        return web.json_response({"held": True}, status=202)

    monkeypatch.setattr(memory_holds, "held", _held)
    resp = await handlers.api_memory_lint(_post())
    assert resp.status == 202
    assert _kept(vs) == 1
    ((change, write),) = held
    from personalclaw.memory_lint import _SUPERSEDED_RETENTION_DAYS

    assert change.operation == "memory.lint_purge"
    assert f"more than {_SUPERSEDED_RETENTION_DAYS} days ago" in change.said
    await write()  # your Allow
    assert _kept(vs) == 0


@pytest.mark.asyncio
async def test_your_own_purge_runs_at_once(vs, handlers, monkeypatch):
    """The control: your own call, from a chat that keeps memory."""
    from personalclaw import memory_writes

    monkeypatch.setattr(handlers, "_is_restricted_session", lambda state, request: False)
    monkeypatch.setattr(memory_writes, "asker", lambda: {})
    resp = await handlers.api_memory_lint(_post())
    assert resp.status == 200
    assert _kept(vs) == 0


@pytest.mark.asyncio
async def test_a_get_neither_purges_nor_asks(vs, handlers, monkeypatch):
    """A GET only reads, whoever asked: it holds nothing and is refused nothing."""
    from personalclaw import memory_writes

    monkeypatch.setattr(handlers, "_is_restricted_session", lambda state, request: True)
    monkeypatch.setattr(memory_writes, "asker", lambda: {"source": "telegram"})
    app = web.Application()
    app["state"] = None
    resp = await handlers.api_memory_lint(make_mocked_request("GET", "/api/memory/lint", app=app))
    assert resp.status == 200
    assert _kept(vs) == 1
