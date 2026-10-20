"""The source-tree checks split from
``tests/test_surface_layers_safe_mode.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import surface_layers_safe_mode_shared

_clean_latch = surface_layers_safe_mode_shared._clean_latch


class TestTheTileActionRoute:
    def test_the_route_is_registered_and_its_handler_is_exported(self) -> None:
        """A handler nothing routes to is this repo's most repeated failure shape.

        Audited STATICALLY (ast over `dashboard/routes.py`), which is the house precedent —
        `test_api_manifest_drift` documents why: booting the dashboard to walk the live table
        has security-critical startup side effects. The handler half is checked by IMPORT, so
        a registered path pointing at a name that does not exist still fails here."""
        import ast

        import personalclaw.dashboard.routes as routes_mod
        from personalclaw.dashboard.handlers import views as views_mod

        tree = ast.parse(Path(routes_mod.__file__).read_text(encoding="utf-8"))
        paths = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("add_post", "add_get", "add_put", "add_delete")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        assert "/api/dashboard/views/{view_id}/tiles/action" in paths
        assert callable(views_mod.api_dashboard_view_tile_action)
