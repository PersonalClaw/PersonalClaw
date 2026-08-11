"""Tests for the structured `rigor: deep` protocol (UP-R5, S45).

The property carrying this module is that **a question the system could answer itself is never
asked**. A grill that asks what it could look up reads as not having paid attention, and the user
who answers it learns that answering carefully is wasted effort — so the next round gets skimmed.

The second property is that a deferral is not a decision. "You decide" recorded as a confirmed
requirement is the guess-as-requirement failure Step-0 exists to prevent, and it is silent: the plan
looks fully specified.
"""

import pytest

from personalclaw.workflows.grill_protocol import (
    BOUNDARY_QUESTION,
    MAX_BATCH,
    OTHER,
    Channel,
    Probe,
    Question,
    boundary_question,
    build_rounds,
    contradiction,
    deep_triggered,
    fold_answers,
    inject_prohibitions,
    pace,
    prohibitions_block,
    route_question,
    split_facts_and_decisions,
    stress_probes,
)
from personalclaw.workflows.human_input import AskKind
from personalclaw.workflows.intent import Intent, Rigor


def q(key: str, text: str = "", **kw) -> Question:
    return Question(key=key, text=text or f"question {key}?", **kw)


# ── facts are looked up, not asked ──


@pytest.mark.parametrize(
    "text,channel",
    [
        ("Which file holds the retry logic?", Channel.CODEBASE),
        ("What does the code currently do on a timeout?", Channel.CODEBASE),
        ("Did I decide on a retention window already?", Channel.MEMORY),
        ("What do I know about the ingest pipeline?", Channel.KNOWLEDGE),
        ("Should this run nightly or weekly?", Channel.ASK),
        ("Who is the audience for the report?", Channel.ASK),
    ],
)
def test_a_discoverable_question_routes_to_a_lookup(text, channel):
    assert route_question(text) is channel


def test_the_memory_and_knowledge_channels_are_never_merged():
    """Two subsystems with two lifecycles. A merged "context fetch" would make it impossible to say
    which one answered, and the plan states the boundary normatively for exactly that reason."""
    _asked, lookups = split_facts_and_decisions(
        [
            q("a", "Did I decide on the format?"),
            q("b", "What do I know about the vendor?"),
            q("c", "Which module owns this?"),
        ]
    )
    assert set(lookups) == {Channel.MEMORY, Channel.KNOWLEDGE, Channel.CODEBASE}
    assert [x.key for x in lookups[Channel.MEMORY]] == ["a"]


def test_a_looked_up_question_is_not_also_asked():
    """The whole point of the split. Asking it anyway would cost the user's attention for something
    already on disk — which is the most expensive thing a grill can do."""
    to_ask, lookups = split_facts_and_decisions(
        [q("f", "Which file defines the schema?"), q("d", "How aggressive should the sweep be?")]
    )
    assert [x.key for x in to_ask] == ["d"]
    assert lookups[Channel.CODEBASE][0].key == "f"


def test_an_explicit_channel_is_honored_over_the_router():
    """A caller that already knows where an answer lives should not have its knowledge overridden by
    a phrase match."""
    to_ask, lookups = split_facts_and_decisions(
        [q("x", "Should we include drafts?", channel=Channel.KNOWLEDGE)]
    )
    assert to_ask == []
    assert lookups[Channel.KNOWLEDGE][0].key == "x"


def test_the_boundary_question_can_never_be_routed_away():
    """ "What must this not do" is not discoverable by definition, and a lookup that captured it
    would silently drop the only question whose answers become hard constraints."""
    rounds = build_rounds([q("a", "Which file is it in?")])
    keys = [x.key for r in rounds for x in r.questions]
    assert "prohibitions" in keys


def test_the_boundary_question_is_present_even_with_no_other_questions():
    rounds = build_rounds([])
    assert [x.text for r in rounds for x in r.questions] == [BOUNDARY_QUESTION]


# ── every question ships a recommendation ──


def test_a_recommendation_arrives_as_the_field_DEFAULT():
    """Not as prose in the label: a recommendation the user has to retype is not one they can
    accept, and accepting is the entire speed story of a structured grill."""
    round_ = pace([q("a", recommended="weekly"), q("b", recommended=3), q("c", recommended="yes")])[
        0
    ]
    ask = round_.to_ask()
    assert {f.name: f.default for f in ask.fields} == {"a": "weekly", "b": 3, "c": "yes"}


def test_a_choice_question_always_carries_an_escape_hatch():
    """A closed option set claims the planner enumerated the possibilities. It is wrong often
    enough that removing the hatch silently forces a wrong answer instead of surfacing a gap."""
    round_ = pace([q("a", choices=["daily", "weekly"])])[0]
    assert OTHER in round_.to_ask().choices


def test_the_escape_hatch_is_not_duplicated():
    round_ = pace([q("a", choices=["daily", OTHER])])[0]
    assert round_.to_ask().choices.count(OTHER) == 1


# ── pacing ──


def test_three_independent_decisions_earn_one_batched_round():
    """A user who can see all of them answers faster than one led through a sequence."""
    rounds = pace([q("a"), q("b"), q("c")])
    assert len(rounds) == 1
    assert rounds[0].batched
    assert len(rounds[0].questions) == 3


def test_two_questions_are_asked_one_at_a_time():
    """Below the threshold a batch is a form for two fields, which reads as heavier than the two
    questions it replaces."""
    rounds = pace([q("a"), q("b")])
    assert [r.batched for r in rounds] == [False, False]


def test_a_dependent_question_never_rides_in_a_batch():
    """Its phrasing is only meaningful after its dependency is answered, so batching it asks it
    wrong — and a question asked wrong gets an answer that looks valid."""
    rounds = pace([q("a"), q("b"), q("c"), q("d", depends_on=["a"])])
    batched = [x.key for r in rounds if r.batched for x in r.questions]
    assert "d" not in batched
    assert rounds[-1].questions[0].key == "d"


def test_a_batch_is_capped():
    rounds = pace([q(f"k{i}") for i in range(MAX_BATCH + 3)])
    assert all(len(r.questions) <= MAX_BATCH for r in rounds)


def test_dependent_questions_come_after_what_they_name():
    rounds = pace([q("b", depends_on=["a"]), q("a", depends_on=[])])
    order = [x.key for r in rounds for x in r.questions]
    assert order.index("a") < order.index("b")


def test_a_dependency_cycle_does_not_hang():
    """A cycle in planner-authored questions is a bug. Silently reordering around one would hide
    it; the order is preserved and the caller can see it."""
    rounds = pace([q("a", depends_on=["b"]), q("b", depends_on=["a"])])
    assert {x.key for r in rounds for x in r.questions} == {"a", "b"}


def test_a_batch_renders_as_ONE_form_submit():
    """Three round-trips for three independent answers is three round-trips the user would have
    given in one."""
    assert pace([q("a"), q("b"), q("c")])[0].to_ask().kind is AskKind.FORM


def test_a_single_choice_question_renders_as_a_choice():
    assert pace([q("a", choices=["x", "y"])])[0].to_ask().kind is AskKind.CHOICE


def test_the_round_carries_the_reason_it_was_paced_that_way():
    assert "independent" in pace([q("a"), q("b"), q("c")])[0].reason


# ── the stress-test phase ──


def test_probes_come_from_the_users_OWN_stated_constraints():
    """A probe about cost posed to someone who never mentioned cost is a question about nothing,
    and it is the fastest way to make a grill feel like a form."""
    probes = stress_probes("I need this done fast, today if possible")
    assert [p.tests for p in probes] == ["speed"]


def test_a_goal_with_no_constraints_earns_no_probes():
    assert stress_probes("write something about latency") == []


def test_probes_are_capped():
    goal = "fast thorough cheap accurate unattended"
    assert len(stress_probes(goal)) <= 3


def test_a_contradiction_fires_only_when_the_answer_picks_the_RULED_OUT_side():
    probe = Probe(scenario="ship it or wait?", tests="speed")
    assert contradiction(probe, "wait, do it properly")
    assert contradiction(probe, "ship it") == ""


def test_an_empty_probe_answer_is_not_a_contradiction():
    """A user who skipped the probe has not contradicted themselves, and reporting one would put a
    finding in the review that nobody said."""
    assert contradiction(Probe(scenario="s", tests="speed"), "") == ""


def test_the_contradiction_names_what_was_stated_and_what_was_chosen():
    text = contradiction(Probe(scenario="s", tests="autonomy"), "stop and wait for me")
    assert "autonomy" in text and "stop and wait" in text


# ── Step-0: never treat a guess as a requirement ──


def test_an_answered_question_is_CONFIRMED():
    step = fold_answers([q("a", "How often?")], {"a": "weekly"})
    assert step.confirmed == ["How often? → weekly"]
    assert step.assumptions == []


def test_an_unanswered_question_with_a_recommendation_is_an_ASSUMPTION():
    """Marked assumed, never confirmed. A user re-reads what they said and skims what the system
    filled in, so an assumption presented as a requirement is the one that ships wrong."""
    step = fold_answers([q("a", "How often?", recommended="weekly")], {})
    assert step.assumptions == ["How often? → weekly (assumed)"]
    assert step.confirmed == []


def test_a_deferral_is_an_assumption_not_a_decision():
    """ "You decide" is the user declining to decide. Recording it as a confirmed requirement is
    the guess-as-requirement failure, and it is silent — the plan looks fully specified."""
    step = fold_answers([q("a", "How often?", recommended="weekly")], {"a": "you decide"})
    assert step.assumptions == ["How often? → weekly (assumed)"]
    assert step.confirmed == []


@pytest.mark.parametrize("answer", ["no preference", "up to you", "idk", "not sure", "either"])
def test_every_deferral_phrasing_is_caught(answer):
    step = fold_answers([q("a", recommended="x")], {"a": answer})
    assert step.confirmed == []


def test_an_unanswered_load_bearing_question_with_no_recommendation_BLOCKS():
    """A plan emitted over an open question is built on a guess nobody labelled."""
    step = fold_answers([q("a", "Which environment?")], {})
    assert step.open_questions == ["Which environment?"]
    assert step.ready is False


def test_an_optional_unanswered_question_does_not_block():
    step = fold_answers([q("a", load_bearing=False)], {})
    assert step.ready is True


def test_a_looked_up_fact_is_confirmed_but_TAGGED():
    """A fact from a subsystem is not the user's word, and a review that could not tell them apart
    would let the system's own finding be quoted back as the user's requirement."""
    step = fold_answers([], {}, looked_up={"retry policy": "3 attempts"})
    assert step.confirmed == ["retry policy → 3 attempts (looked up)"]


def test_an_empty_lookup_result_adds_nothing():
    step = fold_answers([], {}, looked_up={"retry policy": ""})
    assert step.confirmed == []


def test_the_step_zero_payload_reports_readiness():
    payload = fold_answers([q("a", "Which env?")], {}).to_dict()
    assert payload["ready"] is False
    assert payload["open_questions"] == ["Which env?"]


# ── prohibitions are frozen ──


def test_prohibitions_split_on_lines_and_semicolons():
    step = fold_answers(
        [boundary_question()], {"prohibitions": "never touch prod\nno force pushes; no deletes"}
    )
    assert step.prohibitions == ["never touch prod", "no force pushes", "no deletes"]


def test_a_prohibition_containing_a_comma_LIST_is_not_shredded():
    """Splitting on commas would turn one boundary ("don't touch prod, staging, or CI") into three
    partial ones, each of which reads as a different, weaker rule."""
    step = fold_answers([boundary_question()], {"prohibitions": "don't touch prod, staging, or CI"})
    assert step.prohibitions == ["don't touch prod, staging, or CI"]


def test_a_list_answer_is_accepted():
    step = fold_answers([boundary_question()], {"prohibitions": ["no deletes", "no pushes"]})
    assert step.prohibitions == ["no deletes", "no pushes"]


def test_no_boundary_answer_yields_no_prohibitions():
    assert fold_answers([boundary_question()], {}).prohibitions == []


def test_the_block_says_the_rules_are_not_negotiable():
    """A block phrased as advice is one a model weighs against the task. These were set by the
    user, so the framing has to be that they are not up for trade."""
    text = prohibitions_block(["never touch prod"])
    assert "not negotiable" in text
    assert "never touch prod" in text


def test_no_prohibitions_produces_no_block_rather_than_an_empty_header():
    """A header with no rules reads as "no restrictions apply", which is a claim."""
    assert prohibitions_block([]) == ""


def test_the_block_reaches_every_stage_not_just_the_root():
    """A worker sees its own config. A prohibition parked at the root is one no worker reads."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "stage", "id": "a", "config": {"prompt": "x"}},
                {"kind": "stage", "id": "b", "config": {"prompt": "y"}},
            ],
        }
    }
    out = inject_prohibitions(spec, ["never touch prod"])
    for child in out["root"]["children"]:
        assert "never touch prod" in child["config"]["prohibitions"]


def test_the_block_reaches_into_a_loop_body():
    spec = {
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "counted", "n": 2},
            "body": {"kind": "stage", "id": "inner", "config": {"prompt": "x"}},
        }
    }
    out = inject_prohibitions(spec, ["no deletes"])
    assert "no deletes" in out["root"]["body"]["config"]["prohibitions"]


def test_a_zero_token_node_gets_no_block():
    """It runs no model that could violate a prohibition, so an unread block there is noise in the
    spec diff — and noise in a diff is what makes a real change hard to see."""
    spec = {
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [{"kind": "action", "id": "s", "config": {"provider": "x"}}],
        }
    }
    out = inject_prohibitions(spec, ["no deletes"])
    assert "prohibitions" not in (out["root"]["children"][0].get("config") or {})


def test_injection_with_no_prohibitions_returns_the_spec_unchanged():
    spec = {"root": {"kind": "stage", "id": "a", "config": {"prompt": "x"}}}
    assert inject_prohibitions(spec, []) == spec


def test_the_original_spec_is_not_mutated_by_injection():
    spec = {"root": {"kind": "stage", "id": "a", "config": {"prompt": "x"}}}
    inject_prohibitions(spec, ["no deletes"])
    assert "prohibitions" not in spec["root"]["config"]


# ── what triggers the grill ──


def test_a_risk_hit_triggers_the_grill_and_says_which_signal():
    class Hit:
        signal = "destructive_op"

    triggered, why = deep_triggered(Intent(), [Hit()])
    assert triggered
    assert "destructive_op" in why


def test_the_classifiers_deep_routing_triggers_the_grill():
    triggered, why = deep_triggered(Intent(rigor=Rigor.DEEP, reason="complex + uncertain"))
    assert triggered
    assert "complex" in why


def test_an_ordinary_intent_is_not_grilled():
    """Every heavyweight mechanism is entered by escalation, never by default — a planner that
    interrogated every request would be over-machinery for a single user."""
    assert deep_triggered(Intent(rigor=Rigor.STANDARD))[0] is False


def test_the_trigger_reason_is_returned_rather_than_only_logged():
    """A user who was interrogated deserves to know what earned it. An unexplained grill reads as
    the system being slow, not careful."""
    _triggered, why = deep_triggered(Intent(rigor=Rigor.DEEP, reason="high stakes"))
    assert why


# ── the wired plan tool ──


def test_the_plan_tool_ships_the_grill_surface_when_deep():
    """The end-to-end claim: a deep-classified goal arrives with its trigger, its reason, the
    protocol's own vocabulary, and probes derived from the goal's stated constraints."""
    import json

    from personalclaw.workflows import bundled_defs

    bundled_defs.register_bundled_provider()
    from personalclaw.mcp_workflows import _plan

    out = _plan({"goal": "figure out why the pipeline drops records across every region"})
    body = json.loads(out[out.find("{") :])
    grill = body.get("grill") or {}
    assert grill.get("triggered") is True
    assert grill.get("why")
    assert grill["protocol"]["boundary_question"] == BOUNDARY_QUESTION
    assert "memory" in grill["protocol"]["lookup_channels"]
    assert "knowledge" in grill["protocol"]["lookup_channels"]


def test_an_ordinary_goal_gets_no_grill_block():
    """An empty grill block would put a heavyweight affordance in front of a plan that did not earn
    one, which is the over-machinery risk the plan names explicitly."""
    import json

    from personalclaw.workflows import bundled_defs

    bundled_defs.register_bundled_provider()
    from personalclaw.mcp_workflows import _plan

    out = _plan({"goal": "write a note about cold starts", "rigor": "minimal"})
    assert "grill" not in json.loads(out[out.find("{") :])


def test_a_RISK_hit_grills_a_plan_the_classifier_called_standard():
    """Measured: `deep_triggered` implemented the plan's "any risk hit forces deep" rule, but
    nothing was feeding it hits — so a destructive plan the classifier happened to call standard
    went ungrilled. The rule was present and inert."""
    from personalclaw.workflows.autonomy import scan_risk

    spec = {
        "root": {
            "kind": "action",
            "id": "a",
            "config": {"provider": "bash", "with": {"command": "rm -rf /var/data"}},
        }
    }
    hits = scan_risk(spec)
    triggered, why = deep_triggered(Intent(rigor=Rigor.STANDARD), hits)
    assert triggered
    assert "destructive_op" in why


# ── the SAVE seam (WF2LEA-7 clause D): a settled decision must persist ──


def test_only_confirmed_answers_count_as_settled():
    """`grill.SaveFn` was declared and every caller passed None, so a grill could settle a question
    and forget the answer — the next pass re-asked what the user had already decided.

    What counts as settled is the load-bearing distinction. An ASSUMPTION is the planner's guess,
    and persisting it as a lesson would harden a guess into a standing instruction; an OPEN
    QUESTION is by definition unsettled. Only the user's own answers, plus prohibitions,
    may persist.
    """
    from personalclaw.workflows.grill_protocol import settled_decisions

    questions = [
        Question(key="q1", text="Which database?"),
        Question(key="q2", text="Retry limit?", recommended="3"),
        Question(key="q3", text="Deploy target?"),
    ]
    step = fold_answers(questions, {"q1": "postgres", "q2": "", "q3": ""})
    settled = settled_decisions(step)

    assert any("postgres" in s for s in settled), settled
    # q2 fell to an ASSUMPTION (it had a recommendation) and q3 to an OPEN QUESTION.
    assert not any("Retry limit" in s for s in settled), settled
    assert not any("Deploy target" in s for s in settled), settled


def test_a_deferral_is_never_settled():
    """ "You decide" is the guess-as-requirement failure. It must not persist as a decision."""
    from personalclaw.workflows.grill_protocol import settled_decisions

    step = fold_answers([Question(key="q1", text="Which region?")], {"q1": "you decide"})
    assert settled_decisions(step) == []


def test_prohibitions_are_settled_and_labelled():
    """A stated boundary is the most durable decision a grill produces, and it must be
    distinguishable from a choice when a reviewer reads it back."""
    from personalclaw.workflows.grill_protocol import settled_decisions

    step = fold_answers(
        [Question(key="prohibitions", text=BOUNDARY_QUESTION)],
        {"prohibitions": "never touch production"},
    )
    settled = settled_decisions(step)
    assert any(s.startswith("Prohibition:") and "production" in s for s in settled), settled


def test_the_frontend_question_key_format_actually_folds():
    """The inert-wiring trap, pinned: `_persist_grill_decisions` rebuilds the FE's questions
    using `p<phase>s<step>` (LoopPlanReview.tsx:178). If that spelling drifts from the key the
    answers dict uses, `fold_answers` sees every question as UNANSWERED and settles nothing — the
    seam would be fully wired and silently persist zero decisions."""
    from personalclaw.workflows.grill_protocol import settled_decisions

    questions = [Question(key="p0s0", text="Which store?"), Question(key="p0s1", text="Which UI?")]
    answers = {"p0s0": "sqlite", "p0s1": "the dashboard"}
    settled = settled_decisions(fold_answers(questions, answers))
    assert len(settled) == 2, settled
