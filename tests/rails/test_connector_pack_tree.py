"""The source-tree checks split from
``tests/test_connector_pack.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

# Reached through the SDK facade on purpose: this is the exact import a pack's own
# ``provider.py`` writes, so exercising it here means the published boundary is what the
# tests drive rather than the core module behind it.
import connector_pack_shared

_isolated_home = connector_pack_shared._isolated_home


def test_the_spawn_really_carries_the_bounds_its_audit_entry_claims():
    """`test_spawn_ceiling_audit.py` CLASSIFIES this spawn site; it does not check the code.

    Deleting the `spawn_shim_argv` line from `run_parse_script` leaves that audit — and
    every other test — green, because its `_CEILING_WRAPPED` map asserts that a site is *described*
    as ceiling-wrapped, not that it *is*. That is the declared-but-inert shape one layer up from
    the code, so the four bounds this change promises are asserted against the AST of the function
    that must apply them.
    """
    import ast
    import inspect

    from personalclaw.knowledge_providers import pack_parse as mod

    tree = ast.parse(inspect.getsource(mod.run_parse_script))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    attr_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    kwargs = {
        kw.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg
    }
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    assert "spawn_shim_argv" in calls, "the tool resource ceiling is not applied"
    assert "PROFILE_TOOL" in names, "the ceiling is applied under some other profile"
    assert "build_child_env" in calls, "the child inherits an unfiltered environment"
    assert "wrap_argv" in calls, "the OS path sandbox is not applied"
    assert "timeout" in kwargs, "the spawn has no wall clock"
    assert "run" in attr_calls or "subprocess" in names, "no spawn found at all"
