"""The device-scope table names each route once (``device_scope_routes.DEVICE_SCOPE_ROUTES``).

A dict literal keeps the last of two equal keys and says nothing, so a second row for a route would
silently replace the first: a route meant OUTSIDE could be admitted further down the table, and
the census of every route (``tests/test_the_device_scope_table_holds_every_route.py``), which reads
the dict as built, would see only the survivor. This reads the table's source instead.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import source_index

from personalclaw.dashboard import device_scope_routes


def test_the_table_names_each_route_once() -> None:
    tree = source_index.tree(Path(device_scope_routes.__file__))
    tables = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign | ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "DEVICE_SCOPE_ROUTES"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]
    assert len(tables) == 1 and isinstance(tables[0], ast.Dict), "the table is one dict literal"
    keys = Counter(ast.literal_eval(key) for key in tables[0].keys if key is not None)
    assert len(keys) > 20, "the scan reads the table's rows"
    assert not [key for key, seen in keys.items() if seen > 1]
