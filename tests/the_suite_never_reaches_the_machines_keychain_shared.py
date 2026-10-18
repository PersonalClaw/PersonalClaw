"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_the_suite_never_reaches_the_machines_keychain.py``

Its checks:
``tests/rails/test_the_suite_never_reaches_the_machines_keychain_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

#: What only ``keychain_stub`` may touch: the store's switch, and the probe the tests used to
#: patch one by one before the switch made each of those patches redundant.
_THE_SWITCH = frozenset({"_keychain_off", "_usable_keyring"})


def _reaches_around(tree: ast.AST) -> list[int]:
    """Lines of *tree* that name the switch: an attribute read or set
    (``credentials._keychain_off``), or a name handed to ``monkeypatch.setattr``/``patch`` as a
    string."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _THE_SWITCH:
            lines.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in _THE_SWITCH or node.value.rsplit(".", 1)[-1] in _THE_SWITCH:
                lines.append(node.lineno)
    return lines
