"""Every call PersonalClaw makes with its internal credential names an operation that takes it.

PersonalClaw's own processes call their gateway over loopback with its internal credential
(``X-Internal-Secret``, the ``.local_secret`` the gateway writes when it starts), and the credential
opens exactly the operations ``server.INTERNAL_ROUTES`` and ``server.MIXED_INTERNAL_ROUTES`` list.
The two halves were kept by hand in different files with nothing holding them together, and
eleven of the agent's own tool calls named an operation neither list had: ``memory_recall``,
``get_context``, the triage rules, ``prompt_render``, a batch of subagents, the self-nudge stop and
the artifact tools' project. On a gateway that asks for a sign-in, every call of theirs failed with
the browser's sign-in sentence. The development server skips the sign-in for loopback callers, and
every test that booted a gateway turned the sign-in off, so neither saw it.

So both directions are read off the source here:

* every call that carries the credential names an operation one of the lists opens, and
* every operation on the lists is a route the gateway registers and one a call names: an entry
  nothing calls holds the credential's door open for nobody.

The calls are found where the credential is put on a request (:data:`SENDERS`). A new place that
sends it fails :func:`test_the_census_knows_every_place_the_credential_is_sent` until the census
reads its calls too.
"""

from __future__ import annotations

from every_internal_call_names_an_operation_that_takes_it_shared import _routes

from personalclaw.dashboard import server
from personalclaw.manifest_meta import canonical_route
from personalclaw.manifest_reference import _routes_from_ast


def _registered() -> set[tuple[str, str]]:
    """Every ``(method, canonical path)`` the gateway registers, from the package's own census of
    its routes: the one the route reference and ``/api/manifest`` are built from, so however the
    routes are registered, this reads them the way those do. A ``*`` route serves every method."""
    return {(route["method"], route["path"]) for route in _routes_from_ast()}


def test_every_listed_operation_is_a_route_the_gateway_registers():
    registered = _registered()
    assert ("GET", "/api/memory/recall") in registered, "vacuity: no registration was read"
    stale = [
        entry
        for entry, route in _routes().items()
        if not {
            (route.method, canonical_route(route.template)),
            ("*", canonical_route(route.template)),
        }
        & registered
    ]
    assert stale == [], f"listed, and registered nowhere: {stale}"


def test_an_operation_is_on_one_list():
    assert server.INTERNAL_ROUTES.isdisjoint(server.MIXED_INTERNAL_ROUTES)
