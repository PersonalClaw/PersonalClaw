"""S73 — the refiner's acceptance discipline (LEARN-R2 / §3.1).

The flagship spoke, and the one with the most ways to go wrong: an optimizer editing templates
run outcomes will random-walk them under judge noise unless the acceptance rules are strict. §3.1's
"acceptance discipline" section is longer than its mechanism section; each gate is asserted here.

**Measured before writing.** Every prerequisite was already in place — `journal.LEDGER_KINDS`
carries
all the events the refiner reads INCLUDING `user_edited_mid_flight` (§3.1's "gold" signal), and
`mutations.OpKind` is a CLOSED ten-op vocabulary, so a diff is expressed in the engine's own terms
rather than in a second edit language needing its own validator.
`test_evidence_kinds_exist_in_the_real_ledger` is what keeps that true.
"""

from __future__ import annotations

import pytest

from personalclaw.learning.refiner import (
    CHECK_SCORES,
    CRITIC_EPSILON,
    CRITIC_RUNS,
    DIFF_OPS,
    EVIDENCE_KINDS,
    FROZEN_FIELDS,
    GATEOK_REGRESSION_EPS,
    MIN_RUNS_FOR_EVIDENCE,
    MIN_TARGET_IMPROVEMENT,
    STAGNATION_ROUNDS,
    Cluster,
    CriticScore,
    RiskTier,
    build_manifest,
    canary_verdict,
    check_diff,
    check_op,
    clamp01,
    cluster_failures,
    evaluate_diff,
    failure_signature,
    gate_ok,
    judge,
    median,
    risk_tier,
    should_stop,
    top_cluster,
)


def _score(value):
    return CriticScore(scores={name: value for name in CHECK_SCORES})


def _failures(count, *, node="fetch", message="HTTP 503 from api.example.com"):
    return [
        {
            "kind": "step_failed",
            "node_id": node,
            "run_id": f"r{i}",
            "error": f"{message} after {1000 + i}ms (trace a3f9c8d{i})",
        }
        for i in range(count)
    ]


_GOOD = {
    "scores": [_score(0.9), _score(0.88), _score(0.92)],
    "baseline": 0.5,
    "target": "t",
    "before": {"t": 0.5, "a": 0.8},
    "after": {"t": 0.9, "a": 0.8},
}
_OK_OPS = [{"op": "update_node", "node": "fetch", "fields": {"prompt": "retry on 503"}}]


# ── the ledger contract ──


def test_evidence_kinds_exist_in_the_real_ledger():
    """A renamed event would STARVE the refiner silently.

    It would see zero failures and propose nothing, which is indistinguishable from a healthy
    template.
    """
    from personalclaw.workflows.journal import LEDGER_KINDS

    for kind in EVIDENCE_KINDS:
        assert kind in LEDGER_KINDS, f"{kind} is not a real ledger kind"


def test_the_gold_signal_is_among_them():
    """§3.1: a repeated identical hand-fix is the user saying what the template should say."""
    assert "user_edited_mid_flight" in EVIDENCE_KINDS


def test_diff_ops_are_a_subset_of_the_engines_own_vocabulary():
    """Expressed in the engine's terms so accepted diffs are machine-applicable."""
    from personalclaw.workflows.mutations import OpKind

    assert DIFF_OPS < {k.value for k in OpKind}


# ── clustering ──


def test_one_mechanism_across_many_runs_is_ONE_cluster():
    """Without noise-stripping, every failure carries its own run id and path, so 100 instances
    of one bug cluster into a hundred clusters of one, and the refiner proposes against a cluster of
    size 1."""
    clusters = cluster_failures(_failures(5))
    assert len([c for c in clusters if c.node == "fetch"]) == 1
    assert clusters[0].count == 5


def test_signatures_strip_run_specific_noise():
    a = failure_signature("HTTP 503 from api.example.com after 1200ms (trace a3f9c8d1)")
    b = failure_signature("HTTP 503 from api.example.com after 87ms (trace ffff0000)")
    assert a == b and a


def test_different_mechanisms_stay_separate():
    events = _failures(3) + _failures(3, message="permission denied writing /var/db")
    nodes = {c.signature for c in cluster_failures(events)}
    assert len(nodes) >= 2


def test_rank_is_frequency_TIMES_unresolvedness():
    """The product, not the sum: a frequent failure that always self-heals is not worth an edit, and
    neither is a permanent failure that happened once."""
    healed = Cluster(signature="s", count=10, resolved=10)
    permanent = Cluster(signature="s", count=1, resolved=0)
    real = Cluster(signature="s", count=8, resolved=1)
    assert healed.rank == 0.0
    assert real.rank > permanent.rank


def test_a_completion_resolves_failures_on_the_same_node():
    events = _failures(3) + [{"kind": "step_completed", "node_id": "fetch", "run_id": "r9"}]
    cluster = next(c for c in cluster_failures(events) if c.node == "fetch")
    assert cluster.resolved >= 1
    assert cluster.unresolvedness < 1.0


def test_a_repeatedly_skipped_step_is_its_own_mechanism():
    """The user keeps saying this step should not be here — a deletion, not a prompt rewrite."""
    events = [{"kind": "step_skipped", "node_id": "summarize", "run_id": f"r{i}"} for i in range(4)]
    cluster = cluster_failures(events)[0]
    assert "skipped" in cluster.signature and cluster.count == 4


def test_each_skip_is_attributed_to_its_OWN_node_through_the_REAL_writer(tmp_path, monkeypatch):
    """Per-node attribution, asserted against rows the LEDGER WRITER actually produced.

    The regression this pins: `cluster_failures` used to read `event["node"]`/`event["path"]`,
    but `journal.step_skipped` stamps `node_id`/`instance_path` and no writer has ever stamped
    the other two. Every skip therefore attributed to the empty string, and the mechanism this
    module documents — "a repeatedly SKIPPED step is a failure of the template" — could not name
    the step. TWO distinct nodes are required to see it at all: with one skipped node, an
    all-events-under-`""` bucket is indistinguishable from correct attribution.
    """
    from personalclaw.workflows import store
    from personalclaw.workflows.journal import Journal, ledger

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    assert store.config_dir() == home  # the isolation itself, not a real home

    skips = [
        ("skipnode0", "root.children[0]", "summarize"),
        ("skipnode1", "root.children[1]", "translate"),
        ("skipnode2", "root.children[2]", "summarize"),
    ]
    events: list[dict] = []
    for run_id, path, node_id in skips:
        Journal(run_id).step_skipped(path, node_id, epoch=0, actor="user")
        for row in ledger(run_id):
            events.append({**row, "run_id": run_id})

    assert len(events) == len(skips)

    # ── the vacuity floor ──
    # A regression to the old keys is only *catchable* because the real writer stamps NEITHER of
    # them. Assert that, so this test cannot silently pass on hand-built rows that carry `node`.
    for row in events:
        assert "node" not in row, "writer stamps `node_id`; a bare `node` would hide the defect"
        assert "path" not in row, "writer stamps `instance_path`, never `path`"
        assert row["node_id"] and row["instance_path"]

    by_node = {c.node: c for c in cluster_failures(events)}

    # The load-bearing assertion: each skip lands under its OWN node id, and nothing lands in the
    # anonymous bucket the buggy read produced.
    assert "" not in by_node, f"skips collapsed into the anonymous node bucket: {by_node}"
    assert set(by_node) == {"summarize", "translate"}
    assert by_node["summarize"].count == 2
    assert by_node["translate"].count == 1
    assert by_node["summarize"].signature == "skipped summarize"
    assert by_node["translate"].signature == "skipped translate"


def test_hand_fixes_are_captured_verbatim():
    events = [
        {
            "kind": "user_edited_mid_flight",
            "node_id": "fetch",
            "run_id": f"r{i}",
            "ops": ["update_node"],
        }
        for i in range(3)
    ]
    cluster = cluster_failures(events)[0]
    assert cluster.hand_fixes == ["update_node"] * 3


def test_unknown_ledger_kinds_are_ignored_not_guessed_at():
    """The ledger is append-only and gains kinds; reacting to one nobody designed would
    propose against an unintended signal."""
    assert cluster_failures([{"kind": "buffer_seal", "node_id": "x", "run_id": "r1"}]) == []


def test_clustering_survives_malformed_events():
    cluster_failures([None, "nope", 7, {}, {"kind": "step_failed"}])  # type: ignore[list-item]


def test_clusters_come_back_worst_first():
    events = _failures(2, node="a") + _failures(6, node="b")
    assert cluster_failures(events)[0].node == "b"


# ── the power-discipline floor ──


def test_an_under_evidenced_cluster_never_reaches_a_model():
    """Enforced BEFORE the LLM tier: a proposal built from two runs would be rejected later anyway,
    after paying for it."""
    assert top_cluster(cluster_failures(_failures(MIN_RUNS_FOR_EVIDENCE - 1))) is None
    assert top_cluster(cluster_failures(_failures(MIN_RUNS_FOR_EVIDENCE))) is not None


def test_the_floor_counts_DISTINCT_runs_not_occurrences():
    """Three failures in one run is one run's evidence."""
    same_run = [
        {"kind": "step_failed", "node_id": "x", "run_id": "r1", "error": "same thing"}
        for _ in range(5)
    ]
    assert top_cluster(cluster_failures(same_run)) is None


def test_a_fully_resolved_cluster_is_never_the_target():
    events = _failures(4) + [
        {"kind": "step_completed", "node_id": "fetch", "run_id": f"r{i}"} for i in range(8)
    ]
    top = top_cluster(cluster_failures(events))
    assert top is None or top.rank > 0


# ── the frozen region ──


def test_a_prompt_edit_is_allowed():
    assert check_op({"op": "update_node", "node": "a", "fields": {"prompt": "better"}}).allowed


@pytest.mark.parametrize("frozen", sorted(FROZEN_FIELDS))
def test_no_op_may_touch_the_frozen_region(frozen):
    """These decide WHEN a template runs. A self-editing system that can change its own trigger
    conditions drifts without anyone approving the drift."""
    verdict = check_op({"op": "update_node", "node": "a", "fields": {frozen: "x"}})
    assert not verdict.allowed
    assert "frozen region" in verdict.reason


def test_frozen_fields_are_detected_in_every_container():
    for container in ("fields", "config", "set", "patch"):
        assert not check_op({"op": "update_node", container: {"triggers": []}}).allowed
    assert not check_op({"op": "update_node", "field": "triggers"}).allowed


def test_a_run_control_op_is_a_category_error():
    """`rewind`/`run_from`/`fork`/`skip` act on a LIVE run, not a stored template."""
    for name in ("rewind", "run_from", "fork", "skip"):
        verdict = check_op({"op": name, "node": "a"})
        assert not verdict.allowed
        assert "live RUN" in verdict.reason


def test_an_unrecognized_op_is_refused_against_the_engines_vocabulary():
    """A typo must not become a silently-ignored no-op inside an accepted diff."""
    verdict = check_op({"op": "teleport"})
    assert not verdict.allowed
    assert "not one of the engine's ops" in verdict.reason


def test_an_op_with_no_name_is_refused():
    assert not check_op({}).allowed


def test_one_illegal_op_rejects_the_WHOLE_diff():
    """A partially applied diff is a template nobody authored: not what was proposed, and not
    what the user reviewed."""
    ok, refusals = check_diff(_OK_OPS + [{"op": "update_node", "fields": {"id": "x"}}])
    assert not ok and len(refusals) == 1


def test_an_empty_diff_is_refused():
    ok, refusals = check_diff([])
    assert not ok and refusals


# ── the median-of-3 critic ──


def test_a_single_critic_run_can_never_accept():
    """§3.1: "single-run judge acceptance is indistinguishable from noise"."""
    verdict = judge([_score(1.0)], baseline=0.0)
    assert not verdict.accepted
    assert str(CRITIC_RUNS) in verdict.reason


def test_three_runs_with_a_real_margin_accept():
    assert judge([_score(0.9), _score(0.88), _score(0.92)], baseline=0.5).accepted


def test_a_margin_inside_epsilon_is_rejected():
    """Judge jitter alone would otherwise accept roughly half of all no-op diffs."""
    verdict = judge([_score(0.53), _score(0.52), _score(0.54)], baseline=0.5)
    assert not verdict.accepted
    assert "judge noise" in verdict.reason


def test_a_single_enthusiastic_outlier_cannot_carry_acceptance():
    """The whole reason for a median rather than a mean."""
    assert not judge([_score(0.99), _score(0.51), _score(0.52)], baseline=0.5).accepted


def test_a_parse_failure_scores_zero_not_neutral():
    """Reject-by-default: an LLM with no parseable score has endorsed nothing."""
    assert CriticScore(scores={}).total == 0.0
    partial = CriticScore(scores={CHECK_SCORES[0]: 1.0})
    assert partial.total == pytest.approx(1.0 / len(CHECK_SCORES))
    assert not partial.complete


def test_all_four_named_checks_are_scored():
    assert len(CHECK_SCORES) == 4
    assert "safe_to_publish" in CHECK_SCORES
    assert _score(1.0).complete


def test_the_median_helper_handles_both_parities():
    assert median([1.0, 5.0, 2.0]) == 2.0
    assert median([1.0, 3.0]) == 2.0
    assert median([]) == 0.0


def test_the_epsilon_is_documented():
    assert CRITIC_EPSILON == 0.05


# ── the held-out replay gate ──


def test_a_clean_improvement_passes():
    assert gate_ok(target="t", before={"t": 0.5, "a": 0.8}, after={"t": 0.9, "a": 0.8}).passed


def test_an_edit_that_fixes_one_failure_by_breaking_another_is_refused():
    """A regression that looks like progress on the metric it was written against."""
    result = gate_ok(target="t", before={"t": 0.5, "a": 0.8}, after={"t": 0.9, "a": 0.5})
    assert not result.passed
    assert result.regressed == ["a"]


def test_noise_below_epsilon_on_another_cluster_is_tolerated():
    assert gate_ok(target="t", before={"t": 0.5, "a": 0.800}, after={"t": 0.9, "a": 0.795}).passed


def test_a_target_improvement_below_the_floor_is_churn():
    result = gate_ok(target="t", before={"t": 0.50}, after={"t": 0.51})
    assert not result.passed
    assert "churn" in result.reason


def test_a_gain_elsewhere_cannot_substitute_for_the_target():
    """Requiring TARGET improvement stops a diff being accepted for a coincidental gain."""
    assert not gate_ok(target="t", before={"t": 0.5, "a": 0.5}, after={"t": 0.5, "a": 0.99}).passed


def test_an_unmeasured_target_FAILS_rather_than_scoring_zero():
    """ "No evidence" must not read as "no regression"."""
    result = gate_ok(target="ghost", before={"t": 0.5}, after={"t": 0.9})
    assert not result.passed
    assert "unmeasured" in result.reason


def test_a_cluster_that_stopped_being_scored_counts_as_a_regression():
    """It may have started erroring outright; silence is not a pass."""
    result = gate_ok(target="t", before={"t": 0.5, "a": 0.8}, after={"t": 0.9})
    assert not result.passed and "a" in result.regressed


def test_the_gate_constants_are_documented():
    assert GATEOK_REGRESSION_EPS == 0.01
    assert MIN_TARGET_IMPROVEMENT == 0.02


# ── risk tiers ──


def test_the_tier_is_the_riskiest_op_in_the_diff():
    """A destructive delete bundled with four parameter tweaks is a destructive diff."""
    assert risk_tier([{"op": "set_input"}]) == RiskTier.LOW.value
    assert risk_tier([{"op": "update_node"}]) == RiskTier.REVIEW.value
    assert risk_tier([{"op": "set_input"}, {"op": "delete"}]) == RiskTier.MANUAL_ONLY.value


def test_an_empty_diff_is_manual_only():
    assert risk_tier([]) == RiskTier.MANUAL_ONLY.value


def test_there_is_no_auto_tier_to_reach_for():
    """§3.1: any "auto" tier is guardrail-violating — the human-installs invariant is absolute."""
    assert "auto" not in {t.value for t in RiskTier}


# ── the assembled conjunction ──


def test_all_gates_must_pass_for_a_diff_to_surface():
    assert evaluate_diff(ops=_OK_OPS, **_GOOD).surfaced


def test_a_frozen_op_drops_the_diff_before_any_critic_run():
    """Cheapest and most decisive first: paying three critic runs to discover an unfixable op is
    waste."""
    decision = evaluate_diff(ops=[{"op": "update_node", "fields": {"id": "x"}}], **_GOOD)
    assert not decision.surfaced
    assert decision.critic is None


def test_a_thin_critic_pass_drops_the_diff_before_the_gate():
    thin = dict(_GOOD, scores=[_score(0.9)])
    decision = evaluate_diff(ops=_OK_OPS, **thin)
    assert not decision.surfaced
    assert decision.critic is not None and decision.gate is None


def test_a_held_out_regression_drops_a_critic_approved_diff():
    regressing = dict(_GOOD, after={"t": 0.9, "a": 0.4})
    decision = evaluate_diff(ops=_OK_OPS, **regressing)
    assert not decision.surfaced
    assert decision.critic is not None and decision.critic.accepted
    assert decision.gate is not None and not decision.gate.passed


def test_a_dropped_diff_still_records_why():
    """Dropped SILENTLY from the user's view, but the log has to say what happened."""
    decision = evaluate_diff(ops=[{"op": "delete", "fields": {"triggers": []}}], **_GOOD)
    assert not decision.surfaced and decision.refusals


def test_the_decision_serializes_for_a_log():
    payload = evaluate_diff(ops=_OK_OPS, **_GOOD).to_dict()
    assert set(payload) >= {"surfaced", "tier", "refusals", "critic", "gate"}


# ── stop rules, manifests, verdicts ──


def test_a_converged_cycle_stops():
    """A cycle that keeps proposing after convergence spends budget on diffs the critic rejects."""
    stop, reason = should_stop([0.70, 0.7001, 0.70, 0.7002, 0.70])
    assert stop and "converged" in reason


def test_an_improving_cycle_continues():
    assert not should_stop([0.5, 0.6, 0.7, 0.8, 0.9])[0]


def test_a_short_history_never_stops_early():
    assert not should_stop([0.7, 0.7])[0]
    assert STAGNATION_ROUNDS == 5


def test_a_manifest_is_falsifiable_or_it_is_just_an_assertion():
    cluster = Cluster(signature="http 503", count=5, runs=[f"r{i}" for i in range(5)])
    decision = evaluate_diff(ops=_OK_OPS, **_GOOD)
    manifest = build_manifest(
        cluster=cluster, decision=decision, measured_at="2024-01-01T00:00:00Z", model="haiku"
    )
    assert manifest.falsifiable
    assert manifest.run_ids and manifest.metric and manifest.measured_at
    assert manifest.evaluating_model == "haiku"


def test_manifest_confidence_is_derived_not_self_reported():
    """A self-reported confidence is the same ornamental signal §2.5 rejects for helpfulness."""
    decision = evaluate_diff(ops=_OK_OPS, **_GOOD)
    manifest = build_manifest(
        cluster=Cluster(signature="s", runs=["r1"]), decision=decision, measured_at="2024-01-01"
    )
    assert 0.0 <= manifest.confidence <= 1.0
    assert manifest.confidence > 0


def test_manifest_run_ids_are_bounded_and_deduped():
    cluster = Cluster(signature="s", count=99, runs=["r1"] * 50 + [f"x{i}" for i in range(40)])
    manifest = build_manifest(
        cluster=cluster,
        decision=evaluate_diff(ops=_OK_OPS, **_GOOD),
        measured_at="2024-01-01",
    )
    assert len(manifest.run_ids) <= 20
    assert len(manifest.run_ids) == len(set(manifest.run_ids))


@pytest.mark.parametrize(
    "before,after,runs,expected",
    [
        (0.9, 0.5, 5, "HARMFUL"),
        (0.9, 0.9, 5, "INEFFECTIVE"),
        (0.5, 0.9, 5, "EFFECTIVE"),
        (0.5, 0.9, 1, "PENDING"),
        (0.50, 0.51, 5, "PARTIALLY_EFFECTIVE"),
    ],
)
def test_canary_verdicts(before, after, runs, expected):
    assert canary_verdict(before=before, after=after, runs=runs) == expected


def test_a_harmful_verdict_is_reachable_because_it_files_a_revert():
    """§3.1 auto-FILES a revert proposal for HARMFUL — through the queue, never silently."""
    assert canary_verdict(before=1.0, after=0.0, runs=10) == "HARMFUL"


def test_a_single_lucky_run_never_declares_a_diff_effective():
    assert canary_verdict(before=0.0, after=1.0, runs=1) == "PENDING"


def test_clamp01_rejects_by_default_on_nan():
    assert clamp01(float("nan")) == 0.0
    assert clamp01(-5) == 0.0
    assert clamp01(5) == 1.0
