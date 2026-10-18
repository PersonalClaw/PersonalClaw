"""The source-tree checks split from
``tests/test_acp_effective_risk_correlation.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import pathlib


class TestTheProductionCallShapes:
    """Pin the two call sites these tests replicate, so the replication cannot drift."""

    @staticmethod
    def _chat_runner() -> ast.Module:
        import personalclaw.dashboard.chat_runner as cr

        return ast.parse(pathlib.Path(cr.__file__).read_text())

    def test_the_card_and_the_sel_row_share_one_risk_vocabulary(self):
        """Requirement: the SEL row and the approval notification derive from ONE place.

        Both read the local ``effective_risk``, and that name is bound from
        ``resolve_effective_risk`` — ``task_modes``' resolver, which is itself the only
        consumer of ``classify_invocation``. No second risk vocabulary.
        """
        tree = self._chat_runner()
        bound_from_resolver = {
            t.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "resolve_effective_risk"
        }
        assert "effective_risk" in bound_from_resolver

        # The approval's one publication — the registry entry every surface (the card, the
        # listing, the Inbox row and its notification) reads its risk from.
        holds = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "hold_session_approval"
        ]
        assert len(holds) == 1, "one publication call site"
        risk_arg = next(kw.value for kw in holds[0].keywords if kw.arg == "risk")
        assert isinstance(risk_arg, ast.Name) and risk_arg.id == "effective_risk"

    def test_the_task_mode_gate_is_not_handed_the_declared_kind(self):
        """The recorded decision: the carried kind informs the LABEL, never the gate.

        A CLI that labels its own mutation "read" must not be able to turn the gate's
        deny-by-default into an allow. This asserts the gate call still passes a literal
        empty kind — if that ever changes, ``_production_gate`` above is measuring a path
        production no longer takes.
        """
        tree = self._chat_runner()
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "task_mode_denies"
        ]
        assert len(calls) == 1
        # (session, declared, title, tool_kind, tool_input)
        kind_arg = calls[0].args[3]
        assert isinstance(kind_arg, ast.Constant) and kind_arg.value == ""
