"""The source-tree checks split from
``tests/test_context_gauge_sdk_surface.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import pytest
from context_gauge_sdk_surface_shared import _CONTRACT
from rails_repo import REPO

_REPO_ROOT = REPO


_SDK_MODEL = _REPO_ROOT / "src" / "personalclaw" / "sdk" / "model.py"


@pytest.mark.parametrize("symbol", sorted(_CONTRACT))
def test_the_symbol_is_in_dunder_all(symbol: str) -> None:
    """``__all__`` is the declared surface, and it is what the inertness census reads. A
    symbol importable but unlisted is a surface an app cannot rely on."""
    tree = ast.parse(_SDK_MODEL.read_text(encoding="utf-8"))
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            declared = {
                el.value
                for el in getattr(node.value, "elts", [])
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
    assert symbol in declared, f"{symbol!r} is not in personalclaw.sdk.model.__all__"
