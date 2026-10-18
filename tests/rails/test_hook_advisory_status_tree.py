"""The source-tree checks split from
``tests/test_hook_advisory_status.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import hook_advisory_status_shared
import source_index
from rails_repo import REPO

_isolated = hook_advisory_status_shared._isolated


def test_only_gating_callers_exist():
    """A census, not a style check: `enforced=True` is asserted by `fire_for_ids` on behalf of its
    callers, so a caller that does NOT gate on exit 2 would make the status a lie again.

    The one blocking event, `PreToolUse`, is fired through `fire_for_ids` at one step
    (`pre_tool_hooks`), whose exit 2 refuses the call. The chat runner fires the turn's other
    lifecycle events through it, none of which can block, and never `PreToolUse`. A third caller
    fails here and must prove it gates (or use `fire()`)."""
    import ast

    root = REPO / "src" / "personalclaw"
    callers = {p for p in source_index.package_files() if ".fire_for_ids(" in source_index.text(p)}
    assert {p.name for p in callers} == {"chat_runner.py", "pre_tool_hooks.py"}
    step = source_index.text(root / "pre_tool_hooks.py")
    assert "HOOK_EVENT_PRE_TOOL_USE" in step and "== _BLOCKS" in step, "the step must gate exit 2"
    chat_runner = source_index.tree(root / "dashboard" / "chat_runner.py")
    names = {n.id for n in ast.walk(chat_runner) if isinstance(n, ast.Name)}
    assert "HOOK_EVENT_PRE_TOOL_USE" not in names, "the chat runner fires the pre-tool hooks itself"
