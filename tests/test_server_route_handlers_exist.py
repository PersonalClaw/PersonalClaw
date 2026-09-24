"""Guard: every handler referenced in a route registration in server.py must
actually exist on the module it's attributed to.

Regression test for the class of bug where a handler is deleted (e.g. the Autopilot
`api_chat_plan_action`) but its `app.router.add_*(..., chat.api_chat_plan_action)`
registration is left behind — which raises AttributeError at `start_dashboard()`
time and prevents the gateway from booting. The full suite never caught it because
nothing stands up the whole dashboard route block. This static check does.
"""

import ast
import importlib
from pathlib import Path

import personalclaw.dashboard.server as server_mod

# Module aliases as imported at the top of server.py (verified by the test below).
_ALIAS_TO_MODULE = {
    "chat": "personalclaw.dashboard.chat",
    "handlers": "personalclaw.dashboard.handlers",
}


def _route_handler_refs() -> list[tuple[str, str, int]]:
    """Find every `app.router.add_*(path, <alias>.<attr>)` reference in server.py,
    returning (alias, attr, lineno) for the handler argument."""
    src = Path(server_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    refs: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # match app.router.add_get / add_post / add_put / add_delete / add_patch
        if not (isinstance(fn, ast.Attribute) and fn.attr.startswith("add_")):
            continue
        for arg in node.args:
            # handler is an attribute access like `chat.api_chat_session_resume`
            if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                alias = arg.value.id
                if alias in _ALIAS_TO_MODULE:
                    refs.append((alias, arg.attr, arg.lineno))
    return refs


def test_all_route_handlers_resolve():
    refs = _route_handler_refs()
    assert refs, "expected to find route-handler references in server.py"
    missing = []
    for alias, attr, lineno in refs:
        mod = importlib.import_module(_ALIAS_TO_MODULE[alias])
        if not hasattr(mod, attr):
            missing.append(f"server.py:{lineno} → {alias}.{attr} does not exist")
    assert not missing, "Route handlers referenced in server.py but not defined:\n" + "\n".join(
        missing
    )
