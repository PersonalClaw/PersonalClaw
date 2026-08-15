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


class TestDependencyOrdering:
    """`WF_UNORDERED_DEP` (PP-1) — the engine's two edge lists, reconciled.

    Admission comes from container order plus sibling-only `needs`; bindings are a separate
    graph that never admitted anything. A spec could therefore read
    `{{nodes.x.output}}` from a node running beside `x`, and the only symptom was a mid-run
    `USER` failure telling the author to check an id that was correct.

    Authoring-time only: no scheduling changes here, and `tick.py` is untouched.
    """

    @staticmethod
    def _msg(spec: dict) -> str:
        return "\n".join(
            i.message for i in validate_spec(spec).issues if i.code == "WF_UNORDERED_DEP"
        )

    # ── the `parallel` half: only a `needs` chain orders two branches ──

    def test_a_parallel_sibling_binding_without_needs_is_refused(self) -> None:
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}},
                    {"kind": "infer", "id": "b", "config": {"prompt": "{{nodes.a.output}}"}},
                ],
            }
        )
        assert "WF_UNORDERED_DEP" in _codes(spec)

    def test_the_message_names_the_reader_the_producer_and_the_missing_edge(self) -> None:
        """Three facts, so an author can act without reading the engine."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}},
                    {"kind": "infer", "id": "b", "config": {"prompt": "{{nodes.a.output}}"}},
                ],
            }
        )
        msg = self._msg(spec)
        assert "'b'" in msg  # the reader
        assert "'a'" in msg  # the producer
        assert 'needs: ["a"]' in msg  # the missing edge, spelled as JSON

    def test_a_needs_edge_satisfies_it(self) -> None:
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}},
                    {
                        "kind": "infer",
                        "id": "b",
                        "config": {"prompt": "{{nodes.a.output}}"},
                        "needs": ["a"],
                    },
                ],
            }
        )
        assert validate_spec(spec).ok

    def test_a_transitive_needs_chain_satisfies_it(self) -> None:
        """A `needs` CHAIN to the producer counts, not only a direct edge."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}},
                    {"kind": "infer", "id": "b", "config": {"prompt": "y"}, "needs": ["a"]},
                    {
                        "kind": "infer",
                        "id": "c",
                        "config": {"prompt": "{{nodes.a.output}}"},
                        "needs": ["b"],
                    },
                ],
            }
        )
        assert validate_spec(spec).ok

    def test_a_needs_edge_that_does_not_reach_the_producer_does_not_satisfy_it(self) -> None:
        """Having *some* `needs` is not ordering. Nor is reversing the edge a fix: `needs`
        and bindings feed one Kahn graph, so a backwards `needs` is already a `WF_CYCLE`."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}},
                    {
                        "kind": "infer",
                        "id": "b",
                        "config": {"prompt": "{{nodes.a.output}}"},
                        "needs": ["c"],
                    },
                    {"kind": "infer", "id": "c", "config": {"prompt": "y"}},
                ],
            }
        )
        assert "WF_UNORDERED_DEP" in _codes(spec)

    def test_the_needs_edge_is_wanted_between_the_two_parallel_BRANCHES(self) -> None:
        """The comparison happens at the divergence point, so the edge an author must add
        joins the two branches of the parallel — which may be ancestors of the reader and
        the producer, not the nodes themselves."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {
                        "kind": "sequence",
                        "id": "left",
                        "children": [{"kind": "infer", "id": "prod", "config": {"prompt": "x"}}],
                    },
                    {
                        "kind": "sequence",
                        "id": "right",
                        "children": [
                            {
                                "kind": "infer",
                                "id": "reader",
                                "config": {"prompt": "{{nodes.prod.output}}"},
                            }
                        ],
                    },
                ],
            }
        )
        msg = self._msg(spec)
        assert 'needs: ["left"]' in msg and "right" in msg
        # …and declaring exactly that edge clears it.
        spec["root"]["children"][1]["needs"] = ["left"]
        assert validate_spec(spec).ok

    def test_an_anonymous_parallel_branch_says_to_give_it_an_id(self) -> None:
        """`needs` addresses siblings by id, so an anonymous branch cannot be named. The
        message has to say that rather than suggest an empty edge."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {
                        "kind": "sequence",
                        "children": [{"kind": "infer", "id": "prod", "config": {"prompt": "x"}}],
                    },
                    {
                        "kind": "infer",
                        "id": "reader",
                        "config": {"prompt": "{{nodes.prod.output}}"},
                    },
                ],
            }
        )
        assert "give child 0 an id" in self._msg(spec)

    # ── the `sequence` half: earlier sibling, at whatever depth ──

    def test_an_earlier_sequence_sibling_can_be_read(self) -> None:
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}},
                    {"kind": "infer", "id": "b", "config": {"prompt": "{{nodes.a.output}}"}},
                ],
            }
        )
        assert validate_spec(spec).ok

    def test_a_later_sequence_sibling_cannot_be_read(self) -> None:
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "{{nodes.b.output}}"}},
                    {"kind": "infer", "id": "b", "config": {"prompt": "x"}},
                ],
            }
        )
        assert "WF_UNORDERED_DEP" in _codes(spec)
        assert "AFTER the reader" in self._msg(spec)

    def test_a_producer_nested_inside_an_earlier_sibling_is_ordered(self) -> None:
        """The shape 36 of the shipped library's 111 dependencies use: the producer lives
        inside a `parallel` that is an earlier child of the enclosing `sequence`. A
        container completes only after its children, so the producer is terminal."""
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "parallel",
                        "id": "fan",
                        "children": [
                            {"kind": "infer", "id": "l1", "config": {"prompt": "x"}},
                            {"kind": "infer", "id": "l2", "config": {"prompt": "y"}},
                        ],
                    },
                    {
                        "kind": "transform",
                        "id": "merge",
                        "config": {"expr": "{{nodes.l1.output}} {{nodes.l2.output}}"},
                    },
                ],
            }
        )
        assert validate_spec(spec).ok

    def test_a_producer_inside_an_earlier_foreach_body_is_ordered(self) -> None:
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "foreach",
                        "id": "each",
                        "config": {"items": "{{inputs.xs}}"},
                        "body": {"kind": "infer", "id": "gen", "config": {"prompt": "{{item}}"}},
                    },
                    {
                        "kind": "transform",
                        "id": "sum",
                        "config": {"expr": "{{nodes.gen.output}}"},
                    },
                ],
            }
        )
        assert validate_spec(spec).ok

    def test_a_later_sibling_inside_a_loop_body_cannot_be_read(self) -> None:
        """A loop body is a `sequence` like any other: reading a step that runs later in the
        body fails on the first iteration, when there is no previous output to read."""
        spec = _wrap(
            {
                "kind": "loop",
                "id": "l",
                "config": {"mode": "counted", "n": 3},
                "body": {
                    "kind": "sequence",
                    "id": "body",
                    "children": [
                        {
                            "kind": "infer",
                            "id": "use",
                            "config": {"prompt": "{{nodes.refine.output}}"},
                        },
                        {"kind": "infer", "id": "refine", "config": {"prompt": "x"}},
                    ],
                },
            }
        )
        assert "WF_UNORDERED_DEP" in _codes(spec)

    # ── containment and exclusivity ──

    def test_a_container_cannot_be_read_by_its_own_descendant(self) -> None:
        """A container's output is not available until after the children that produce it."""
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "outer",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "{{nodes.outer.output}}"}}
                ],
            }
        )
        assert "WF_UNORDERED_DEP" in _codes(spec)
        assert "ENCLOSES the reader" in self._msg(spec)

    def test_a_foreach_cannot_bind_items_from_inside_its_own_body(self) -> None:
        spec = _wrap(
            {
                "kind": "foreach",
                "id": "each",
                "config": {"items": "{{nodes.gen.output}}"},
                "body": {"kind": "infer", "id": "gen", "config": {"prompt": "x"}},
            }
        )
        assert "WF_UNORDERED_DEP" in _codes(spec)
        assert "runs INSIDE the reader" in self._msg(spec)

    def test_two_branch_cases_cannot_read_each_other(self) -> None:
        """Only one case ever runs, so no ordering between them exists to declare."""
        spec = _wrap(
            {
                "kind": "branch",
                "id": "b",
                "config": {"on": "{{inputs.k}}"},
                "cases": {
                    "yes": {"kind": "infer", "id": "y", "config": {"prompt": "x"}},
                    "no": {"kind": "infer", "id": "n", "config": {"prompt": "{{nodes.y.output}}"}},
                },
            }
        )
        assert "WF_UNORDERED_DEP" in _codes(spec)
        assert "only one of the two ever runs" in self._msg(spec)

    def test_a_branch_case_producer_stays_readable_after_the_branch(self) -> None:
        """Deliberate scope line: the branch IS ordered before a later sibling, so this is
        not an ordering defect even though the case may not be taken. Whether the producer
        actually ran is a reachability question `PP-2` owns; answering half of it here
        would leave a weaker second reachability rule for `PP-2` to delete."""
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {
                        "kind": "branch",
                        "id": "b",
                        "config": {"on": "{{inputs.k}}"},
                        "cases": {"yes": {"kind": "infer", "id": "y", "config": {"prompt": "x"}}},
                    },
                    {"kind": "transform", "id": "t", "config": {"expr": "{{nodes.y.output}}"}},
                ],
            }
        )
        assert validate_spec(spec).ok

    # ── it does not double-report what another code already owns ──

    def test_a_cycle_suppresses_the_ordering_advice(self) -> None:
        """On a cyclic graph the only advice this rule can give is the advice that closes
        the loop."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "{{nodes.b.output}}"}},
                    {"kind": "transform", "id": "b", "config": {"expr": "{{nodes.a.output}}"}},
                ],
            }
        )
        codes = _codes(spec)
        assert "WF_CYCLE" in codes
        assert "WF_UNORDERED_DEP" not in codes

    def test_a_self_reference_is_reported_as_a_cycle_only(self) -> None:
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "{{nodes.a.output}}"}}
                ],
            }
        )
        codes = _codes(spec)
        assert "WF_CYCLE" in codes
        assert "WF_UNORDERED_DEP" not in codes

    def test_an_unknown_producer_is_one_error_not_two(self) -> None:
        spec = _wrap(
            {
                "kind": "sequence",
                "id": "s",
                "children": [
                    {"kind": "transform", "id": "a", "config": {"expr": "{{nodes.ghost.output}}"}}
                ],
            }
        )
        codes = _codes(spec)
        assert "WF_UNKNOWN_NODE_REF" in codes
        assert "WF_UNORDERED_DEP" not in codes

    def test_an_anonymous_reader_is_still_checked(self) -> None:
        """`_kahn_levels` only tracks nodes that have an id, so an anonymous reader's
        bindings were invisible to every existing dependency check."""
        spec = _wrap(
            {
                "kind": "parallel",
                "id": "p",
                "children": [
                    {"kind": "infer", "id": "a", "config": {"prompt": "x"}},
                    {"kind": "infer", "config": {"prompt": "{{nodes.a.output}}"}},
                ],
            }
        )
        assert "WF_UNORDERED_DEP" in _codes(spec)
        assert "root.children[1]" in self._msg(spec)
