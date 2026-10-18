"""A chat turn's engine runs only as the turn its phase frames say.

``chat_runner.run_chat`` is decorated with ``_says_its_phases``, which says the turn is waiting to
start before the turn awaits anything and ends it on every way out (``chat_phase``), an exception
and a cancellation included, so a running turn is never left saying it runs. That holds only while
nothing runs the engine around its decorator: a turn run from its undecorated body would end
unsaid, and every surface would show it running forever.
``tests/test_every_way_a_turn_ends_sends_the_final_phase.py`` walks the endings; this rail holds
the engine to its decorator.
"""

from __future__ import annotations

import ast

import source_index
from rails_repo import REPO

RUNNER = REPO / "src" / "personalclaw" / "dashboard" / "chat_runner.py"
WRAPPER = "_says_its_phases"


def _decorators(name: str) -> list[str]:
    """The decorators of the top-level function *name* in the runner, as written."""
    (function,) = [
        node
        for node in source_index.tree(RUNNER).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    ]
    return [ast.unparse(decorator) for decorator in function.decorator_list]


def _unwrapped_reads(trees) -> list[str]:
    """Every read of a ``run_chat``'s ``__wrapped__`` in *trees*, as ``<where>:<line>``: a way to
    the engine around its decorators."""
    return [
        f"{where}:{node.lineno}"
        for where, tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "__wrapped__"
        and "run_chat" in ast.unparse(node.value)
    ]


def test_the_turn_engine_runs_only_inside_the_wrapper_that_ends_it() -> None:
    assert WRAPPER in _decorators("run_chat"), _decorators("run_chat")
    package = [(source_index.rel(path), tree) for path, tree in source_index.package_trees()]
    assert len(package) > 1000, "read too little of the package — vacuous"
    assert _unwrapped_reads(package) == []


def test_a_way_around_the_wrapper_is_seen() -> None:
    """The positive control: a planted call of the undecorated engine is found."""
    planted = ast.parse("await chat_runner.run_chat.__wrapped__(state, session, message)\n")
    assert _unwrapped_reads([("planted.py", planted)]) == ["planted.py:1"]
