"""Every reader of workflow runs asks whose run it reads, or is listed with why it need not.

A chat's own run is that chat's: a batch of its helpers, or a run a Temporary or Incognito chat
started. ``workflows.chat_runs`` holds the rule (``reads``, ``reads_id``), and a door that shows
runs to someone asks it. A reader that does not ask can let a chat's run reach what is no chat's.
One door fixed is one door; this rail is the class.

It reads every package module and names each function that reads runs from a store: a call of
``list_runs``, ``active_runs``, ``all_runs``, ``started_runs``, ``runs_with_mode`` or ``get_run``
on anything, or of ``get`` on the workflow store module (``personalclaw.workflows.store``, under
whatever name the module binds it). A function that asks the rule passes: it calls ``reads``,
``reads_id``, ``hidden`` or ``live_runs_read``, or it is a route the ``_of_its_chat`` guard wraps.
Every other one is listed in :data:`BASELINE`, one line per function (``path::function``) with its
reason: its own run, counts or ids only, the engine's or a sweep's own maintenance, another store's
runs, or the door that asks for it. A new reader fails by name until it asks or is listed, and the
list only shrinks.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

import site_baseline
import source_index

BASELINE = "tests/rails/baselines/run-readers.sites.jsonl"

#: The workflow store module, whose ``get`` reads one run.
_STORE = "personalclaw.workflows.store"
#: Calls that read runs, on whatever holds them.
_READERS = frozenset(
    {"list_runs", "active_runs", "all_runs", "started_runs", "runs_with_mode", "get_run"}
)
#: The rule's questions (``workflows.chat_runs``): a function that calls one asks whose run it is.
_ASKS = frozenset({"reads", "reads_id", "hidden", "live_runs_read"})
#: A route decorated with one asks before its body runs (``workflows.handlers._of_its_chat``).
_GUARDS = frozenset({"_of_its_chat"})
#: Where runs are kept and the rule is defined: their own readers are the store and the rule.
_OWN = frozenset({"src/personalclaw/workflows/store.py"})


def _store_names(tree: ast.AST) -> set[str]:
    """The names a module binds the workflow store module to."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.asname for alias in node.names if alias.name == _STORE and alias.asname}
        elif isinstance(node, ast.ImportFrom) and node.module == "personalclaw.workflows":
            names |= {alias.asname or alias.name for alias in node.names if alias.name == "store"}
    return names


def _called(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _reads_runs(call: ast.Call, store_names: set[str]) -> bool:
    name = _called(call)
    if name in _READERS:
        return True
    func = call.func
    return (
        name == "get"
        and isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in store_names
    )


def _guarded(node: ast.AST) -> bool:
    decorators = getattr(node, "decorator_list", [])
    return any(ast.unparse(d).split(".")[-1].split("(")[0] in _GUARDS for d in decorators)


def _functions(tree: ast.AST) -> Iterator[tuple[str, list[ast.Call], bool]]:
    """Each function or method with the calls directly in it (a nested function's are its own),
    by qualified name, and whether a guard asks before it runs; the module's top level as
    ``<module>``."""

    def walk(node: ast.AST, scope: str, calls: list[ast.Call]) -> Iterator[tuple[str, list, bool]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name if scope == "<module>" else f"{scope}.{child.name}"
                mine: list[ast.Call] = []
                yield from walk(child, inner, mine)
                yield inner, mine, _guarded(child)
            else:
                if isinstance(child, ast.Call):
                    calls.append(child)
                yield from walk(child, scope, calls)

    top: list[ast.Call] = []
    yield from walk(tree, "<module>", top)
    yield "<module>", top, False


def readers(trees: list[tuple[str, ast.AST]]) -> list[str]:
    """Each function in *trees* (``(path, tree)`` pairs) that reads runs and asks no question of
    the rule, as ``path::function``."""
    found: list[str] = []
    for rel, tree in trees:
        if rel in _OWN:
            continue
        store_names = _store_names(tree)
        for scope, calls, guarded in _functions(tree):
            asks = guarded or any(_called(call) in _ASKS for call in calls)
            if not asks and any(_reads_runs(call, store_names) for call in calls):
                found.append(f"{rel}::{scope}")
    return sorted(set(found))


def _tree() -> list[tuple[str, ast.AST]]:
    return [(source_index.rel(path), tree) for path, tree in source_index.package_trees()]


def test_every_run_reader_asks_whose_run_it_is_or_is_listed_with_why() -> None:
    found = readers(_tree())
    assert len(found) > 50, f"the scan found {len(found)} run readers: it is reading nothing"
    site_baseline.check(found, BASELINE)


_PLANTED = """
from personalclaw.workflows import store as wf_store
from personalclaw.workflows import chat_runs


def leaks(state):
    runs, _total = wf_store.list_runs(limit=50)
    return [run.error for run in runs]


def one(run_id):
    return wf_store.get(run_id)


def asks(state, reader):
    return [run for run in wf_store.active_runs() if chat_runs.reads(reader, run)]


def asks_by_id(reader, run_id):
    return chat_runs.reads_id(reader, run_id) and wf_store.get(run_id)


@_of_its_chat
async def guarded_route(request):
    return wf_store.get(request.match_info["run_id"])


class Card:
    def runs(self, store):
        return store.list_runs(workflow_name="x", limit=5)


def unrelated(cache):
    return cache.get("x")
"""


def test_a_planted_reader_is_named_and_one_that_asks_is_not() -> None:
    found = readers([("planted.py", ast.parse(_PLANTED))])
    assert found == ["planted.py::Card.runs", "planted.py::leaks", "planted.py::one"], found


def test_a_new_reader_fails_the_baseline_by_name() -> None:
    planted = "src/personalclaw/planted.py::leaks"
    try:
        site_baseline.check([*readers(_tree()), planted], BASELINE, None, prune=False)
    except AssertionError as exc:
        assert planted in str(exc), exc
    else:
        raise AssertionError("a run reader the baseline does not list passed")
