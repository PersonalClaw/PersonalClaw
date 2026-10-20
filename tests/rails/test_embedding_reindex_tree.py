"""The source-tree checks split from
``tests/test_embedding_reindex.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations


def test_the_gateway_registers_the_chunk_backfill_maintenance_pass(monkeypatch):
    """The pass is only worth anything if something registers it — assert the CALL SITE, not
    just the mechanism, and assert the boot hook it replaced is really gone."""
    import ast
    import pathlib

    from personalclaw.dashboard import lifecycle_hooks, server
    from personalclaw.dashboard.embedding_reindex import register_chunk_backfill_pass
    from personalclaw.knowledge import maintenance

    tree = ast.parse(pathlib.Path(server.__file__).read_text())
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "register_chunk_backfill_pass" in called
    # The gateway's start hooks are appended in `dashboard/lifecycle_hooks.py`.
    hooks = ast.parse(pathlib.Path(lifecycle_hooks.__file__).read_text())
    appended = {
        node.args[0].id
        for node in ast.walk(hooks)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "on_startup"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert "_model_providers_startup" in appended, "the scan found the start hooks"
    assert "_backfill_item_chunks_startup" not in appended, "the boot hook must stay deleted"

    # …and the registrar really registers, under the name the host will look up.
    monkeypatch.setattr(maintenance, "_PASSES", {})
    register_chunk_backfill_pass()
    assert maintenance.registered_passes() == ["chunk_backfill"]
