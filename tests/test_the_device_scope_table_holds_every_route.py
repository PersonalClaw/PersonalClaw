"""The device-scope census: the one table a scoped session's gate reads holds every route a scope
touches, and nothing else (scoped-pairing design §3, ruling 2).

``device_scopes.DEVICE_SCOPE_ROUTES`` is keyed on canonical route templates. The gate refuses any
request whose route has no row, so a route the table forgot fails closed; what this census holds
is that nobody forgets one. It reads the real router (:func:`gateway_routes`, built as
``server.py`` builds it, as the stale-write census builds it), and requires:

* **families have rows** — every route under a family a scope touches has an explicit row, in a
  scope or ``OUTSIDE``, so a route added to ``/api/loops`` tomorrow is classified on purpose;
* **a new family fails** — a route whose path starts like a family's as a string (``/api/loops-
  archive``) is held to the same rule;
* **no ghosts** — no row names a route the router doesn't serve;
* **deny by default** — a session holding every scope is refused a route outside every family.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from personalclaw.dashboard import device_scopes


def gateway_routes() -> web.Application:
    """An application holding every route the gateway serves, registered as ``server.py`` does."""
    from personalclaw.dashboard import server
    from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes
    from personalclaw.dashboard.routes import register_dashboard_routes
    from personalclaw.providers.entity_routes import register_entity_routes
    from personalclaw.providers.instance_routes import register_instance_routes
    from personalclaw.providers.routes import register_routes as register_extension_routes

    app = web.Application()
    server._register_mcp_routes(app)
    register_dashboard_routes(app, pages=False)
    register_extension_routes(app)
    register_instance_routes(app)
    register_entity_routes(app)
    setup_knowledge_routes(app)
    return app


def _served(app: web.Application) -> set[tuple[str, str]]:
    return {
        (route.method, route.resource.canonical)
        for route in app.router.routes()
        if route.resource is not None and route.method not in ("HEAD", "*")
    }


@pytest.fixture(scope="module")
def served() -> set[tuple[str, str]]:
    return _served(gateway_routes())


def test_every_route_in_a_scoped_family_has_a_row(served) -> None:
    missing = sorted(device_scopes.unclassified(served))
    assert not missing, (
        "routes under a family a scope touches with no DEVICE_SCOPE_ROUTES row (give each a "
        f"scope, or OUTSIDE): {missing}"
    )


def test_no_row_names_a_route_the_router_does_not_serve(served) -> None:
    ghosts = sorted(set(device_scopes.DEVICE_SCOPE_ROUTES) - served)
    assert not ghosts, f"rows for routes nothing serves: {ghosts}"


def test_a_route_that_starts_like_a_family_is_held_to_it() -> None:
    app = web.Application()

    async def handler(_request: web.Request) -> web.Response:
        return web.Response()

    app.router.add_get("/api/loops-archive", handler)
    app.router.add_get("/api/approvalsx/{id}", handler)
    assert device_scopes.unclassified(_served(app)) == {
        ("GET", "/api/loops-archive"),
        ("GET", "/api/approvalsx/{id}"),
    }


def test_every_row_names_a_scope_of_the_vocabulary_or_is_outside() -> None:
    known = set(device_scopes.SCOPES) | {device_scopes.SHELL, device_scopes.PUBLIC}
    for key, row in device_scopes.DEVICE_SCOPE_ROUTES.items():
        assert row.scope in known or row is device_scopes.OUTSIDE, key


def test_a_session_holding_every_scope_is_refused_what_no_row_holds(served) -> None:
    every = device_scopes.SCOPES
    outside_every_family = [key for key in sorted(served) if not device_scopes.in_a_family(key[1])]
    assert outside_every_family, "the router serves routes outside every family"
    for method, route in outside_every_family[:50]:
        assert not device_scopes.route_admits(method, route, every), (method, route)


def test_head_is_get() -> None:
    assert device_scopes.route_scope("HEAD", "/api/loops/{id}") is device_scopes.route_scope(
        "GET", "/api/loops/{id}"
    )


def test_this_release_admits_no_steer_answer_or_approval(served) -> None:
    """PR 1's rule: every row a client could act through is OUTSIDE until its principal exists."""
    for (method, route), row in device_scopes.DEVICE_SCOPE_ROUTES.items():
        if row is device_scopes.OUTSIDE or row.scope in (device_scopes.SHELL, device_scopes.PUBLIC):
            continue
        assert row.scope in device_scopes.GRANTABLE, (method, route, row.scope)
        if row.scope == "loops":
            assert method == "GET", (method, route)
