"""Hazard-site audit for the two spawns PLATFORM-HARDENING-FLOORS §1.1 named (SH1.5).

§1.1 named two spawn sites as candidates for the *pre-existing* form of the fork-wedge bug —
a spawn reached off a thread while the event loop / another thread holds locks, that could
wedge holding inherited fds:

  (a) ``apps/backend_runtime.py`` — the watchdog daemon thread respawns app backends
      (``BackendSupervisor.start`` is called from ``_check_and_revive`` on a 30s timer).
  (b) ``action_providers/bash_provider.py`` — a bash-action spawn on the event-loop thread.

The audit outcome (recorded here as executable assertions, per the done-when "a regression
test for a proven wedge, or a recorded finding that the sites are safe and why"):

**Both sites are SAFE — and PHF-1 makes them safer.** The wedge in §1.1 is specifically a
``preexec_fn`` hazard: only ``preexec_fn`` forces the ``fork()``-of-a-multithreaded-process
that can wedge before ``exec`` while holding inherited fds. Neither site ever passed
``preexec_fn`` (there are zero on the whole tree — see ``test_spawn_preexec_guard``), so
neither could exhibit the pre-exec wedge. PHF-1's ceiling delivery is post-``exec`` (the
shim), which is why it does NOT reintroduce the hazard at either site:

  * (a) backend respawn uses argv-prepend (``spawn_shim_argv``) + a plain ``subprocess.Popen``
    with no ``preexec_fn`` — the ceiling is applied by the shim in the exec'd child, so the
    watchdog thread's ``Popen`` cannot run agent bytecode pre-exec and cannot wedge on a
    lock. (A ``preexec_fn`` here would fork the whole gateway from the daemon thread — the
    exact hazard §1.1 warned about — which is why we route via argv, not preexec_fn.)
  * (b) the bash action spawn goes through ``create_subprocess_limited`` (async, no
    preexec_fn), so it stays on ``posix_spawn`` and the event loop is never blocked on a
    forked child's errpipe read.

These tests assert the safety-relevant invariants at each site so a future change that
reintroduces ``preexec_fn`` (or drops the shim routing) reds CI here as well as in the
preexec guard.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _src_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _func_node(rel: str, qualname_tail: str) -> ast.AST:
    """Return the AST node of the function whose (possibly dotted) name tail matches."""
    path = _src_root() / rel
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = qualname_tail.split(".")[-1]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
            return node
    raise AssertionError(f"function {qualname_tail} not found in {rel}")


def _func_calls_named(node: ast.AST) -> set[str]:
    """The set of called function names (attr or bare) inside *node*."""
    names: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                names.add(f.attr)
            elif isinstance(f, ast.Name):
                names.add(f.id)
    return names


def _func_passes_preexec_fn(node: ast.AST) -> bool:
    """True if any Call inside *node* passes a ``preexec_fn=`` keyword (AST, not substring —
    a comment mentioning preexec_fn must not trip this)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and any(kw.arg == "preexec_fn" for kw in n.keywords):
            return True
    return False


# ── Hazard (a): watchdog-thread backend respawn ──────────────────────────────


def test_backend_respawn_uses_argv_shim_not_preexec_fn():
    """SAFE-finding (a): the backend respawn Popen carries no preexec_fn and prepends the
    ceiling shim to argv, so the watchdog daemon thread never forks-then-runs-bytecode."""
    node = _func_node("apps/backend_runtime.py", "BackendSupervisor.start")
    calls = _func_calls_named(node)
    assert "spawn_shim_argv" in calls, "backend respawn must ceiling-wrap via spawn_shim_argv"
    assert not _func_passes_preexec_fn(node), (
        "backend respawn must NOT pass preexec_fn — that would fork the gateway from the "
        "watchdog daemon thread (the §1.1 wedge hazard)"
    )


def test_backend_respawn_is_reached_from_watchdog_thread():
    """Corroborates that this IS the watchdog-thread path §1.1 named (revive → start)."""
    src = (_src_root() / "apps" / "backend_runtime.py").read_text(encoding="utf-8")
    assert "_check_and_revive" in src and "start_backend_watchdog" in src


# ── Hazard (b): event-loop bash spawn ────────────────────────────────────────


def test_bash_action_spawn_is_async_and_has_no_preexec_fn():
    """SAFE-finding (b): the bash action provider spawns via create_subprocess_limited
    (async, no preexec_fn), so the event loop is never blocked on a forked child."""
    node = _func_node("action_providers/bash_provider.py", "execute")
    assert "create_subprocess_limited" in _func_calls_named(
        node
    ), "bash action must ceiling-wrap (async helper)"
    assert not _func_passes_preexec_fn(node), "bash action spawn must NOT pass preexec_fn"


def test_native_bash_spawn_has_no_preexec_fn():
    """The native bash tool (the other event-loop bash spawn) is equally clean."""
    node = _func_node("agents/native/builtin_tools.py", "_t_bash")
    assert "create_subprocess_limited" in _func_calls_named(node)
    assert not _func_passes_preexec_fn(node)
