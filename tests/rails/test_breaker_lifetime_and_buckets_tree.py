"""The source-tree checks split from
``tests/test_breaker_lifetime_and_buckets.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index


class TestTheCircuitCeilingIsConfigurable:
    """The ceiling is the ONE rung an operator can retune, and the round trip is what makes
    the circuit trip drivable on a shared instance instead of only in a unit test."""

    def test_the_module_constant_is_now_only_a_default(self):
        """The bare-constant defect, pinned: no production module may *evaluate*
        ``CIRCUIT_THRESHOLD``, because a comparison against it would bypass the config field
        and make the seam decoration.

        Walked as an AST, not grepped: the prose in this module and in ``loop_breaker``'s own
        docstrings names the constant repeatedly, and a line-based sweep reports every one of
        those sentences. The only legitimate evaluations are inside ``loop_breaker`` itself —
        its own declaration and the fallback return — so the census is a count there and a
        zero everywhere else.
        """
        import ast
        import pathlib

        import personalclaw

        src_root = pathlib.Path(personalclaw.__file__).parent
        owner = src_root / "guardrails" / "loop_breaker.py"
        hits: dict[str, int] = {}
        for p, tree in source_index.package_trees():
            n = sum(
                1
                for node in ast.walk(tree)
                if (isinstance(node, ast.Name) and node.id == "CIRCUIT_THRESHOLD")
                or (isinstance(node, ast.Attribute) and node.attr == "CIRCUIT_THRESHOLD")
            )
            if n:
                hits[str(p.relative_to(src_root))] = n
        assert set(hits) == {
            "guardrails/loop_breaker.py"
        }, f"a consumer still evaluates the constant instead of the ceiling: {hits}"
        # Its declaration and the one fallback return in `configured_circuit_threshold`.
        assert hits["guardrails/loop_breaker.py"] == 2, hits
        assert "return CIRCUIT_THRESHOLD" in owner.read_text(encoding="utf-8")
