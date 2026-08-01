"""Spec validation — accumulated typed issues, cycle detection, and the security lint.

Two properties are the point (WF2-R12):

* **Never throws, accumulates everything.** An LLM authoring a spec must get every
  problem in one message, not one-per-turn ping-pong. `summary()` is that message.
* **Codes are stable.** An agent branches on `WF_MISSING_PROMPT`; rewording the human
  text must never change the code.

The untrusted-origin lint is an ERROR rather than a warning on purpose (WF2-R9): it is
the mechanical seam that keeps a trigger payload from reaching a prompt unfenced, and
advisory-only would leave that to template-author discipline.
"""

from __future__ import annotations

from personalclaw.workflows.validator import validate_spec


def _codes(spec: dict, *, strict: bool = False) -> set[str]:
    return {i.code for i in validate_spec(spec, strict=strict).issues}


def _wrap(node: dict, name: str = "wf") -> dict:
    return {"name": name, "root": node}


class TestValidSpecs:
    def test_a_realistic_spec_passes(self) -> None:
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "root",
                "children": [
                    {
                        "kind": "infer",
                        "id": "classify",
                        "config": {"prompt": "Classify {{inputs.text}}", "model_tier": "fast"},
                    },
                    {
                        "kind": "parallel",
                        "id": "lenses",
                        "config": {"join": "quorum", "quorum": 2},
                        "children": [
                            {
                                "kind": "infer",
                                "id": "a",
                                "config": {"prompt": "A {{nodes.classify.output}}"},
                            },
                            {"kind": "infer", "id": "b", "config": {"prompt": "B"}},
                            {"kind": "infer", "id": "c", "config": {"prompt": "C"}, "needs": ["a"]},
                        ],
                    },
                    {
                        "kind": "transform",
                        "id": "rank",
                        "config": {"expr": "{{nodes.lenses.output | count}}"},
                    },
                ],
            }
        )
        result = validate_spec(spec)
        assert result.ok, result.summary()
        assert result.summary() == "Spec is valid."

    def test_levels_group_data_dependencies(self) -> None:
        """These are DATA levels, not a schedule — container ordering is the frontier's
        job. `c` needs `a`, so it lands a level later."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}},
                    {"kind": "infer", "id": "c", "config": {"prompt": "y"}, "needs": ["a"]},
                ],
            }
        )
        levels = validate_spec(spec).levels
        assert any("a" in lv for lv in levels)
        a_level = next(i for i, lv in enumerate(levels) if "a" in lv)
        c_level = next(i for i, lv in enumerate(levels) if "c" in lv)
        assert c_level > a_level


class TestAccumulation:
    def test_every_problem_is_reported_in_one_pass(self) -> None:
        """The anti-ping-pong property: nine distinct defects, one call."""
        spec = {
            "name": "Bad Name!",
            "root": {
                "kind": "sequence",
                "children": [
                    {
                        "kind": "parallel",
                        "id": "p",
                        "config": {"join": "quorum", "quorum": 99},
                        "children": [
                            {"kind": "infer", "id": "x", "config": {}},
                            {"kind": "stage", "id": "x", "config": {"prompt": "dup"}},
                        ],
                    },
                    {"kind": "loop", "id": "l", "config": {"mode": "counted", "n": 0}},
                    {"kind": "action", "id": "act", "config": {"token": "ghp_" + "a" * 26}},
                    {"kind": "infer", "id": "g", "config": {"prompt": "{{nodes.nope.output}}"}},
                ],
            },
        }
        codes = _codes(spec)
        for expected in (
            "WF_BAD_NAME",
            "WF_BAD_QUORUM",
            "WF_MISSING_PROMPT",
            "WF_DUPLICATE_NODE_ID",
            "WF_MISSING_BODY",
            "WF_BAD_LOOP_COUNT",
            "WF_MISSING_PROVIDER",
            "WF_INLINE_SECRET",
            "WF_UNKNOWN_NODE_REF",
        ):
            assert expected in codes, f"{expected} missing from {sorted(codes)}"

    def test_summary_lists_severity_code_and_path(self) -> None:
        spec = _wrap({"kind": "infer", "id": "x", "config": {}})
        line = validate_spec(spec).summary()
        assert "[error]" in line and "WF_MISSING_PROMPT" in line and "at root" in line

    def test_no_errors_means_ok_even_with_warnings(self) -> None:
        spec = _wrap({"kind": "transform", "id": "t", "config": {"expr": "{{weird.thing}}"}})
        r = validate_spec(spec, strict=True)
        assert r.warnings and r.ok


class TestStructuralRules:
    def test_empty_containers_are_rejected(self) -> None:
        assert "WF_EMPTY_CONTAINER" in _codes(_wrap({"kind": "sequence", "children": []}))
        assert "WF_EMPTY_CONTAINER" in _codes(_wrap({"kind": "parallel", "children": []}))

    def test_needs_may_only_name_siblings(self) -> None:
        """A cross-container edge would make the tree a graph and break the frontier's
        locality."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}, "needs": ["elsewhere"]}
                ],
            }
        )
        assert "WF_UNKNOWN_NEEDS" in _codes(spec)

    def test_foreach_requires_items_and_body(self) -> None:
        codes = _codes(_wrap({"kind": "foreach", "id": "f", "config": {}}))
        assert {"WF_MISSING_ITEMS", "WF_MISSING_BODY"} <= codes

    def test_loop_modes(self) -> None:
        body = {"kind": "transform", "id": "t", "config": {"expr": "{{iter}}"}}
        assert "WF_MISSING_CONDITION" in _codes(
            _wrap({"kind": "loop", "id": "l", "config": {"mode": "until"}, "body": body})
        )
        assert "WF_BAD_STREAK" in _codes(
            _wrap(
                {
                    "kind": "loop",
                    "id": "l",
                    "config": {"mode": "until_dry", "streak": 0},
                    "body": body,
                }
            )
        )
        assert "WF_BAD_LOOP_MODE" in _codes(
            _wrap({"kind": "loop", "id": "l", "config": {"mode": "forever"}, "body": body})
        )

    def test_gate_kinds_and_their_required_fields(self) -> None:
        assert "WF_BAD_GATE_KIND" in _codes(
            _wrap({"kind": "gate", "id": "g", "config": {"kind": "vibes"}})
        )
        assert "WF_MISSING_VERIFY" in _codes(
            _wrap({"kind": "gate", "id": "g", "config": {"kind": "verify_command"}})
        )
        assert "WF_MISSING_EXPR" in _codes(
            _wrap({"kind": "gate", "id": "g", "config": {"kind": "expression"}})
        )
        assert validate_spec(_wrap({"kind": "gate", "id": "g", "config": {"kind": "approval"}})).ok

    def test_model_tier_is_a_closed_set(self) -> None:
        spec = _wrap({"kind": "infer", "id": "i", "config": {"prompt": "p", "model_tier": "turbo"}})
        assert "WF_BAD_MODEL_TIER" in _codes(spec)

    def test_subworkflow_ref_must_be_a_valid_name(self) -> None:
        """The ref becomes a directory lookup, so a path-shaped ref is a traversal risk."""
        assert "WF_BAD_REF" in _codes(
            _wrap({"kind": "subworkflow", "id": "s", "config": {"ref": "../escape"}})
        )
        assert validate_spec(
            _wrap({"kind": "subworkflow", "id": "s", "config": {"ref": "research@2"}})
        ).ok

    def test_wait_needs_a_duration_or_a_deadline(self) -> None:
        assert "WF_MISSING_WAIT" in _codes(_wrap({"kind": "wait", "id": "w", "config": {}}))


class TestBranchCoverage:
    def test_declared_enum_without_full_cases_is_an_error(self) -> None:
        spec = _wrap(
            {
                "kind": "branch",
                "id": "r",
                "config": {"on": "{{inputs.k}}", "enum": ["bug", "feat", "docs"]},
                "cases": {"bug": {"kind": "action", "id": "f", "config": {"provider": "notify"}}},
            }
        )
        assert "WF_BRANCH_COVERAGE" in _codes(spec)

    def test_a_default_case_satisfies_coverage(self) -> None:
        spec = _wrap(
            {
                "kind": "branch",
                "id": "r",
                "config": {"on": "{{inputs.k}}", "enum": ["bug", "feat"]},
                "cases": {"bug": {"kind": "action", "id": "f", "config": {"provider": "notify"}}},
                "default": {"kind": "action", "id": "d", "config": {"provider": "notify"}},
            }
        )
        assert validate_spec(spec).ok

    def test_branch_needs_an_on_binding_and_cases(self) -> None:
        assert {"WF_MISSING_ON", "WF_EMPTY_BRANCH"} <= _codes(_wrap({"kind": "branch", "id": "b"}))


class TestCycles:
    def test_a_binding_cycle_is_rejected(self) -> None:
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "root",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "{{nodes.b.output}}"}},
                    {"kind": "transform", "id": "b", "config": {"expr": "{{nodes.a.output}}"}},
                ],
            }
        )
        r = validate_spec(spec)
        assert "WF_CYCLE" in {i.code for i in r.issues}
        assert r.levels == []  # no schedule can be derived

    def test_a_needs_cycle_is_rejected(self) -> None:
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}, "needs": ["b"]},
                    {"kind": "infer", "id": "b", "config": {"prompt": "y"}, "needs": ["a"]},
                ],
            }
        )
        assert "WF_CYCLE" in _codes(spec)


class TestSecurityLint:
    def test_untrusted_input_into_a_prompt_is_an_error(self) -> None:
        """The seam that stops a trigger payload reaching a prompt unfenced."""
        for root in ("trigger", "payload", "webhook", "fetched"):
            spec = _wrap(
                {
                    "kind": "infer",
                    "id": "x",
                    "config": {"prompt": f"Summarize: {{{{{root}.body}}}}"},
                }
            )
            assert "WF_UNFENCED_UNTRUSTED" in _codes(spec), root

    def test_a_sanitization_pipe_satisfies_the_lint(self) -> None:
        for pipe in ("xml_escape", "truncate(500)", "json", "slugify"):
            spec = _wrap(
                {
                    "kind": "infer",
                    "id": "x",
                    "config": {"prompt": f"S: {{{{trigger.body | {pipe}}}}}"},
                }
            )
            assert validate_spec(spec).ok, pipe

    def test_untrusted_input_outside_a_prompt_is_allowed(self) -> None:
        """The risk is prompt injection; the same value in a non-prompt field is fine.

        Arguments under `with`, which is where the engine reads them from — a flat `ref_id`
        beside `provider` is separately (and correctly) refused by the action-arg-shape check,
        which would make this test pass or fail for the wrong reason.
        """
        spec = _wrap(
            {
                "kind": "action",
                "id": "a",
                "config": {"provider": "notify", "with": {"ref_id": "{{trigger.id}}"}},
            }
        )
        assert validate_spec(spec).ok

    def test_inline_credentials_are_flagged(self) -> None:
        """A literal key in a spec gets persisted to the journal the flywheel later reads."""
        for value in ("sk-" + "a" * 32, "ghp_" + "b" * 26, "xoxb-" + "c" * 24, "hf_" + "d" * 30):
            spec = _wrap(
                {"kind": "action", "id": "a", "config": {"provider": "bash", "key": value}}
            )
            assert "WF_INLINE_SECRET" in _codes(spec), value

    def test_ordinary_config_strings_are_not_mistaken_for_secrets(self) -> None:
        spec = _wrap(
            {
                "kind": "action",
                "id": "a",
                "config": {"provider": "bash", "command": "echo hello world"},
            }
        )
        assert "WF_INLINE_SECRET" not in _codes(spec)

    def test_unknown_pipes_are_rejected(self) -> None:
        """The closed pipe set is what keeps a spec from becoming an eval surface."""
        spec = _wrap({"kind": "transform", "id": "t", "config": {"expr": "{{inputs.x | eval}}"}})
        assert "WF_UNKNOWN_PIPE" in _codes(spec)


class TestMalformedInput:
    def test_a_non_object_spec_is_reported_not_raised(self) -> None:
        for bad in ([], "string", 42, None):
            r = validate_spec(bad)  # type: ignore[arg-type]
            assert not r.ok and "WF_NOT_AN_OBJECT" in {i.code for i in r.issues}

    def test_a_missing_root_is_reported(self) -> None:
        assert "WF_MISSING_ROOT" in _codes({"name": "x"})

    def test_an_unknown_node_kind_is_reported_not_raised(self) -> None:
        r = validate_spec({"name": "x", "root": {"kind": "quantum"}})
        assert not r.ok and "WF_UNKNOWN_NODE_KIND" in {i.code for i in r.issues}

    def test_depth_and_size_caps(self) -> None:
        node: dict = {"kind": "transform", "id": "leaf", "config": {"expr": "{{iter}}"}}
        for i in range(15):
            node = {"kind": "sequence", "id": f"s{i}", "children": [node]}
        assert "WF_SPEC_TOO_DEEP" in _codes(_wrap(node))

    def test_bad_names_are_rejected(self) -> None:
        for bad in ("Caps", "with space", "../escape", "a" * 64):
            assert "WF_BAD_NAME" in _codes(
                {
                    "name": bad,
                    "root": {
                        "kind": "sequence",
                        "children": [{"kind": "wait", "id": "w", "config": {"duration_secs": 1}}],
                    },
                }
            )
