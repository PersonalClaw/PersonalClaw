"""Async bundled-model downloads with live progress (the model-fetch job runner).

Bundled model providers (embedding / STT / TTS) fetch their weights from
HuggingFace, which can take minutes. A synchronous download route would hold the
request open for the whole fetch and give the UI no progress. This module turns
each download into a background **job** that streams progress over the SSE
substrate (``dashboard/sse.py``):

- :class:`ModelDownloadJob` — one in-flight or finished download (``kind``,
  ``model``, ``status``, ``phase``, bytes-on-disk vs expected size).
- :class:`ModelDownloadRegistry` — owns the jobs, dedupes by ``(kind, model)``,
  runs the blocking fetch off the event loop, and polls bytes-on-disk to publish
  ``progress`` frames on a per-job hub keyed ``download:<id>``.

Progress is **best-effort by on-disk growth**, not HuggingFace's internal tqdm:
a job snapshots the size of the kind's cache root at start and reports the delta
as it grows. No coupling to ``hf_hub`` internals; the trade-off is that two
concurrent downloads of the *same kind* would share a baseline (rare — jobs
dedupe per model, and the UI downloads one at a time). The expected total
(``size_mb`` from each provider's catalog) lets the client render a determinate
bar; without it the client falls back to indeterminate.

Cancellation detaches the job (stops the stream, drops it from the registry).
A HuggingFace fetch already in a worker thread cannot be interrupted cleanly, so
the underlying download may still finish in the background — the job just stops
being tracked.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from personalclaw.dashboard.sse import SseRegistry

logger = logging.getLogger(__name__)

# How often the byte-poller samples on-disk size and publishes a progress frame.
_POLL_SECS = 0.7


def registry_key(job_id: str) -> str:
    """The SSE hub key for a download job's progress stream."""
    return f"download:{job_id}"


@dataclass
class ModelDownloadJob:
    """One bundled-model download — its identity, lifecycle state, and progress.

    ``status`` is the coarse lifecycle (``running`` → ``done`` / ``error`` /
    ``cancelled``); ``phase`` is the human-facing step shown in the UI. ``bytes``
    is the best-effort on-disk delta since the job started; ``size_bytes`` is the
    expected total from the provider catalog (0 if unknown → indeterminate bar).
    """

    id: str
    provider: str
    model: str
    status: Literal["running", "done", "error", "cancelled"] = "running"
    phase: str = "queued"
    bytes: int = 0
    size_bytes: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "phase": self.phase,
            "bytes": self.bytes,
            "size_bytes": self.size_bytes,
            "error": self.error,
        }


@dataclass
class _Running:
    """The live bits backing a job that the wire shape (:class:`ModelDownloadJob`)
    doesn't carry: its baseline disk size and background tasks."""

    job: ModelDownloadJob
    baseline: int = 0
    tasks: set[asyncio.Task] = field(default_factory=set)  # type: ignore[type-arg]


def _dir_size(path: Path) -> int:
    """Total size in bytes of every file under ``path`` (0 if absent/unreadable)."""
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


# Model download management is fully provider-scoped: a download names its PROVIDER
# (the local-model registry key) + model. Core holds no per-backend knowledge — it
# resolves the provider from the one local-model registry and drives that provider's own
# catalog + download/delete. Any local downloadable provider (faster-whisper, piper,
# sentence-transformers, the diarization backends, ollama, …) works identically; a
# not-installed provider degrades to "unknown provider" rather than erroring elsewhere.

def _provider(name: str):
    """The registered local-model provider by name (or None)."""
    from personalclaw.local_models.registry import get_provider
    return get_provider(name)


def _list_models_for_provider(name: str) -> list:
    """The provider's catalog as uniform LocalModels (empty if unknown/failed).

    Sync helper called from BOTH sync (never, currently) and async (the download
    handler runs ``registry.start`` inside the gateway loop) contexts. A bare
    ``asyncio.run`` raises inside a running loop, so run the async catalog on a worker
    thread when a loop is already active (mirrors the embedding registry's dim lookup)."""
    from personalclaw.local_models.registry import catalog_for
    provider = _provider(name)
    if provider is None:
        return []
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(catalog_for(provider))
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, catalog_for(provider)).result(timeout=30)
    except Exception:
        logger.debug("catalog list failed for provider=%s", name, exc_info=True)
        return []


def _cache_root(name: str) -> Path:
    """The dir whose growth tracks a download for provider ``name`` (best-effort).

    The provider MAY expose its cache dir via ``cache_dir()``; otherwise fall back to
    the shared models root, so byte-progress degrades gracefully rather than coupling
    core to a backend's cache layout."""
    provider = _provider(name)
    getter = getattr(provider, "cache_dir", None)
    if callable(getter):
        try:
            got = getter()
            if got:
                return Path(got)
        except Exception:
            pass
    home = os.environ.get("PERSONALCLAW_HOME", str(Path.home() / ".personalclaw"))
    return Path(home) / "models"


def _expected_size_bytes(name: str, model: str) -> int:
    """Catalog size for ``model`` in bytes (0 if unknown), from the provider."""
    for m in _list_models_for_provider(name):
        if getattr(m, "name", None) == model:
            return int(getattr(m, "size_mb", 0) or 0) * 1024 * 1024
    return 0


def _is_downloaded(name: str, model: str) -> bool:
    """Whether ``model`` is already present locally (skip the fetch if so)."""
    for m in _list_models_for_provider(name):
        if getattr(m, "name", None) == model:
            return bool(getattr(m, "downloaded", False))
    return False


def _model_exists(name: str, model: str) -> bool:
    """Whether ``model`` is downloadable from the provider.

    For a FIXED-catalog provider (faster-whisper/piper/…), the model must be a known
    catalog entry. For a SEARCHABLE provider (ollama), the installable catalog is the
    remote library — any non-empty model id is valid to pull (validating against the
    full remote catalog would be a needless network round-trip), so we trust it."""
    provider = _provider(name)
    if provider is not None and getattr(provider, "searchable", False):
        return bool(model)
    return any(getattr(m, "name", None) == model for m in _list_models_for_provider(name))


async def _run_fetch(name: str, model: str) -> None:
    """Perform the actual (blocking) download for ``provider``/``model``. Resolves the
    named provider from the local-model registry and drives its ``download_model``.
    Raises on failure / no such provider installed."""
    provider = _provider(name)
    if provider is None:
        raise RuntimeError(f"No provider named {name!r} installed — install its app first")
    ok = await provider.download_model(model)
    if not ok:
        raise RuntimeError(f"Failed to download model '{model}' from {name}")


class ModelDownloadRegistry:
    """Owns local-model download jobs and their per-job SSE progress streams.

    Jobs dedupe by ``(provider, model)`` while running: a second request for an
    in-flight download returns the same job. Finished jobs are retained so a
    re-attaching client (page reload) sees the terminal state, and are dropped on
    explicit cancel.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ModelDownloadJob] = {}
        self._running: dict[str, _Running] = {}
        self._by_model: dict[tuple[str, str], str] = {}  # (provider, model) → job id
        self._sse = SseRegistry()
        self._counter = 0

    @property
    def sse(self) -> SseRegistry:
        """The per-job SSE registry (hubs keyed ``download:<id>``)."""
        return self._sse

    def _next_id(self) -> str:
        self._counter += 1
        return f"dl-{self._counter}"

    def get(self, job_id: str) -> ModelDownloadJob | None:
        return self._jobs.get(job_id)

    def list(self) -> list[ModelDownloadJob]:
        return list(self._jobs.values())

    def start(self, provider: str, model: str) -> tuple[ModelDownloadJob | None, str | None]:
        """Begin (or re-use) a download for ``provider``/``model``.

        Returns ``(job, None)`` on success, or ``(None, error)`` with a message
        for an unknown provider / unknown model. An already-running job for the same
        ``(provider, model)`` is returned as-is (dedupe); an already-downloaded model
        yields an immediately-``done`` job.
        """
        if not provider:
            return None, "Missing 'provider'"
        if not model:
            return None, "Missing 'model'"
        if _provider(provider) is None:
            return None, f"Unknown provider {provider!r}"
        if not _model_exists(provider, model):
            return None, f"Unknown model {model!r} for provider {provider!r}"

        existing_id = self._by_model.get((provider, model))
        if existing_id and (existing := self._jobs.get(existing_id)) and existing.status == "running":
            return existing, None

        job = ModelDownloadJob(
            id=self._next_id(),
            provider=provider,
            model=model,
            size_bytes=_expected_size_bytes(provider, model),
        )
        self._jobs[job.id] = job
        self._by_model[(provider, model)] = job.id

        if _is_downloaded(provider, model):
            job.status = "done"
            job.phase = "done"
            job.bytes = job.size_bytes
            return job, None

        run = _Running(job=job, baseline=_dir_size(_cache_root(provider)))
        self._running[job.id] = run
        run.tasks.add(asyncio.ensure_future(self._drive(run)))
        return job, None

    def cancel(self, job_id: str) -> bool:
        """Detach a job: stop its tasks, publish ``cancelled``, drop it.

        Returns False if the job is unknown. A worker-thread fetch already in
        flight may still complete in the background (HuggingFace downloads can't
        be interrupted cleanly) — the job simply stops being tracked.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return False
        run = self._running.pop(job_id, None)
        if run is not None:
            for t in run.tasks:
                t.cancel()
        if job.status == "running":
            job.status = "cancelled"
            job.phase = "cancelled"
            self._publish(job, "cancelled")
        self._jobs.pop(job_id, None)
        self._by_model.pop((job.provider, job.model), None)
        return True

    def _publish(self, job: ModelDownloadJob, event: str) -> None:
        """Fan a job frame out to its progress stream (no-op if no subscribers)."""
        self._sse.publish(registry_key(job.id), event, job.to_dict())

    async def _drive(self, run: _Running) -> None:
        """Run one download: poll on-disk progress while the fetch proceeds."""
        job = run.job
        job.phase = "downloading"
        self._publish(job, "progress")

        poller = asyncio.ensure_future(self._poll(run))
        run.tasks.add(poller)
        try:
            await _run_fetch(job.provider, job.model)
            job.status = "done"
            job.phase = "done"
            job.bytes = job.size_bytes or _measure(run)
            event = "done"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface any provider failure to the UI
            logger.warning("Model download failed (%s/%s): %s", job.provider, job.model, exc)
            job.status = "error"
            job.phase = "error"
            job.error = str(exc)
            event = "error"
        finally:
            poller.cancel()
            self._running.pop(job.id, None)

        self._publish(job, event)


    async def _poll(self, run: _Running) -> None:
        """Sample on-disk growth and publish a ``progress`` frame each tick."""
        job = run.job
        try:
            while True:
                await asyncio.sleep(_POLL_SECS)
                grew = _measure(run)
                if grew != job.bytes:
                    job.bytes = grew
                    self._publish(job, "progress")
        except asyncio.CancelledError:
            pass


def _measure(run: _Running) -> int:
    """Bytes written for this job: current cache-root size minus the baseline."""
    current = _dir_size(_cache_root(run.job.provider))
    return max(0, current - run.baseline)
