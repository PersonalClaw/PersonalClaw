"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_sel_subject_census.py``

Its checks:
``tests/rails/test_sel_subject_census_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

from personalclaw.audit_subject import kind_of_subject
from personalclaw.run_bounds import FILE_WRITES
from personalclaw.task_modes import SHELL_TITLE_PREFIXES, SHELL_TOOL_NAMES

#: Every name a call's row records a command or a path for, by name alone.
_SUBJECT_NAMES = frozenset(SHELL_TOOL_NAMES) | frozenset(FILE_WRITES)


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _fixed_head(node: ast.expr | None) -> str | None:
    """An f-string name's literal head when no subject tool's name can be completed from it."""
    if not isinstance(node, ast.JoinedStr) or not node.values:
        return None
    head = _literal(node.values[0])
    if not head or head.lower().startswith(SHELL_TITLE_PREFIXES):
        return None
    if any(name.startswith(head) for name in _SUBJECT_NAMES):
        return None
    return head


def _problem(call: ast.Call) -> str:
    """Why this audit row would leave out what its call ran, or ``""`` when it cannot."""
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    if "tool_input" in keywords:
        return ""
    name, kind = keywords.get("tool_name"), keywords.get("tool_kind")
    kind_text = _literal(kind) if kind is not None else ""
    if kind_text is None:
        return "its tool's kind is read at run time, and it hands over none of the call's arguments"
    fixed = shown = _literal(name)
    if fixed is None:
        fixed = _fixed_head(name)
        if fixed is None:
            return "its tool is named at run time, and it hands over none of the call's arguments"
        shown = f"{fixed}…"
    if kind_of_subject(fixed, kind_text):
        return f"{shown!r} is a tool whose row records what it ran, and no arguments come with it"
    return ""
