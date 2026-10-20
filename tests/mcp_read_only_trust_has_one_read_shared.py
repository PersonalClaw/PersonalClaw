"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_mcp_read_only_trust_has_one_read.py``

Its checks:
``tests/rails/test_mcp_read_only_trust_has_one_read_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


OWNER = SRC / "mcp_read_only_trust.py"


GATE = SRC / "mcp_client.py"


#: What only the record's own module may name: its records, by name and by file, and what it keeps
#: of them.
_RECORD_NAMES = ("mcp_read_only", "mcp_tool_descriptions")


_PRIVATE_PREFIX = "_"


_RECORD_CONSTANTS = {"TRUST_RECORD", "SEEN_RECORD"}


#: Where whether the owner trusts a server at all may be read: the words of a refusal or a note.
_STANDING_READERS = {SRC / "guardrails" / "policy.py"}


def _record_reads(path: Path, tree: ast.Module) -> list[str]:
    """Where *tree* names the record or reaches into its module's own state."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            named = node.value.rsplit("/", 1)[-1].removesuffix(".json")
            if named in _RECORD_NAMES:
                found.append(f"{path.name}:{node.lineno} names the record: {node.value!r}")
        elif isinstance(node, ast.ImportFrom) and node.module == "personalclaw.mcp_read_only_trust":
            for alias in node.names:
                if alias.name.startswith(_PRIVATE_PREFIX) or alias.name in _RECORD_CONSTANTS:
                    found.append(f"{path.name}:{node.lineno} imports {alias.name}")
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "mcp_read_only_trust"
            and (node.attr.startswith(_PRIVATE_PREFIX) or node.attr in _RECORD_CONSTANTS)
        ):
            found.append(f"{path.name}:{node.lineno} reaches mcp_read_only_trust.{node.attr}")
    return found


def _called(node: ast.Call) -> str:
    fn = node.func
    return fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """Each call's id → the name of the function it is written in."""
    inside: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    inside[id(node)] = fn.name
    return inside


def _label_reads(path: Path, tree: ast.Module) -> tuple[list[str], int]:
    """Where *tree* takes a risk of a label with a trust that is not the literal ``True`` and is not
    the gate's digest check, and how many calls it read."""
    found, seen = [], 0
    inside = _enclosing_functions(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called(node) == "risk_from_annotations"):
            continue
        seen += 1
        trusted = next((k.value for k in node.keywords if k.arg == "trusted"), None)
        if isinstance(trusted, ast.Constant) and trusted.value is True:
            continue
        if (
            path == GATE
            and inside.get(id(node)) == "declared_risk"
            and isinstance(trusted, ast.Call)
            and _called(trusted) == "believes"
        ):
            continue
        found.append(
            f"{path.name}:{node.lineno} takes a label's risk with trusted="
            f"{ast.unparse(trusted) if trusted is not None else '(missing)'}"
        )
    return found, seen


def _standing_reads(path: Path, tree: ast.Module) -> list[str]:
    """Where *tree* asks whether the owner trusts a server at all, outside the words that may."""
    if path == OWNER or path in _STANDING_READERS:
        return []
    return [
        f"{path.name}:{node.lineno} reads holds_trust"
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "holds_trust")
        or (isinstance(node, ast.Attribute) and node.attr == "holds_trust")
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "holds_trust" for alias in node.names)
        )
    ]
