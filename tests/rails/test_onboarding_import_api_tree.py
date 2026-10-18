"""The source-tree checks split from
``tests/test_onboarding_import_api.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _registrars_called_by_the_gateway() -> set[str]:
    """Every ``register_*(app)`` call the gateway's app builder makes, from the AST.

    Static, because booting ``start_dashboard`` has heavy security-critical startup
    side effects (extension load, binding migration) — the same reason
    ``test_api_manifest_drift`` audits routes from the AST rather than a live table.
    A registrar that is defined but never CALLED is a route that exists in a module and
    on no running server, which is the failure this guard exists for.
    """
    import personalclaw.dashboard.routes as routes_mod
    import personalclaw.dashboard.server as server_mod

    # The builder is `start_dashboard` and the route table it runs (`dashboard/routes.py`).
    trees = [
        ast.parse(Path(m.__file__).read_text(encoding="utf-8")) for m in (server_mod, routes_mod)
    ]
    return {
        node.func.id
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("register_")
    }


def test_the_gateway_builder_mounts_the_import_routes():
    called = _registrars_called_by_the_gateway()
    # Vacuity: the walk can find a registrar (a long-mounted neighbour) and does not
    # invent one — so a green here means the call is really in the builder.
    assert "register_pack_routes" in called
    assert "register_nothing_at_all_routes" not in called
    assert "register_onboarding_import_routes" in called
