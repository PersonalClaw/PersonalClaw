"""The source-tree checks split from
``tests/test_a_stop_never_waits_forever_on_a_cancelled_task.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import source_index
from rails_repo import REPO

_SRC = REPO / "src" / "personalclaw"


#: The waits the rail allows, each with the reason its task cannot be starting a process. A task
#: that could be starting one goes through ``cancel_and_wait`` instead.
_ALLOWED = {
    "acp/connection_pool.py::AcpConnectionPool.shutdown::self._sweep_task": (
        "the lease sweep sleeps, then releases idle leases in the default executor"
    ),
    "acp/reader.py::FrameRouter.close::self._reader_task": (
        "it reads frames from an agent process that is already running"
    ),
    "browse/transport.py::WebSocketCdpTransport.close::task": (
        "the reader and the dispatcher read and route frames on an open websocket"
    ),
    "dashboard/handlers/terminal.py::_kill_session::sess.reader_task": (
        "never set outside tests: the terminal reads its PTY with the loop's add_reader"
    ),
    "testing/channel_conformance.py::_assert_approval_endings::wait": (
        "not a stop: the conformance kit checking that a cancelled approval wait ends, under the "
        "kit's own deadline"
    ),
}


_UNWRAP_CALLS = {"list", "tuple", "set", "sorted", "frozenset"}


_UNWRAP_METHODS = {"values", "items", "copy"}


def _key(node: ast.AST) -> str:
    """A cancelled task's name as written, with ``list(...)`` and ``.values()`` taken off."""
    while True:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _UNWRAP_CALLS
            and node.args
        ):
            node = node.args[0]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _UNWRAP_METHODS
            and not node.args
        ):
            node = node.func.value
        elif isinstance(node, ast.Starred):
            node = node.value
        else:
            return ast.unparse(node)


def _own_nodes(fn: ast.AST):
    """Every node in *fn*'s body, not descending into functions or classes defined in it."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _is_cancel(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "cancel"
        and not node.args
    )


def _cancelled(fn: ast.AST) -> set[str]:
    """What *fn* cancels: each task it calls ``cancel()`` on, the collection a loop cancels the
    members of, and a list or set it collects a cancelled task into."""
    names: set[str] = {_key(node.func.value) for node in _own_nodes(fn) if _is_cancel(node)}
    for node in _own_nodes(fn):
        if isinstance(node, ast.For):
            loop_vars = {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
            if any(
                _is_cancel(inner) and _key(inner.func.value) in loop_vars
                for inner in ast.walk(node)
            ):
                names.add(_key(node.iter))
    for node in _own_nodes(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("append", "add")
            and len(node.args) == 1
            and _key(node.args[0]) in names
        ):
            names.add(_key(node.func.value))
    return names


def _waited_on(value: ast.AST) -> list[str]:
    """What an ``await`` waits for without a bound: a task, a ``gather`` of them, an
    ``asyncio.wait`` with no timeout, or ``wait_for``, which at its timeout cancels the task
    again and then waits for it to leave."""
    if not isinstance(value, ast.Call):
        return [_key(value)]
    name, kwargs = _call_name(value), {k.arg for k in value.keywords}
    if name == "gather":
        return [_key(arg) for arg in value.args]
    if name == "wait" and "timeout" not in kwargs and len(value.args) < 2 and value.args:
        first = value.args[0]
        if isinstance(first, (ast.Set, ast.List, ast.Tuple)):
            return [_key(element) for element in first.elts]
        return [_key(first)]
    if name == "wait_for" and value.args:
        return [_key(value.args[0])]
    return []


def _unbounded_waits(root: Path) -> set[str]:
    """``file::qualname::task`` for every unbounded wait on a task the same function cancelled."""
    found: set[str] = set()

    def visit(node: ast.AST, rel: str, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, rel, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                cancelled = _cancelled(child)
                if cancelled:
                    for inner in _own_nodes(child):
                        if isinstance(inner, ast.Await):
                            for name in _waited_on(inner.value):
                                if name in cancelled:
                                    found.add(f"{rel}::{qualname}::{name}")
                visit(child, rel, f"{qualname}.")
            else:
                visit(child, rel, prefix)

    # The repository's own package is the suite's one source index; a fixture root is walked here.
    if root == _SRC:
        trees = source_index.package_trees()
    else:
        trees = [
            (path, ast.parse(path.read_text(encoding="utf-8")))
            for path in sorted(root.rglob("*.py"))
        ]
    for path, tree in trees:
        visit(tree, path.relative_to(root).as_posix(), "")
    return found


@pytest.mark.timeout(40)
def test_no_stop_waits_without_a_bound_on_a_task_it_cancelled():
    found = _unbounded_waits(_SRC)
    new = sorted(found - set(_ALLOWED))
    assert not new, (
        "these wait without a bound on a task they cancelled. A task can outlive its cancel (on "
        "Python 3.12 a start cancelled while it connects its child's pipes never leaves), and the "
        f"stop then never finishes. Use cancellation.cancel_and_wait: {new}"
    )
    gone = sorted(set(_ALLOWED) - found)
    assert not gone, f"these allowed waits are gone; take them out of _ALLOWED: {gone}"


def test_the_rail_sees_each_shape_it_is_for(tmp_path):
    (tmp_path / "planted.py").write_text(
        "import asyncio\n"
        "class Owner:\n"
        "    async def awaits_it(self):\n"
        "        self._task.cancel()\n"
        "        await self._task\n"
        "    async def gathers_them(self):\n"
        "        for t in list(self._tasks.values()):\n"
        "            t.cancel()\n"
        "        await asyncio.gather(*self._tasks.values(), return_exceptions=True)\n"
        "    async def waits_with_no_timeout(self, start):\n"
        "        start.cancel()\n"
        "        await asyncio.wait({start})\n"
        "    async def waits_for_it(self, turn):\n"
        "        turn.cancel()\n"
        "        await asyncio.wait_for(turn, timeout=2.0)\n"
        "    async def collects_them(self):\n"
        "        waiting = []\n"
        "        for key, task in list(self._tasks.items()):\n"
        "            if not task.done():\n"
        "                task.cancel()\n"
        "                waiting.append(task)\n"
        "        await asyncio.gather(*waiting, return_exceptions=True)\n",
        encoding="utf-8",
    )
    assert _unbounded_waits(tmp_path) == {
        "planted.py::Owner.awaits_it::self._task",
        "planted.py::Owner.gathers_them::self._tasks",
        "planted.py::Owner.waits_with_no_timeout::start",
        "planted.py::Owner.waits_for_it::turn",
        "planted.py::Owner.collects_them::waiting",
    }


def test_the_rail_passes_what_is_bounded_or_was_not_cancelled(tmp_path):
    (tmp_path / "clean.py").write_text(
        "import asyncio\n"
        "from personalclaw.cancellation import cancel_and_wait\n"
        "async def through_the_helper(task):\n"
        "    await cancel_and_wait([task], what='x')\n"
        "async def with_a_timeout(task):\n"
        "    task.cancel()\n"
        "    await asyncio.wait({task}, timeout=1.0)\n"
        "async def other_work_after_a_cancel(sweeper, conns):\n"
        "    sweeper.cancel()\n"
        "    await asyncio.gather(*(c.shutdown() for c in conns))\n"
        "async def outer(task):\n"
        "    def inner():\n"
        "        task.cancel()\n"
        "    inner()\n"
        "    await task\n",
        encoding="utf-8",
    )
    assert _unbounded_waits(tmp_path) == set()


def test_the_family_waits_through_the_helper():
    """The stops that cancel a task that may be starting a process each call the helper: the
    census above is a zero because they do, not because the scan went blind."""
    calls = [
        node
        for _path, tree in source_index.package_trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "cancel_and_wait"
    ]
    assert len(calls) >= 12, len(calls)
