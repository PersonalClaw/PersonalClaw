"""The source-tree checks split from
``tests/test_computer_use_unsupported_platforms.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import pathlib

import computer_use_unsupported_platforms_shared
import pytest
from computer_use_unsupported_platforms_shared import PENDING

_isolated = computer_use_unsupported_platforms_shared._isolated


@pytest.mark.parametrize(("module", "_system", "_api"), PENDING)
def test_a_pending_driver_re_derives_no_wording(module, _system, _api):
    """One refusal, two modules. Neither platform module may build its own envelope or its own
    ``DriverError``: two copies of a WHAT/WHY/FIX drift the moment one is edited, which is the
    family the structural-duplication ratchet counts. The sentence lives in
    ``unsupported_platform`` and these modules supply only the two facts that differ."""
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    built = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "DriverError" not in built
    assert not [
        d
        for d in ast.walk(tree)
        if isinstance(d, ast.Dict)
        and any(isinstance(k, ast.Constant) and k.value == "error" for k in d.keys)
    ]
