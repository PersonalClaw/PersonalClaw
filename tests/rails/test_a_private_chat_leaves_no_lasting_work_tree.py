"""The source-tree checks split from
``tests/test_a_private_chat_leaves_no_lasting_work.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import inspect

from test_an_agent_clis_workflow_tools_reach_the_gateway import (  # noqa: F401 - a fixture
    CLI_MODEL,
    TEMPORARY_KEY,
    _tool_server,
    gateway,
)

from personalclaw import lasting_work, mcp_automation


def test_every_automation_tool_that_makes_or_changes_one_says_so_before_it_is_allowed():
    """The automation tools that reach the trigger store's make or change are the ones whose
    preflight asks the rule, so a new one cannot be added without it."""
    tree = ast.parse(inspect.getsource(mcp_automation._call_tool_inner))
    reaching: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        named = node.test.comparators[0]
        if not isinstance(named, ast.Constant):
            continue
        for call in (n for stmt in node.body for n in ast.walk(stmt)):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                if (
                    call.func.attr in ("create", "update")
                    and getattr(call.func.value, "id", "") == "T"
                ):
                    reaching[named.value] = (
                        lasting_work.CREATE if call.func.attr == "create" else lasting_work.CHANGE
                    )
    assert reaching == mcp_automation._LASTING_ACTS
