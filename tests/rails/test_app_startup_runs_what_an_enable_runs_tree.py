"""The source-tree checks split from
``tests/test_app_startup_runs_what_an_enable_runs.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

# Imported before any test patches `config_dir`: the probe imports the SDK, and a module first
# imported under a patch keeps the mock bound.
import personalclaw.sdk.channel  # noqa: F401
import personalclaw.sdk.model  # noqa: F401
import personalclaw.sdk.sandbox  # noqa: F401
import personalclaw.sdk.tool  # noqa: F401


def test_a_provider_has_no_switch_of_its_own():
    """A provider is switched by switching its app. A providers route that toggled the instances
    left the app's code loaded, and it sat outside the owner-only app routes: an app that declared
    ``/api/providers`` could switch another app's code on through it."""
    import ast
    import inspect

    from personalclaw.providers import routes

    registered = {
        node.args[0].value
        for node in ast.walk(ast.parse(inspect.getsource(routes.register_routes)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith("add_")
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert "/api/providers/{name}/availability" in registered, registered  # it reads the routes
    switches = sorted(r for r in registered if r.rsplit("/", 1)[-1] in {"enable", "disable"})
    assert switches == [], f"a provider has a switch of its own again: {switches}"
