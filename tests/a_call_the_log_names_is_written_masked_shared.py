"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_call_the_log_names_is_written_masked.py``

Its checks:
``tests/rails/test_a_call_the_log_names_is_written_masked_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

#: The attributes a tool call's event carries its title and its arguments in.
_CALL_TEXT = {"title", "tool_input"}


#: The names a call's event goes by where a log line reads it.
_EVENT_NAMES = {"event", "ev", "call"}


#: What may stand between them and the log: the title's writer, the audit text it is built on,
#: and a size (a length is not the text).
_MASKERS = {"log_title", "audit_text", "len"}


_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _LOG_METHODS:
        return False
    owner = func.value
    name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
    return "log" in name.lower()


def _raw_call_text(node: ast.AST, masked: bool = False) -> list[str]:
    """Each ``event.title``/``event.tool_input`` in *node* that reaches the log unmasked."""
    if isinstance(node, ast.Call):
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        masked = masked or name in _MASKERS
    if (
        isinstance(node, ast.Attribute)
        and node.attr in _CALL_TEXT
        and isinstance(node.value, ast.Name)
        and node.value.id in _EVENT_NAMES
        and not masked
    ):
        return [ast.unparse(node)]
    return [hit for child in ast.iter_child_nodes(node) for hit in _raw_call_text(child, masked)]


def _offenders(source: str | ast.Module, where: str) -> list[str]:
    hits = []
    tree = source if isinstance(source, ast.Module) else ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_log_call(node):
            for arg in [*node.args[1:], *(k.value for k in node.keywords)]:
                hits += [f"{where}:{node.lineno}: {raw}" for raw in _raw_call_text(arg)]
    return hits
