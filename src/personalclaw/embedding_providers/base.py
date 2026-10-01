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
    so nothing is ever created that cannot be awaited. Every call runs on the shared
    bridge loop, from a thread with a loop running or without one.

    🔴 Without one it used to be a plain ``asyncio.run`` — a fresh event loop per call —
    and that is the path a worker thread takes: the embedding re-index, an import writing
    memories. A provider keeps its HTTP client for its life, and the client's pooled
    connection belongs to the loop it was opened on, so the next call's new loop found it
    stranded on a closed one ("Event loop is closed"). Measured with the Ollama provider:
    every other text failed to embed, and each re-index embedded half of what was left.
    One loop for every call keeps that state valid, and the deadline is honoured on both
    paths (``asyncio.run`` took no timeout at all).

    Raises ``TimeoutError`` when the deadline passes, having cancelled the task. The
    deadline is real: no per-call executor is joined on the way out.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

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

    async def unavailable_reason(self) -> str:
        """Why this provider cannot embed right now, as the sentence a surface shows: what is
        missing and what to do. ``""`` when it cannot say, and the surface uses its own words.

        Read when an embedding it was asked for did not come back — the re-index's readiness
        check — since :meth:`embed` answers a failure with ``None`` and nothing else. A remote
        provider whose credentials failed knows why, and "the model is not available" on its own
        sent the user to download or reconnect a model that was already set up.
        """
        return ""

    def untestable_reason(self) -> str:
        """Why Settings → Models offers no Test for this provider's models, or ``""`` when it does.

        A Test (``providers.model_test``) is one real :meth:`embed` of one word. A provider that
        cannot afford even that on a click says so here, and its rows show the sentence instead.
        """
        return ""

    @abstractmethod
    async def embed(self, text: str, model: str = "") -> list[float] | None:
        """Embed a single text. Returns vector or None on failure."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str], model: str = "") -> list[list[float] | None]:
        """Embed multiple texts: one entry per text, in order, and ``None`` for a text that was not
        embedded, as :meth:`embed` answers one.

        Never an empty vector for it: core's batch path (``knowledge/embed_batch.py``) stores
        what comes back as each text's vector, and keeps a text answered ``None`` without one,
        still keyword-searchable. A failure that is the whole batch's may raise instead; that path
        retries and splits a batch that raises.
        """
        ...

    def get_embed_fn(self, model: str = "") -> Callable[[str], list[float] | None]:
        """Return a sync embedding function for use with vector stores."""

        def _sync_embed(text: str) -> list[float] | None:
            return run_embed_sync(lambda: self.embed(text, model), timeout=30)

        return _sync_embed

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}
