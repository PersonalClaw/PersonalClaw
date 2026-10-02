"""No GET route waits on a model.

A browser keeps six HTTP/1.1 connections to the gateway, shared by every tab. A GET that waits on
a model holds one of them for as long as the model takes, and a busy local model takes tens of
seconds per answer, one background chore at a time. The chat's organize chip re-read its proposal
after every turn and each read waited for its own model answer, until seven held every connection
and the chat's own send sat 138 s inside the browser. So a read answers at once with what is
known, and the model's work runs in the background and is announced over the socket when it lands.

The rail reads the source. Every route registered for GET under ``src/personalclaw`` (an
``add_get``, an ``add_route("GET", …)``, a ``web.get``) is resolved to its handler as Python
resolves it, and every call the handler awaits is followed into the function it names: plain
functions, methods on ``self`` and on annotated locals, a class built in place, a function
returning an annotated class, a nested coroutine, and the coroutines handed to
``asyncio.wait_for`` and its siblings. A handler waits on a model when the walk reaches

* ``async for … in <x>.stream(…)``: a model's answer streaming in;
* ``await <x>.sessions.get_or_create(…)``: taking a model session, which waits for whatever turn
  or chore holds it;
* a function in :data:`RUNS_MODELS_BY_DISPATCH`: model work chosen at run time, which no name in
  the source leads to.

A handler shared by GET and POST is walked along its GET branch only.

The walk skips what it cannot resolve, so the rail proves its reach on the real tree: it resolves
every GET handler, the same walk over the POST routes finds the ones that wait on a model by
design, and a handler shaped like the read that held the browser is caught through its helper.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import personalclaw

SRC = Path(personalclaw.__file__).parent

#: Functions that run model work chosen at run time, where no name in the source leads the walk to
#: the model: the knowledge graph runs whichever nodes the item's type needs, and its readers ask
#: an image model, a speech model or the chat model.
RUNS_MODELS_BY_DISPATCH = frozenset(
    {"personalclaw.knowledge.pipeline.executor.PipelineExecutor.run"}
)

#: The walk follows only the product's own code; the leaves are all in it.
_OWN = "personalclaw"

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _dotted(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted(expr.value)
        return f"{base}.{expr.attr}" if base else None
    return None


def _follow(obj: Any, attrs: list[str]) -> Any:
    for attr in attrs:
        try:
            obj = getattr(obj, attr)
        except Exception:  # noqa: BLE001 — an unresolvable name is skipped, never fatal
            return None
    return obj


def _imports_in(node: ast.AST, module: str) -> dict[str, Any]:
    """The names the imports inside *node* bind, resolved as Python would."""
    bound: dict[str, Any] = {}
    for imp in ast.walk(node):
        if isinstance(imp, ast.ImportFrom):
            source = imp.module or ""
            if imp.level:
                package = module.rsplit(".", imp.level)[0]
                source = f"{package}.{source}" if source else package
            try:
                mod = importlib.import_module(source)
            except Exception:  # noqa: BLE001
                continue
            for alias in imp.names:
                value = getattr(mod, alias.name, None)
                if value is None:
                    try:
                        value = importlib.import_module(f"{source}.{alias.name}")
                    except Exception:  # noqa: BLE001
                        continue
                bound[alias.asname or alias.name] = value
        elif isinstance(imp, ast.Import):
            for alias in imp.names:
                try:
                    mod = importlib.import_module(alias.name)
                except Exception:  # noqa: BLE001
                    continue
                if alias.asname:
                    bound[alias.asname] = mod
                else:
                    head = alias.name.split(".")[0]
                    bound[head] = importlib.import_module(head)
    return bound


def _own_nodes(stmts: list[ast.stmt]) -> Iterator[ast.AST]:
    """Every node of *stmts*, without descending into a nested function, lambda or class: its
    body runs only when it is called, which the walk follows where the call is awaited."""
    stack: list[ast.AST] = list(reversed(stmts))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (*_DEFS, ast.Lambda, ast.ClassDef)):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _resolve(dotted: str) -> Any:
    """The object a fully qualified *dotted* name names: its longest importable module, then
    attributes."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        return _follow(module, parts[cut:])
    return None


def _method_test(test: ast.expr) -> bool | None:
    """Whether an ``if`` on ``request.method`` takes its body for a GET, or None for any other
    test: ``== "GET"`` and ``!= "POST"`` do, ``!= "GET"`` and ``== "POST"`` do not."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return None
    left, op, right = test.left, test.ops[0], test.comparators[0]
    if isinstance(left, ast.Constant):
        left, right = right, left
    if not (isinstance(left, ast.Attribute) and left.attr == "method"):
        return None
    if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(right, ast.Constant):
        verbs = {right.value}
        negated = isinstance(op, ast.NotEq)
    elif isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
        verbs = {e.value for e in right.elts if isinstance(e, ast.Constant)}
        negated = isinstance(op, ast.NotIn)
    else:
        return None
    return ("GET" in verbs) != negated


def _ends(stmts: list[ast.stmt]) -> bool:
    return bool(stmts) and isinstance(stmts[-1], (ast.Return, ast.Raise))


def _get_branch(stmts: list[ast.stmt]) -> list[ast.stmt]:
    """The statements a GET runs through a handler that also serves another verb."""
    out: list[ast.stmt] = []
    for stmt in stmts:
        takes = _method_test(stmt.test) if isinstance(stmt, ast.If) else None
        if takes is None:
            out.append(stmt)
            continue
        branch = stmt.body if takes else stmt.orelse
        out.extend(_get_branch(branch))
        if _ends(branch):
            return out
    return out


@dataclass
class _Scope:
    names: dict[str, Any]
    cls: type | None
    typed: dict[str, type]
    nested: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)


def _type_of(annotation: ast.expr | None, names: dict[str, Any]) -> type | None:
    if annotation is None:
        return None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
    dotted = _dotted(annotation)
    if not dotted:
        return None
    head, *rest = dotted.split(".")
    found = _follow(names.get(head), rest) if head in names else None
    return found if isinstance(found, type) else None


def _scope_for(fn: Any, top: ast.FunctionDef | ast.AsyncFunctionDef) -> _Scope:
    names = dict(getattr(fn, "__globals__", {}) or {})
    names.update(_imports_in(top, fn.__module__))
    cls = None
    qualname = getattr(fn, "__qualname__", "")
    if "." in qualname and "<locals>" not in qualname:
        owner = _follow(sys.modules.get(fn.__module__), qualname.split(".")[:-1])
        cls = owner if isinstance(owner, type) else None
    typed: dict[str, type] = {}
    arguments = top.args.posonlyargs + top.args.args + top.args.kwonlyargs
    for arg in arguments:
        found = _type_of(arg.annotation, names)
        if found is not None:
            typed[arg.arg] = found
    for node in _own_nodes(top.body):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found = _type_of(node.annotation, names)
            if found is not None:
                typed[node.target.id] = found
    nested = {n.name: n for n in ast.walk(top) if isinstance(n, _DEFS) and n is not top}
    return _Scope(names, cls, typed, nested)


def _target(func: ast.expr, scope: _Scope) -> Any:
    """What *func* names, or None when the source alone cannot say."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
        made = _target(func.value.func, scope)
        if inspect.isfunction(made):
            # A factory: what it builds is what its return annotation names.
            parsed = _def_of(made)
            made = _type_of(parsed[1].returns, dict(made.__globals__)) if parsed else None
        return getattr(made, func.attr, None) if isinstance(made, type) else None
    dotted = _dotted(func)
    if not dotted:
        return None
    head, *rest = dotted.split(".")
    if not rest and head in scope.nested:
        return scope.nested[head]
    if head in ("self", "cls") and scope.cls is not None:
        return _follow(scope.cls, rest)
    if head in scope.typed and rest:
        return _follow(scope.typed[head], rest)
    if head in scope.names:
        return _follow(scope.names[head], rest)
    return None


def _def_of(fn: Any) -> tuple[ast.Module, ast.FunctionDef | ast.AsyncFunctionDef] | None:
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return None
    top = tree.body[0] if tree.body else None
    return (tree, top) if isinstance(top, _DEFS) else None


def _qualified(fn: Any) -> str:
    return f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', '?')}"


class _Walk:
    """Whether a function waits on a model, and through which calls."""

    def __init__(self) -> None:
        self._seen: dict[Any, list[str] | None] = {}

    def of(self, fn: Any, *, get_branch: bool = False) -> list[str] | None:
        fn = inspect.unwrap(getattr(fn, "__func__", fn))
        key = (fn, get_branch)
        if key in self._seen:
            return self._seen[key]
        self._seen[key] = None  # a cycle reads as "no model", and is re-read nowhere
        if _qualified(fn) in RUNS_MODELS_BY_DISPATCH:
            found: list[str] | None = [_qualified(fn)]
        else:
            parsed = _def_of(fn) if str(getattr(fn, "__module__", "")).startswith(_OWN) else None
            found = None
            if parsed is not None:
                _tree, top = parsed
                body = _get_branch(top.body) if get_branch else top.body
                chain = self._body(body, _scope_for(fn, top), set())
                found = [_qualified(fn), *chain] if chain else None
        self._seen[key] = found
        return found

    def _body(self, stmts: list[ast.stmt], scope: _Scope, nested_seen: set[str]) -> list[str]:
        for node in _own_nodes(stmts):
            if isinstance(node, ast.AsyncFor) and isinstance(node.iter, ast.Call):
                func = node.iter.func
                if isinstance(func, ast.Attribute) and func.attr == "stream":
                    return [f"async for … in {ast.unparse(func)}(…)"]
            if not (isinstance(node, ast.Await) and isinstance(node.value, ast.Call)):
                continue
            handed = [*node.value.args, *(k.value for k in node.value.keywords)]
            for call in [node.value, *(a for a in handed if isinstance(a, ast.Call))]:
                func = call.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get_or_create"
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "sessions"
                ):
                    return [f"await {ast.unparse(func)}(…)"]
                target = _target(func, scope)
                if isinstance(target, _DEFS):
                    if target.name in nested_seen:
                        continue
                    chain = self._body(target.body, scope, nested_seen | {target.name})
                    if chain:
                        return [target.name, *chain]
                elif inspect.isfunction(target) or inspect.ismethod(target):
                    chain = self.of(target)
                    if chain:
                        return chain
        return []


@dataclass(frozen=True)
class _Route:
    verb: str
    path: str
    where: str
    handler: Any


def _registrations(verb: str) -> tuple[list[_Route], list[str]]:
    """Every route registered for *verb* under ``src/personalclaw``, and what failed to resolve."""
    adds = {"GET": "add_get", "POST": "add_post"}
    routes: list[_Route] = []
    unresolved: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        if not any(verb_name in text for verb_name in (adds[verb], "add_route", "web.")):
            continue
        tree = ast.parse(text)
        parent = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name, args = node.func.attr, node.args
            if name == adds[verb] and len(args) >= 2:
                path, handler = args[0], args[1]
            elif name == "add_route" and len(args) >= 3 and isinstance(args[0], ast.Constant):
                if args[0].value not in (verb, "*"):
                    continue
                path, handler = args[1], args[2]
            elif name == verb.lower() and _dotted(node.func.value) == "web" and len(args) >= 2:
                path, handler = args[0], args[1]
            else:
                continue
            module = ".".join(py.relative_to(SRC.parent).with_suffix("").parts)
            module = module.removesuffix(".__init__")
            where = f"{py.relative_to(SRC.parent)}:{node.lineno}"
            names = dict(vars(importlib.import_module(module)))
            up: ast.AST | None = node
            while up in parent:
                up = parent[up]
                if isinstance(up, _DEFS):
                    names.update(_imports_in(up, module))
            dotted = _dotted(handler)
            head, *rest = (dotted or "").split(".")
            fn = _follow(names.get(head), rest) if dotted and head in names else None
            if not callable(fn):
                unresolved.append(f"{where} {ast.unparse(handler)}")
                continue
            routes.append(_Route(verb, ast.unparse(path), where, fn))
    return routes, unresolved


def _waiting(routes: list[_Route], verb: str) -> dict[str, list[str]]:
    walk = _Walk()
    return {
        f"{r.verb} {r.path} ({r.where})": chain
        for r in routes
        if (chain := walk.of(r.handler, get_branch=verb == "GET"))
    }


def test_no_get_route_waits_on_a_model() -> None:
    routes, unresolved = _registrations("GET")
    assert len(routes) >= 300, f"only {len(routes)} GET routes found — the scan is broken"
    assert unresolved == [], f"GET handlers the rail cannot see into: {unresolved}"
    waiting = _waiting(routes, "GET")
    assert waiting == {}, "\n".join(
        f"{route} waits on a model: {' -> '.join(chain)}" for route, chain in waiting.items()
    )


def test_the_walk_finds_the_post_routes_that_wait_on_a_model_by_design() -> None:
    """The positive control on the real tree: a title being written and a prompt being optimized
    wait on a model, and the same walk must find both, through their helpers."""
    routes, _unresolved = _registrations("POST")
    found = {key.split(" (")[0] for key in _waiting(routes, "POST")}
    assert "POST '/api/chat/sessions/{session}/generate-title'" in found, sorted(found)
    assert "POST '/api/optimizer/optimize'" in found, sorted(found)


def test_every_dispatcher_it_names_is_real() -> None:
    """A renamed dispatcher would leave the rail blind to the reads that reach it."""
    for dotted in RUNS_MODELS_BY_DISPATCH:
        fn = _resolve(dotted)
        assert inspect.isfunction(fn) and _qualified(fn) == dotted, dotted


_SHAPES = """
import asyncio


class Session:
    async def stream(self, prompt):
        yield prompt


client = Session()


async def ask_the_model(prompt):
    text = ""
    async for chunk in client.stream(prompt):
        text += chunk
    return text


async def a_read_that_waits(request):
    return await asyncio.wait_for(ask_the_model("sort this chat"), timeout=60)


async def a_read_that_answers_at_once(request):
    asyncio.get_running_loop().create_task(ask_the_model("sort this chat"))
    return None


async def one_route_for_both_verbs(request):
    if request.method == "GET":
        return None
    return await ask_the_model("sort this chat")


async def a_get_branch_that_waits(request):
    if request.method != "GET":
        return None

    async def nested():
        return await ask_the_model("sort this chat")

    return await nested()
"""


@pytest.fixture
def shapes(tmp_path, monkeypatch):
    """The shapes as a module of their own. Its name starts like the product's, because the walk
    follows only the product's own code."""
    (tmp_path / "personalclaw_rail_shapes.py").write_text(_SHAPES, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module("personalclaw_rail_shapes")
    yield module
    sys.modules.pop("personalclaw_rail_shapes", None)


def test_a_read_shaped_like_the_one_that_held_the_browser_is_caught(shapes) -> None:
    walk = _Walk()
    assert walk.of(shapes.a_read_that_waits, get_branch=True), "a read that waits went unseen"
    assert walk.of(shapes.a_get_branch_that_waits, get_branch=True), "a nested wait went unseen"
    assert not walk.of(shapes.a_read_that_answers_at_once, get_branch=True)
    assert not walk.of(shapes.one_route_for_both_verbs, get_branch=True)
    assert walk.of(shapes.one_route_for_both_verbs), "its POST branch does wait"
