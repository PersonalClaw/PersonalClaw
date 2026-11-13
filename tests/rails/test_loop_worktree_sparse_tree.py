"""The source-tree checks split from
``tests/test_loop_worktree_sparse.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import shutil

import loop_worktree_sparse_shared
import pytest

_wt_root = loop_worktree_sparse_shared._wt_root


# `shutil.which`, not `wt.git_available()`: that probe reads git's version with git's child
# environment, built from the owner's settings, and a module-level mark runs at collection,
# before any test chose a home.
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def test_sdlc_scheduler_calls_the_scoped_batch_api():
    """The call site, by AST — the whole value is that the SDLC fan-out uses the
    batched, scoped API. A green suite over ``worktree.py`` alone would not notice
    ``sdlc`` still calling ``add_worktree`` one task at a time in its loop."""
    import ast
    import inspect
    import textwrap

    from personalclaw.loop.kinds import sdlc

    tree = ast.parse(textwrap.dedent(inspect.getsource(sdlc.CodeKind._fan_out)))
    calls = {
        ast.unparse(n.func)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "worktree.add_worktrees" in calls, calls
    assert "worktree.scope_for_task" in calls, calls
    assert "worktree.add_worktree" not in calls, "serial per-task creation is still here"
    # Each git step is handed to a worker thread, named or inside the nested `cut`, so the
    # gateway's event loop is never what waits on git: none is called at either one's own level.
    for fn in (sdlc.CodeKind._fan_out, sdlc.CodeKind._keep_or_sweep):
        (body,) = ast.parse(textwrap.dedent(inspect.getsource(fn))).body
        own = []
        todo = list(ast.iter_child_nodes(body))
        while todo:
            node = todo.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            todo.extend(ast.iter_child_nodes(node))
            if isinstance(node, ast.Call) and ast.unparse(node.func).startswith("worktree."):
                own.append(ast.unparse(node.func))
        assert own == [], f"git runs on the event loop in {fn.__name__}: {own}"


def test_a_redo_resets_the_worktree_then_falls_back_to_teardown():
    """The reuse pool's real CALL SITE, by AST: the redo its owner chose for a task whose work
    conflicted (``loop.conflicts._redo``), the one place a task's worktree is finished with and
    reused. Asserting on ``worktree.py`` alone would not notice the redo never adopting it — and
    the ``remove_worktree`` call must still be REACHABLE, because it is the documented teardown
    fallback for any reset failure."""
    import ast
    import inspect
    import textwrap

    from personalclaw.loop import conflicts

    tree = ast.parse(textwrap.dedent(inspect.getsource(conflicts._redo)))
    # Named, not only called: both run off the event loop, handed to ``asyncio.to_thread``.
    named = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert "worktree.reset_worktree" in named, named
    assert "worktree.remove_worktree" in named, "the teardown fallback is gone"
    # The teardown must be GUARDED by the reset's failure, not run unconditionally.
    guarded = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and "worktree.reset_worktree" in ast.unparse(n.test)
        and "worktree.remove_worktree" in ast.unparse(n.body)
    ]
    assert guarded, "remove_worktree is not gated on a failed reset"
