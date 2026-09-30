"""A stop that cancels a task waits for it a bounded time, names what did not finish, and goes on.

🔴 The defect. Every stop that cancelled a task then awaited it (``task.cancel(); await task``, or
a ``gather`` of the cancelled tasks) waited for the task to LEAVE, and nothing bounded how long
that took. A task can outlive its cancel in ways it cannot help. On Python 3.12, a cancel that
lands while asyncio starts a child process and connects its pipes, together with asyncio's own
task that connects them, leaves the start waiting for a wake-up that never comes (3.13 fixed it).
A task whose cleanup waits for its child's pipes waits for as long as a grandchild that inherited
them runs. And a task can catch the cancel. The stop then never finished: the availability
board's, a subagent's, a session's, the gateway's own.

Now each of those stops goes through :func:`personalclaw.cancellation.cancel_and_wait`: cancel,
wait at most a grace period, log what did not finish, carry on.

What this file holds:

* the helper itself, against a task that never leaves after a cancel, one that leaves at once,
  and a caller that is itself cancelled while it waits;
* two real stops, the availability board's and the gateway's, against a start that never leaves
  after a cancel, which is how Python 3.12 leaves one. At ``origin/main`` both hang;
* a tree-wide rail: no function in ``src/personalclaw`` waits without a bound on a task it
  cancelled, except the few named below, each with the reason its task cannot be starting a
  process.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path

import pytest
from test_gateway import _make_orchestrator

from personalclaw import cancellation
from personalclaw.cancellation import cancel_and_wait
from personalclaw.providers.availability import AvailabilityBoard

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Long enough that a stop meeting its bound is plainly not the stop that never returns.
_WITHIN = 5.0


class _StartThatNeverLeaves:
    """Stands in for starting a child process: once cancelled, the start does not leave until the
    test lets it. Python 3.12 does this to a start cancelled while it connects the child's pipes,
    together with asyncio's own task that connects them: it waits for a wake-up that never comes.
    """

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancels = 0
        self._released = asyncio.Event()

    def release(self) -> None:
        self._released.set()

    async def __call__(self, *_args: object, **_kwargs: object) -> object:
        self.entered.set()
        while not self._released.is_set():
            try:
                await self._released.wait()
            except asyncio.CancelledError:
                self.cancels += 1  # does not leave on a cancel
        raise OSError("the start was let go by the test")


@pytest.fixture
def short_grace(monkeypatch) -> float:
    monkeypatch.setattr(cancellation, "CANCEL_GRACE_SECS", 0.3)
    return 0.3


async def _finished(future: asyncio.Future, within: float = _WITHIN) -> bool:
    """Whether *future* finished within *within* seconds, without cancelling it."""
    done, _ = await asyncio.wait({future}, timeout=within)
    return future in done


async def _let_go(start: _StartThatNeverLeaves, *futures: asyncio.Future) -> None:
    """Release the stub and let what waited on it end, so nothing outlives the test."""
    start.release()
    live = {f for f in futures if f is not None}
    if live:
        await asyncio.wait(live, timeout=_WITHIN)
    for future in live:
        if future.done() and not future.cancelled():
            future.exception()


# ── the helper ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_task_that_never_leaves_is_named_and_left_behind(short_grace, caplog):
    start = _StartThatNeverLeaves()
    task = asyncio.ensure_future(start())
    await start.entered.wait()
    waiting = asyncio.ensure_future(cancel_and_wait([task], what="the fixture stop"))
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
            assert await _finished(waiting), "the wait on a task that never leaves never ended"
        assert waiting.result() == {task}
        assert start.cancels == 1 and not task.done(), "it was cancelled once, and left running"
        assert "the fixture stop" in caplog.text and "_StartThatNeverLeaves" in caplog.text
    finally:
        await _let_go(start, task, waiting)


@pytest.mark.asyncio
async def test_a_task_that_leaves_on_its_cancel_is_waited_for(short_grace, caplog):
    task = asyncio.ensure_future(asyncio.sleep(60))
    with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
        assert await cancel_and_wait([task, None], what="the fixture stop") == set()
    assert task.cancelled()
    assert "the fixture stop" not in caplog.text


@pytest.mark.asyncio
async def test_a_task_already_done_is_not_cancelled_and_its_error_is_taken(short_grace, caplog):
    async def fails() -> None:
        raise ValueError("the fixture task failed")

    failed = asyncio.ensure_future(fails())
    await asyncio.wait({failed})
    with caplog.at_level(logging.DEBUG, logger="personalclaw.cancellation"):
        assert await cancel_and_wait([failed], what="the fixture stop") == set()
    assert not failed.cancelled()
    assert "the fixture task failed" in caplog.text


@pytest.mark.asyncio
async def test_the_caller_being_cancelled_is_not_swallowed(monkeypatch):
    """The gateway's stop runs under its own deadline, and that deadline is a cancel of the stop.
    A wait that swallowed it would let the rest of the stop run with no deadline at all."""
    monkeypatch.setattr(cancellation, "CANCEL_GRACE_SECS", 60.0)
    start = _StartThatNeverLeaves()
    task = asyncio.ensure_future(start())
    await start.entered.wait()
    stop = asyncio.ensure_future(cancel_and_wait([task], what="the fixture stop"))
    try:
        await asyncio.sleep(0.05)
        stop.cancel()
        assert await _finished(stop, within=2.0), "the caller's cancel was held by the wait"
        assert stop.cancelled()
    finally:
        await _let_go(start, task, stop)


# ── the real stops ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_availability_board_stop_is_not_held_by_a_start_that_never_leaves(
    monkeypatch, short_grace, caplog
):
    """The gateway stop's hook (``dashboard/lifecycle_hooks.py``): the board's drain is starting the
    availability child when the stop cancels it. At ``origin/main`` the stop never returns."""
    start = _StartThatNeverLeaves()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    board = AvailabilityBoard(argv_for=lambda names: ["/nonexistent/pc-fixture-probe", *names])
    board.recheck("fixture-app")
    drain = board._task
    await asyncio.wait_for(start.entered.wait(), timeout=_WITHIN)
    stop = asyncio.ensure_future(board.shutdown())
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
            assert await _finished(stop), "the stop waited on a drain that never left"
        assert start.cancels == 1
        assert "provider availability check" in caplog.text
        assert "AvailabilityBoard._drain" in caplog.text
    finally:
        await _let_go(start, drain, stop)


@pytest.mark.parametrize("where", ["an in-flight channel message", "a background service"])
@pytest.mark.asyncio
async def test_the_gateway_stop_is_not_held_by_a_task_that_never_leaves(short_grace, caplog, where):
    """The gateway's own stop, with every service it would stop absent but the one stuck task.
    At ``origin/main`` it never returns."""
    orch = _make_orchestrator()
    orch.heartbeat_svc = None
    orch.inbox_svc = None
    orch.subagent_mgr = None
    orch.sessions = None
    orch.dashboard_state = None
    orch._dashboard_runner = None
    start = _StartThatNeverLeaves()
    stuck = asyncio.ensure_future(start())
    if where == "an in-flight channel message":
        orch._handler_tasks.add(stuck)
    else:
        orch._clock_task = stuck
    await start.entered.wait()
    stop = asyncio.ensure_future(orch._shutdown())
    try:
        with caplog.at_level(logging.WARNING, logger="personalclaw.cancellation"):
            assert await _finished(stop), f"the gateway stop waited on {where} that never left"
        stop.result()
        assert start.cancels == 1
        assert "_StartThatNeverLeaves" in caplog.text
    finally:
        await _let_go(start, stuck, stop)


# ── the rail: no unbounded wait on a task the same function cancelled ─────────────────────────

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

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visit(tree, path.relative_to(root).as_posix(), "")
    return found


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
        for path in _SRC.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and _call_name(node) == "cancel_and_wait"
    ]
    assert len(calls) >= 12, len(calls)
