"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_write_door_scalar_typing.py``

Its checks:
``tests/rails/test_write_door_scalar_typing_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
from typing import Any

import pytest

_TARGET_FIELDS = frozenset({"name", "title"})


_BODY_LOCALS = frozenset({"body", "fields", "payload", "raw", "data"})


class _CoercionScan(ast.NodeVisitor):
    """Finds ``str(<body read>)`` and ``<body read>.strip()`` inside ``api_*`` handlers.

    These are the two fingerprints the issues measured. ``str()`` is the one that PERSISTS
    a repr (``{"name": {"a": "b"}}`` → the stored name ``{'a': 'b'}``); the bare ``.strip()``
    is the one that raises ``AttributeError`` into the request-shape boundary, which answers
    a generic ``bad_request`` naming no field — a swallowed crash rather than validation.
    """

    def __init__(self) -> None:
        self.fn: str | None = None
        self.hits: set[tuple[str, str]] = set()

    def visit_AsyncFunctionDef(self, node: Any) -> None:
        outer = self.fn
        if node.name.startswith("api_"):
            self.fn = node.name
        self.generic_visit(node)
        self.fn = outer

    visit_FunctionDef = visit_AsyncFunctionDef  # nested sync helpers count as the door

    @staticmethod
    def _field_read(node: ast.AST) -> str | None:
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Subscript)
                and isinstance(inner.value, ast.Name)
                and inner.value.id in _BODY_LOCALS
                and isinstance(inner.slice, ast.Constant)
                and inner.slice.value in _TARGET_FIELDS
            ):
                return str(inner.slice.value)
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "get"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in _BODY_LOCALS
                and inner.args
                and isinstance(inner.args[0], ast.Constant)
                and inner.args[0].value in _TARGET_FIELDS
            ):
                return str(inner.args[0].value)
        return None

    def visit_Call(self, node: ast.Call) -> None:
        if self.fn:
            if isinstance(node.func, ast.Name) and node.func.id == "str":
                field = self._field_read(node)
                if field:
                    self.hits.add((self.fn, field))
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "strip":
                inner = node.func.value
                already_counted = (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "str"
                )
                field = self._field_read(inner)
                if field and not already_counted:
                    self.hits.add((self.fn, field))
        self.generic_visit(node)


#: Everything a door "persisted", so a test can assert a refusal wrote NOTHING. A status
#: code alone would miss the defect these issues are about: the damage was a stored value,
#: not a wrong number on the wire.
_STORE: list[dict[str, Any]] = []


@pytest.fixture(autouse=True)
def _clear_store():
    _STORE.clear()
    yield
    _STORE.clear()
