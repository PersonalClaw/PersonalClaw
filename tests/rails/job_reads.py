"""What a file of the repository names, for the rails that hold a job's or a workflow's declared
paths to what it reads (``test_ci_job_inputs_tree.py``, ``test_workflow_path_filters_tree.py``).

A Python file names a path with a string literal whose first part is a tracked top-level entry,
or with a literal run joined onto a base (``root / "web" / "dist"``, ``os.path.join(base,
"web")``), read from its first part that is one. A bare top-level folder name (``"docs"``) is a
read of that folder only when it is joined or assigned to a name, so a dictionary key like
``"scripts"`` is not one; a bare name of a top-level file names that file.
"""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

_PATH_CALLS = frozenset({"join", "joinpath", "Path", "PurePath", "PurePosixPath"})


@lru_cache(maxsize=None)
def tracked_paths(root: Path) -> frozenset[str]:
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return frozenset(out.splitlines())


@lru_cache(maxsize=None)
def tracked_dirs(root: Path) -> frozenset[str]:
    """Every folder above a tracked path."""
    return frozenset(
        p.rsplit("/", i)[0] for p in tracked_paths(root) for i in range(1, p.count("/") + 1)
    )


def forget() -> None:
    tracked_paths.cache_clear()
    tracked_dirs.cache_clear()


def top_entries(tracked: Iterable[str]) -> frozenset[str]:
    return frozenset(p.split("/")[0] for p in tracked)


def literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _run_from_a_top(parts: list[ast.AST], tops: frozenset[str]) -> list[ast.Constant]:
    """The literal run of ``parts`` from its first literal whose first part is a top-level
    entry, up to the first part that is not a literal."""
    for i, part in enumerate(parts):
        text = literal(part)
        if text is not None and text.strip("/").split("/")[0] in tops:
            run: list[ast.Constant] = []
            for later in parts[i:]:
                if literal(later) is None:
                    break
                run.append(later)  # type: ignore[arg-type]
            return run
    return []


def _joined(node: ast.AST, inner: set[int]) -> list[ast.AST] | None:
    """The parts a path expression joins: a ``/`` chain or a join call's arguments. The chain's
    inner ``/`` nodes go into ``inner``, so a chain is read once, whole, from its outermost."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        if id(node) in inner:
            return None
        chain: list[ast.AST] = []
        current: ast.AST = node
        while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
            inner.add(id(current))
            chain.insert(0, current.right)
            current = current.left
        return [current, *chain]
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _PATH_CALLS:
            return list(node.args)
    return None


def python_names(root: Path, rel: str, tops: frozenset[str]) -> set[str]:
    """Each repository path the Python file ``rel`` names. A file it cannot parse fails: what
    the census cannot read, it cannot account for."""
    try:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise AssertionError(f"{rel} cannot be parsed, so the census cannot read it: {exc}")
    # A bare literal naming a top-level file names that file; one naming a folder may be any word.
    top_files = tops - tracked_dirs(root)
    used: set[int] = set()
    inner: set[int] = set()
    found: set[str] = set()
    for node in ast.walk(tree):
        parts = _joined(node, inner)
        run = _run_from_a_top(parts, tops) if parts else []
        if run:
            used.update(id(n) for n in run)
            found.add("/".join(n.value.strip("/") for n in run))
    assigned = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
    }
    for node in ast.walk(tree):
        text = literal(node)
        if text is None or id(node) in used:
            continue
        text = text.strip().removeprefix("./").rstrip("/")
        if not text or "\n" in text or " " in text or len(text) > 300:
            continue
        if text.split("/")[0] not in tops:
            continue
        if "/" in text or text in top_files or id(node) in assigned:
            found.add(text)
    return found


def python_reads(root: Path, scripts: Iterable[str]) -> dict[str, str]:
    """Each path ``scripts`` name, followed into every tracked Python file one of them names."""
    tracked = tracked_paths(root)
    tops = top_entries(tracked)
    found: dict[str, str] = {}
    todo = list(scripts)
    seen: set[str] = set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        found.setdefault(rel, "a step runs it")
        for path in sorted(python_names(root, rel, tops)):
            found.setdefault(path, f"named by {rel}")
            if path.endswith(".py") and path in tracked:
                todo.append(path)
    return found
