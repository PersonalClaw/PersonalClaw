"""A suggestions refresh no model answered is tried again only after a wait.

While the model that writes follow-up suggestions failed, a failed refresh kept the suggestions
there were but left their time as it was, so they still read as owed a refresh. The refresh pushed
the `suggestions` hint anyway, every open page that shows them re-read `/api/suggestions` on it,
and each read started another refresh against the same failing model: measured, about 89 requests
a second, each one a warning line, for as long as Home or a new chat was open.

A refresh that failed now waits before it is tried again, as an owed chore does
(`owed_chores.FIRST_WAIT_SECS`, doubling while it keeps failing, up to `LONGEST_WAIT_SECS`). A read
during the wait starts nothing. The widget's Refresh still asks at once, and a refresh that works
clears the wait.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw import owed_chores, suggestions


class _State:
    """The few parts of the dashboard state a refresh uses."""

    def __init__(self) -> None:
        self._background_tasks: set[Any] = set()
        self.hints: list[str] = []

    def push_refresh(self, topic: str) -> None:
        self.hints.append(topic)


class _Model:
    """The suggestions model: down until `up` is set, counting each time it is asked."""

    def __init__(self) -> None:
        self.asked = 0
        self.up = False

    async def __call__(self, _state: Any) -> list[str]:
        self.asked += 1
        if not self.up:
            raise httpx.ConnectError("couldn't connect to the model provider")
        return ["Plan the week"]


@pytest.fixture
def model(monkeypatch) -> _Model:
    fake = _Model()
    monkeypatch.setattr(suggestions, "generate_suggestions", fake)
    return fake


class _Clock:
    def __init__(self, monkeypatch) -> None:
        self.now = time.time()
        monkeypatch.setattr(suggestions.time, "time", lambda: self.now)


async def _refresh(
    state: _State, cache: suggestions.SuggestionsCache, *, force: bool = False
) -> bool:
    """`start_refresh`, and the refresh it started run to its end."""
    started = suggestions.start_refresh(state, cache, force=force)
    if started and cache._task is not None:
        await cache._task
    return started


def test_a_read_after_a_failed_refresh_starts_nothing(model):
    state, cache = _State(), suggestions.SuggestionsCache()

    async def scenario() -> None:
        assert await _refresh(state, cache) is True
        assert model.asked == 1
        assert await _refresh(state, cache) is False, "asked again at once"
        assert model.asked == 1

    asyncio.run(scenario())
    assert state.hints == ["suggestions"], "the page still hears the refresh ended"


def test_refresh_still_asks_at_once(model):
    state, cache = _State(), suggestions.SuggestionsCache()

    async def scenario() -> None:
        await _refresh(state, cache)
        assert await _refresh(state, cache, force=True) is True

    asyncio.run(scenario())
    assert model.asked == 2


def test_the_wait_starts_at_a_minute_and_doubles_while_it_keeps_failing(model, monkeypatch):
    clock = _Clock(monkeypatch)
    state, cache = _State(), suggestions.SuggestionsCache()
    first = owed_chores.FIRST_WAIT_SECS

    async def scenario() -> None:
        await _refresh(state, cache)
        clock.now += first - 1
        assert await _refresh(state, cache) is False, "inside the first wait"
        clock.now += 2
        assert await _refresh(state, cache) is True, "the first wait is over"
        assert model.asked == 2
        clock.now += first * 2 - 1
        assert await _refresh(state, cache) is False, "the second wait is twice the first"
        clock.now += 2
        assert await _refresh(state, cache) is True

    asyncio.run(scenario())
    assert model.asked == 3


def test_the_wait_stops_growing_at_its_longest(model, monkeypatch):
    clock = _Clock(monkeypatch)
    state, cache = _State(), suggestions.SuggestionsCache()

    async def scenario() -> None:
        for _ in range(12):
            await _refresh(state, cache, force=True)
        clock.now += owed_chores.LONGEST_WAIT_SECS - 1
        assert await _refresh(state, cache) is False
        clock.now += 2
        assert await _refresh(state, cache) is True

    asyncio.run(scenario())


def test_a_refresh_that_works_clears_the_wait(model, monkeypatch):
    clock = _Clock(monkeypatch)
    state, cache = _State(), suggestions.SuggestionsCache()

    async def scenario() -> None:
        await _refresh(state, cache)
        model.up = True
        await _refresh(state, cache, force=True)
        assert cache.suggestions == ["Plan the week"]
        # Due again only by its refresh interval, as before any failure.
        clock.now += suggestions._REFRESH_INTERVAL_SECS + 1
        model.up = False
        await _refresh(state, cache)
        clock.now += owed_chores.FIRST_WAIT_SECS + 1
        assert await _refresh(state, cache) is True, "the wait started over at its first"

    asyncio.run(scenario())


def test_two_page_reads_after_a_failed_refresh_ask_the_model_once(model, monkeypatch):
    from personalclaw import chores

    monkeypatch.setattr(chores, "model_chosen", lambda: True)
    state = _State()
    app = web.Application()
    app["state"] = state

    async def read() -> dict:
        req = make_mocked_request("GET", "/api/suggestions", app=app)
        resp = await suggestions.api_suggestions(req)
        cache = suggestions.get_suggestions_cache(state)
        if cache._task is not None:
            await cache._task
        import json

        return json.loads(resp.text)

    async def scenario() -> None:
        await read()
        second = await read()
        assert second["refreshing"] is False

    asyncio.run(scenario())
    assert model.asked == 1
