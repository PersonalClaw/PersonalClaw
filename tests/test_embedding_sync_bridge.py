"""The sync/async bridge every embedding call crosses (KL-15: "per-call thread-pool churn").

Embedding is resolved as a SYNC callable (vector stores take ``embed_fn(text) -> vec``)
while every provider's ``embed`` is a coroutine, and the callers sit on both sides — the
CLI and context builder are sync, the dashboard handlers are async. Four sites crossed
that boundary by hand, each with its own inline copy of:

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _embed(text)).result(timeout=30)

Two defects, both covered here.

1. CHURN — one executor, one thread and one fresh event loop PER TEXT. A 2,100-chunk
   library built and tore down 2,100 of each.
2. THE TIMEOUT DID NOT BOUND THE CALLER — ``with ThreadPoolExecutor() as pool`` calls
   ``shutdown(wait=True)`` on ``__exit__``, which JOINS the still-running worker. So
   ``.result(timeout=30)`` raised on schedule and then the ``with`` block blocked until
   the work finished anyway. Measured on a 3.0s call with a 0.2s budget: 3.00s to return.

Both now go through one shared ``run_embed_sync`` over one persistent bridge loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time

import pytest

from personalclaw.embedding_providers import base
from personalclaw.embedding_providers import registry as reg
from personalclaw.embedding_providers.base import EmbeddingProvider, run_embed_sync

# A budget small enough to measure and a workload far larger than it. The gap is what
# makes the "did the timeout actually bound the caller?" question answerable.
BUDGET = 0.25
FAR_LONGER = 3.0


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """No test here may read or write the real ``~/.personalclaw``."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))


class _FakeProvider(EmbeddingProvider):
    """Minimal concrete provider: a fixed answer, optionally after a delay."""

    def __init__(self, vec: list[float] | None = None, delay: float = 0.0) -> None:
        self.vec = vec
        self.delay = delay
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake-embedder"

    @property
    def display_name(self) -> str:
        return "Fake Embedder"

    async def is_available(self) -> bool:
        return True

    async def embed(self, text: str, model: str = "") -> list[float] | None:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.vec

    async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float]]:
        return [await self.embed(t, model) or [] for t in texts]


def _in_running_loop(fn):
    """Call a sync ``fn`` from inside a running event loop, as an async handler does."""

    async def _outer():
        asyncio.get_running_loop()  # assert the premise: we really are on a loop
        return fn()

    return asyncio.run(_outer())


# --------------------------------------------------------------------------------------
# 1. The timeout bounds the CALLER. This is the assertion the old shape failed.
# --------------------------------------------------------------------------------------


def test_timeout_bounds_the_caller_inside_a_running_loop():
    """A 3.0s embed under a 0.25s budget must give the caller back its thread at 0.25s.

    The pre-KL-15 shape raised TimeoutError on time and then blocked in ``__exit__``'s
    ``shutdown(wait=True)`` for the full 3.0s. Measured: 3.00s for a 0.2s budget.
    """

    async def _slow():
        await asyncio.sleep(FAR_LONGER)
        return [1.0, 2.0]

    def _call():
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            run_embed_sync(_slow, timeout=BUDGET)
        return time.monotonic() - t0

    elapsed = _in_running_loop(_call)
    assert elapsed < BUDGET * 4, (
        f"the timeout did not bound the caller: returned after {elapsed:.2f}s "
        f"on a {BUDGET}s budget for {FAR_LONGER}s of work"
    )
    # And it really was the budget that ended the wait, not the work finishing early.
    assert elapsed >= BUDGET * 0.8


def test_timeout_does_not_wedge_the_bridge_for_the_next_caller():
    """After a timeout the abandoned task is cancelled, and the next embed still works."""

    async def _slow():
        await asyncio.sleep(FAR_LONGER)
        return [9.0]

    provider = _FakeProvider(vec=[0.5, 0.6])

    def _call():
        with pytest.raises(TimeoutError):
            run_embed_sync(_slow, timeout=BUDGET)
        t0 = time.monotonic()
        vec = provider.get_embed_fn()("after the timeout")
        return vec, time.monotonic() - t0

    vec, elapsed = _in_running_loop(_call)
    assert vec == [0.5, 0.6]
    assert elapsed < 1.0, f"the bridge stayed wedged: next call took {elapsed:.2f}s"


def _spy_on_timeouts(monkeypatch) -> list[float]:
    """Record the timeout each site hands the bridge, and still run the coroutine."""
    seen: list[float] = []

    def _spy(factory, timeout):
        seen.append(timeout)
        return asyncio.run(factory())

    monkeypatch.setattr(base, "run_embed_sync", _spy)
    monkeypatch.setattr(reg, "run_embed_sync", _spy)
    return seen


# The four sites keep their ORIGINAL budgets — 30s in base, 60s remote, 60s direct, 30s
# for the native dimension lookup. Unifying them silently would be a behaviour change
# nobody asked for, so each is pinned. One test per site: a stub that fails to build must
# not take the other three legs down with it (and a skip would read as a pass).


def test_base_get_embed_fn_keeps_its_30s_timeout(monkeypatch):
    seen = _spy_on_timeouts(monkeypatch)
    assert _FakeProvider(vec=[1.0]).get_embed_fn()("t") == [1.0]
    assert seen == [30]


def test_llm_embed_fn_keeps_its_60s_timeout(monkeypatch):
    seen = _spy_on_timeouts(monkeypatch)

    class _LLMish:
        async def start(self):
            return None

        async def embed(self, texts):
            return [[2.0]]

    class _FakeLLMRegistry:
        def build(self, name, **kwargs):
            return _LLMish()

    from personalclaw.llm import registry as llm_reg

    monkeypatch.setattr(llm_reg, "get_default_registry", lambda: _FakeLLMRegistry())

    fn = reg._llm_embed_fn("some-remote", "m1")
    assert fn is not None, "the stub LLM provider should have built"
    assert fn("t") == [2.0]
    assert seen == [60]


def test_direct_embed_keeps_its_60s_timeout(monkeypatch):
    seen = _spy_on_timeouts(monkeypatch)
    monkeypatch.setattr(reg, "_active_embedding_spec", lambda: ("fake-embedder", "m1"))
    monkeypatch.setattr(reg, "_ensure_scanned", lambda: None)
    monkeypatch.setattr(reg, "_providers", {"fake-embedder": _FakeProvider(vec=[3.0])})
    fn = reg.get_active_embed_fn()
    assert fn is not None
    assert fn("t") == [3.0]
    assert seen == [60]


def test_native_dim_lookup_keeps_its_30s_timeout(monkeypatch):
    seen = _spy_on_timeouts(monkeypatch)

    class _Native(_FakeProvider):
        async def list_models(self):
            from personalclaw.embedding_providers.base import EmbeddingModel

            return [EmbeddingModel(name="m1", dimension=384)]

    monkeypatch.setattr(reg, "_active_embedding_spec", lambda: ("native", "m1"))
    monkeypatch.setattr(reg, "ensure_registered", lambda: None)
    monkeypatch.setattr(reg, "_providers", {"native": _Native(vec=[0.0])})
    assert reg.get_active_embedding_dim() == 384
    assert seen == [30], "the dim lookup must not fall through to the embed probe"


# --------------------------------------------------------------------------------------
# 2. No per-call churn.
#
# Asserted three ways, because one leg is not enough:
#   (a) ``ThreadPoolExecutor.__init__`` is called ZERO times over N embeds — stronger
#       than "not N", since the bridge creates no executor at all;
#   (b) ``sync_bridge_loop()`` returns the SAME loop object across all N calls (identity
#       of the shared resource);
#   (c) ``threading.active_count()`` grows by at most 1 over N embeds.
# --------------------------------------------------------------------------------------

N_CALLS = 25


def test_n_embeds_create_no_executors_and_one_bridge_loop(monkeypatch):
    provider = _FakeProvider(vec=[0.1, 0.2, 0.3])
    embed_fn = provider.get_embed_fn()

    base.sync_bridge_loop()  # start the bridge first so (c) measures only per-call growth

    made: list[int] = []
    real_init = concurrent.futures.ThreadPoolExecutor.__init__

    def _counting_init(self, *a, **k):
        made.append(1)
        return real_init(self, *a, **k)

    monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, "__init__", _counting_init)

    def _call():
        before = threading.active_count()
        loops = {id(base.sync_bridge_loop())}
        vecs = []
        for i in range(N_CALLS):
            vecs.append(embed_fn(f"chunk {i}"))
            loops.add(id(base.sync_bridge_loop()))
        return vecs, loops, threading.active_count() - before

    vecs, loops, thread_delta = _in_running_loop(_call)

    assert provider.calls == N_CALLS, "the embeds did not actually run"
    assert vecs == [[0.1, 0.2, 0.3]] * N_CALLS
    # (a) no executor at all — the old shape made one per text.
    assert made == [], f"{len(made)} ThreadPoolExecutor(s) created for {N_CALLS} embeds"
    # (b) one shared loop, not one per text.
    assert len(loops) == 1, f"{len(loops)} distinct bridge loops across {N_CALLS} embeds"
    # (c) no thread churn.
    assert thread_delta <= 1, f"thread count grew by {thread_delta} over {N_CALLS} embeds"


def test_the_bridge_thread_is_a_daemon_and_is_never_joined():
    """The bridge is a process global. It must not be able to hold the interpreter — or
    the test suite — open, because nothing ever joins it."""
    base.sync_bridge_loop()
    bridge = [t for t in threading.enumerate() if t.name == "personalclaw-embed-bridge"]
    assert len(bridge) == 1, f"expected exactly one bridge thread, found {len(bridge)}"
    assert bridge[0].daemon is True


# --------------------------------------------------------------------------------------
# 3. Both paths still work: with a running loop, and without one.
# --------------------------------------------------------------------------------------


def test_embed_returns_its_vector_with_no_running_loop():
    provider = _FakeProvider(vec=[1.0, 2.0, 3.0])
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()  # premise: no loop on this thread
    assert provider.get_embed_fn()("hello") == [1.0, 2.0, 3.0]
    assert provider.calls == 1


def test_embed_returns_its_vector_inside_a_running_loop():
    provider = _FakeProvider(vec=[1.0, 2.0, 3.0])
    assert _in_running_loop(lambda: provider.get_embed_fn()("hello")) == [1.0, 2.0, 3.0]
    assert provider.calls == 1


def test_direct_embed_site_works_on_both_paths(monkeypatch):
    """registry.get_active_embed_fn's directly-registered-provider branch."""
    provider = _FakeProvider(vec=[7.0, 8.0])
    monkeypatch.setattr(reg, "_active_embedding_spec", lambda: ("fake-embedder", "m1"))
    monkeypatch.setattr(reg, "_ensure_scanned", lambda: None)
    monkeypatch.setattr(reg, "_providers", {"fake-embedder": provider})

    fn = reg.get_active_embed_fn()
    assert fn is not None
    assert fn("sync path") == [7.0, 8.0]
    assert _in_running_loop(lambda: fn("async path")) == [7.0, 8.0]
    assert provider.calls == 2


# --------------------------------------------------------------------------------------
# 4. Vacuity: the bridge must not invent a vector.
# --------------------------------------------------------------------------------------


def test_a_provider_returning_none_still_yields_none():
    provider = _FakeProvider(vec=None)
    embed_fn = provider.get_embed_fn()
    assert embed_fn("no loop") is None
    assert _in_running_loop(lambda: embed_fn("running loop")) is None
    assert provider.calls == 2


def test_direct_embed_yields_none_for_a_none_provider(monkeypatch):
    provider = _FakeProvider(vec=None)
    monkeypatch.setattr(reg, "_active_embedding_spec", lambda: ("fake-embedder", "m1"))
    monkeypatch.setattr(reg, "_ensure_scanned", lambda: None)
    monkeypatch.setattr(reg, "_providers", {"fake-embedder": provider})
    fn = reg.get_active_embed_fn()
    assert fn is not None
    assert fn("sync") is None
    assert _in_running_loop(lambda: fn("async")) is None


# --------------------------------------------------------------------------------------
# 5. The re-entrancy guard: submitting to the loop you are running on would deadlock.
# --------------------------------------------------------------------------------------


def test_calling_the_bridge_from_inside_the_bridge_loop_is_refused():
    async def _reentrant():
        # Running ON the bridge loop, ask the bridge to run something else.
        return run_embed_sync(_noop, timeout=BUDGET)

    async def _noop():
        return [0.0]

    loop = base.sync_bridge_loop()
    fut = asyncio.run_coroutine_threadsafe(_reentrant(), loop)
    with pytest.raises(RuntimeError, match="inside the embedding bridge loop"):
        fut.result(timeout=5)
