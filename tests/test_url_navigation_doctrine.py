"""Guardrail tests for the URL-navigation doctrine.

ONE canonical routing model: all navigation goes through the hash router
(`navigate`/`setQuery` from `useHashRoute.ts`). Pages must never write the URL
directly, and must take the full `RouteProps` contract (not `Partial`, which
silently degrades URL state to local state if the App shell contract ever drifts).

These pin the structural invariants so a future page can't silently reintroduce a
raw `location.hash`/`history.*` bypass or a `Partial<RouteProps>` guard — the
project's FE-source-guard idiom (see test_transport_doctrine.py), not an ESLint
dependency (web builds with tsc, no eslint toolchain).
"""

from __future__ import annotations

from pathlib import Path

_ROUTER = Path("web/src/app/useHashRoute.ts")


def test_router_still_owns_history_mechanics():
    """Sanity: the one allowed place (the router) still performs the hash/history
    writes — so the guard above is banning *bypasses*, not the mechanism itself."""
    src = _ROUTER.read_text(encoding="utf-8")
    assert (
        "location.hash" in src and "replaceState" in src
    ), "useHashRoute.ts should own the location.hash/replaceState mechanics"
