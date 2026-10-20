"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_every_model_call_writes_its_usage_row.py``

Its checks:
``tests/rails/test_every_model_call_writes_its_usage_row_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

#: A model's stream, consumed by ``async for``.
_STREAMS = {"stream", "complete"}


#: The helpers that collect a stream; each must be handed ``on_complete``, the row's writer.
_COLLECTORS = {"stream_and_collect"}


#: What writes a row: the ledger's own two doors, and the wrappers around them.
_WRITERS = {
    "recorder",
    "record_from_event",
    "_record_turn_usage",
    "_record_subagent_usage",
    "_usage_recorder",
}


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _on_self(func: ast.expr) -> bool:
    """``self.stream(...)`` / ``super().stream(...)``: a provider composing its own methods."""
    if not isinstance(func, ast.Attribute):
        return False
    target = func.value
    if isinstance(target, ast.Name):
        return target.id == "self"
    return isinstance(target, ast.Call) and _name(target.func) == "super"


#: What a site wraps its stream in to write the row of a turn that ends in an error from its
#: ``EVENT_SPENT`` (``usage_ledger.spent_rows``): the stream inside it is still the site.
_SPENT_WRAPPERS = {"spent_rows"}


#: What a site reads its stream inside, so the stream is closed when the reading stops
#: (``turn_streams.closing_stream``): the stream it is handed is the site, and the ``async for``
#: inside reads it by the name the block binds.
_CLOSERS = {"closing_stream"}


def _read_stream(node: ast.AST) -> ast.Call | None:
    """The call whose stream *node* reads: an ``async for`` over a call, or a ``closing_stream``
    block over one. ``None`` for any other node."""
    if isinstance(node, ast.AsyncFor) and isinstance(node.iter, ast.Call):
        return node.iter
    if isinstance(node, ast.AsyncWith):
        for item in node.items:
            held = item.context_expr
            if (
                isinstance(held, ast.Call)
                and _name(held.func) in _CLOSERS
                and held.args
                and isinstance(held.args[0], ast.Call)
            ):
                return held.args[0]
    return None


def _sites(source: str | ast.Module):
    """Each place in *source* that consumes a model's stream: ``(qualified name, the functions
    around it, the collector call or None for an ``async for`` over the stream itself, whether
    the stream is wrapped in :data:`_SPENT_WRAPPERS`)``."""
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    for node in ast.walk(tree):
        read = _read_stream(node)
        if read is not None:
            stream = read
            wrapped = _name(stream.func) in _SPENT_WRAPPERS and bool(stream.args)
            if wrapped and isinstance(stream.args[0], ast.Call):
                stream = stream.args[0]
            func = stream.func
            if _name(func) not in _STREAMS or not isinstance(func, ast.Attribute):
                continue
            if _on_self(func):
                continue
            collect = None
        elif isinstance(node, ast.Call) and _name(node.func) in _COLLECTORS:
            collect, wrapped = node, False
        else:
            continue
        scopes: list[ast.AST] = []
        cursor: ast.AST = node
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scopes.append(cursor)
        qualname = ".".join(reversed([getattr(s, "name", "") for s in scopes])) or "<module>"
        yield qualname, [s for s in scopes if not isinstance(s, ast.ClassDef)], collect, wrapped


def unrecorded_calls(source: str | ast.Module) -> tuple[list[str], list[str]]:
    """``(every site, the sites that write no row)`` in *source* (text or its tree), each as its
    qualified name.

    A site writes its row when a function around it calls a writer (:data:`_WRITERS`), or, for
    a collector, when it passes ``on_complete``.
    """
    sites: list[str] = []
    missing: list[str] = []
    for qualname, functions, collect, _wrapped in _sites(source):
        sites.append(qualname)
        if collect is not None:
            written = any(kw.arg == "on_complete" for kw in collect.keywords)
        else:
            written = any(
                isinstance(call, ast.Call) and _name(call.func) in _WRITERS
                for scope in functions
                for call in ast.walk(scope)
            )
        if not written:
            missing.append(qualname)
    return sites, missing


def spent_unread(source: str | ast.Module) -> list[str]:
    """The sites in *source* that consume a model's stream themselves and never read
    ``EVENT_SPENT``, neither themselves nor through :data:`_SPENT_WRAPPERS`: a turn there that
    ends in an error writes no row for the calls it made."""
    return [
        qualname
        for qualname, functions, collect, wrapped in _sites(source)
        if collect is None
        and not wrapped
        and not any(
            _name(node) == "EVENT_SPENT"
            for scope in functions
            for node in ast.walk(scope)
            if isinstance(node, (ast.Name, ast.Attribute))
        )
    ]
