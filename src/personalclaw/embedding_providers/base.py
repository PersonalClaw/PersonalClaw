"""Abstract base for embedding providers — the INFERENCE axis (``embed``).

Model MANAGEMENT (list/download/delete of local embedding models) is a SEPARATE axis:
a local backend (sentence-transformers) ALSO subclasses
:class:`~personalclaw.local_models.provider.LocalModelProvider`; a remote/hosted
embedder (OpenAI text-embedding-3) implements ONLY this inference axis. The two are
independent — a provider opts into management only if it owns local models.
"""

import asyncio
import threading
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")

# --- The sync bridge -------------------------------------------------------------------
#
# Embedding is resolved as a SYNC callable (vector stores take `embed_fn(text) -> vec`)
# but every provider's `embed` is a coroutine, so each call has to cross the boundary.
# Callers sit on BOTH sides: the CLI and the context builder are sync, the dashboard
# handlers are async. So the bridge must work with and without a running loop.
#
# It used to be done per call, inline, at four sites:
#
#     with concurrent.futures.ThreadPoolExecutor() as pool:
#         return pool.submit(asyncio.run, _embed(text)).result(timeout=30)
#
# which had two defects. (1) CHURN: one executor, one thread and one fresh event loop
# PER TEXT — a 2,100-chunk library built and tore down 2,100 of each. (2) The timeout did
# not bound the caller: `with ThreadPoolExecutor() as pool` calls `shutdown(wait=True)`
# on `__exit__`, which JOINS the still-running worker, so `.result(timeout=30)` raised on
# schedule and then the `with` block blocked until the work finished anyway. Measured on
# a 3.0s call with a 0.2s budget: 3.00s to return, a 15x overrun of the stated timeout.
#
# One shared daemon thread hosting one persistent event loop fixes both: the happy path
# creates nothing per call, and the timeout is honoured because the loop thread is never
# joined — a timed-out task is cancelled and abandoned, and the caller returns at its
# deadline. A persistent loop is also strictly kinder to providers than a fresh loop per
# call was: loop-bound provider state (an aiohttp session, say) now stays valid instead
# of being stranded on a closed loop.
_bridge_lock = threading.Lock()
_bridge_loop: asyncio.AbstractEventLoop | None = None
_bridge_thread: threading.Thread | None = None


def sync_bridge_loop() -> asyncio.AbstractEventLoop:
    """The process-wide bridge loop, started on first use.

    Exposed (rather than kept private) so tests can assert the identity is STABLE across
    calls — that is the observable form of "no per-call churn".
    """
    global _bridge_loop, _bridge_thread
    with _bridge_lock:
        loop, thread = _bridge_loop, _bridge_thread
        if loop is not None and not loop.is_closed() and thread is not None and thread.is_alive():
            return loop

        loop = asyncio.new_event_loop()

        def _serve(loop: asyncio.AbstractEventLoop = loop) -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        # Daemon: the loop is never joined (that is the whole point), so it must not be
        # able to hold interpreter shutdown — or a test suite — open.
        thread = threading.Thread(target=_serve, name="personalclaw-embed-bridge", daemon=True)
        thread.start()
        _bridge_loop, _bridge_thread = loop, thread
        return loop


def run_embed_sync(factory: Callable[[], Coroutine[Any, Any, _T]], timeout: float) -> _T:
    """Run an embedding coroutine from sync code, bounded by ``timeout``.

    ``factory`` is a zero-arg callable returning the coroutine (not the coroutine itself)
    so nothing is ever created that cannot be awaited. With no running loop this is a
    plain ``asyncio.run``; inside one, the coroutine is handed to the shared bridge loop.

    Raises ``TimeoutError`` when the deadline passes, having cancelled the task. The
    deadline is real: no per-call executor is joined on the way out.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        # No loop on this thread — the simple, cheap path, unchanged from before.
        return asyncio.run(factory())

    bridge = sync_bridge_loop()
    if running is bridge:
        # A provider coroutine reached back into the sync embed fn. Submitting to the
        # loop we are running ON would deadlock until the timeout, so say so instead.
        raise RuntimeError("run_embed_sync called from inside the embedding bridge loop")

    future = asyncio.run_coroutine_threadsafe(factory(), bridge)
    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        # Cancel and walk away. Awaiting the task here is exactly the bug being fixed.
        future.cancel()
        raise


@dataclass
class EmbeddingModel:
    """A local embedding model's catalog entry. Carries ``dimension`` (needed by the
    vector store to detect incompatible stored vectors) — richer than the management
    ``LocalModel`` shape, which the local-model registry adapts it down to."""

    name: str
    dimension: int
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    active: bool = False


class EmbeddingProvider(ABC):
    """Provider interface for text embedding backends — INFERENCE only (``embed``)."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    async def embed(self, text: str, model: str = "") -> list[float] | None:
        """Embed a single text. Returns vector or None on failure."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float]]:
        """Embed multiple texts."""
        ...

    def get_embed_fn(self, model: str = "") -> Callable[[str], list[float] | None]:
        """Return a sync embedding function for use with vector stores."""

        def _sync_embed(text: str) -> list[float] | None:
            return run_embed_sync(lambda: self.embed(text, model), timeout=30)

        return _sync_embed

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}
