"""Unified LLM worker pool for Knowledge Library.

Bounded pool of long-lived ProviderWorker instances. Both entity extraction
and URL fetch acquire workers from this pool. Workers route each prompt through
``one_shot_completion``, which resolves the active model selection (Settings →
Models) via the use-case bridge — no per-worker provider state.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

try:
    from personalclaw.acp.client import AcpClient
except ImportError:
    AcpClient = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

DEFAULT_POOL_SIZE = 3
DEFAULT_TIMEOUT = 60.0
FETCH_TIMEOUT = 120.0


class WorkerError(RuntimeError):
    """A model or transport failure — as distinct from a model that answered nothing.

    🔴 The distinction this type exists to preserve was being destroyed, and three
    complete mechanisms were waiting on it (#759). ``ProviderWorker.send_message``
    caught both ``asyncio.TimeoutError`` and bare ``Exception`` and returned ``""``,
    so ``pool.send`` could not fail — and ``""`` is indistinguishable from a model
    that ran fine and found nothing. Everything downstream reads it as the latter:

    * ``match_intent`` parses ``""``, gets no dict, and returns ``None`` — "not
      relevant". So ``run_intent``'s ``errors`` tally could never leave 0, and the
      UI said "No matches in your existing items" for a cold or timing-out model.
      Its ``raise_on_error=True`` branch was unreachable.
    * ``InsightsExtractor.extract`` has the identical ``raise_on_error`` branch, also
      unreachable. So ``_run_insights`` never returned False, an item was left
      ``done`` rather than ``partial``, and the message the runner already writes —
      "insights: model unavailable (insights not refreshed — try regenerating)" —
      could not be reached.
    * Both call sites carry comments stating a model failure "must be counted as an
      error, not silently folded into 'not relevant'". The swallow defeated them.

    Raising is safe rather than sweeping, because every caller of ``pool.send``
    already handles an exception: ``EntityExtractor.extract`` returns
    ``_empty_result()``, ``LLMPool.send_batch`` catches per item and substitutes
    ``""``, skill synthesis catches and sets ``resp = ""``, and the two
    ``raise_on_error`` callers are the ones asking for it. So the pool stops deciding
    that a failure is an empty answer, and each caller keeps choosing for itself.

    It also makes the two workers agree: ``AcpWorker.send_message`` always propagated,
    so the contract depended on which worker you happened to get.
    """


class Worker(ABC):
    """Abstract base for a long-lived LLM worker."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize/spawn the worker process."""

    @abstractmethod
    async def send_message(self, prompt: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        """Send a prompt and return the text response.

        Raises :class:`WorkerError` on a model/transport failure. An empty return
        means the model answered with nothing, which is a different fact.
        """

    @abstractmethod
    async def shutdown(self) -> None:
        """Gracefully destroy this worker."""

    @abstractmethod
    def is_alive(self) -> bool:
        """True if this worker can still process messages."""


class ProviderWorker(Worker):
    """Stateless worker that routes each prompt through the system's configured LLM.

    Provider resolution lives entirely in :func:`one_shot_completion`, which reads
    the active model selection (Settings → Models / active_models.json) via the
    use-case bridge. The worker holds no provider state of its own.
    """

    async def start(self) -> None:
        logger.info("ProviderWorker: ready")

    async def send_message(self, prompt: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        from personalclaw.llm_helpers import one_shot_completion

        try:
            return await asyncio.wait_for(
                one_shot_completion(prompt, use_case="ingestion"),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            logger.warning("ProviderWorker: timeout after %.0fs", timeout)
            raise WorkerError(f"model timed out after {timeout:.0f}s") from exc
        except Exception as exc:
            logger.warning("ProviderWorker: request failed: %s", exc)
            raise WorkerError(f"model request failed: {exc}") from exc

    async def shutdown(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True


class AcpWorker(Worker):
    """Long-lived AcpClient session with personalclaw-knowledge agent."""

    def __init__(self) -> None:
        self._client: Optional[AcpClient] = None

    async def start(self) -> None:
        if AcpClient is None:
            raise RuntimeError("AcpClient not available (personalclaw.acp.client not installed)")
        raise RuntimeError(
            "AcpWorker requires an acp_agent ProviderEntry with options.command. "
            "Configure a knowledge agent via the provider registry."
        )

    async def send_message(self, prompt: str, timeout: float = DEFAULT_TIMEOUT) -> str:
        if self._client is None or not self._client.is_ready:
            await self.start()
        assert self._client is not None
        return await self._client.send_message(prompt, timeout=timeout)

    async def shutdown(self) -> None:
        if self._client is not None:
            try:
                await self._client.shutdown()
            except Exception:
                logger.debug("AcpWorker shutdown error", exc_info=True)
            self._client = None

    def is_alive(self) -> bool:
        return self._client is not None and self._client.is_process_alive()


class LLMPool:
    """Bounded pool of long-lived LLM workers.

    Both extraction and URL fetch acquire workers from this pool.
    If all workers are busy, callers wait on the semaphore.
    Dead workers are replaced transparently on acquire.
    """

    def __init__(self, pool_size: int = DEFAULT_POOL_SIZE):
        self._pool_size = pool_size
        self._semaphore = asyncio.Semaphore(pool_size)
        self._workers: list[Worker] = []
        self._available: asyncio.Queue[int] = asyncio.Queue()
        self._started = False
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        """Spawn all ProviderWorker instances."""
        async with self._start_lock:
            if self._started:
                return
            try:
                for i in range(self._pool_size):
                    worker = await self._create_worker()
                    self._workers.append(worker)
                    await self._available.put(i)
            except Exception:
                for w in self._workers:
                    try:
                        await w.shutdown()
                    except Exception:
                        pass
                self._workers.clear()
                self._available = asyncio.Queue()
                raise
            self._started = True
            logger.info("LLMPool started: %d workers", self._pool_size)

    async def _create_worker(self) -> Worker:
        worker: Worker = ProviderWorker()
        await worker.start()
        return worker

    async def acquire(self) -> tuple[int, Worker]:
        """Acquire a worker from the pool. Blocks if all busy.

        Returns (worker_index, worker). Caller must release after use.
        """
        if not self._started:
            await self.start()
        await self._semaphore.acquire()
        idx = await self._available.get()
        worker = self._workers[idx]

        if not worker.is_alive():
            logger.warning("LLMPool: worker %d dead, replacing", idx)
            try:
                await worker.shutdown()
            except Exception:
                pass
            try:
                worker = await self._create_worker()
                self._workers[idx] = worker
            except Exception:
                self._available.put_nowait(idx)
                self._semaphore.release()
                raise

        return idx, worker

    def release(self, idx: int) -> None:
        """Return a worker to the pool."""
        self._available.put_nowait(idx)
        self._semaphore.release()

    async def send(
        self,
        prompt: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> str:
        """Convenience: acquire a worker, send prompt, release, return response."""
        idx, worker = await self.acquire()
        try:
            return await worker.send_message(prompt, timeout=timeout)
        finally:
            self.release(idx)

    async def send_batch(self, prompts: list[str], timeout: float = DEFAULT_TIMEOUT) -> list[str]:
        """Send multiple prompts concurrently, bounded by pool size.

        Returns responses in same order as prompts.
        """
        if not prompts:
            return []

        results: list[str] = [""] * len(prompts)

        async def _do_one(idx: int, prompt: str) -> None:
            try:
                results[idx] = await self.send(prompt, timeout=timeout)
            except Exception as e:
                logger.warning("LLMPool: batch item %d failed: %s", idx, e)
                results[idx] = ""

        await asyncio.gather(*[_do_one(i, p) for i, p in enumerate(prompts)])
        return results

    async def shutdown(self) -> None:
        """Destroy all workers."""
        for worker in self._workers:
            try:
                await worker.shutdown()
            except Exception:
                logger.debug("Worker shutdown error", exc_info=True)
        self._workers.clear()
        self._started = False
        logger.info("LLMPool: all workers shut down")

    async def __aenter__(self) -> "LLMPool":
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.shutdown()
