"""No ``await`` inside a walk over shared state: an ``async def`` walks a copy.

An ``await`` lets every other request run before the next line. Inside a ``for`` over a live
dict view (``state._sessions.items()``), the next step of the walk then reads a dict other
requests may have changed meanwhile: an entry added or removed raises ``RuntimeError: dictionary
changed size during iteration``, and the rest of the walk never runs. #5605's conversion of the
chat mode switch did just that. Its per-ask security-log row, awaited inside the walk over the
open chats, let a chat opened or closed under it end the switch in a 500, with asks left
unanswered. Code that was one step on the loop became many.

The rail reads every package module. In each ``async def`` (not a ``def``, ``lambda`` or class
nested in one), it names each ``for``, and each comprehension, whose body suspends (an ``await``,
``async for`` or ``async with``) while it walks a live collection of something the function does
not own:

* ``X.items()``, ``X.values()`` or ``X.keys()``,
* or a bare attribute, ``X.attr``,

where ``X`` starts from a name that is not local to the function, seen through ``enumerate``,
``reversed`` and ``zip``. A name is local when the function binds it, and only ever to a new
container: a literal, a comprehension, or ``list``, ``tuple``, ``sorted``, ``set``, ``frozenset``,
``dict``, ``defaultdict``, ``OrderedDict``, ``Counter`` or ``deque`` of something. A parameter,
``self``, a module's global, and a name bound to anything else are not local: an alias such as
``state = request.app["state"]`` is the shared object under another name. A walk over a copy
(``list(...)``, ``tuple(...)``, ``sorted(...)``, ``set(...)``, ``frozenset(...)``, ``dict(...)``)
passes, as does a walk with no suspension in it.

Each site is ``path::function::<what the loop walks>``, repeats in one function told apart.
The ones that were there when the rail came are in :data:`BASELINE`, each with why it is safe or
what would make it fail. A new one fails by name, and the list only shrinks.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

import site_baseline
import source_index

BASELINE = "tests/rails/baselines/awaits-in-walks-over-shared-state.sites.jsonl"

#: A call that walks a copy of what it is given.
_COPIES = frozenset({"list", "tuple", "sorted", "set", "frozenset", "dict"})
#: A call whose result is a new container, so a name bound to it is the function's own.
_NEW_CONTAINERS = _COPIES | {"defaultdict", "OrderedDict", "Counter", "deque"}
#: A call that walks what it is given as it is, so the walk is as live as its argument.
_SEE_THROUGH = frozenset({"enumerate", "reversed", "zip"})
#: A dict's live views.
_VIEWS = frozenset({"items", "values", "keys"})
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _own(node: ast.AST) -> Iterator[ast.AST]:
    """Every node under *node* that runs in its own function: not inside a nested ``def``,
    ``lambda`` or class."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _NESTED):
            continue
        yield child
        yield from _own(child)


def _bound_names(target: ast.AST) -> Iterator[str]:
    """The names *target* binds (``x``, ``a, b``), not those it reads (``x[i]``, ``x.y``)."""
    for node in ast.walk(target):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.id


def _new_container(value: ast.AST | None) -> bool:
    if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple, ast.Constant)):
        return True
    if isinstance(value, (ast.ListComp, ast.DictComp, ast.SetComp)):
        return True
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in _NEW_CONTAINERS
    )


def _locals(fn: ast.AsyncFunctionDef) -> set[str]:
    """The names *fn* binds, and only ever to a new container."""
    values: dict[str, list[ast.AST | None]] = {}
    for node in _own(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                whole = node.value if isinstance(target, ast.Name) else None
                for name in _bound_names(target):
                    values.setdefault(name, []).append(whole)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            values.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            values.setdefault(node.target.id, []).append(None)
        elif isinstance(node, ast.NamedExpr):
            values.setdefault(node.target.id, []).append(node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for name in _bound_names(node.target):
                values.setdefault(name, []).append(None)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for name in _bound_names(item.optional_vars):
                        values.setdefault(name, []).append(None)
    arguments = fn.args
    parameters = {
        a.arg for a in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    } | {a.arg for a in (arguments.vararg, arguments.kwarg) if a is not None}
    return {
        name
        for name, bound in values.items()
        if name not in parameters and all(_new_container(value) for value in bound)
    }


def _root(node: ast.AST) -> str | None:
    """The name an attribute chain starts from (``state`` in ``state.a.b().c``), or None."""
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _walks_shared_state(walked: ast.AST, local: set[str]) -> bool:
    """Whether a loop over *walked* reads a live collection the function does not own."""
    if isinstance(walked, ast.Call) and isinstance(walked.func, ast.Name):
        if walked.func.id in _COPIES:
            return False
        if walked.func.id in _SEE_THROUGH:
            return any(_walks_shared_state(argument, local) for argument in walked.args)
    if (
        isinstance(walked, ast.Call)
        and isinstance(walked.func, ast.Attribute)
        and walked.func.attr in _VIEWS
        and not walked.args
    ):
        return _root(walked.func.value) not in local
    if isinstance(walked, ast.Attribute):
        return _root(walked) not in local
    return False


def _suspends(nodes: list[ast.AST]) -> bool:
    """Whether any of *nodes*, in their own function, suspends it."""
    return any(
        isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith))
        for top in nodes
        if not isinstance(top, _NESTED)
        for node in [top, *_own(top)]
    )


def _async_functions(tree: ast.AST) -> Iterator[tuple[ast.AsyncFunctionDef, str]]:
    for node, scope in site_baseline.scoped(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            yield node, (node.name if scope == "<module>" else f"{scope}.{node.name}")


def walks(rel: str, tree: ast.AST) -> Iterator[str]:
    """Each walk in *tree* that suspends over shared state, as its site."""
    for fn, scope in _async_functions(tree):
        local = _locals(fn)
        for node in _own(fn):
            if isinstance(node, ast.For):
                if _walks_shared_state(node.iter, local) and _suspends([*node.body, *node.orelse]):
                    yield site_baseline.code_site(rel, scope, node.iter)
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                parts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
                for generator in node.generators:
                    if _walks_shared_state(generator.iter, local) and _suspends(
                        [*parts, *generator.ifs]
                    ):
                        yield site_baseline.code_site(rel, scope, generator.iter)


def sites(trees: list[tuple[str, ast.AST]]) -> list[str]:
    """The sites of :func:`walks` over *trees*, ``(path, tree)`` pairs, in order."""
    return site_baseline.numbered(site for rel, tree in trees for site in walks(rel, tree))


def _package() -> list[tuple[str, ast.AST]]:
    trees = [(source_index.rel(path), tree) for path, tree in source_index.package_trees()]
    assert trees, "the scan read no module"
    return trees


def test_no_new_await_walks_shared_state() -> None:
    site_baseline.check(sites(_package()), BASELINE)


_PLANTED = """
async def api_chat_mode(request):
    state = request.app["state"]
    for key, session in state._sessions.items():
        await sel_log_async(sel().log_api_access, resources=key)
    for key, session in list(state._sessions.items()):
        await sel_log_async(sel().log_api_access, resources=key)
    for key in tuple(state._sessions):
        await notify(key)
    for key in sorted(state._sessions.keys()):
        await notify(key)
    for session in state._sessions.values():
        push(session)
    rows = []
    by_key: dict = {}
    by_key["a"] = 1
    for key, value in by_key.items():
        await notify(key)
    alias = state._sessions
    for session in alias.values():
        await notify(session)
    for index, session in enumerate(state._sessions.values()):
        await notify(index)
    for waiter in self._waiters:
        await waiter
    for session in state._sessions.values():
        async with session.lock:
            push(session)
    answers = [await ask(session) for session in state._sessions.values()]
    for session in state._sessions.values():

        async def later():
            await notify(session)

        tasks.append(later)
    for row in rows:
        await sel_log_async(sel().log_api_access, **row)


def sync_walk(state):
    for session in state._sessions.values():
        push(session)
"""


def test_the_walk_file_3s_conversion_had_is_named_and_a_copy_is_not() -> None:
    found = sites([("planted.py", ast.parse(_PLANTED))])
    assert found == [
        "planted.py::api_chat_mode::state._sessions.items()",
        "planted.py::api_chat_mode::alias.values()",
        "planted.py::api_chat_mode::enumerate(state._sessions.values())",
        "planted.py::api_chat_mode::self._waiters",
        "planted.py::api_chat_mode::state._sessions.values()",
        "planted.py::api_chat_mode::state._sessions.values() #2",
    ], found


def test_a_new_walk_fails_the_baseline_by_name() -> None:
    planted = "src/personalclaw/planted.py::handler::state._sessions.items()"
    try:
        site_baseline.check([*sites(_package()), planted], BASELINE, None, prune=False)
    except AssertionError as exc:
        assert planted in str(exc), exc
    else:
        raise AssertionError("a walk over shared state the baseline does not list passed")
