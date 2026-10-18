"""The source-tree checks split from
``tests/test_every_waited_for_one_shot_is_marked.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import pytest
from every_waited_for_one_shot_is_marked_shared import _SRC

#: (file, enclosing function) of each one-shot call a person waits for.
WAITED_FOR = [
    ("nl_to_cron.py", "ask"),  # a chat's automation tool turning a phrase into a schedule
    ("mcp_workflows.py", "summarize"),  # a chat's workflow tool matching a template
    ("dashboard/handlers/loop_routes.py", "_ask"),  # the loop composer's task analysis
    ("inbox_service.py", "draft_reply"),  # the Inbox page's Draft reply
    ("inbox_service.py", "_within_limit"),  # …asked again within her word limit
    ("reply_answers.py", "check"),  # …and checked for answers she never gave
    ("inbox_service.py", "generate_digest"),  # the Inbox page's channel digest
    ("packs/prompt_cards.py", "convert_card"),  # the prompt-card import form
    ("dashboard/chat_handlers.py", "api_nav_resolve_links"),  # link summaries a caller awaits
]


def _one_shot_calls(tree: ast.AST, function: str) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "one_shot_completion"
                ):
                    calls.append(inner)
    return calls


@pytest.mark.parametrize(("path", "function"), WAITED_FOR)
def test_a_waited_for_one_shot_carries_who_waits(path, function):
    tree = ast.parse((_SRC / path).read_text(encoding="utf-8"))
    calls = _one_shot_calls(tree, function)
    assert calls, f"{path}:{function} makes no one-shot call any more; update the census"
    for call in calls:
        assert any(
            kw.arg == "attended" for kw in call.keywords
        ), f"{path}:{function} line {call.lineno}: a call somebody waits for without attended="
