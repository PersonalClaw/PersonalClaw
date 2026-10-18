"""The source-tree checks split from
``tests/test_identity_report_schedule.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations


class TestTheTriggerConverges:
    def test_the_reconciler_is_called_at_boot(self):
        """A reconciler with no caller is the whole defect this change existed to close: the
        delivery function shipped with a POST route as its only caller.

        Matched as a CALL through the AST, not as a substring: the name also appears in the
        `from ... import` line beside it, so a `toContain`-style scan stays green when the call
        is commented out and only the import survives. Measured — the first version of this
        assertion did exactly that.
        """
        import ast
        import inspect
        import textwrap

        from personalclaw.gateway import GatewayOrchestrator

        tree = ast.parse(textwrap.dedent(inspect.getsource(GatewayOrchestrator)))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "reconcile_identity_report_trigger" in called, sorted(
            n for n in called if "reconcile" in n
        )
        # VACUITY: the walk really finds this shape of call, so an empty `called` set (a broken
        # parse) cannot read as a pass.
        assert "reconcile_digest_cron" in called
