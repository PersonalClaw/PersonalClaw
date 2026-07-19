"""Config-tree FS watcher → live UI refresh (#44, filesystem-as-truth).

PClaw's editable state lives on disk (config.json, agents/, skills/, lessons). When a
file changes *out of band* (edited on disk, by another tool, by an agent), the UI
should refresh — the OpenForge filesystem-as-truth loop. This generalizes the
knowledge watcher (poll + content-hash; no `watchdog` dependency) over the editable
config trees and broadcasts a per-file ``changed`` event on a per-resource SSE feed
(transport doctrine), which the UI subscribes to.

Poll + mtime/size signature (cheap, dependency-free); debounced by the poll interval.
Best-effort — a scan error never stops the loop. SSE-only when something subscribes.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# SSE feed key for config-tree changes (per-resource; transport doctrine).
FS_WATCH_FEED = "fs:config"


def _signature(path: Path) -> tuple[float, int]:
    """A cheap change signature (mtime, size) — avoids hashing every poll."""
    try:
        st = path.stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return (0.0, -1)


class ConfigFsWatcher:
    """Polls editable config trees for changes → publishes ``changed`` per file.

    *roots* are directories/files to watch (relative to config_dir or absolute).
    *publish* is a ``(feed, event, data) -> None`` SSE emitter (the gateway's
    SseRegistry.publish). *suffixes* filters which files matter (config + skill/agent
    authoring formats)."""

    def __init__(self, roots, *, publish=None, interval: float = 3.0,
                 suffixes=(".json", ".md", ".yaml", ".yml")):
        self._roots = [Path(r) for r in roots]
        self._publish = publish
        self._interval = interval
        self._suffixes = tuple(suffixes)
        self._sigs: dict[str, tuple[float, int]] = {}
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._seeded = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())
            logger.info("Config FS watcher started (%d root(s))", len(self._roots))

    def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        from personalclaw import shutdown_event

        while not self._stop.is_set() and not shutdown_event.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.debug("fs watch scan failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except asyncio.TimeoutError:
                pass

    def _iter_files(self):
        for root in self._roots:
            if root.is_file():
                yield root
            elif root.is_dir():
                for p in root.rglob("*"):
                    if p.is_file() and p.suffix.lower() in self._suffixes:
                        yield p

    def scan_once(self) -> list[str]:
        """One poll pass. Returns the list of changed file paths (also published).

        The FIRST pass only seeds the signature baseline (no spurious "everything
        changed" storm on startup)."""
        changed: list[str] = []
        current: dict[str, tuple[float, int]] = {}
        for p in self._iter_files():
            key = str(p)
            sig = _signature(p)
            current[key] = sig
            if self._seeded and self._sigs.get(key) != sig:
                changed.append(key)
        # Deletions (seen before, gone now).
        if self._seeded:
            for key in self._sigs:
                if key not in current:
                    changed.append(key)
        self._sigs = current
        self._seeded = True
        for key in changed:
            self._emit(key)
        return changed

    def _emit(self, path: str) -> None:
        if self._publish is None:
            return
        try:
            self._publish(FS_WATCH_FEED, "changed", {"path": path})
        except Exception:
            logger.debug("fs watch publish failed", exc_info=True)


def default_config_roots():
    """The editable config trees: config.json + agents/ + skills/ + workflows/."""
    from personalclaw.config.loader import config_dir

    base = config_dir()
    return [base / "config.json", base / "agents", base / "skills", base / "workflows"]
