"""Template macros — one-liner patterns that expand into core nodes (Slice 9a).

The engine has twelve node kinds and no more. A judge panel, adversarial verification, intent
routing and a multi-modal sweep are all COMPOSITIONS of those kinds — adding `judge_panel` as
a thirteenth kind would mean a scheduler case, a dispatcher, a resume path, a rewind story and
a widget row for it.

The load-bearing claims:

* **expansion is at definition time**, so nothing downstream knows macros exist. The engine,
  the journal, the resume cache and the rewind cascade all see ordinary nodes;
* **every expansion validates**, which is the whole reason to expand before the write — a
  malformed macro fails at save with a real node path instead of mid-run with a synthetic one;
* **scoring legs are `infer`, never `stage`** (WF2-R16). A judge that reads text and returns a
  score needs no session, no tools and no lane slot; using `stage` turns a five-judge panel
  into five concurrent subagent sessions;
* **the input is never mutated**, because the save path re-reads the author's original for its
  response and expanding under it would make stored and returned specs silently differ.
"""

from __future__ import annotations

import copy

import pytest

from personalclaw.workflows.macros import (
    MAX_DEPTH,
    MacroError,
    expand_spec,
    has_macros,
    macro_names,
)
from personalclaw.workflows.validator import validate_spec


def _spec(node: dict) -> dict:
    # `root_seq`, not `s`: a macro case in this file legitimately uses `id: "s"`, and a wrapper
    # sharing it would fail the duplicate-id check for a reason that is the fixture's fault.
    return {"name": "t", "root": {"kind": "sequence", "id": "root_seq", "children": [node]}}


def _nodes(spec: dict) -> dict[str, dict]:
    """Every node in an expanded spec, keyed by id — what the assertions address."""
    out: dict[str, dict] = {}

    def walk(n) -> None:
        if not isinstance(n, dict):
            return
        if n.get("id"):
            out[str(n["id"])] = n
        for k in n.get("children") or []:
            walk(k)
        walk(n.get("body"))
        for v in (n.get("cases") or {}).values():
            walk(v)
        walk(n.get("default"))

    walk(spec.get("root"))
    return out


class TestRegistry:
    def test_the_four_documented_macros_exist(self) -> None:
        assert macro_names() == ["judge_panel", "research_sweep", "route", "verify_panel"]

    def test_an_unknown_macro_names_the_available_ones(self) -> None:
        """A typo'd macro is the common authoring error, and "unknown macro" alone leaves the
        author guessing at the spelling."""
        with pytest.raises(MacroError) as exc:
            expand_spec(_spec({"macro": "judge_pannel", "id": "x", "config": {}}))
        assert "judge_panel" in str(exc.value)


class TestPurity:
    def test_the_input_spec_is_never_mutated(self) -> None:
        """The save path re-reads the author's original for its response; expanding under it
        would make the stored spec and the returned one silently differ."""
        spec = _spec(
            {
                "macro": "route",
                "id": "r",
                "config": {"subject": "x"},
                "cases": {"a": {"kind": "transform", "id": "ta", "config": {"expr": 1}}},
            }
        )
        before = copy.deepcopy(spec)
        expand_spec(spec)
        assert spec == before

    def test_a_spec_with_no_macros_is_unchanged(self) -> None:
        spec = _spec({"kind": "transform", "id": "t", "config": {"expr": 1}})
        assert expand_spec(spec) == spec

    def test_nothing_macro_shaped_survives_expansion(self) -> None:
        """The invariant everything downstream relies on: by the time a run starts there are no
        macros left, so no engine component needs to know they exist."""
        spec = _spec(
            {
                "macro": "judge_panel",
                "id": "p",
                "config": {"subject": "x", "lenses": ["a", "b"]},
            }
        )
        assert has_macros(spec) is True
        assert has_macros(expand_spec(spec)) is False


class TestJudgePanel:
    def _panel(self) -> dict:
        return expand_spec(
            _spec(
                {
                    "macro": "judge_panel",
                    "id": "review",
                    "config": {
                        "subject": "{{inputs.design}}",
                        "criteria": "is it right?",
                        "lenses": [
                            "correctness",
                            {"name": "UX / a11y", "prompt": "who gets stuck?"},
                        ],
                    },
                }
            )
        )

    def test_it_expands_to_parallel_infer_plus_a_transform(self) -> None:
        nodes = _nodes(self._panel())
        assert nodes["review_judges"]["kind"] == "parallel"
        assert nodes["review_synthesis"]["kind"] == "transform"

    def test_every_judge_is_infer_not_stage(self) -> None:
        """WF2-R16. A judge reads text and returns a score — no session, no tools, no lane
        slot. `stage` would make a five-judge panel five concurrent subagent sessions."""
        judges = self._panel()["root"]["children"][0]["children"][0]["children"]
        assert judges
        assert all(j["kind"] == "infer" for j in judges)

    def test_each_judge_gets_its_own_lens_in_its_prompt(self) -> None:
        """Diversity, not redundancy: N identical prompts catch a flaky answer, N different
        lenses catch a failure mode the others are blind to."""
        judges = self._panel()["root"]["children"][0]["children"][0]["children"]
        prompts = [j["config"]["prompt"] for j in judges]
        assert any("correctness" in p for p in prompts)
        assert any("UX / a11y" in p for p in prompts)
        assert any("who gets stuck?" in p for p in prompts)

    def test_a_lens_name_becomes_a_safe_node_id(self) -> None:
        """Node ids address the journal and the resume cache, so "UX / a11y" must not become an
        id with a slash in it."""
        nodes = _nodes(self._panel())
        assert "review_ux_a11y" in nodes

    def test_the_synthesis_is_zero_token(self) -> None:
        """Ranking N scores is arithmetic. Spending a model call on it would make the panel's
        cheapest step its most expensive."""
        assert _nodes(self._panel())["review_synthesis"]["kind"] == "transform"

    def test_the_synthesis_reads_every_judge(self) -> None:
        """A panel that dropped a judge from the synthesis would silently weight it at zero."""
        expr = _nodes(self._panel())["review_synthesis"]["config"]["expr"]
        rendered = str(expr)
        assert "review_correctness" in rendered
        assert "review_ux_a11y" in rendered

    def test_it_honours_a_declared_model_tier(self) -> None:
        panel = expand_spec(
            _spec(
                {
                    "macro": "judge_panel",
                    "id": "p",
                    "config": {"subject": "x", "lenses": ["a"], "model_tier": "reasoning"},
                }
            )
        )
        judges = panel["root"]["children"][0]["children"][0]["children"]
        assert judges[0]["config"]["model_tier"] == "reasoning"

    def test_missing_lenses_is_a_clear_error(self) -> None:
        with pytest.raises(MacroError, match="lenses"):
            expand_spec(_spec({"macro": "judge_panel", "id": "p", "config": {"subject": "x"}}))

    def test_a_nameless_lens_is_refused(self) -> None:
        with pytest.raises(MacroError, match="name"):
            expand_spec(
                _spec(
                    {
                        "macro": "judge_panel",
                        "id": "p",
                        "config": {"subject": "x", "lenses": [{"prompt": "no name"}]},
                    }
                )
            )


class TestVerifyPanel:
    def _panel(self) -> dict:
        return expand_spec(
            _spec(
                {
                    "macro": "verify_panel",
                    "id": "v",
                    "config": {"findings": "{{nodes.find.output.findings}}"},
                }
            )
        )

    def test_it_expands_to_a_pipelined_foreach_of_infer(self) -> None:
        nodes = _nodes(self._panel())
        assert nodes["v_verify"]["kind"] == "foreach"
        assert nodes["v_verify"]["config"]["pipeline"] is True
        assert nodes["v_refute"]["kind"] == "infer"

    def test_the_prompt_asks_to_REFUTE_not_to_confirm(self) -> None:
        """A verifier asked "is this real?" agrees, because agreeing is locally plausible.
        Asking it to attack the finding is what makes survival mean anything."""
        prompt = _nodes(self._panel())["v_refute"]["config"]["prompt"]
        assert "REFUTE" in prompt

    def test_it_defaults_to_refuted_when_uncertain(self) -> None:
        """The asymmetry is deliberate: a false finding acted on costs more than a real one
        missed by one verifier."""
        assert "refuted=true" in _nodes(self._panel())["v_refute"]["config"]["prompt"]

    def test_an_item_error_skips_rather_than_halting(self) -> None:
        """A verifier that errored is not evidence the finding is fake, so it must not sink the
        rest of the sweep."""
        assert _nodes(self._panel())["v_verify"]["config"]["on_item_error"] == "skip"

    def test_missing_findings_is_a_clear_error(self) -> None:
        with pytest.raises(MacroError, match="findings"):
            expand_spec(_spec({"macro": "verify_panel", "id": "v", "config": {}}))


class TestRoute:
    def _route(self) -> dict:
        return expand_spec(
            _spec(
                {
                    "macro": "route",
                    "id": "triage",
                    "config": {"subject": "{{inputs.task}}", "criteria": "how big?"},
                    "cases": {
                        "small": {"kind": "transform", "id": "quick", "config": {"expr": 1}},
                        "large": {"kind": "stage", "id": "full", "config": {"prompt": "do it"}},
                    },
                }
            )
        )

    def test_it_expands_to_classify_then_branch(self) -> None:
        nodes = _nodes(self._route())
        assert nodes["triage_classify"]["kind"] == "infer"
        assert nodes["triage_dispatch"]["kind"] == "branch"

    def test_the_classifier_defaults_to_the_FAST_tier(self) -> None:
        """Choosing which of three paths to take is a cheap judgment. Paying reasoning-tier
        prices to route INTO a reasoning-tier branch doubles the decision's cost for nothing."""
        assert _nodes(self._route())["triage_classify"]["config"]["model_tier"] == "fast"

    def test_the_branch_declares_the_enum_so_coverage_is_checked_at_save(self) -> None:
        """Without it, an uncovered category raises a binding error mid-run — after the
        classifier has already spent its tokens."""
        assert _nodes(self._route())["triage_dispatch"]["config"]["enum"] == ["small", "large"]

    def test_the_branch_reads_the_classifiers_category(self) -> None:
        on = _nodes(self._route())["triage_dispatch"]["config"]["on"]
        assert on == "{{nodes.triage_classify.output.category}}"

    def test_the_categories_reach_the_classifier_prompt(self) -> None:
        """A classifier that was not told the categories invents its own."""
        prompt = _nodes(self._route())["triage_classify"]["config"]["prompt"]
        assert "small" in prompt and "large" in prompt

    def test_a_default_case_is_carried_through(self) -> None:
        routed = expand_spec(
            _spec(
                {
                    "macro": "route",
                    "id": "r",
                    "config": {"subject": "x"},
                    "cases": {"a": {"kind": "transform", "id": "ta", "config": {"expr": 1}}},
                    "default": {"kind": "transform", "id": "fallback", "config": {"expr": 0}},
                }
            )
        )
        assert "fallback" in _nodes(routed)

    def test_missing_cases_is_a_clear_error(self) -> None:
        with pytest.raises(MacroError, match="cases"):
            expand_spec(_spec({"macro": "route", "id": "r", "config": {"subject": "x"}}))


class TestResearchSweep:
    def _sweep(self) -> dict:
        return expand_spec(
            _spec(
                {
                    "macro": "research_sweep",
                    "id": "sweep",
                    "config": {
                        "question": "{{inputs.q}}",
                        "modes": ["by-content", "by-entity", "by-time"],
                    },
                }
            )
        )

    def test_it_expands_to_parallel_search_then_dedup_then_per_source_read(self) -> None:
        nodes = _nodes(self._sweep())
        assert nodes["sweep_sweep"]["kind"] == "parallel"
        assert nodes["sweep_sources"]["kind"] == "transform"
        assert nodes["sweep_read"]["kind"] == "foreach"

    def test_the_search_legs_are_stage_and_the_extraction_leg_is_infer(self) -> None:
        """Searching needs tools; extracting only reads text. Making extraction a `stage` would
        spend a subagent session per source."""
        nodes = _nodes(self._sweep())
        searches = nodes["sweep_sweep"]["children"]
        assert all(s["kind"] == "stage" for s in searches)
        assert nodes["sweep_extract"]["kind"] == "infer"

    def test_one_search_leg_per_mode_each_naming_its_angle(self) -> None:
        """One angle finds what that angle can see; the sweep exists because the angles are
        blind to different things."""
        searches = _nodes(self._sweep())["sweep_sweep"]["children"]
        assert len(searches) == 3
        prompts = [s["config"]["prompt"] for s in searches]
        assert any("by-content" in p for p in prompts)
        assert any("by-time" in p for p in prompts)

    def test_the_read_leg_is_pipelined(self) -> None:
        """Sources are independent; a barrier would waste the gap between fastest and slowest."""
        assert _nodes(self._sweep())["sweep_read"]["config"]["pipeline"] is True

    def test_missing_modes_is_a_clear_error(self) -> None:
        with pytest.raises(MacroError, match="modes"):
            expand_spec(_spec({"macro": "research_sweep", "id": "s", "config": {"question": "q"}}))


class TestNesting:
    def test_a_macro_inside_a_container_expands(self) -> None:
        spec = {
            "name": "t",
            "root": {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"macro": "judge_panel", "id": "j", "config": {"subject": "x", "lenses": ["a"]}}
                ],
            },
        }
        assert has_macros(expand_spec(spec)) is False

    def test_a_macro_inside_a_branch_case_expands(self) -> None:
        spec = _spec(
            {
                "kind": "branch",
                "id": "b",
                "config": {"on": "{{inputs.x}}"},
                "cases": {
                    "deep": {
                        "macro": "research_sweep",
                        "id": "s",
                        "config": {"question": "q", "modes": ["by-content"]},
                    }
                },
            }
        )
        assert "s_sweep" in _nodes(expand_spec(spec))

    def test_a_macro_inside_a_loop_body_expands(self) -> None:
        spec = _spec(
            {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 2},
                "body": {"macro": "verify_panel", "id": "v", "config": {"findings": "{{x}}"}},
            }
        )
        assert "v_refute" in _nodes(expand_spec(spec))

    def test_runaway_recursion_fails_loudly(self) -> None:
        """A macro expanding into itself is an authoring bug; a RecursionError would report it
        as a crash with no spec path in it."""
        import personalclaw.workflows.macros as m

        original = dict(m._MACROS)
        try:
            m._MACROS["loopy"] = lambda node: {"macro": "loopy", "id": "x", "config": {}}
            with pytest.raises(MacroError, match=str(MAX_DEPTH)):
                expand_spec(_spec({"macro": "loopy", "id": "x", "config": {}}))
        finally:
            m._MACROS.clear()
            m._MACROS.update(original)


class TestExpansionsValidate:
    """The reason to expand before the write: a malformed macro must fail at save with a real
    node path, not mid-run with a synthetic one."""

    # Each case is self-contained: a binding must reference a node the SAME spec creates, or the
    # validator's unknown-node-ref check fires on the fixture rather than on the macro. The
    # verify case therefore reads the judge panel's synthesis, and the judge panel travels with
    # it — which also makes the pair the realistic composition (find, then refute).
    JUDGE = {"macro": "judge_panel", "id": "j", "config": {"subject": "x", "lenses": ["a", "b"]}}
    CASES = [
        [JUDGE],
        [
            JUDGE,
            {
                "macro": "verify_panel",
                "id": "v",
                "config": {"findings": "{{nodes.j_synthesis.output.findings}}"},
            },
        ],
        [
            {
                "macro": "research_sweep",
                "id": "sw",
                "config": {"question": "q", "modes": ["by-content", "by-time"]},
            }
        ],
        [
            {
                "macro": "route",
                "id": "r",
                "config": {"subject": "x"},
                "cases": {"a": {"kind": "transform", "id": "ta", "config": {"expr": 1}}},
            }
        ],
    ]

    @pytest.mark.parametrize("nodes", CASES, ids=lambda ns: str(ns[-1]["macro"]))
    def test_each_macro_expands_to_a_STRICTLY_valid_spec(self, nodes: list) -> None:
        spec = {
            "name": "t",
            "root": {"kind": "sequence", "id": "root_seq", "children": copy.deepcopy(nodes)},
        }
        result = validate_spec(expand_spec(spec), strict=True)
        assert result.issues == [], [i.to_dict() for i in result.issues]

    def test_all_four_together_still_validate(self) -> None:
        """The composition case: four macros in one spec must not collide on a generated node
        id. Every generated id is prefixed with the macro's own id, which is what makes that
        safe — a panel and a sweep in one spec would otherwise both want `_sources`."""
        children: list = []
        for group in self.CASES:
            for node in group:
                if not any(c.get("id") == node.get("id") for c in children):
                    children.append(copy.deepcopy(node))
        spec = {"name": "t", "root": {"kind": "sequence", "id": "root_seq", "children": children}}
        result = validate_spec(expand_spec(spec), strict=True)
        assert result.issues == [], [i.to_dict() for i in result.issues]
