"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_wf_error_codes_registry.py``

Its checks:
``tests/rails/test_wf_error_codes_registry_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
import re

_CODE_RE = re.compile(r"^WF_[A-Z][A-Z0-9_]*$")


#: ``WF_``-prefixed names that are NOT error codes and must not be read as such. Enumerated
#: rather than pattern-excluded, so a real code can never be silently dropped by a rule that
#: was written for these two.
#:
#: * ``WF_DEPTH_KEY`` — ``engine.py``'s environment-variable NAME for the subworkflow depth
#:   counter a leaf inherits. A constant, never an error code.
#: * ``WF_UPPER_SNAKE`` — prose naming the convention itself (``http_errors.py`` and
#:   ``service.py`` both write it when explaining which vocabulary they are on).
#:
#: Both are excluded by NAME here rather than by the scanner, because the scanner reading
#: only raising positions already means neither can appear; this list is what documents why
#: a reader greping for ``WF_`` in core finds two more names than the registry holds.
_NOT_ERROR_CODES = frozenset({"WF_DEPTH_KEY", "WF_UPPER_SNAKE"})


def _literal_codes(node: ast.AST) -> list[str]:
    """``WF_*`` string literals directly under *node* (not recursively)."""
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if _CODE_RE.match(node.value) and node.value not in _NOT_ERROR_CODES:
            out.append(node.value)
    return out


def scan(
    text: str | ast.Module, where: str = "?"
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(raised, mapped)`` of *text* (source, or its tree), each ``code -> ["<where>:<line>"]``.

    RAISED — a code literal in a position that *emits* it:

    * an argument or a ``code=``/``error_code=`` keyword of a call
      (``_service_failure(...)``, ``_add(...)``, ``Issue(...)``, ``Finding(...)``,
      ``tool_failure(...)``, ``_ApplyError(...)``);
    * the ``"code"`` value of a dict literal (``controller.py``'s inline result dicts);
    * an element of a tuple literal (``revision.py`` returns ``(node_id, code, message)``).

    MAPPED — a code literal used as a dict KEY, i.e. ``handlers.py``'s ``_STATUS_MAP``.
    Deliberately a separate bucket; see the module docstring.
    """
    raised: dict[str, list[str]] = {}
    mapped: dict[str, list[str]] = {}
    tree = text if isinstance(text, ast.Module) else ast.parse(text)

    def note(bucket: dict[str, list[str]], code: str, lineno: int) -> None:
        bucket.setdefault(code, []).append(f"{where}:{lineno}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in node.args:
                for code in _literal_codes(arg):
                    note(raised, code, getattr(arg, "lineno", node.lineno))
            for kw in node.keywords:
                if kw.arg in ("code", "error_code"):
                    for code in _literal_codes(kw.value):
                        note(raised, code, getattr(kw.value, "lineno", node.lineno))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                for code in _literal_codes(key):
                    note(mapped, code, getattr(key, "lineno", 0))
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "code"
                    and (codes := _literal_codes(value))
                ):
                    for code in codes:
                        note(raised, code, getattr(value, "lineno", 0))
        elif isinstance(node, ast.Tuple):
            for elt in node.elts:
                for code in _literal_codes(elt):
                    note(raised, code, getattr(elt, "lineno", 0))
    return raised, mapped
