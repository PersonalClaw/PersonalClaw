"""The source-tree checks split from
``tests/test_files_git_status.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from spawn_ceiling_audit_shared import _SPAWN_CALLEES, _callee, _normalize

from personalclaw.dashboard.handlers import files as F


def _spawns_git(node) -> bool:
    """True iff *node* is a process spawn of git: its argv[0] is the literal ``"git"``, or its
    argv is built by ``net.git.git_argv`` (passed whole, or spread with ``*``).

    The spawn vocabulary and the callee matcher are imported from
    ``test_spawn_ceiling_audit`` rather than re-listed, so a new spawn shape has to be
    taught to the tree-wide census and this rail at once — one place, not two.
    """
    import ast

    if not isinstance(node, ast.Call) or not node.args:
        return False
    if _normalize(_callee(node)) not in _SPAWN_CALLEES:
        return False
    first = node.args[0]
    if isinstance(first, ast.Starred):
        first = first.value
    if isinstance(first, ast.Call) and _callee(first).split(".")[-1] == "git_argv":
        return True
    if isinstance(first, ast.List) and first.elts:
        first = first.elts[0]
    return isinstance(first, ast.Constant) and first.value == "git"


def _git_spawning_functions() -> set[str]:
    """Names of the functions in files.py that spawn ``git``. Derived from the AST."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(F.__file__).read_text())
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}

    def enclosing(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(cur)
        return "<module>"

    return {enclosing(n) for n in ast.walk(tree) if _spawns_git(n)}


def test_files_has_exactly_one_git_invoker():
    """Every ``git`` spawn in files.py must live in ``_git``. Derived, not hand-listed.

    #432 was one function re-implementing ``_git``: the copy leaked a process per
    timeout, 500'd on an unexecutable git, and served a tree listing as a file's
    content. Deleting the copy fixes those three; this keeps a fourth from being
    written. The callee vocabulary is the spawn-census one (``test_spawn_ceiling_audit``
    owns the tree-wide version), so a new spawn shape is caught in both places.
    """
    spawners = _git_spawning_functions()

    assert spawners == {"_git"}, (
        f"git is spawned outside _git in files.py: {sorted(spawners - {'_git'})}. Route it "
        "through _git — that is where the kill+reap of a blown deadline, the degrade when "
        "git cannot be executed, and the redaction of git's output all live. A second "
        "invoker re-earns #432 one forgotten branch at a time."
    )


def test_the_git_invoker_matcher_is_not_vacuous():
    """VACUITY: the rail must SEE a hand-rolled git spawn, and must ignore a non-git one.

    Both directions. A matcher that matched nothing would let the rail above pass by
    inspecting an empty set, which is the failure mode a census rail is most prone to.
    """
    import ast

    sneaky = ast.parse(
        "import asyncio\n"
        "async def api_sneaky():\n"
        "    proc = await asyncio.create_subprocess_exec('git', 'show', 'HEAD:x')\n"
    )
    assert [
        n for n in ast.walk(sneaky) if _spawns_git(n)
    ], "the matcher would not notice a hand-rolled git spawn — the rail above is vacuous"
    helper_built = ast.parse(
        "import asyncio\n"
        "async def api_sneaky():\n"
        "    proc = await asyncio.create_subprocess_exec(*git_argv(['show', 'HEAD:x']))\n"
    )
    assert [
        n for n in ast.walk(helper_built) if _spawns_git(n)
    ], "the matcher would not notice a git spawn built by git_argv — the rail above is vacuous"
    other = ast.parse(
        "import asyncio\n"
        "async def f():\n"
        "    proc = await asyncio.create_subprocess_exec('rg', '--json')\n"
    )
    assert not [n for n in ast.walk(other) if _spawns_git(n)], "rg is not a git invocation"
    # And the rail must find the real one, so `== {"_git"}` is not `== set()` passing.
    assert "_git" in _git_spawning_functions()
