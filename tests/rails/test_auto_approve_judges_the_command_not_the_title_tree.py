"""The source-tree checks split from
``tests/test_auto_approve_judges_the_command_not_the_title.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


#: Every call of the screen in the package, by ``file::function``, and whether its verdict can
#: approve a call. One that can must say what the call is, or a shell call would be read as a name.
APPROVES = {
    "dashboard/chat_runner.py::run_chat": "a chat's call: an operator's pattern answers it",
    "llm_helpers.py::_resolve_permission": "the background helper's hook-based policy",
    "chat_trust.py::_hook_verdict": "a channel's own turn (chat_grant)",
    "run_bounds.py::screen": "a subagent's call, and anything else that screens an event",
}


REFUSES_ONLY = {
    "agents/native/approval.py::deny_list_refusal": "the native runtime's deny step",
    "dashboard/handlers/tools.py::api_tool_invoke": "Tools → Try it, by the tool's name",
    "eval/runner.py::EvalRunner._decide_permission": "an evaluation approves by its allowlist",
}


def _screen_calls() -> dict[str, list[ast.Call]]:
    sites: dict[str, list[ast.Call]] = {}
    for path, tree in source_index.package_trees():
        rel = path.relative_to(SRC).as_posix()

        def visit(node, scope):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visit(child, [*scope, child.name])
                    continue
                if isinstance(child, ast.Call):
                    func = child.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name == "screen_tool_call":
                        sites.setdefault(f"{rel}::{'.'.join(scope)}", []).append(child)
                visit(child, scope)

        visit(tree, [])
    return sites


def test_every_screen_that_can_approve_is_told_what_the_call_is():
    sites = _screen_calls()
    assert set(sites) == set(APPROVES) | set(REFUSES_ONLY), (
        "classify each call of screen_tool_call: "
        f"{sorted(set(sites) ^ (set(APPROVES) | set(REFUSES_ONLY)))}"
    )
    for site in APPROVES:
        for call in sites[site]:
            given = {kw.arg for kw in call.keywords}
            assert {"tool_kind", "declared"} <= given, f"{site} does not say what the call is"
