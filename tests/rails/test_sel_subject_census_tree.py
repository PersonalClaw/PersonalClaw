"""The source-tree checks split from
``tests/test_sel_subject_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO
from sel_subject_census_shared import _problem

SRC = REPO / "src" / "personalclaw"


def _audit_calls() -> list[tuple[str, int, ast.Call]]:
    found: list[tuple[str, int, ast.Call]] = []
    for path, tree in source_index.package_trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "log_tool_invocation"
            ):
                found.append((str(path.relative_to(SRC)), node.lineno, node))
    return found


def test_every_audited_tool_call_hands_the_log_what_it_ran():
    calls = _audit_calls()
    problems = [f"{where}:{line}: {why}" for where, line, call in calls if (why := _problem(call))]
    assert not problems, (
        "these audit rows would name a tool and not what it ran — hand the call's arguments over "
        "(`tool_input=`), or `tool_input=None` for a row about no call:\n  " + "\n  ".join(problems)
    )


def test_the_census_reads_the_whole_surface():
    """Vacuity floor: a census that found a handful of calls would pass the test above."""
    calls = _audit_calls()
    handing = [c for _, _, c in calls if any(k.arg == "tool_input" for k in c.keywords)]
    assert len(calls) >= 200, len(calls)
    assert len(handing) >= 40, len(handing)
    files = {where for where, _, _ in calls}
    for host in (
        "dashboard/chat_runner.py",
        "subagent.py",
        "llm_helpers.py",
        "eval/runner.py",
        "acp/ungated.py",
        "dashboard/handlers/tools.py",
        "mcp_shared.py",
    ):
        assert host in files, f"the census no longer reads {host}"
