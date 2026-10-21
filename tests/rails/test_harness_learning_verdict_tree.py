"""The source-tree checks split from
``tests/test_harness_learning_verdict.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import sys
import types

import pytest
from harness_learning_verdict_shared import MODULE

from harness import learning_verdict


def test_the_sibling_defines_no_threshold_of_its_own():
    """The structural half: an AST census, so a future edit that re-derives a threshold reds here
    rather than passing because the number happened to match."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    numeric_consts = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float))
        if not isinstance(node.value.value, bool)
    }
    assert (
        numeric_consts == {}
    ), f"the sibling minted its own numeric threshold(s): {numeric_consts}"


def _arms(*, tokens_recorded: bool) -> dict:
    """Two scored arms of five trials each, spending 100 tokens a trial, their count recorded or
    not (a cell whose provider omitted its usage arrives as a recorded-looking 0 otherwise)."""
    return {
        "on_trials": [learning_verdict.Trial(score=80.0, tokens=100) for _ in range(5)],
        "off_trials": [learning_verdict.Trial(score=60.0, tokens=100) for _ in range(5)],
        "spend_observed": True,
        "tokens_recorded": tokens_recorded,
    }


def _the_module_without_its_refusal(monkeypatch) -> types.ModuleType:
    """`learning_verdict` as it would be with the refusal branch deleted, the edit the second
    rail is there for: the module's own code, with the one `if not tokens_recorded and ...` that
    returns the refusal taken out of `verdict_task` and nothing else changed."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    (task,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "verdict_task"]

    def _is_the_refusal(node: ast.stmt) -> bool:
        test = node.test if isinstance(node, ast.If) else None
        first = test.values[0] if isinstance(test, ast.BoolOp) else None
        return (
            isinstance(first, ast.UnaryOp)
            and isinstance(first.op, ast.Not)
            and isinstance(first.operand, ast.Name)
            and first.operand.id == "tokens_recorded"
            and any(isinstance(n, ast.Return) for n in node.body)
        )

    refusals = [n for n in task.body if _is_the_refusal(n)]
    assert len(refusals) == 1, "the refusal branch is not where this test takes it out"
    task.body.remove(refusals[0])
    module = types.ModuleType("harness.learning_verdict_without_its_refusal")
    module.__file__ = str(MODULE)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(tree, str(MODULE), "exec"), module.__dict__)
    return module


def test_the_second_rail_stops_an_unrecorded_count_before_the_gate_once_the_refusal_is_gone(
    monkeypatch,
):
    """Two guards on one flag: the refusal, then a raise between it and `compare`.

    The defect the raise prevents: with the refusal deleted, the placeholder zeros of cells whose
    provider reported no usage would reach `compare()`, and a token ratio would be published over
    them as a spend match. The raise CANNOT be reached while the refusal above it stands, which
    is the point: it is a mutation rail, not a runtime path, and its property is where it sits,
    so it is read from the module's code. The module is built with the refusal branch deleted,
    the edit it guards, and `compare` replaced with a bomb: an unrecorded count then raises
    `IncommensurableSpendError`, naming the gate and the task, before `compare` is reached; a
    recorded count still reaches it, so the raise is about the flag and not every call. With the
    refusal in place the shipped module refuses and raises nothing.
    """
    raises = [
        node
        for node in ast.walk(ast.parse(MODULE.read_text(encoding="utf-8")))
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name)
        and node.exc.func.id == "IncommensurableSpendError"
    ]
    # One for the spend BASIS, one for the unrecorded COUNT. Both bases the gate cannot use.
    assert len(raises) == 2, "both IncommensurableSpendError rails must stand"

    bare = _the_module_without_its_refusal(monkeypatch)
    reached: list[str] = []

    def _bomb(task_id, *_a, **_kw):
        reached.append(task_id)
        raise AssertionError("compare() was reached")

    monkeypatch.setattr(bare, "compare", _bomb)
    with pytest.raises(
        bare.IncommensurableSpendError, match="the token-match gate was reached for 't'"
    ):
        bare.verdict_task(task_id="t", skill="s", **_arms(tokens_recorded=False))
    assert reached == [], "the second rail sits after `compare`, a post-mortem"
    with pytest.raises(AssertionError, match=r"compare\(\) was reached"):
        bare.verdict_task(task_id="u", skill="s", **_arms(tokens_recorded=True))
    assert reached == ["u"]
    shipped = learning_verdict.verdict_task(task_id="t", skill="s", **_arms(tokens_recorded=False))
    assert shipped.verdict == learning_verdict.VERDICT_TOKENS_UNRECORDED
