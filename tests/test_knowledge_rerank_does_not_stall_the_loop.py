"""#3097 — turning the knowledge reranker on must not stall the whole gateway event loop.

Three mechanisms, measured on `main` `96691faf8`, all fixed together because fixing any one
of them alone leaves the stall in place (or moves it).

1. **THE BUDGET BOUNDED NOTHING.** `knowledge/retrieval.py::_run_rerank_prompt` bridged its
   async model call as::

       with concurrent.futures.ThreadPoolExecutor() as pool:
           text = pool.submit(asyncio.run, _call()).result(timeout=90)

   `.result(timeout=90)` raised `TimeoutError` on schedule — and then the `with` block's
   `__exit__` called `shutdown(wait=True)`, which JOINS the still-running worker, so the
   caller came back only when the model did. Measured with the real function at a 1.0s
   budget against a 6.0s call: **wall = 6.00s**; the same call owning its pool and shutting
   it down `wait=False` returns in **1.05s**. The defect was therefore never the number
   `90`, and raising or lowering it could not have fixed anything: the effective bound was
   the model bridge's `_DEFAULT_TIMEOUT_SECS = 300.0`, which is deliberately generous and
   is not the thing to change.

2. **THE BRIDGE'S NO-LOOP BRANCH HAD NO BUDGET AT ALL.** The other branch was a bare
   `asyncio.run(_call())`, carrying no timeout whatsoever — and that is the branch every
   OFF-loop caller takes, so it was live rather than theoretical: both native agent
   knowledge tools already hop `search()` onto a worker thread
   (`agents/native/builtin_tools.py:1465`, `inbound/tools.py:165`). It also means fixing
   (1) alone and then fixing (3) would have MOVED the entire rerank path onto the branch
   with no bound. The budget is therefore enforced *inside* the coroutine with
   `asyncio.wait_for`, which is what makes it real on both branches.

3. **THE HANDLERS BLOCKED THE LOOP.** `HybridRetriever.search()` is sync, and both
   `async def list_items` and `async def search_for_context` called it inline with no
   executor hop — so any slow stage inside `search()` stalled every other request the
   gateway was serving, not just its own. Both now `await asyncio.to_thread(...)`, matching
   the two agent-tool sites above and the `run_in_executor` already in that module.

`knowledge.rerank_enabled` is in the `_EDITABLE_CONFIG` PATCH allowlist
(`dashboard/handlers/core.py:1176`) and off by default, so this was one runtime toggle away
with no restart — latent, not unreachable.

**Every measurement here carries a POSITIVE CONTROL** that reproduces the deleted shape
verbatim and asserts it FAILS the same property. Without them a rig like this can quietly
stop measuring anything and still be green.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import knowledge as H
from personalclaw.knowledge import retrieval as knowledge_retrieval
from personalclaw.knowledge.store import KnowledgeStore

# A budget small enough to measure against work far larger than it — the gap is what makes
# "did the budget actually bound the caller?" an answerable question. Same constants as
# `test_embedding_sync_bridge.py`, which measures the identical property one bridge over, so
# this file inherits that already-shipped test's flake profile rather than inventing a
# tighter one.
BUDGET = 0.25
FAR_LONGER = 3.0

# The loop-liveness probes below are cheaper than a full budget: they only need a sync call
# long enough for a 10ms ticker to visibly advance across it.
BLOCKING_WORK = 0.4
TICK = 0.01


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """No test here may read or write the real ``~/.personalclaw``."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))


@pytest.fixture()
def store(tmp_path):
    s = KnowledgeStore(str(tmp_path / "rerank-loop.db"))
    yield s
    s.close()


def _in_running_loop(fn):
    """Call a sync ``fn`` from inside a running event loop, as an async handler does."""

    async def _outer():
        asyncio.get_running_loop()  # assert the premise: we really are on a loop
        return fn()

    return asyncio.run(_outer())


def _slow_model(delay: float):
    """Patch the one-shot model seam with a coroutine that takes ``delay`` seconds."""

    async def _slow(*_a, **_k):
        await asyncio.sleep(delay)
        return '[{"id": "x", "relevance": 9}]'

    return patch("personalclaw.llm_helpers.one_shot_completion", new=_slow)


# ──────────────────────────────────────────────────────────────────────────────────────
# 1. The budget bounds the CALLER — on the running-loop branch (defect 1).
# ──────────────────────────────────────────────────────────────────────────────────────


def test_the_budget_bounds_the_caller_inside_a_running_loop(monkeypatch):
    """A `FAR_LONGER` model call under a `BUDGET` budget must return the caller's thread at
    `BUDGET`, not at `FAR_LONGER`. This is the assertion the `with`-block shape failed."""
    monkeypatch.setattr(knowledge_retrieval, "_RERANK_TIMEOUT_SECS", BUDGET)

    def _call():
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            knowledge_retrieval._run_rerank_prompt("rate these")
        return time.monotonic() - t0

    with _slow_model(FAR_LONGER):
        elapsed = _in_running_loop(_call)

    assert elapsed < BUDGET * 4, (
        f"the budget did not bound the caller: returned after {elapsed:.2f}s on a "
        f"{BUDGET}s budget for {FAR_LONGER}s of model work"
    )
    # …and it really was the budget that ended the wait, not the work finishing early.
    assert elapsed >= BUDGET * 0.8


def test_positive_control_the_deleted_with_block_shape_does_not_bound_the_caller():
    """The shape this fix removed, reproduced VERBATIM — it must still fail the property
    asserted above. If this ever starts passing quickly, the measurement is broken, not the
    defect fixed."""

    async def _slow():
        await asyncio.sleep(FAR_LONGER)
        return "done"

    def _old_shape():
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, _slow()).result(timeout=BUDGET)
        return time.monotonic() - t0

    elapsed = _in_running_loop(_old_shape)
    assert elapsed >= FAR_LONGER * 0.8, (
        "the control did not reproduce the defect: the `with` block returned in "
        f"{elapsed:.2f}s, so this file is no longer measuring the join it exists to catch"
    )


def _shutdown_spy(monkeypatch) -> list[bool]:
    """Record the ``wait`` every ``ThreadPoolExecutor.shutdown`` is called with."""
    seen: list[bool] = []
    real = concurrent.futures.ThreadPoolExecutor.shutdown

    def _spy(self, wait=True, *, cancel_futures=False):  # noqa: ANN001
        seen.append(bool(wait))
        return real(self, wait=wait, cancel_futures=cancel_futures)

    monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, "shutdown", _spy)
    return seen


def test_the_bridge_never_joins_its_worker_on_the_way_out(monkeypatch):
    """The deterministic form of the measurement above: no `shutdown(wait=True)` anywhere on
    the rerank bridge's path. A wall-clock margin can be widened until it stops failing; the
    join either happens or it does not."""
    monkeypatch.setattr(knowledge_retrieval, "_RERANK_TIMEOUT_SECS", BUDGET)
    seen = _shutdown_spy(monkeypatch)

    def _call():
        with pytest.raises(TimeoutError):
            knowledge_retrieval._run_rerank_prompt("rate these")

    with _slow_model(FAR_LONGER):
        _in_running_loop(_call)

    assert seen, "no executor was shut down at all — the spy never fired, so this is vacuous"
    assert True not in seen, f"the bridge joined its worker: shutdown(wait=…) calls were {seen}"


def test_positive_control_the_with_block_shape_trips_the_shutdown_spy(monkeypatch):
    """The same spy, pointed at the deleted shape: `__exit__` must show up as `wait=True`.
    This is what proves the assertion above can fail."""
    seen = _shutdown_spy(monkeypatch)

    with concurrent.futures.ThreadPoolExecutor() as pool:
        assert pool.submit(lambda: 1).result(timeout=5) == 1

    assert seen == [True], f"`with ThreadPoolExecutor()` did not join on exit: {seen}"


# ──────────────────────────────────────────────────────────────────────────────────────
# 2. The budget bounds the caller on the NO-LOOP branch too (defect 2) — the branch every
#    off-loop caller takes, and the one the handler hop in part 3 moves production onto.
# ──────────────────────────────────────────────────────────────────────────────────────


def test_the_budget_bounds_the_caller_with_no_running_loop(monkeypatch):
    """Off the loop there is no worker thread to abandon, so only a budget enforced INSIDE
    the coroutine can bound anything. The pre-fix branch was a bare `asyncio.run` with no
    timeout at all."""
    monkeypatch.setattr(knowledge_retrieval, "_RERANK_TIMEOUT_SECS", BUDGET)
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()  # premise: this thread really has no loop

    with _slow_model(FAR_LONGER):
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            knowledge_retrieval._run_rerank_prompt("rate these")
        elapsed = time.monotonic() - t0

    assert elapsed < BUDGET * 4, (
        f"the off-loop branch is unbounded: {elapsed:.2f}s on a {BUDGET}s budget for "
        f"{FAR_LONGER}s of model work"
    )
    assert elapsed >= BUDGET * 0.8


def test_positive_control_a_bare_asyncio_run_has_no_budget():
    """The deleted no-loop branch, verbatim: `asyncio.run(_call())` waits out the model."""

    async def _slow():
        await asyncio.sleep(FAR_LONGER)
        return "done"

    t0 = time.monotonic()
    assert asyncio.run(_slow()) == "done"
    elapsed = time.monotonic() - t0
    assert (
        elapsed >= FAR_LONGER * 0.8
    ), f"the control did not reproduce the unbounded branch ({elapsed:.2f}s)"


def test_the_budget_is_a_named_constant_not_an_inline_literal():
    """The budget has to be nameable for a caller (or a test) to reason about it. It was an
    inline `90` on a line whose `timeout=` argument bounded nothing."""
    assert isinstance(knowledge_retrieval._RERANK_TIMEOUT_SECS, float)
    assert 0 < knowledge_retrieval._RERANK_TIMEOUT_SECS < 300.0, (
        "an interactive read path must be bounded well inside the model bridge's generous "
        "300s default, which is the bound it used to inherit"
    )


# ──────────────────────────────────────────────────────────────────────────────────────
# 3. The async handlers do not run the sync search on the loop thread (defect 3).
# ──────────────────────────────────────────────────────────────────────────────────────


class _ThreadRecordingRetriever:
    """Stands in for `HybridRetriever`, recording which thread ran `search`."""

    threads: list[int] = []

    def __init__(self, *_a, **_k) -> None:
        pass

    def search(self, *_a, **_k):
        type(self).threads.append(threading.get_ident())
        return []


def _app(store) -> web.Application:
    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    return app


def _run_handler(handler, store, path):
    async def _outer():
        loop_thread = threading.get_ident()
        resp = await handler(make_mocked_request("GET", path, app=_app(store)))
        return loop_thread, json.loads(resp.body)

    return asyncio.run(_outer())


@pytest.mark.parametrize(
    ("handler_name", "path"),
    [
        ("list_items", "/api/knowledge/items?q=anything"),
        ("search_for_context", "/api/knowledge/search-for-context?q=anything"),
    ],
)
def test_the_async_handlers_run_search_off_the_loop_thread(store, monkeypatch, handler_name, path):
    """`search()` is synchronous and can spend a whole model round-trip inside. Running it
    on the loop thread stalls every OTHER request the gateway is serving — the defect is a
    whole-gateway stall, not one slow response."""
    monkeypatch.setattr(_ThreadRecordingRetriever, "threads", [])
    monkeypatch.setattr(H, "HybridRetriever", _ThreadRecordingRetriever)

    loop_thread, _body = _run_handler(getattr(H, handler_name), store, path)

    assert (
        _ThreadRecordingRetriever.threads
    ), f"{handler_name} never called search — the probe is vacuous"
    assert (
        loop_thread not in _ThreadRecordingRetriever.threads
    ), f"{handler_name} ran the synchronous search on the event-loop thread"


async def _ticks_across(awaitable) -> tuple[object, int]:
    """Await ``awaitable`` while a 10ms ticker runs, and report how far the ticker got.

    A ticker that cannot advance is the observable form of "the loop is stalled" — it is
    the other requests the gateway was supposed to be serving.
    """
    ticks = 0
    stop = False

    async def _ticker() -> None:
        nonlocal ticks
        while not stop:
            await asyncio.sleep(TICK)
            ticks += 1

    task = asyncio.create_task(_ticker())
    await asyncio.sleep(0)  # let the ticker reach its first await
    try:
        result = await awaitable
    finally:
        stop = True
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return result, ticks


class _BlockingRetriever(_ThreadRecordingRetriever):
    """A retriever whose `search` blocks the calling thread, as a model round-trip does."""

    def search(self, *_a, **_k):
        super().search()
        time.sleep(BLOCKING_WORK)
        return []


_MIN_TICKS = int(BLOCKING_WORK / TICK / 8)  # ~5 of a possible ~40; deliberately slack


def test_the_loop_keeps_serving_other_work_while_list_items_searches(store, monkeypatch):
    """The user-visible property: a slow knowledge search must not freeze the gateway."""
    monkeypatch.setattr(_BlockingRetriever, "threads", [])
    monkeypatch.setattr(H, "HybridRetriever", _BlockingRetriever)

    async def _outer():
        req = make_mocked_request("GET", "/api/knowledge/items?q=anything", app=_app(store))
        return await _ticks_across(H.list_items(req))

    _resp, ticks = asyncio.run(_outer())
    assert _BlockingRetriever.threads, "search was never called — the probe is vacuous"
    assert ticks >= _MIN_TICKS, (
        f"the loop was starved during the search: only {ticks} ticks advanced across "
        f"{BLOCKING_WORK}s of blocking work (expected >= {_MIN_TICKS})"
    )


def test_positive_control_an_inline_sync_search_freezes_the_loop():
    """The deleted call shape, verbatim: a sync `search()` awaited inline from an `async
    def`. The ticker must NOT advance — that is the stall, and it is what proves the
    assertion above can fail."""
    retriever = _BlockingRetriever()

    async def _outer():
        async def _pre_fix_handler():
            return retriever.search("anything", limit=3)  # exactly the deleted line

        return await _ticks_across(_pre_fix_handler())

    _results, ticks = asyncio.run(_outer())
    assert ticks == 0, (
        f"the control did not reproduce the stall: {ticks} ticks advanced across "
        f"{BLOCKING_WORK}s of on-loop blocking work"
    )
