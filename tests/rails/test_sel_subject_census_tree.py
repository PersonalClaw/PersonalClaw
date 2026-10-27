"""The source-tree checks split from
``tests/test_sel_subject_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO
from sel_log_calls_shared import the_call_it_runs
from sel_subject_census_shared import _problem

SRC = REPO / "src" / "personalclaw"


def audit_calls(trees: list[tuple[str, ast.AST]]) -> list[tuple[str, int, ast.Call]]:
    """Each ``log_tool_invocation`` call in *trees*, ``(path, tree)`` pairs. One handed to
    ``sel_log_async`` is read as the call it runs (``sel_log_calls_shared``)."""
    found: list[tuple[str, int, ast.Call]] = []
    for where, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = the_call_it_runs(node)
            if isinstance(call.func, ast.Attribute) and call.func.attr == "log_tool_invocation":
                found.append((where, call.lineno, call))
    return found


def _audit_calls() -> list[tuple[str, int, ast.Call]]:
    return audit_calls(
        [(str(path.relative_to(SRC)), tree) for path, tree in source_index.package_trees()]
    )


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


_HANDED = """
async def handler(x):
    await sel_log_async(sel().log_tool_invocation, session_key="", tool_name="bash", outcome="ok")
    await sel_log_async(
        sel().log_tool_invocation, session_key="", tool_name="bash", tool_input=x, outcome="ok"
    )
    sel().log_tool_invocation(session_key="", tool_name="bash", outcome="ok")
"""


def test_a_row_handed_to_sel_log_async_is_read_as_the_call_it_runs():
    """A row moved off the loop is still in the census, and still judged: the handed row that
    leaves out what ran is flagged as the direct one is, and the one that hands it over is not."""
    calls = audit_calls([("planted.py", ast.parse(_HANDED))])
    assert sorted(line for _, line, _ in calls) == [3, 4, 7], calls
    assert sorted(line for _, line, call in calls if _problem(call)) == [3, 7]
