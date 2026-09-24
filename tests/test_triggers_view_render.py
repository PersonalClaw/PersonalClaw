"""`POST /api/triggers/view/render` — the `view` kind's production render caller (WF2AUT-6).

🔴 THE GAP THIS CLOSES. `triggers/pull_on_view.py` shipped a complete `view`-kind runtime — TTL
decide, freshness sidecar, `renders()` fan-out — whose ONLY caller was its own module and tests. So
`surface_binding` was set by authors and read by nothing: a `view` trigger could never fire. This
endpoint is the production render caller. A real render surface (an artifact opening) POSTs
`{surface}` here; every bound `view` trigger past its TTL refreshes fire-and-forget, the rest serve
cache.

Deliberately NOT a poll (R10): the runtime is a function a render calls, so a `view` trigger costs
nothing when nobody looks. The gateway module must never import `pull_on_view` as a loop — the
`test_triggers_chain` runtime map and `tests/test_triggers_pull_on_view.py`'s
`test_NO_background_loop_polls_this_kind` guard both pin that, and this endpoint imports the runtime
at request time from the handler, never at gateway module top level.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import triggers as T
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

NOW = 1_800_000_000.0


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp home the handler reads for its trigger store AND the runtime's freshness sidecar.

    The handler resolves its store through this module's `config_dir`, and `pull_on_view` writes its
    freshness sidecar under the store's `base_dir` — both must land in `tmp_path`, never the real
    home.
    """
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "_trigger_store", lambda: TriggerStore(base_dir=tmp_path))
    return tmp_path


@pytest.fixture
def state():
    """A minimal state carrying the fire-and-forget task set the handler tracks tasks on."""

    class _State:
        def __init__(self) -> None:
            self._background_tasks: set[asyncio.Task] = set()

    return _State()


def _view(home, *, tid="view:tile", surface="artifact.notes", **spec):
    store = TriggerStore(base_dir=home)
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="view",
            enabled=True,
            spec={"surface_binding": surface, **spec},
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    return store.get(tid).trigger


def _req(state, *, body):
    app = web.Application()
    app["state"] = state
    req = make_mocked_request("POST", "/api/triggers/view/render", app=app)
    req["user"] = "tester"

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    return req


def _body(resp):
    return json.loads(resp.body.decode())


def _run(coro):
    return asyncio.run(coro)


# ── a bound trigger past TTL refreshes AND schedules a dispatch ──


def test_a_bound_view_trigger_past_TTL_is_REFRESHED_and_dispatched(home, state, monkeypatch):
    """🔴 The wiring. A bound `view` trigger's first render refreshes it — the endpoint returns it in
    `refreshed` AND schedules a dispatch through the shared store-action path."""
    _view(home, ttl_secs=300)

    seen: list[tuple[str, str]] = []

    async def _spy(trigger, payload, *, event="manual.run"):
        seen.append((trigger.id, event))
        return True, "ran"

    monkeypatch.setattr(T, "_dispatch_store_action", _spy)

    resp = _run(T.api_trigger_view_render(_req(state, body={"surface": "artifact.notes"})))
    data = _body(resp)
    assert data["refreshed"] == ["view:tile"]
    assert data["served_cache"] == []

    # The dispatch was SCHEDULED as a background task, not awaited inline — drain the loop's pending
    # tasks so the spy records the fire, proving it fires via the shared path with the view label.
    _run(_drain(state))
    assert seen == [("view:tile", "view.rendered")]


async def _drain(state) -> None:
    """Let every scheduled fire-and-forget task run to completion."""
    pending = [t for t in state._background_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ── two renders inside the TTL: the second serves cache, no second dispatch ──


def test_two_renders_inside_the_TTL_serve_CACHE_with_no_second_dispatch(home, state, monkeypatch):
    """🔴 The point of the kind: two renders inside the window cost nothing. The first refreshes and
    dispatches; the second serves cache and schedules NO dispatch."""
    _view(home, ttl_secs=300)

    calls = {"n": 0}

    async def _spy(trigger, payload, *, event="manual.run"):
        calls["n"] += 1
        return True, "ran"

    monkeypatch.setattr(T, "_dispatch_store_action", _spy)

    first = _body(_run(T.api_trigger_view_render(_req(state, body={"surface": "artifact.notes"}))))
    assert first["refreshed"] == ["view:tile"]

    second = _body(_run(T.api_trigger_view_render(_req(state, body={"surface": "artifact.notes"}))))
    assert second["refreshed"] == []
    assert len(second["served_cache"]) == 1
    assert second["served_cache"][0]["trigger_id"] == "view:tile"
    assert "served cache" in second["served_cache"][0]["reason"]

    # Only the first render dispatched; the cache hit scheduled nothing.
    _run(_drain(state))
    assert calls["n"] == 1


# ── a surface with no bound triggers is a 200 with empty lists ──


def test_an_UNBOUND_surface_is_200_with_empty_lists(home, state):
    """Most renders bind no `view` trigger at all, so an unbound surface is not an error — a 4xx
    here would make every artifact-open log a failure."""
    _view(home, surface="artifact.other")  # bound to a DIFFERENT surface
    resp = _run(T.api_trigger_view_render(_req(state, body={"surface": "artifact.notes"})))
    assert resp.status == 200
    data = _body(resp)
    assert data == {"refreshed": [], "served_cache": []}


def test_a_MISSING_surface_is_200_with_empty_lists(home, state):
    """A render that pings with no surface (or an empty one) matches nothing rather than 400: a
    blank binding must never fan out across the product."""
    _view(home)
    resp = _run(T.api_trigger_view_render(_req(state, body={})))
    assert resp.status == 200
    assert _body(resp) == {"refreshed": [], "served_cache": []}


# ── fire-and-forget: the response returns before the LLM turn completes ──


def test_the_render_returns_WITHOUT_awaiting_the_dispatch(home, state, monkeypatch):
    """🔴 A synchronous render must never block on an LLM turn. The dispatch is scheduled and the
    decision returns immediately; if the endpoint awaited the action, this never-completing spy
    would hang the request instead of answering `refreshed`."""
    _view(home, ttl_secs=300)

    started = asyncio.Event()

    async def _never_finishes(trigger, payload, *, event="manual.run"):
        started.set()
        await asyncio.Event().wait()  # blocks forever — an LLM turn the request must not await
        return True, "ran"

    monkeypatch.setattr(T, "_dispatch_store_action", _never_finishes)

    async def _drive():
        # The endpoint returns even though the dispatch never completes.
        resp = await T.api_trigger_view_render(_req(state, body={"surface": "artifact.notes"}))
        data = _body(resp)
        assert data["refreshed"] == ["view:tile"]
        # The task WAS scheduled (it started running), it is simply not awaited.
        task = next(iter(state._background_tasks))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert not task.done()
        task.cancel()

    _run(_drive())
