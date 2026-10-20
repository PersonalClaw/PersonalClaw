"""The source-tree checks split from
``tests/test_running_a_turn_again_census.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import source_index
from running_a_turn_again_census_shared import _called, _functions, _send_again_calls

import personalclaw

SRC = Path(personalclaw.__file__).resolve().parent


#: The doors that run a turn again, by (file under ``src/personalclaw``, function), with what the
#: owner presses (or does) to use each.
DOORS: dict[tuple[str, str], str] = {
    ("dashboard/chat_regenerate.py", "api_chat_session_regenerate"): (
        "Retry on a turn that ended without its answer, and Regenerate on an answer"
    ),
    ("dashboard/chat_regenerate.py", "api_chat_session_edit_resend"): (
        "Rewind to here, and Edit & resend with her message unchanged"
    ),
    ("dashboard/running_turn.py", "say_moved"): (
        "changing the agent, its CLI, its model or its effort while the turn answers"
    ),
}


#: The gateway's own re-send of a turn: after a lost connection, an empty reply or an agent that
#: stayed busy. Nobody presses anything, so it sends only a turn that made no call.
ON_ITS_OWN: dict[tuple[str, str], str] = {
    ("dashboard/chat_runner.py", "run_chat._send_again"): (
        "the turn that just ended, queued to run again as the same turn"
    ),
}


#: How many places in ``run_chat`` call ``_send_again()``: the floor that keeps the guard check
#: below from passing over a tree it no longer reads.
SEND_AGAIN_CALLS = 5


_STARTS_A_TURN = "run_chat"


_QUEUES_IT_AGAIN = "queue_retry"


_HANDED_THE_RE_SEND = "send_again"


def _own_nodes(func: ast.AST) -> list[ast.AST]:
    """The nodes of *func*'s own body: a function defined inside it is its own entry."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _deletes_transcript_rows(nodes: list[ast.AST]) -> bool:
    """A ``del <...>.messages[...]``: the rows a turn wrote, taken out of the transcript."""
    return any(
        isinstance(node, ast.Delete)
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.value, ast.Attribute)
            and t.value.attr == "messages"
            for t in node.targets
        )
        for node in nodes
    )


def _parameters(func: ast.AST) -> set[str]:
    args = getattr(func, "args", None)
    if args is None:
        return set()
    return {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}


def _runs_a_turn_again(func: ast.AST) -> bool:
    nodes = _own_nodes(func)
    called = {_called(n) for n in nodes}
    return (
        (_STARTS_A_TURN in called and _deletes_transcript_rows(nodes))
        or _QUEUES_IT_AGAIN in called
        or (_HANDED_THE_RE_SEND in called and _HANDED_THE_RE_SEND in _parameters(func))
    )


def _reruns_in_the_tree() -> set[tuple[str, str]]:
    """Every function in core that runs a turn again. A file that names none of the three ways a
    turn is run again cannot hold one, so only the files that do are parsed."""
    names = (_STARTS_A_TURN, _QUEUES_IT_AGAIN, _HANDED_THE_RE_SEND)
    found: set[tuple[str, str]] = set()
    for path in source_index.package_files():
        text = source_index.text(path)
        if not any(name in text for name in names):
            continue
        rel = path.relative_to(SRC).as_posix()
        for qualified, func in _functions(source_index.tree(path)).items():
            if _runs_a_turn_again(func):
                found.add((rel, qualified))
    return found


def _function(key: tuple[str, str]) -> ast.AST:
    rel, qualified = key
    return _functions(ast.parse((SRC / rel).read_text(encoding="utf-8")))[qualified]


def test_every_place_that_runs_a_turn_again_is_named():
    found = _reruns_in_the_tree()
    assert found, "the census found nothing that runs a turn again, so it reads nothing"
    named = set(DOORS) | set(ON_ITS_OWN)
    assert not found - named, (
        f"these run a turn again and are not in the census: {sorted(found - named)}. A door "
        "that runs a turn again asks first through `repeated_steps.ask_first`; name it in DOORS "
        "once it does."
    )
    assert not named - found, f"named here but no longer runs a turn again: {sorted(named - found)}"


@pytest.mark.parametrize("door", sorted(DOORS), ids=lambda d: d[1])
def test_each_door_asks_through_the_one_function_before_it_runs_anything(door):
    func = _function(door)
    nodes = _own_nodes(func)
    asks = [n.lineno for n in nodes if _called(n) == "ask_first"]
    assert asks, f"{door[1]} runs a turn again without asking through `ask_first`"
    acts = [
        n.lineno
        for n in nodes
        if _called(n) in (_STARTS_A_TURN, _HANDED_THE_RE_SEND) or isinstance(n, ast.Delete)
    ]
    assert acts, f"{door[1]} no longer runs anything: the census reads the wrong function"
    assert min(asks) < min(acts), f"{door[1]} deletes or runs something before it asks"


def test_the_gateway_re_sends_only_a_turn_that_made_no_call():
    calls = _send_again_calls(_function(("dashboard/chat_runner.py", "run_chat")))
    assert len(calls) == SEND_AGAIN_CALLS, (
        f"run_chat sends a turn again from {len(calls)} places, not {SEND_AGAIN_CALLS}: read each "
        "new one, and give it the check that the turn made no call"
    )
    unguarded = [line for line, guarded in calls if not guarded]
    assert not unguarded, (
        f"chat_runner.py sends a turn again with no check that it made no call, at lines "
        f"{unguarded}: a turn that made a call is not sent again on its own"
    )
