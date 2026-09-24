"""The engine's ONE boolean-condition dialect (`workflows/conditions.py`).

Three engine sites ask "is this true?" — an `expression` gate, an `until` loop, and a
node's `success_when` — and before WF2LOO-10 the gate answered it by INTERPOLATING the
expression into a string and asking whether the string was truthy. The regression test at
the bottom of this file pins the consequence: two shipped templates carried a gate that
could not reject, because `"false == true"` is a non-empty string.

So the assertions here are mostly about the parse, not the plumbing: a comparison has to
compare, `&&` has to require both sides, and an unresolvable reference has to RAISE rather
than quietly reading as false — a condition that cannot read what it tests has not
answered the question.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows.bindings import BindingContext, BindingError
from personalclaw.workflows.conditions import evaluate, truthy


def _ctx(**inputs) -> BindingContext:
    return BindingContext(
        inputs=dict(inputs),
        node_outputs={
            "init": {
                "can_start": True,
                "can_test": True,
                "can_see_progress": True,
                "can_pick_next": False,
                "blocked_by": "no runnable test",
            },
            "audit": {"verdict": "pass", "findings": []},
        },
    )


class TestTruthiness:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("yes", True),
            ("", False),
            # The reason this rule exists: half the values the engine tests come back from a
            # model as text, and `bool("false")` is True.
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
            ("null", False),
            ("none", False),
            ([], False),
            ([0], True),
            ({}, False),
            ({"a": 1}, True),
            (None, False),
        ],
    )
    def test_the_closed_falsy_set(self, value, expected) -> None:
        assert truthy(value) is expected


class TestLeaves:
    def test_a_braced_reference_resolves_to_its_real_type(self) -> None:
        assert evaluate("{{inputs.flag}}", _ctx(flag=True)) is True
        assert evaluate("{{inputs.flag}}", _ctx(flag=False)) is False

    def test_a_bare_path_resolves_the_same_way(self) -> None:
        """`success_when` must be brace-free: `resolve_config` resolves every braced binding
        BEFORE a node runs, and at that moment the node's own `output` does not exist."""
        assert evaluate("nodes.init.output.can_start", _ctx()) is True
        assert evaluate("nodes.init.output.can_pick_next", _ctx()) is False

    def test_a_pipe_chain_still_works_inside_a_condition(self) -> None:
        assert evaluate("nodes.audit.output.findings | count", _ctx()) is False

    def test_an_unresolvable_reference_raises(self) -> None:
        """Not false. "I could not tell" and "no" are different answers, and the callers each
        turn the raise into their own typed failure."""
        with pytest.raises(BindingError):
            evaluate("nodes.nope.output.x", _ctx())
        with pytest.raises(BindingError):
            evaluate("{{inputs.missing}} == true", _ctx())

    def test_an_empty_condition_raises(self) -> None:
        with pytest.raises(BindingError):
            evaluate("   ", _ctx())


class TestComparison:
    def test_equality_against_a_boolean_literal(self) -> None:
        assert evaluate("{{inputs.flag}} == true", _ctx(flag=True)) is True
        assert evaluate("{{inputs.flag}} == true", _ctx(flag=False)) is False
        assert evaluate("{{inputs.flag}} != true", _ctx(flag=False)) is True

    def test_a_boolean_spelled_as_a_string_still_compares(self) -> None:
        """A model-filled field arrives as `"true"` often enough that comparing it to a real
        `True` as different types would make one template pass or fail depending on who wrote
        the value."""
        assert evaluate("{{inputs.flag}} == true", _ctx(flag="true")) is True
        assert evaluate("{{inputs.flag}} == false", _ctx(flag="false")) is True

    def test_equality_against_a_quoted_string(self) -> None:
        assert evaluate("{{nodes.audit.output.verdict}} == 'pass'", _ctx()) is True
        assert evaluate('{{nodes.audit.output.verdict}} == "fail"', _ctx()) is False

    def test_inequality_against_the_empty_string(self) -> None:
        """The form R5f's inverted repro predicate needs."""
        assert evaluate("nodes.init.output.blocked_by != ''", _ctx()) is True
        assert evaluate("inputs.reason != ''", _ctx(reason="")) is False

    def test_numbers_compare_as_numbers(self) -> None:
        assert evaluate("inputs.n == 3", _ctx(n=3)) is True
        assert evaluate("inputs.n != 3", _ctx(n=4)) is True

    def test_an_unquoted_bare_word_is_a_reference_not_a_string(self) -> None:
        """Deliberate: silently treating `pass` as the string "pass" would make a typo in a
        node reference compare equal to nothing forever, without a word of complaint."""
        with pytest.raises(BindingError):
            evaluate("{{nodes.audit.output.verdict}} == pass", _ctx())


class TestCombinators:
    def test_conjunction_requires_every_term(self) -> None:
        four = (
            "{{nodes.init.output.can_start}} && {{nodes.init.output.can_test}} "
            "&& {{nodes.init.output.can_see_progress}} && {{nodes.init.output.can_pick_next}}"
        )
        assert evaluate(four, _ctx()) is False
        ctx = _ctx()
        ctx.node_outputs["init"]["can_pick_next"] = True
        assert evaluate(four, ctx) is True

    def test_disjunction_needs_only_one(self) -> None:
        assert evaluate("inputs.a || inputs.b", _ctx(a=False, b=True)) is True
        assert evaluate("inputs.a || inputs.b", _ctx(a=False, b=False)) is False

    def test_negation(self) -> None:
        assert evaluate("!inputs.a", _ctx(a=False)) is True
        assert evaluate("!inputs.a", _ctx(a=True)) is False

    def test_negation_is_not_confused_with_inequality(self) -> None:
        assert evaluate("inputs.a != 'x'", _ctx(a="y")) is True

    def test_precedence_puts_or_below_and(self) -> None:
        # false && false || true  →  (false && false) || true  →  true
        assert evaluate("inputs.a && inputs.b || inputs.c", _ctx(a=False, b=False, c=True)) is True
        # Parentheses override it.
        assert (
            evaluate("inputs.a && (inputs.b || inputs.c)", _ctx(a=False, b=False, c=True)) is False
        )

    def test_a_quoted_operator_is_not_a_combinator(self) -> None:
        assert evaluate("inputs.a == 'x && y'", _ctx(a="x && y")) is True

    def test_every_term_is_resolved_even_when_the_answer_is_already_known(self) -> None:
        """Not short-circuited on purpose: a typo on the right of an `||` whose left side
        happens to be true would otherwise stay hidden until the day the left side flips."""
        with pytest.raises(BindingError):
            evaluate("inputs.a || nodes.nope.output.x", _ctx(a=True))

    def test_word_forms_are_not_a_second_spelling(self) -> None:
        """`and`/`or`/`not` are deliberately unsupported — two spellings of one operator is a
        dialect nobody memorises. The failure is legible: it parses as one bare path, which
        does not resolve."""
        with pytest.raises(BindingError):
            evaluate("inputs.a and inputs.b", _ctx(a=True, b=True))


class TestTheGateRegression:
    """🔴 The defect this module was written to fix, pinned so it cannot come back.

    `audit-sweep` ships `expr: "{{inputs.fix}} == true"` and `produce-and-audit` ships
    `expr: "{{nodes.audit.output.verdict}} == 'pass'"`. Under the old
    interpolate-then-truthy path both evaluated the STRING `"false == true"` /
    `"fail == 'pass'"` — non-empty, therefore truthy, therefore the gate ALWAYS passed. A
    gate that cannot reject is a gate whose absence would look identical.
    """

    def test_a_false_comparison_is_now_false(self) -> None:
        assert evaluate("{{inputs.fix}} == true", _ctx(fix=False)) is False
        assert evaluate("{{inputs.fix}} == true", _ctx(fix=True)) is True

    def test_the_shipped_verdict_gate_can_reject(self) -> None:
        ctx = _ctx()
        ctx.node_outputs["audit"]["verdict"] = "fail"
        assert evaluate("{{nodes.audit.output.verdict}} == 'pass'", ctx) is False

    def test_the_old_interpolated_reading_would_have_passed(self) -> None:
        """The proof that the old path was broken rather than merely different: rendering the
        expression to a string and asking `truthy` about it says yes to a false comparison."""
        assert truthy("false == true") is True
