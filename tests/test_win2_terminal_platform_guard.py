"""WIN-2 — the POSIX-only terminal modules are import-guarded so a native-Windows
process boots and the dashboard loads with the terminal feature ABSENT, not broken.

The bug (windows-native-audit ``WIN-2``): ``dashboard/handlers/terminal.py`` did a bare
``import fcntl`` / ``import pty`` / ``import termios`` at module load. Those modules are
POSIX-only, so on native Windows the module ``ImportError``ed at import — and because
``dashboard/handlers/__init__.py`` imports it eagerly, the whole dashboard (and the gateway)
died at boot, regardless of the ``dashboard.terminal.enabled`` flag.

The fix guards the three imports (mirroring the guarded ``import resource`` seam, WIN-1) and
exposes :func:`terminal_supported`, so the dashboard OMITS the terminal routes on a platform
without the PTY substrate instead of crashing. These tests prove the two done_when clauses by
OBSERVATION, each with a real vacuity floor:

* **Module still loads on non-POSIX** — exercised the real ``except ImportError`` way, by
  reimporting the module with ``fcntl``/``pty``/``termios`` blocked in ``sys.modules`` (the
  technique WIN-1's ``test_resource_limits`` uses). Without the guard this reimport would raise.
* **Router omits the terminal route while the rest mounts** — with ``sys.platform`` forced to
  ``win32``, ``_register_terminal_routes`` registers NO terminal route while a sibling registrar
  still mounts its routes; and on this real POSIX host the same registrar DOES register all five
  terminal routes (so the win32 assertion is the guard firing, not routes that were never there).
"""

from __future__ import annotations

import importlib
import sys

from aiohttp import web

import personalclaw.dashboard.server as server_mod
from personalclaw.dashboard import handlers

# The five routes the terminal feature owns (path, as aiohttp canonicalises it).
_TERMINAL_PATHS = {
    "/api/ws/terminal/{session_id}",
    "/api/terminal/sessions",
    "/api/terminal/sessions/{session_id}",
    "/api/sandbox/providers",
}


def _registered_paths(app: web.Application) -> set[str]:
    return {route.resource.canonical for route in app.router.routes() if route.resource}


# ── module still loads on a platform without fcntl/pty/termios (native Windows) ──


def test_terminal_module_imports_with_posix_modules_absent():
    """Exercise the real ``except ImportError`` branch (the Windows condition) by reimporting
    the module with the POSIX modules blocked in ``sys.modules``. The guard must swallow the
    ImportError so the module still LOADS, with ``_PTY_SUPPORTED``/``terminal_supported()`` False.
    Restores ``sys.modules`` to exactly its prior state so the canonical module object other
    test modules hold a binding to is unchanged."""
    mod_name = "personalclaw.dashboard.handlers.terminal"
    saved = {name: sys.modules.get(name) for name in ("fcntl", "pty", "termios", mod_name)}
    sys.modules.pop(mod_name, None)
    for name in ("fcntl", "pty", "termios"):
        sys.modules[name] = None  # type: ignore[assignment]  # makes `import <name>` raise
    try:
        reloaded = importlib.import_module(mod_name)
        # The guard swallowed the ImportError and the module still imported.
        assert reloaded._PTY_SUPPORTED is False
        assert reloaded.terminal_supported() is False
    finally:
        # Restore the original entries — including the real terminal module object other
        # test modules already hold a binding to (the reimport above made a throwaway copy).
        for name, obj in saved.items():
            if obj is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = obj


# ── the router registers WITHOUT the terminal route on Windows, WITH it on POSIX ──


def test_terminal_routes_present_on_this_posix_host():
    """Positive/anti-vacuity direction: this suite runs on macOS/Linux, so the PTY substrate
    is genuinely present and ALL five terminal routes register. If they were simply never wired
    the win32 assertion below would be vacuous — this proves they exist by default."""
    assert handlers.terminal_supported() is True
    app = web.Application()
    server_mod._register_terminal_routes(app)
    assert _TERMINAL_PATHS <= _registered_paths(app)


def test_terminal_routes_absent_when_platform_forced_windows(monkeypatch):
    """With the platform forced to native Windows, ``terminal_supported()`` is False and
    ``_register_terminal_routes`` registers NONE of the terminal routes — the feature is absent.
    A sibling registrar (uploads) still mounts on the same app, so 'the rest of the dashboard
    loads' is observed, not assumed."""
    monkeypatch.setattr(sys, "platform", "win32")
    assert handlers.terminal_supported() is False

    app = web.Application()
    server_mod._register_terminal_routes(app)
    paths = _registered_paths(app)
    assert not (_TERMINAL_PATHS & paths), f"terminal routes must be absent on Windows, saw {paths}"

    # The rest of the dashboard still registers under the same forced platform.
    server_mod._register_upload_routes(app)
    assert "/api/uploads/limits" in _registered_paths(app)
