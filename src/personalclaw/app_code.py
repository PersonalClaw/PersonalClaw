"""Which code in this process is an app's — and how to take all of it back out.

An installed app's Python runs inside the gateway: the loader imports its modules, and that code
registers things into core's process-wide registries — a model type, a catalog, a callback, a
sidecar runner. Unloading an app has to take every one of those back. Otherwise the version it
replaced keeps answering from a registry long after its files are gone, and the next import of
the new version is refused as a duplicate (a model type's ``register_type`` is strict, and every
shipped model app swallows that refusal).

Three calls:

* :func:`claim` — the loader names the directory an app's code runs from, before running any of
  it.
* :func:`keep` — a process-wide registry records how to take an entry back at the moment it
  stores one. The entry is the app's when the app's code made the call: walking out from the
  registry, the app's code is reached before any module body that is not the app's. So a core
  module that registers its own provider type while it is being imported stays core's, even when
  it was the app's import that pulled it in.
* :func:`release` — every entry the app's code registered is taken back (newest first), every
  module loaded from its directory leaves ``sys.modules``, and what Python cannot take back
  in-process is returned as the reasons a restart is needed.

Deliberately standard-library only: the registries that call :func:`keep` sit below the app
platform, and must be able to import this without importing it.
"""

from __future__ import annotations

import gc
import importlib
import importlib.machinery
import logging
import os
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_lock = threading.RLock()
#: A directory an app's code is loaded from (with a trailing separator) → the app.
_roots: dict[str, str] = {}
#: The app → how to take back each registration its code made, oldest first.
_undo: dict[str, list[Callable[[], None]]] = {}

#: How many times, and how far apart, a thread must be seen running an app's code before it
#: counts as left running. A thread passing through the app's code for a moment (the event
#: loop finishing a receiver's stop) is not one the owner has to restart the gateway for.
_THREAD_SAMPLES = 3
_THREAD_SAMPLE_GAP_SECS = 0.05


def claim(app: str, root: Path) -> None:
    """The code under *root* is *app*'s. Called by the loader before it runs any of it."""
    with _lock:
        for path in {str(root), str(root.resolve())}:
            _roots[os.path.join(path, "")] = app


def owner() -> str | None:
    """The app whose code made the current call, or ``None`` when core made it.

    Walks out from the caller: the first frame running an app's code names the app; the first
    module body that is not an app's (a core module registering its own entries at import)
    means core. Core's own functions on the way are passed through.
    """
    with _lock:
        roots = tuple(_roots.items())
    if not roots:
        return None
    frame: types.FrameType | None = sys._getframe(1)
    while frame is not None:
        code = frame.f_code
        for prefix, app in roots:
            if code.co_filename.startswith(prefix):
                return app
        if code.co_name == "<module>":
            return None
        frame = frame.f_back
    return None


def keep(undo: Callable[[], None]) -> None:
    """Record how to take back the registration the caller is making — if app code made it.

    A no-op for core's own registrations. *undo* must be safe to run once the registry holds
    something newer under the same key (take back only what it recorded).
    """
    app = owner()
    if app is None:
        return
    with _lock:
        _undo.setdefault(app, []).append(undo)


@dataclass
class Released:
    """What :func:`release` took out of the process, and what it could not."""

    modules: list[str] = field(default_factory=list)
    #: Sentences, each one reason the app's previous code is still in the process.
    left_running: list[str] = field(default_factory=list)


def release(app: str) -> Released:
    """Take back everything *app*'s code registered and unload every module it loaded.

    What cannot be taken back is reported, not hidden: a compiled extension module (Python has
    no way to unload one), a thread still running the app's code, and a coroutine of its code
    that is still suspended (a task it started and never stopped).
    """
    with _lock:
        undo = _undo.pop(app, [])
        prefixes = tuple(p for p, a in _roots.items() if a == app)
    for take_back in reversed(undo):
        try:
            take_back()
        except Exception:  # noqa: BLE001 — one registry's failure must not strand the rest
            logger.warning(
                "app %s: taking back one of its registrations failed", app, exc_info=True
            )
    out = Released()
    if not prefixes:
        return out
    compiled: list[str] = []
    for name, module in list(sys.modules.items()):
        origin = _origin(module, prefixes)
        if origin is None:
            continue
        del sys.modules[name]
        out.modules.append(name)
        _drop_bytecode(module)
        if origin.endswith(tuple(importlib.machinery.EXTENSION_SUFFIXES)):
            compiled.append(os.path.basename(origin))
    for key in [k for k in sys.path_importer_cache if _under(os.path.join(k, ""), prefixes)]:
        del sys.path_importer_cache[key]
    importlib.invalidate_caches()
    if compiled:
        out.left_running.append(
            f"it loaded compiled Python code ({', '.join(sorted(compiled))}), which Python "
            "cannot unload"
        )
    threads = _threads_running(prefixes)
    if threads:
        out.left_running.append(
            "a thread its previous version started is still running "
            f"({', '.join(sorted(threads))})"
        )
    tasks = _coroutines_suspended_in(prefixes)
    if tasks:
        out.left_running.append(
            "a task its previous version started is still running " f"({', '.join(sorted(tasks))})"
        )
    return out


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return path.startswith(prefixes)


def _drop_bytecode(module: Any) -> None:
    """Delete the bytecode Python cached for *module*'s file.

    Python accepts cached bytecode when the source's size and mtime, to the second, match what
    the cache recorded. The next version's file sits at the same path, so one of the same size
    written within the same second would run the old bytecode — and with a bytecode prefix
    (``PYTHONPYCACHEPREFIX``, which the test suite sets) the cache lives outside the app's tree
    and outlasts the swap. Measured: v2's one-line ``version.py`` imported as v1's.
    """
    cached = getattr(module, "__cached__", None)
    if isinstance(cached, str) and cached:
        try:
            os.unlink(cached)
        except OSError:
            pass


def _origin(module: Any, prefixes: tuple[str, ...]) -> str | None:
    """The file *module* was loaded from if that is under *prefixes*, else ``None``.

    A namespace package has no file; it is the app's when one of its search locations is.
    """
    spec = getattr(module, "__spec__", None)
    candidates = [getattr(module, "__file__", None), getattr(spec, "origin", None)]
    for path in candidates:
        if isinstance(path, str) and _under(path, prefixes):
            return path
    for location in list(getattr(module, "__path__", None) or []):
        if isinstance(location, str) and _under(os.path.join(location, ""), prefixes):
            return location
    return None


def _threads_running(prefixes: tuple[str, ...]) -> set[str]:
    """Threads (by name) seen running code from under *prefixes* on every sample."""
    me = threading.get_ident()
    seen: set[int] | None = None
    for sample in range(_THREAD_SAMPLES):
        if sample:
            time.sleep(_THREAD_SAMPLE_GAP_SECS)
        now: set[int] = set()
        for ident, frame in sys._current_frames().items():
            if ident == me:
                continue
            walker: types.FrameType | None = frame
            while walker is not None:
                if _under(walker.f_code.co_filename, prefixes):
                    now.add(ident)
                    break
                walker = walker.f_back
        seen = now if seen is None else seen & now
        if not seen:
            return set()
    names = {t.ident: t.name for t in threading.enumerate()}
    return {names.get(ident, f"thread {ident}") for ident in seen or ()}


def _coroutines_suspended_in(prefixes: tuple[str, ...]) -> set[str]:
    """Coroutines of code from under *prefixes* that have started and not finished.

    A task an app started and never stopped holds one: it is the app's previous code, waiting
    to run again. The collection first, so a finished task nothing refers to is not counted.
    """
    gc.collect()
    found: set[str] = set()
    for obj in gc.get_objects():
        if not isinstance(obj, types.CoroutineType) or obj.cr_frame is None:
            continue
        if _under(obj.cr_code.co_filename, prefixes):
            found.add(obj.cr_code.co_qualname)
    return found
