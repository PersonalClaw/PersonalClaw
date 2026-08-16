"""Async bundled-model downloads with live progress (the model-fetch job runner).

Bundled model providers (embedding / STT / TTS) fetch their weights from
HuggingFace, which can take minutes. A synchronous download route would hold the
request open for the whole fetch and give the UI no progress. This module turns
each download into a background **job** that streams progress over the SSE
substrate (``dashboard/sse.py``):

- :class:`ModelDownloadJob` — one in-flight or finished download in the ONE
  canonical wire shape (LMMV §4.1): ``kind`` / ``state`` / ``progress`` /
  ``downloaded_bytes`` vs ``total_bytes`` / ``speed_bps`` / ``eta_s`` / ``reason``.
- :class:`ModelDownloadRegistry` — owns the jobs, dedupes by ``(kind, model)``,
  runs the blocking fetch off the event loop, and polls bytes-on-disk to publish
  ``progress`` frames on a per-job hub keyed ``download:<id>``.

Progress is **best-effort by on-disk growth**, not HuggingFace's internal tqdm:
a job snapshots the size of the kind's cache root at start and reports the delta
as it grows. No coupling to ``hf_hub`` internals; the trade-off is that two
concurrent downloads of the *same kind* would share a baseline (rare — jobs
dedupe per model, and the UI downloads one at a time). The expected total
(``size_mb`` from each provider's catalog) lets the client render a determinate
``progress`` bar; without it ``progress`` is ``0.0`` (indeterminate). ``speed_bps``
and ``eta_s`` are derived coarsely from the per-tick on-disk delta — honest zeros
when the delta or total is unknown, never fabricated.

Cancellation detaches the job (stops the stream, drops it from the registry).
A HuggingFace fetch already in a worker thread cannot be interrupted cleanly, so
the underlying download may still finish in the background — the job just stops
being tracked.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from personalclaw.dashboard.sse import SseRegistry

logger = logging.getLogger(__name__)

# How often the byte-poller samples on-disk size and publishes a progress frame.
_POLL_SECS = 0.7

# The ``model`` slot of a sidecar-install job. An install is per-PROVIDER — there is no
# model to name — so the dedupe key needs a stable non-empty placeholder that no real
# model id can collide with.
_INSTALL_MODEL = "__sidecar_install__"


def registry_key(job_id: str) -> str:
    """The SSE hub key for a download job's progress stream."""
    return f"download:{job_id}"


@dataclass
class ModelDownloadJob:
    """One bundled-model download — the ONE canonical wire shape (LMMV §4.1).

    ``kind`` names WHAT is being fetched (``weights`` for a model's weights,
    ``sidecar-install`` for a runtime/tooling install). ``state`` is the coarse
    lifecycle (``queued`` → ``running`` → ``done`` / ``error`` / ``cancelled``).
    ``downloaded_bytes`` is the best-effort on-disk delta since the job started;
    ``total_bytes`` is the expected total from the provider catalog (0 if unknown).
    ``progress`` is ``downloaded_bytes/total_bytes`` clamped to 0.0–1.0 when the
    total is known, else ``0.0`` (indeterminate). ``speed_bps`` / ``eta_s`` are
    coarse derivations from the on-disk poller (0 when not cheaply knowable — an
    honest indeterminate, never a fabricated number). ``reason`` carries a typed,
    machine-readable string on error/cancel (``"cancelled"``, ``"network"``,
    ``"disk_full"``, …), ``""`` when there is none.
    """

    id: str
    provider: str
    model: str
    kind: Literal["weights", "sidecar-install"] = "weights"
    state: Literal["queued", "running", "done", "error", "cancelled"] = "queued"
    progress: float = 0.0
    speed_bps: int = 0
    eta_s: int = 0
    total_bytes: int = 0
    downloaded_bytes: int = 0
    error: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "kind": self.kind,
            "state": self.state,
            "progress": self.progress,
            "speed_bps": self.speed_bps,
            "eta_s": self.eta_s,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "error": self.error,
            "reason": self.reason,
        }


@dataclass
class _Running:
    """The live bits backing a job that the wire shape (:class:`ModelDownloadJob`)
    doesn't carry: its baseline disk size, background tasks, and the last poll
    sample (bytes + monotonic timestamp) used to derive a coarse ``speed_bps``."""

    job: ModelDownloadJob
    baseline: int = 0
    tasks: set[asyncio.Task] = field(default_factory=set)  # type: ignore[type-arg]
    last_bytes: int = 0
    last_ts: float = 0.0


def _apply_progress(job: ModelDownloadJob) -> None:
    """Recompute ``progress`` from ``downloaded_bytes``/``total_bytes`` on *job*.

    ``progress`` is the fraction 0.0–1.0 when the total is known, else ``0.0``
    (indeterminate — the client renders an indeterminate bar). Kept in one place so
    every writer of ``downloaded_bytes`` yields the same derived fraction.
    """
    total = job.total_bytes
    job.progress = max(0.0, min(1.0, job.downloaded_bytes / total)) if total > 0 else 0.0


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
    """Whether ``model`` is already present locally (skip the fetch if so).

    Two sources, provider first: the provider's own ``downloaded`` flag is authoritative when
    it says yes, because only the provider knows layouts specific to its backend.

    When it says NO, the shared layout probe gets a second opinion
    (LOCAL-MODEL-MANAGER-V2 §4.4). That asymmetry is the point: a false NO makes the user
    re-download gigabytes they already have, and it is the common failure — a provider that
    checks its own `save()` layout misses a model the HF hub fetched into
    `models--{org}--{name}/`, where the model id never appears literally. A false YES would be
    worse (a load that fails with no explanation), so the probe only ever ADDS a yes, and only
    when it finds finished, non-empty bytes.
    """
    for m in _list_models_for_provider(name):
        if getattr(m, "name", None) == model:
            if bool(getattr(m, "downloaded", False)):
                return True
            break
    try:
        from personalclaw.local_models.layouts import is_downloaded as probe

        if probe(_cache_root(name), model):
            logger.info(
                "provider %s reports %r not downloaded, but it is present on disk", name, model
            )
            return True
    except Exception:
        logger.debug("layout probe failed for %s/%s", name, model, exc_info=True)
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
        self._installs: dict[str, Any] = {}  # provider → SidecarInstall (its step state)
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
        if (
            existing_id
            and (existing := self._jobs.get(existing_id))
            and existing.state in ("queued", "running")
        ):
            return existing, None

        job = ModelDownloadJob(
            id=self._next_id(),
            provider=provider,
            model=model,
            total_bytes=_expected_size_bytes(provider, model),
        )
        self._jobs[job.id] = job
        self._by_model[(provider, model)] = job.id

        if _is_downloaded(provider, model):
            job.state = "done"
            job.downloaded_bytes = job.total_bytes
            _apply_progress(job)
            return job, None

        run = _Running(job=job, baseline=_dir_size(_cache_root(provider)))
        self._running[job.id] = run
        run.tasks.add(asyncio.ensure_future(self._drive(run)))
        return job, None

    # ── sidecar installs (LMMV §3.2) ────────────────────────────────────────
    # A sidecar install (venv + pip + weights check) is a background job with the same
    # lifecycle as a weights fetch, so it rides THIS registry and the same SSE hub rather
    # than a second one. It differs only in ``kind`` and in carrying step detail, which
    # lives on the install object because the canonical job record (§4.1) is deliberately
    # one flat shape for every kind.

    def install(self, provider: str) -> Any:
        """The tracked :class:`~personalclaw.local_models.sidecar.SidecarInstall`.

        Created on first use and RETAINED, so a poll after the job finished still sees
        which steps ran and which were skipped. None when the app is not installed or
        declares ``execution: in-process`` (no sidecar to install).
        """
        existing = self._installs.get(provider)
        if existing is not None:
            return existing
        from personalclaw.local_models.sidecar import SidecarInstall

        created = SidecarInstall.for_app(provider)
        if created is not None:
            self._installs[provider] = created
        return created

    def install_job(self, provider: str) -> ModelDownloadJob | None:
        """The current/last install job for *provider* (None if never started)."""
        job_id = self._by_model.get((provider, _INSTALL_MODEL))
        return self._jobs.get(job_id) if job_id else None

    def start_install(self, provider: str) -> tuple[ModelDownloadJob | None, str | None]:
        """Begin (or re-use) the resumable sidecar install for *provider*.

        Idempotent twice over: an in-flight job is returned as-is, and re-running a
        finished install re-runs steps that existence-check themselves into ``skipped``.
        """
        if not provider:
            return None, "Missing 'provider'"
        install = self.install(provider)
        if install is None:
            return None, f"{provider!r} declares no sidecar provider to install"

        existing_id = self._by_model.get((provider, _INSTALL_MODEL))
        if (
            existing_id
            and (existing := self._jobs.get(existing_id))
            and existing.state in ("queued", "running")
        ):
            return existing, None

        job = ModelDownloadJob(
            id=self._next_id(), provider=provider, model=_INSTALL_MODEL, kind="sidecar-install"
        )
        self._jobs[job.id] = job
        self._by_model[(provider, _INSTALL_MODEL)] = job.id
        run = _Running(job=job)
        self._running[job.id] = run
        run.tasks.add(asyncio.ensure_future(self._drive_install(run, install)))
        return job, None

    async def _drive_install(self, run: _Running, install: Any) -> None:
        """Run the install step by step, publishing a frame after each one."""
        job = run.job
        job.state = "running"
        self._publish(job, "progress")
        total = max(1, len(install.steps))
        try:
            for index, step in enumerate(list(install.steps)):
                ok = await asyncio.to_thread(install.run_one, step.name)
                job.progress = round((index + 1) / total, 3)
                if not ok:
                    job.state = "error"
                    job.error = install.error
                    job.reason = install.reason
                    self._publish(job, "error")
                    return
                self._publish(job, "progress")
            job.state = "done"
            job.progress = 1.0
            event = "done"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface any install failure to the UI
            logger.warning("sidecar install failed (%s): %s", job.provider, exc)
            job.state = "error"
            job.error = str(exc)[:200]
            job.reason = "install_failed"
            event = "error"
        finally:
            self._running.pop(job.id, None)
        self._publish(job, event)

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
        if job.state in ("queued", "running"):
            job.state = "cancelled"
            job.reason = "cancelled"
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
        job.state = "running"
        run.last_ts = time.monotonic()
        self._publish(job, "progress")

        poller = asyncio.ensure_future(self._poll(run))
        run.tasks.add(poller)
        try:
            await _run_fetch(job.provider, job.model)
            job.state = "done"
            job.downloaded_bytes = job.total_bytes or _measure(run)
            job.speed_bps = 0
            job.eta_s = 0
            _apply_progress(job)
            event = "done"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface any provider failure to the UI
            logger.warning("Model download failed (%s/%s): %s", job.provider, job.model, exc)
            job.state = "error"
            job.error = str(exc)
            job.reason = _classify_error(exc)
            job.speed_bps = 0
            job.eta_s = 0
            event = "error"
        finally:
            poller.cancel()
            self._running.pop(job.id, None)

        self._publish(job, event)

    async def _poll(self, run: _Running) -> None:
        """Sample on-disk growth and publish a ``progress`` frame each tick.

        Each tick recomputes ``downloaded_bytes`` from cache-root growth, derives a
        coarse ``speed_bps`` from the delta since the previous sample, and — when the
        total is known — an ``eta_s`` from that speed. Both are honest zeros when the
        delta is non-positive or the total is unknown (indeterminate), never guessed.
        """
        job = run.job
        try:
            while True:
                await asyncio.sleep(_POLL_SECS)
                now = time.monotonic()
                grew = _measure(run)
                if grew != job.downloaded_bytes:
                    elapsed = now - run.last_ts
                    delta = grew - run.last_bytes
                    if elapsed > 0 and delta > 0:
                        job.speed_bps = int(delta / elapsed)
                        remaining = job.total_bytes - grew
                        job.eta_s = (
                            int(remaining / job.speed_bps)
                            if job.total_bytes > 0 and remaining > 0 and job.speed_bps > 0
                            else 0
                        )
                    else:
                        job.speed_bps = 0
                        job.eta_s = 0
                    run.last_bytes = grew
                    run.last_ts = now
                    job.downloaded_bytes = grew
                    _apply_progress(job)
                    self._publish(job, "progress")
        except asyncio.CancelledError:
            pass


def _measure(run: _Running) -> int:
    """Bytes written for this job: current cache-root size minus the baseline."""
    current = _dir_size(_cache_root(run.job.provider))
    return max(0, current - run.baseline)


def _classify_error(exc: Exception) -> str:
    """A typed, machine-readable ``reason`` for a failed fetch (``""`` if unknown).

    Coarse and honest: matches the exception's text against a few well-understood
    failure classes the FE can key on (``disk_full``, ``network``, ``gated``,
    ``not_found``). Anything unrecognized yields ``""`` — the ``error`` string still
    carries the human detail; ``reason`` only promises a machine label when sure.
    """
    text = str(exc).lower()
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:  # ENOSPC
        return "disk_full"
    if "no space" in text or "disk full" in text:
        return "disk_full"
    if any(
        w in text for w in ("connection", "timed out", "timeout", "network", "dns", "unreachable")
    ):
        return "network"
    if any(w in text for w in ("token", "gated", "401", "403", "unauthorized", "forbidden")):
        return "gated"
    if "not found" in text or "404" in text:
        return "not_found"
    return ""
