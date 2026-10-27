"""No security-log write runs on the event loop: async code writes through ``sel_log_async``
(#5605).

``SecurityEventLog.log`` is disk work: the log's file opened, appended to and closed, and on a
home's first event its key made with an fsync. Called directly in an ``async def`` it runs on the
event loop, and every other request waits while the disk does: up to 0.343 s beside a large
upload on a Linux runner, most of it the key's fsync. ``sel_log_async`` runs the write in a worker
thread and is awaited, so the event is on disk before the caller answers.

The rail reads every package module and names each call that writes the log directly inside an
``async def`` (not inside a ``def`` or a ``lambda`` nested in one, which a thread may run): a
method of the log reached through ``sel()`` or ``_sel()``, or a module's ``_sel_log`` helper. Each
site is ``path::function::callee``, repeats in one function told apart. The ones that were there
when the rail came are in :data:`BASELINE`, each with why; a new one fails by name, and the list
only shrinks, file by file, as #5605 converts them.

Moving a write off the loop puts an ``await`` where there was none. Under a ``with`` over a lock
that blocks (``write_lock``, the heartbeat queue's ``flock``, the memory documents' lock, a
``threading`` lock), that await holds the lock while the loop runs other work, and loop code that
takes the same lock then blocks the loop thread with the holder unable to resume. So no ``await``
in an ``async def`` may sit inside a sync ``with`` whose context names a lock (``lock``,
``hold_``, ``flock``, ``mutex`` or ``semaphore`` in its text). ``async with`` is an asyncio
lock's, which an await may hold. There were none when this came, so it has no baseline.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator

import site_baseline
import source_index

BASELINE = "tests/rails/baselines/security-log-writes-on-the-loop.sites.jsonl"

#: What reaches the log: the accessors whose result is the log, and the helpers that write it.
_ACCESSORS = frozenset({"sel", "_sel", "SecurityEventLog"})
_HELPERS = frozenset({"_sel_log"})


def _writes_the_log(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in _HELPERS
    if not (isinstance(func, ast.Attribute) and func.attr.startswith("log")):
        return False
    base = func.value
    if not isinstance(base, ast.Call):
        return False
    name = base.func.id if isinstance(base.func, ast.Name) else getattr(base.func, "attr", "")
    return name in _ACCESSORS


def _on_the_loop(tree: ast.AST) -> Iterator[tuple[str, ast.Call]]:
    """Each call that writes the log directly in an ``async def``, with that function's
    qualified name."""

    def walk(node: ast.AST, scope: str, is_async: bool) -> Iterator[tuple[str, ast.Call]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
                yield from walk(child, inner, isinstance(child, ast.AsyncFunctionDef))
            elif isinstance(child, ast.Lambda):
                yield from walk(child, scope, False)
            else:
                if is_async and isinstance(child, ast.Call) and _writes_the_log(child):
                    yield scope, child
                yield from walk(child, scope, is_async)

    yield from walk(tree, "<module>", False)


def sites(trees: list[tuple[str, ast.AST]]) -> list[str]:
    """The sites of :func:`_on_the_loop` over *trees*, ``(path, tree)`` pairs, in order."""
    return site_baseline.numbered(
        site_baseline.code_site(rel, scope, call.func)
        for rel, tree in trees
        for scope, call in _on_the_loop(tree)
    )


#: A sync ``with`` whose context names a lock that blocks.
_LOCKISH = re.compile(r"lock|hold_|flock|mutex|semaphore", re.IGNORECASE)


def awaits_under_a_sync_lock(rel: str, tree: ast.AST) -> list[str]:
    """Each ``await`` directly in an ``async def`` (not in a ``def`` or ``lambda`` nested in it)
    inside a sync ``with`` over a lock, as ``path:line function under <lock>``."""
    found: list[str] = []

    def walk(node: ast.AST, scope: str, is_async: bool, held: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
                walk(child, inner, isinstance(child, ast.AsyncFunctionDef), [])
            elif isinstance(child, ast.Lambda):
                walk(child, scope, False, [])
            elif isinstance(child, ast.With):
                locks = [
                    ast.unparse(item.context_expr)
                    for item in child.items
                    if _LOCKISH.search(ast.unparse(item.context_expr))
                ]
                for item in child.items:
                    walk(item, scope, is_async, held)
                for statement in child.body:
                    walk(
                        ast.Module(body=[statement], type_ignores=[]), scope, is_async, held + locks
                    )
            else:
                if is_async and held and isinstance(child, ast.Await):
                    found.append(f"{rel}:{child.lineno} {scope} under {held[-1]}")
                walk(child, scope, is_async, held)

    walk(tree, "<module>", False, [])
    return found


def test_no_new_security_log_write_runs_on_the_event_loop() -> None:
    found = sites([(source_index.rel(path), tree) for path, tree in source_index.package_trees()])
    assert found, "the scan found no security-log write at all: it is reading nothing"
    site_baseline.check(found, BASELINE)


_PLANTED = """
async def handler(request):
    sel().log_api_access(caller="x", operation="y", outcome="success")
    _sel().log_tool_invocation(session_key="s", tool_name="t", outcome="success")
    _sel_log("item.create", item_id=1)
    await sel_log_async(sel().log_api_access, caller="x", operation="y", outcome="success")
    await sel_log_async(_sel_log, "item.create", item_id=1)
    await sel_log_async(lambda: sel().log_api_access(caller="x", operation="y", outcome="ok"))

    def in_a_thread():
        sel().log_api_access(caller="x", operation="y", outcome="success")

    await asyncio.to_thread(in_a_thread)


def sync_caller():
    sel().log_api_access(caller="x", operation="y", outcome="success")
"""


def test_a_planted_write_on_the_loop_is_named_and_one_through_a_thread_is_not() -> None:
    found = sites([("planted.py", ast.parse(_PLANTED))])
    assert found == [
        "planted.py::handler::sel().log_api_access",
        "planted.py::handler::_sel().log_tool_invocation",
        "planted.py::handler::_sel_log",
    ], found


def test_a_new_write_on_the_loop_fails_the_baseline_by_name() -> None:
    found = sites([(source_index.rel(path), tree) for path, tree in source_index.package_trees()])
    planted = "src/personalclaw/planted.py::handler::sel().log_api_access"
    try:
        site_baseline.check([*found, planted], BASELINE, None, prune=False)
    except AssertionError as exc:
        assert planted in str(exc), exc
    else:
        raise AssertionError("a write on the loop the baseline does not list passed")


def test_no_await_in_async_code_holds_a_sync_lock() -> None:
    trees = [(source_index.rel(path), tree) for path, tree in source_index.package_trees()]
    assert trees, "the scan read no module"
    found = [site for rel, tree in trees for site in awaits_under_a_sync_lock(rel, tree)]
    assert found == [], (
        "an await inside a sync with over a lock holds that lock while the loop runs other work: "
        "do the awaited work before taking the lock or after letting it go\n" + "\n".join(found)
    )


_PLANTED_LOCKS = """
async def save(request, path):
    with write_lock(path):
        head = read_head(path)
        if head is None:
            await sel_log_async(sel().log_api_access, caller="x", operation="y", outcome="z")
    await sel_log_async(sel().log_api_access, caller="x", operation="y", outcome="ok")
    with self._lock:
        for item in items:
            await item.flush()
    with heartbeat.hold_queue(), open(path) as f:
        await asyncio.sleep(0)
    async with self._async_lock:
        await item.flush()
    with open(path) as f:
        await asyncio.sleep(0)
    with self._lock:

        async def later():
            await item.flush()

        task = later

    def in_a_thread():
        with self._lock:
            write()


def sync_caller():
    with self._lock:
        write()
"""


def test_a_planted_await_under_a_sync_lock_is_named_and_the_safe_ones_are_not() -> None:
    found = awaits_under_a_sync_lock("planted.py", ast.parse(_PLANTED_LOCKS))
    assert found == [
        "planted.py:6 save under write_lock(path)",
        "planted.py:10 save under self._lock",
        "planted.py:12 save under heartbeat.hold_queue()",
    ], found
