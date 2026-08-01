"""Binding resolution — the two paths, the closed pipe set, and typed failures.

The distinction that matters most (WF2-R9): "the node produced null" is a VALUE that
flows through, while "this reference does not resolve" RAISES. A silent empty string in
the second case is how a prompt quietly loses its input and the run produces confident
nonsense against no data at all.
"""

from __future__ import annotations

import pytest

from personalclaw.workflows.bindings import (
    BindingContext,
    BindingError,
    node_deps,
    refs_in,
    resolve,
    resolve_expr,
)


@pytest.fixture
def ctx() -> BindingContext:
    return BindingContext(
        inputs={"topic": "checkout latency", "count": 3, "flag": True, "empty": None},
        node_outputs={
            "find": {
                "findings": [
                    {"verdict": "CONFIRMED", "title": "N+1 query", "score": 9},
                    {"verdict": "REFUTED", "title": "DNS", "score": 2},
                    {"verdict": "CONFIRMED", "title": "cold cache", "score": 7},
                ],
                "total": 3,
            },
            "empty_node": None,
        },
        node_artifacts={"big": "artifacts/big.json"},
    )


class TestWholeValueVsInterpolation:
    def test_whole_value_ref_preserves_the_source_type(self, ctx) -> None:
        """A foreach over `{{nodes.x.output.items}}` needs a real list, not its repr."""
        out = resolve("{{nodes.find.output.findings}}", ctx)
        assert isinstance(out, list) and len(out) == 3
        assert isinstance(resolve("{{inputs.count}}", ctx), int)
        assert resolve("{{inputs.flag}}", ctx) is True
        assert isinstance(resolve("{{nodes.find.output}}", ctx), dict)

    def test_surrounding_whitespace_still_counts_as_whole_value(self, ctx) -> None:
        assert isinstance(resolve("  {{ inputs.count }}  ", ctx), int)

    def test_interpolation_stringifies_containers_as_json(self, ctx) -> None:
        """A Python repr's single quotes are not JSON, and models reproduce them badly."""
        out = resolve("data: {{nodes.find.output.findings.0}}", ctx)
        assert out.startswith("data: {") and '"verdict"' in out and "'" not in out

    def test_interpolation_renders_scalars_bare(self, ctx) -> None:
        assert resolve("n={{inputs.count}} f={{inputs.flag}}", ctx) == "n=3 f=true"

    def test_null_interpolates_as_empty_string(self, ctx) -> None:
        assert resolve("x={{inputs.empty}}!", ctx) == "x=!"

    def test_non_strings_pass_through_untouched(self, ctx) -> None:
        assert resolve(42, ctx) == 42
        assert resolve(None, ctx) is None

    def test_dicts_and_lists_resolve_recursively(self, ctx) -> None:
        """A whole node `config` resolves in one call."""
        out = resolve({"p": "on {{inputs.topic}}", "n": ["{{inputs.count}}"]}, ctx)
        assert out == {"p": "on checkout latency", "n": [3]}


class TestPipes:
    def test_filter_by_key_and_value(self, ctx) -> None:
        got = resolve("{{nodes.find.output.findings | filter('verdict','CONFIRMED')}}", ctx)
        assert [f["title"] for f in got] == ["N+1 query", "cold cache"]

    def test_bare_filter_is_filter_boolean(self, ctx) -> None:
        c = BindingContext(node_outputs={"n": {"xs": [1, 0, None, 2, "", 3]}})
        assert resolve("{{nodes.n.output.xs | filter}}", c) == [1, 2, 3]

    def test_chained_pipes(self, ctx) -> None:
        expr = (
            "{{nodes.find.output.findings | filter('verdict','CONFIRMED') | map('title') | count}}"
        )
        assert resolve(expr, ctx) == 2

    def test_flatten_slice_count(self, ctx) -> None:
        c = BindingContext(node_outputs={"n": {"xs": [[1, 2], [3], []]}})
        assert resolve("{{nodes.n.output.xs | flatten}}", c) == [1, 2, 3]
        assert resolve("{{nodes.n.output.xs | flatten | slice(1,3)}}", c) == [2, 3]
        assert resolve("{{nodes.n.output.xs | flatten | count}}", c) == 3

    def test_default_substitutes_for_empty_values(self, ctx) -> None:
        assert resolve("{{inputs.empty | default('n/a')}}", ctx) == "n/a"
        # A real value is NOT replaced.
        assert resolve("{{inputs.count | default(99)}}", ctx) == 3

    def test_null_output_flows_through_pipes_without_raising(self, ctx) -> None:
        """ "Produced null" is a value; only an unresolvable REFERENCE raises."""
        assert resolve("{{nodes.empty_node.output | filter}}", ctx) == []
        assert resolve("{{nodes.empty_node.output | count}}", ctx) == 0

    def test_sanitization_pipes(self, ctx) -> None:
        c = BindingContext(inputs={"x": "<script>&\"'"})
        assert resolve("{{inputs.x | xml_escape}}", c) == "&lt;script&gt;&amp;&quot;&apos;"
        assert resolve("{{inputs.topic | truncate(8)}}", ctx) == "checkout…"
        assert resolve("{{inputs.topic | slugify}}", ctx) == "checkout-latency"

    def test_artifact_pointer_resolves(self, ctx) -> None:
        assert resolve("{{nodes.big.artifact}}", ctx) == "artifacts/big.json"

    def test_foreach_and_loop_variables(self) -> None:
        c = BindingContext(
            item={"id": 7}, has_item=True, iter_index=2, last_output={"done": True}, has_last=True
        )
        assert resolve("{{item.id}}", c) == 7
        assert resolve("{{iter}}", c) == 2
        assert resolve("{{last.output.done}}", c) is True


class TestTypedFailures:
    def test_unknown_node_id_raises(self, ctx) -> None:
        with pytest.raises(BindingError) as exc:
            resolve("{{nodes.ghost.output}}", ctx)
        assert "ghost" in str(exc.value)

    def test_missing_path_segment_raises(self, ctx) -> None:
        with pytest.raises(BindingError):
            resolve("{{nodes.find.output.nope.deeper}}", ctx)

    def test_unknown_input_raises(self, ctx) -> None:
        with pytest.raises(BindingError):
            resolve("{{inputs.never_declared}}", ctx)

    def test_the_error_names_the_expression(self, ctx) -> None:
        """The journal entry must say WHAT broke, not only where."""
        with pytest.raises(BindingError) as exc:
            resolve("{{nodes.ghost.output}}", ctx)
        assert exc.value.expr == "nodes.ghost.output"

    def test_unknown_pipe_raises(self, ctx) -> None:
        with pytest.raises(BindingError) as exc:
            resolve("{{inputs.topic | eval}}", ctx)
        assert "eval" in str(exc.value)

    def test_pipe_type_misuse_raises(self, ctx) -> None:
        with pytest.raises(BindingError):
            resolve("{{inputs.topic | flatten}}", ctx)  # a string is not a list

    def test_pipe_args_must_be_literals(self, ctx) -> None:
        """No identifiers in pipe args — an argument must never name a variable, or the
        closed pipe set becomes an expression language."""
        with pytest.raises(BindingError):
            resolve("{{nodes.find.output.findings | filter(some_var,'x')}}", ctx)

    def test_reading_a_field_off_a_scalar_raises(self, ctx) -> None:
        with pytest.raises(BindingError):
            resolve("{{inputs.count.nope}}", ctx)


class TestSecrets:
    def test_secret_resolves_through_the_injected_resolver(self) -> None:
        c = BindingContext(secret_resolver=lambda k: "s3cr3t" if k == "API_KEY" else None)
        assert resolve("{{secret:API_KEY}}", c) == "s3cr3t"

    def test_absent_secret_raises_rather_than_yielding_empty(self) -> None:
        c = BindingContext(secret_resolver=lambda k: None)
        with pytest.raises(BindingError):
            resolve("{{secret:MISSING}}", c)

    def test_no_resolver_means_no_secret_access(self) -> None:
        """Nothing here reads the credential store directly, which also keeps secrets
        out of unit tests by default."""
        with pytest.raises(BindingError):
            resolve("{{secret:ANY}}", BindingContext())


class TestDependencyExtraction:
    def test_node_deps_finds_every_referenced_id(self) -> None:
        cfg = {
            "prompt": "{{nodes.a.output}} vs {{nodes.b.output.x}}",
            "n": ["{{nodes.c.artifact}}"],
        }
        assert node_deps(cfg) == {"a", "b", "c"}

    def test_non_node_roots_are_not_dependencies(self) -> None:
        assert node_deps({"p": "{{inputs.x}} {{item}} {{iter}}"}) == set()

    def test_refs_in_walks_nested_structures(self) -> None:
        assert sorted(refs_in({"a": ["{{x}}"], "b": {"c": "{{y}}"}})) == ["x", "y"]


class TestResolveExprDirect:
    def test_expression_bodies_resolve_without_braces(self, ctx) -> None:
        assert resolve_expr("inputs.topic", ctx) == "checkout latency"
