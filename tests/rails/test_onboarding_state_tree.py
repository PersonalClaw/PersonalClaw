"""The source-tree checks split from
``tests/test_onboarding_state.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import onboarding_state_shared

_isolate_home = onboarding_state_shared._isolate_home


def test_post_route_is_registered():
    """The handler must be reachable — a store with no route is inert."""
    import ast
    from pathlib import Path

    import personalclaw.dashboard.routes as route_table

    tree = ast.parse(Path(route_table.__file__).read_text(encoding="utf-8"))
    posts = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_post"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert "/api/onboarding/state" in posts
