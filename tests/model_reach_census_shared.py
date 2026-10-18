"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_model_reach_census.py``

Its checks:
``tests/rails/test_model_reach_census_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

#: What asking looks like: the one question, its raising form, the model the work stays on, or
#: the media tools' helper that asks it — each spelled as the seam spells it, so a name another
#: object happens to carry (a provider entry's ``own_model``) is not taken for asking.
ASKS = frozenset(
    {
        "memory_writes.model_may_read",
        "memory_writes.require_model",
        "memory_writes.own_model",
        "_other_model_refusal",
    }
)


def plain_pools(tree: ast.Module) -> list[int]:
    """Lines that make a worker pool which does not carry the caller's context: a plain
    ``ThreadPoolExecutor`` however it is spelled or aliased, or a class built on it other than
    the one that carries the scope."""
    aliases = {"ThreadPoolExecutor"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "concurrent.futures":
            aliases |= {a.asname or a.name for a in node.names if a.name == "ThreadPoolExecutor"}
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id in aliases) or (
                isinstance(func, ast.Attribute) and func.attr == "ThreadPoolExecutor"
            ):
                found.append(node.lineno)
        elif isinstance(node, ast.ClassDef) and node.name != "ScopeCarryingExecutor":
            for base in node.bases:
                named = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                if named in aliases or named == "ThreadPoolExecutor":
                    found.append(node.lineno)
    return found


def scope_carrying_pools(tree: ast.Module) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ScopeCarryingExecutor"
    )


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Every function in the module by its qualified name (``Class.method`` for a method)."""
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{item.name}"] = item
    return out


def names_used(node: ast.AST) -> set[str]:
    """Every name the function reads or calls: each bare name, each attribute name, and each
    attribute with the name it is read from (``memory_writes.model_may_read``)."""
    used: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            used.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            used.add(sub.attr)
            if isinstance(sub.value, ast.Name):
                used.add(f"{sub.value.id}.{sub.attr}")
    return used


def direct_embeds(tree: ast.Module) -> list[int]:
    """Lines that call a provider's embedding (``.embed``, ``.embed_batch``, ``.get_embed_fn``)
    on anything but ``self``: a holder's own wrapper method calls through the seam."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"embed", "embed_batch", "get_embed_fn"}
        and not (isinstance(node.func.value, ast.Name) and node.func.value.id == "self")
    ]


def registry_builds(tree: ast.Module) -> list[int]:
    """Lines that build a model provider from the model registry: ``registry.build(...)`` or
    ``get_default_registry().build(...)``."""
    found: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "build"
        ):
            continue
        recv = node.func.value
        if (isinstance(recv, ast.Name) and recv.id == "registry") or (
            isinstance(recv, ast.Call)
            and isinstance(recv.func, ast.Name)
            and recv.func.id == "get_default_registry"
        ):
            found.append(node.lineno)
    return found


def media_generators(tree: ast.Module) -> list[str]:
    """Functions that resolve the bound image or video model and generate with it."""
    out: list[str] = []
    for name, fn in _functions(tree).items():
        used = names_used(fn)
        if used & {"active_image_gen", "active_video_gen"} and used & {"generate", "edit"}:
            out.append(name)
    return out


def reads_their_input(tree: ast.Module) -> list[str]:
    """Functions that run work as reading what the person gave the chat."""
    return [
        name
        for name, fn in _functions(tree).items()
        if "memory_writes.reading_their_input" in names_used(fn)
    ]


def stdio_dispatches(tree: ast.Module) -> list[str]:
    """The function each stdio tool server started in the module hands its calls to."""
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        named = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if named != "run_mcp_stdio_loop":
            continue
        dispatch = node.args[3] if len(node.args) > 3 else None
        for kw in node.keywords:
            if kw.arg == "call_tool_fn":
                dispatch = kw.value
        out.append(ast.unparse(dispatch) if dispatch is not None else "")
    return out
