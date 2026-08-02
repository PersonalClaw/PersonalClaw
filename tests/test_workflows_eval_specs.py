"""Tests for derived per-template eval specs (UP-R13.3, S45).

These run against the SHIPPED library, because a derived benchmark's whole value is that it cannot
drift from the artifact — and a fixture-backed test of a derivation would let it drift into being
confidently wrong, which is the failure a hand-maintained benchmark has.

Two findings measured here, both encoded as regressions:

* A hand-rolled tree walk over guessed key names found **4 of 13** nodes in `deep-research`, because
  the engine's branch children live under `cases`/`default_case`. Every branch subtree was skipped.
* Testing for `stage` alone reported `deep-research` — which makes eight model calls via `infer` —
  as fully deterministic and needing no judge. A confident false claim about the exact property the
  eval exists to check.
"""

import pytest

from personalclaw.workflows import bundled_defs
from personalclaw.workflows.eval_specs import (
    FIXTURES_PER_TEMPLATE,
    EvalSpec,
    _as_metadata,
    _as_spec,
    _walk,
    derive_eval_spec,
    derive_library_suite,
    suite,
)

TEMPLATES = sorted(bundled_defs.template_names())


def parts(name: str) -> tuple[dict, dict]:
    definition = bundled_defs.read_template(name)
    return _as_spec(definition), _as_metadata(definition)


def spec_for(name: str) -> EvalSpec:
    spec, metadata = parts(name)
    return derive_eval_spec(name, spec, metadata)


# ── the walk has to be the engine's ──


def test_the_walk_finds_nodes_inside_a_BRANCH():
    """Measured: a hand-rolled walk over `branches`/`then`/`otherwise` found 4 of 13 nodes in
    `deep-research`, because the engine's branch children live under `cases`/`default_case`. A
    traversal kept in sync with the node algebra by hand is one that will drift."""
    spec = {
        "root": {
            "kind": "branch",
            "id": "b",
            "config": {"on": "{{inputs.x}}"},
            "cases": {"yes": {"kind": "stage", "id": "hit", "config": {"prompt": "x"}}},
            "default": {"kind": "stage", "id": "miss", "config": {"prompt": "y"}},
        }
    }
    found = {n.get("id") for n in _walk(spec["root"])}
    assert {"hit", "miss"} <= found


@pytest.mark.parametrize("name", TEMPLATES)
def test_the_walk_agrees_with_the_engine_about_every_shipped_template(name):
    """The derivation reads the same tree the engine runs. A walk that saw fewer nodes would derive
    a benchmark for a template that does not exist."""
    from personalclaw.workflows.models import Node, walk

    spec, _metadata = parts(name)
    engine = walk(Node.from_dict(spec["root"]))
    assert len(_walk(spec["root"])) == len(engine)


def test_a_malformed_spec_walks_to_nothing_rather_than_raising():
    assert _walk("junk") == []
    assert _walk({"kind": "nonsense-kind"}) == []


# ── the model-bearing test has to use the engine's own kinds ──


def test_an_INFER_only_template_is_not_called_deterministic():
    """Measured: testing for `stage` alone filed `deep-research` (eight `infer` calls) as needing no
    judge. An `infer` is one bounded model call — exactly the output only a judge can assess."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [{"kind": "infer", "id": "i", "config": {"prompt": "x"}}],
        }
    }
    derived = derive_eval_spec("t", spec, {"example_outputs": ["a summary"]})
    assert derived.graded_checks
    assert derived.free is False


def test_a_genuinely_deterministic_template_is_free():
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "action", "id": "a", "config": {"provider": "knowledge-health"}},
                {"kind": "transform", "id": "t", "config": {"expr": "{{nodes.a.output}}"}},
            ],
        }
    }
    derived = derive_eval_spec("t", spec, {"example_outputs": ["a report"]})
    assert derived.graded_checks == []
    assert derived.free is True


def test_deep_research_needs_a_judge():
    """The specific template the bug misfiled. Pinned by name because it is the measurement."""
    assert spec_for("deep-research").free is False


def test_knowledge_health_is_genuinely_free():
    """The other side: it is all zero-token nodes, so its output IS the check. Demanding a model
    judge over a deterministic scan spends a call to form an opinion about arithmetic."""
    assert spec_for("knowledge-health").free is True


# ── fixtures come from the template's own matchable surface ──


@pytest.mark.parametrize("name", TEMPLATES)
def test_every_shipped_template_derives_at_least_one_fixture(name):
    """A benchmark that silently omitted a template would leave the newest one — the most likely to
    be wrong — untested while reporting a pass."""
    assert spec_for(name).fixtures


@pytest.mark.parametrize("name", TEMPLATES)
def test_a_fixture_expects_its_OWN_template(name):
    assert all(f.expected_template == name for f in spec_for(name).fixtures)


@pytest.mark.parametrize("name", TEMPLATES)
def test_fixtures_are_capped(name):
    """This runs on every CI pass. A suite that costs minutes gets marked slow and then skipped."""
    assert len(spec_for(name).fixtures) <= FIXTURES_PER_TEMPLATE


def test_a_fixture_intent_is_built_from_keywords_a_user_would_TYPE():
    derived = derive_eval_spec("t", {"root": {}}, {"keywords": ["audit", "review", "find issues"]})
    assert "audit" in derived.fixtures[0].intent


def test_an_example_output_becomes_a_fixture_phrased_as_a_REQUEST():
    """An intent resembles its desired output far more than it resembles prose about a workflow —
    session 40's T2 finding, reused rather than re-derived."""
    derived = derive_eval_spec(
        "t", {"root": {}}, {"example_outputs": ["A ranked list of findings"]}
    )
    assert any("I need a ranked list" in f.intent for f in derived.fixtures)


def test_a_template_with_no_matchable_surface_still_enters_the_suite():
    """Its missing metadata should show up as a low-confidence match in the report, not as absence
    from it — an omitted template reads as a passing one."""
    derived = derive_eval_spec("lonely-template", {"root": {}}, {})
    assert derived.fixtures[0].intent == "lonely template"


@pytest.mark.parametrize("name", TEMPLATES)
def test_a_fixture_never_expects_a_parameter_the_TREE_does_not_read(name):
    """The mirror of session 42's finding. A fixture asserting a phantom parameter would fail
    forever on a correct template, which trains a maintainer to loosen the assertion."""
    from personalclaw.workflows.contracts import resolve_unfilled_inputs

    spec, _metadata = parts(name)
    readable = {p.name for p in resolve_unfilled_inputs(spec)}
    for fixture in spec_for(name).fixtures:
        assert set(fixture.expected_params) <= readable


def test_expected_params_are_the_REQUIRED_subset():
    """Asserting the exact set would make every added optional parameter a failure, which trains a
    maintainer to update expectations without reading them."""
    spec = {
        "inputs": {"topic": {"required": True}, "depth": {"default": "normal"}},
        "root": {
            "kind": "stage",
            "id": "s",
            "config": {"prompt": "{{inputs.topic}} at {{inputs.depth}}"},
        },
    }
    derived = derive_eval_spec("t", spec, {"keywords": ["k"]})
    assert derived.fixtures[0].expected_params == ["topic"]


# ── structural checks assert only what is PRESENT ──


def test_a_loop_earns_a_bounded_exit_check():
    spec = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "until_dry", "streak": 2},
            "body": {"kind": "stage", "id": "s", "config": {"prompt": "x"}},
        }
    }
    checks = derive_eval_spec("t", spec, {}).structural_checks
    assert any("bounded exit" in c for c in checks)


def test_a_template_with_no_loop_gets_no_loop_check():
    """A check that the plan "should have a gate" on a template with no gate is a design opinion
    masquerading as a regression — and it fails on a correct template."""
    spec = {"root": {"kind": "stage", "id": "s", "config": {"prompt": "x"}}}
    checks = derive_eval_spec("t", spec, {}).structural_checks
    assert not any("loop" in c for c in checks)


def test_gates_are_asserted_to_SURVIVE_parameterization():
    """A parameterized template that lost its judge gate would pass a routing suite while producing
    unverified output — which is the whole gap this module closes."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "w", "config": {"prompt": "x"}},
                {"kind": "gate", "id": "g", "config": {"kind": "judge", "prompt": "ok?"}},
            ],
        }
    }
    checks = derive_eval_spec("t", spec, {}).structural_checks
    assert any("gates survive" in c and "g" in c for c in checks)


@pytest.mark.parametrize("name", TEMPLATES)
def test_every_shipped_template_derives_structural_checks(name):
    assert spec_for(name).structural_checks


# ── parameterization checks ──


def test_a_parameterless_template_says_the_form_stays_empty():
    derived = derive_eval_spec("t", {"root": {"kind": "stage", "id": "s"}}, {})
    assert any("empty" in c for c in derived.parameterization_checks)


def test_required_and_optional_parameters_are_checked_SEPARATELY():
    """Asking for something the template already answers is the most common way a launch form
    becomes tedious, and it is a different defect from failing to ask at all."""
    spec = {
        "inputs": {"topic": {"required": True}, "depth": {"default": "normal"}},
        "root": {
            "kind": "stage",
            "id": "s",
            "config": {"prompt": "{{inputs.topic}} {{inputs.depth}}"},
        },
    }
    checks = derive_eval_spec("t", spec, {}).parameterization_checks
    assert any("required parameters asked" in c for c in checks)
    assert any("defaulted, not asked" in c for c in checks)


# ── graded checks are NAMED, never graded here ──


def test_the_graded_standard_comes_from_the_templates_OWN_claim():
    """Grading anything else would invent a standard the template never claimed, and a template
    failing an invented standard is a benchmark arguing with the library."""
    derived = derive_eval_spec(
        "t",
        {"root": {"kind": "stage", "id": "s"}},
        {"example_outputs": ["a cited research report"]},
    )
    assert derived.graded_checks == ["output is recognizably: a cited research report"]


def test_a_template_with_no_stated_standard_says_WHY_it_has_no_graded_checks():
    """An empty list with no explanation reads as "nothing to grade", which is a claim."""
    derived = derive_eval_spec("t", {"root": {"kind": "stage", "id": "s"}}, {})
    assert "no example_outputs declared" in derived.graded_note


def test_a_deterministic_template_says_why_TOO():
    derived = derive_eval_spec("t", {"root": {"kind": "action", "id": "a"}}, {})
    assert "deterministic" in derived.graded_note


# ── the suite ──


def test_the_suite_separates_free_from_judge_bearing():
    """Folding them together would let a passing CI run be read as "every template was evaluated",
    when the graded half never ran."""
    payload = suite(
        [
            EvalSpec(template="a", graded_checks=["x"]),
            EvalSpec(template="b"),
        ]
    )
    assert payload["needs_judge"] == ["a"]
    assert payload["free_only_templates"] == ["b"]


def test_the_library_suite_covers_every_shipped_template():
    """A benchmark you have to remember to register is one that omits the newest template."""
    payload = derive_library_suite()
    assert payload["templates"] == len(TEMPLATES)
    assert {s["template"] for s in payload["specs"]} == set(TEMPLATES)


def test_the_library_suite_reports_real_counts():
    payload = derive_library_suite()
    assert payload["free_fixtures"] > 0
    assert payload["structural_checks"] > 0


def test_the_library_suite_is_json_serializable():
    """It is an artifact CI and the flywheel both read. A payload that only round-trips in memory
    could not gate anything."""
    import json

    assert json.dumps(derive_library_suite())
