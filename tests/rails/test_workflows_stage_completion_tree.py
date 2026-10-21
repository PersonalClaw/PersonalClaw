"""The source-tree checks split from
``tests/test_workflows_stage_completion.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

from personalclaw.workflows import stage_settlement


def test_the_poll_delay_and_the_reconciler_read_ONE_predicate():
    """The rail. Both consumers must derive their set from `awaiting_out_of_band_work`.

    A second copy of the condition is how the pair drifts, and the drift restores exactly this
    bug: a node kind the reconciler learns to poll but the loop does not learn to wake for is
    polled at `sleep(0)` frequency. Parsed with `ast` so a mention inside the (long) prose of
    either docstring cannot satisfy it. The two consumers live in different modules — the delay is
    the tick loop's (`controller`), the settle is `stage_settlement`'s — so each is looked up in
    its own, and the predicate counts whether it is called by name or through its module.
    """
    from personalclaw.workflows import controller as controller_mod

    bodies = {}
    for module, name in (
        (controller_mod, "_dispatched_poll_delay"),
        (stage_settlement, "reconcile_dispatched_stages"),
    ):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
                bodies[name] = fn
    assert set(bodies) == {
        "reconcile_dispatched_stages",
        "_dispatched_poll_delay",
    }, f"a consumer was renamed or removed: {sorted(bodies)}"

    for name, fn in bodies.items():
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        assert "awaiting_out_of_band_work" in calls, (
            f"`{name}` no longer derives its set from `awaiting_out_of_band_work` — the "
            "condition is duplicated, and the two copies will drift"
        )
        compared = {
            sub.attr
            for node in ast.walk(fn)
            if isinstance(node, ast.Compare)
            for sub in ast.walk(node)
            if isinstance(sub, ast.Attribute)
        }
        assert (
            "RUNNING" not in compared
        ), f"`{name}` re-derives the RUNNING test itself instead of asking the predicate"


def test_stage_is_the_ONLY_dispatcher_THAT_CAN_RETURN_RUNNING():
    """The blast-radius assertion: there is no non-stage RUNNING path to change.

    Parsed with `ast`, not grepped — a text scan counts the string inside comments and
    docstrings, and this file's whole subject is a claim about which code runs.
    """
    from personalclaw.workflows import engine

    # From the imported module, not a cwd-relative path. A scan of the wrong file, or of none,
    # finds no producer, and the equality below names the one there must be, so an empty scan
    # fails it rather than passing as a clean one.
    tree = ast.parse(Path(engine.__file__).read_text(encoding="utf-8"))

    producers: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            for sub in ast.walk(node.value):
                if (
                    isinstance(sub, ast.Attribute)
                    and sub.attr == "RUNNING"
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "InstanceState"
                ):
                    producers.add(fn.name)

    assert producers == {"dispatch_stage"}, (
        f"the dispatchers that return RUNNING are {sorted(producers)}, not `dispatch_stage` "
        "alone. Another one means the stage reconciliation would silently claim its completion "
        "too; none means the scan read no engine."
    )
