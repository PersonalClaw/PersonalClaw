"""The source-tree checks split from
``tests/test_feedback_suppression_enforcement.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import source_index
from feedback_suppression_enforcement_shared import SRC

from personalclaw import feedback as fb


class TestTheConstantMatchesTheCode:
    def test_the_withholding_set_is_consumed_only_where_the_constant_claims(self):
        """The constant says ``skill_synthesis``, i.e. the SKILLS surfacing path. Assert that is
        still the only place the set GATES anything.

        Counts CALLS via AST rather than grepping for the word: ``workflows/`` uses "suppressed"
        freely for unrelated attention-indicator logic, and a text scan reports those as consumers.
        That false positive is what would make this rail unmaintainable.
        """
        consumers: set[str] = set()
        for path, tree in source_index.package_trees():
            rel = str(path.relative_to(SRC))
            if rel == "feedback.py":
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                # the module-local fail-open wrapper is the same consumption
                if name in ("suppressed_producers", "_suppressed_producers"):
                    consumers.add(rel)
        assert consumers, (
            "nothing calls suppressed_producers() outside feedback.py — the withholding set is "
            "computed and never consulted, so `suppressed` cannot be true of anything. Either a "
            "gate was deleted or this rail is measuring the wrong symbol."
        )
        gating = {c for c in consumers if c.startswith("skills/")}
        non_gating = consumers - gating
        assert gating, (
            f"no module under skills/ consults the withholding set any more (consumers: "
            f"{sorted(consumers)}), but ENFORCED_SUPPRESSION_KINDS still claims "
            f"{list(fb.ENFORCED_SUPPRESSION_KINDS)} is withheld. The API now over-claims."
        )
        # These two READ the set — one to report it, one to simulate surfacing in Doctor. Neither
        # is a gate, so neither earns a kind in ENFORCED_SUPPRESSION_KINDS.
        allowed_readers = {
            "dashboard/handlers/feedback.py",
            "dashboard/handlers/doctor.py",
        }
        assert non_gating <= allowed_readers, (
            f"a NEW module consults the withholding set: {sorted(non_gating - allowed_readers)}. "
            f"If it is a surfacing gate, add its producer kind to ENFORCED_SUPPRESSION_KINDS in "
            f"the same change — otherwise the API under-reports a real effect. If it only reads to "
            f"display or simulate, add it to allowed_readers and say which."
        )
