"""The source-tree checks split from
``tests/test_a_deployed_page_runs_in_an_origin_of_its_own.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import a_deployed_page_runs_in_an_origin_of_its_own_shared

from personalclaw.artifacts import handlers as artifact_handlers

_no_sessions = a_deployed_page_runs_in_an_origin_of_its_own_shared._no_sessions


def test_the_serve_route_answers_only_through_its_fenced_exits() -> None:
    """The rail behind the branch list above: a branch added later cannot answer without the
    fence. Every ``return`` in the serve handler hands back one of the two fenced exits, and both
    exits build their response with the serve headers."""
    exits = {"_served", "_refuse_serve"}
    handler = ast.parse(
        textwrap.dedent(inspect.getsource(artifact_handlers.serve_deployed_artifact))
    )
    returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return)]
    assert returns, "the serve handler returns nothing the rail can read"
    for ret in returns:
        call = ret.value
        name = (
            call.func.id if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) else ""
        )
        assert name in exits, f"line {ret.lineno}: the serve handler answers without its fence"
    # And no response is built in the handler itself, outside those exits.
    built = [
        n
        for n in ast.walk(handler)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "web"
    ]
    assert built == [], "the serve handler builds a response of its own"
    for name in exits:
        source = inspect.getsource(getattr(artifact_handlers, name))
        assert "SERVE_HEADERS" in source, f"{name} answers without the serve headers"
