"""The outcome record as a GENERAL ledger facility (PP-9).

`pending_outcome`/`outcome_resolved` used to be openable by one producer — a decision-making
workflow node — with `instance_path`/`node_id`/`epoch` welded into the record shape. This module
covers the generalized facility and the two non-decision producers wired onto it in the same
change, because a facility that is available and uncalled is exactly the declared-but-inert class
`PLATFORM-PRIMITIVES` §3 exists to kill.

What is asserted here, and why each clause is load-bearing:

* the CLOSED producer/source vocabulary — a typo becomes a loud `ValueError` at the open, not a
  producer nobody can query for later;
* IDEMPOTENCY via `pending_event_id` — `open_questions` subtracts answered questions by set
  membership, so a second resolver pass writes nothing. Driven by resolving the SAME question
  twice, not by reading the code;
* `measured` vs `inconclusive` drive DIFFERENT DECAY — the two resolutions map onto different
  `learning.decay` profiles, and at the same age the kernel keeps the measured evidence while
  pruning the inconclusive one. The direction is railed against the kernel's own table, so the
  two cannot drift into agreeing;
* the ledger-sourced measurement — ground truth read off the producer's own log, ordered by FILE
  POSITION rather than `seq` (a second writer for one run restarts its sequence at 1);
* both WIRED producers — a `publish:` artifact and a gate escalation actually open a question when
  driven, proven end-to-end rather than by grepping for the call.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.learning import decay
from personalclaw.ledger import outcomes
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import InstanceState, Node, WorkflowRun


@pytest.fixture
def home(tmp_path, monkeypatch):
    """`workflows.store` binds `config_dir` at import, so the env var is what isolates it."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _run(name: str = "pp9") -> WorkflowRun:
    return store_mod.create(WorkflowRun(id="", workflow_name=name))


# ── the closed vocabulary ──


def test_an_unknown_producer_is_refused(home):
    """A closed set, because a mistyped producer writes a record no consumer will ever match and
    the mistake surfaces as missing data weeks later."""
    with pytest.raises(ValueError, match="unknown outcome producer"):
        journal_mod.Journal(_run().id).open_outcome(
            producer="descision", subject="s", metric="m", horizon_secs=1.0
        )


def test_an_unknown_metric_source_is_refused(home):
    """The source is what tells the resolver HOW to read ground truth. An unknown one would be
    silently skipped forever, which reads exactly like a question still inside its horizon."""
    with pytest.raises(ValueError, match="unknown outcome metric source"):
        journal_mod.Journal(_run().id).open_outcome(
            producer=outcomes.PRODUCER_CONTROL,
            subject="s",
            metric="m",
            horizon_secs=1.0,
            metric_source="prometheus",
        )


def test_every_producer_can_open_a_question(home):
    """The generalization itself: five producers, one facility, one record shape. A decision is one
    of them and not the shape the others have to imitate."""
    run = _run()
    journal = journal_mod.Journal(run.id)
    for producer in sorted(outcomes.PRODUCERS):
        journal.open_outcome(
            producer=producer, subject=f"{producer} bet", metric="m", horizon_secs=1.0
        )
    opened = journal_mod.ledger(run.id, kinds={journal_mod.PENDING_OUTCOME})
    assert {e["producer"] for e in opened} == set(outcomes.PRODUCERS)


def test_a_producer_keeps_its_own_context_fields(home):
    """`context` rides through untouched: the facility does not know what a slug or an instance
    path is, and a producer that had to translate its coordinates into a node path is how the
    mechanism ended up decision-shaped the first time."""
    run = _run()
    record = journal_mod.Journal(run.id).open_outcome(
        producer=outcomes.PRODUCER_PUBLISH,
        subject="published artifact `weekly-digest`",
        metric="artifact.weekly-digest.consumed",
        horizon_secs=60.0,
        slug="weekly-digest",
        action="create",
    )
    assert record["slug"] == "weekly-digest"
    assert record["action"] == "create"


# ── idempotency via pending_event_id ──


def _question(**over: Any) -> dict[str, Any]:
    base = {
        "kind": journal_mod.PENDING_OUTCOME,
        "event_id": "R-evt-1",
        "producer": outcomes.PRODUCER_DECISION,
        "subject": "chose A",
        "metric": "lesson.m",
        "metric_source": outcomes.SOURCE_MEMORY,
        "horizon_secs": 10.0,
        "baseline": 0.5,
        "ts": "2026-08-14T00:00:00Z",
    }
    base.update(over)
    return base


def test_an_answered_question_is_not_open(home):
    """The idempotency primitive, in isolation: a resolution citing the question's `event_id`
    removes it from the open set. No flag file, no timestamp — the ledger IS the state."""
    events = [
        _question(),
        {"kind": journal_mod.OUTCOME_RESOLVED, "pending_event_id": "R-evt-1"},
    ]
    assert outcomes.open_questions(events) == []


def test_an_unrelated_resolution_does_not_close_a_question(home):
    """A resolution citing a different question leaves this one open — otherwise one run's
    resolution would silently answer every question beside it."""
    events = [
        _question(),
        {"kind": journal_mod.OUTCOME_RESOLVED, "pending_event_id": "R-evt-99"},
    ]
    assert [q.event_id for q in outcomes.open_questions(events)] == ["R-evt-1"]


def test_a_question_with_no_event_id_is_ignored(home):
    """A question that cannot be cited back cannot be closed idempotently, so it would re-resolve
    on every tick forever. Ignoring it is the lesser failure."""
    assert outcomes.open_questions([_question(event_id="")]) == []


def test_a_pre_generalization_record_reads_back_as_a_decision(home):
    """Records written before PP-9 carry no `producer`/`metric_source`. Tolerant reads mean the
    resolver grades them as the decision questions they were instead of skipping them."""
    (question,) = outcomes.open_questions(
        [{k: v for k, v in _question().items() if k not in ("producer", "metric_source")}]
    )
    assert question.producer == outcomes.PRODUCER_DECISION
    assert question.metric_source == outcomes.SOURCE_MEMORY


# ── measured vs inconclusive drive different decay ──


def test_the_two_resolutions_map_onto_different_decay_profiles(home):
    """Not "the field exists": the two resolutions name DIFFERENT profiles in the one decay
    kernel, and the inconclusive one is the faster of the two."""
    measured = outcomes.decay_profile(outcomes.MEASURED)
    inconclusive = outcomes.decay_profile(outcomes.INCONCLUSIVE)
    assert measured != inconclusive
    assert measured in decay.KIND_MULTIPLIERS and inconclusive in decay.KIND_MULTIPLIERS
    assert decay.KIND_MULTIPLIERS[inconclusive] > decay.KIND_MULTIPLIERS[measured]


def test_an_inconclusive_outcome_decays_out_while_a_measured_one_survives(home):
    """The rule stated as an outcome rather than a rate: at the SAME age the kernel prunes the
    evidence of a bet nobody could grade and keeps the evidence of one that was measured. An
    unmeasurable outcome ageing at a measured one's rate would let a permanently unreadable metric
    sit in the library looking like a confirmed result."""
    age = 60.0  # active days
    measured = decay.evaluate(
        kind=outcomes.decay_profile(outcomes.MEASURED), active_days_since_use=age
    )
    inconclusive = decay.evaluate(
        kind=outcomes.decay_profile(outcomes.INCONCLUSIVE), active_days_since_use=age
    )
    assert inconclusive.strength < measured.strength
    assert inconclusive.prune and not measured.prune


def test_the_proposal_tier_follows_the_resolution(home):
    """One place decides what a resolution is worth to the human-gated queue, so the resolver
    cannot rank a measurement as a hunch in one branch and a hunch as a measurement in another."""
    assert outcomes.evidence_strength(outcomes.MEASURED) == "correlated"
    assert outcomes.evidence_strength(outcomes.INCONCLUSIVE) == "anecdotal"
    assert outcomes.confidence(outcomes.MEASURED, -0.6) == pytest.approx(0.6)
    assert outcomes.confidence(outcomes.INCONCLUSIVE, -0.6) == outcomes.INCONCLUSIVE_CONFIDENCE


def test_an_unreadable_metric_resolves_inconclusive_rather_than_zero(home):
    """A fabricated measurement is worse than an honest "could not tell": None is a closure with
    weaker evidence, not a measurement of 0."""
    assert outcomes.resolution_for(None) == outcomes.INCONCLUSIVE
    assert outcomes.resolution_for(0.0) == outcomes.MEASURED


def test_a_resolution_stamps_the_ageing_rule_on_the_record(home):
    """The record carries its own decay profile so a later consumer reads the ageing rule off the
    ledger instead of re-deriving it and picking a different one."""
    run = _run()
    resolved = journal_mod.Journal(run.id).resolve_outcome(
        pending_event_id="R-evt-1",
        producer=outcomes.PRODUCER_CONTROL,
        subject="rail `x` declared",
        metric="control.x.fired",
        baseline=1.0,
        measured=None,
        score=0.0,
        resolution=outcomes.INCONCLUSIVE,
    )
    assert resolved["decay_profile"] == outcomes.decay_profile(outcomes.INCONCLUSIVE)


# ── the ledger-sourced measurement ──


def _escalation(event_id: str = "R-evt-1") -> dict[str, Any]:
    return _question(
        event_id=event_id,
        producer=outcomes.PRODUCER_ESCALATION,
        metric=journal_mod.CONFIRMATION_RESOLVED,
        metric_source=outcomes.SOURCE_LEDGER,
        match={"confirmation_id": "c-1"},
        value_field="approved",
        baseline=1.0,
    )


def _answer(confirmation_id: str = "c-1", approved: bool = True) -> dict[str, Any]:
    return {
        "kind": journal_mod.CONFIRMATION_RESOLVED,
        "confirmation_id": confirmation_id,
        "approved": approved,
    }


def test_a_boolean_value_field_is_a_measurement(home):
    (question,) = outcomes.open_questions([_escalation()])
    events = [_escalation(), _answer(approved=True)]
    assert outcomes.measure_from_events(question, events) == 1.0
    assert outcomes.measure_from_events(question, [_escalation(), _answer(approved=False)]) == 0.0


def test_a_non_matching_event_does_not_measure(home):
    """`match` is what keeps two concurrent gates in one run from answering each other."""
    (question,) = outcomes.open_questions([_escalation()])
    assert outcomes.measure_from_events(question, [_escalation(), _answer("c-2")]) is None


def test_an_event_BEFORE_the_question_does_not_measure(home):
    """Ground truth has to arrive after the bet, or a previous gate's answer would grade this
    one. Ordered by file position: the log is append-only, and a second writer built for the same
    run restarts `seq` at 1."""
    (question,) = outcomes.open_questions([_escalation()])
    assert outcomes.measure_from_events(question, [_answer(), _escalation()]) is None


def test_the_last_matching_answer_wins(home):
    """A gate answered, rewound and answered again resolves to the answer that stuck."""
    (question,) = outcomes.open_questions([_escalation()])
    events = [_escalation(), _answer(approved=True), _answer(approved=False)]
    assert outcomes.measure_from_events(question, events) == 0.0


def test_presence_alone_measures_when_no_value_field_is_declared(home):
    """A control that only needs to know whether it ever fired declares no `value_field`: the
    event's existence is the measurement."""
    question_record = _escalation()
    question_record.pop("value_field")
    (question,) = outcomes.open_questions([question_record])
    assert outcomes.measure_from_events(question, [question_record, _answer()]) == 1.0


# ── producer 1: a published artifact opens a question ──


class _FakeArtifact:
    slug = "weekly-digest"
    content = ""


class _FakeProvider:
    """The narrow slice `apply_publish` touches. Real enough to drive the seam end to end."""

    readonly = False

    def find_similar(self, name: str) -> None:
        return None

    def get(self, slug: str) -> None:  # pragma: no cover - only reached when find_similar hits
        return None

    def create(self, **kwargs: Any) -> _FakeArtifact:
        return _FakeArtifact()


def _publish(run_id: str, monkeypatch) -> Any:
    from personalclaw.workflows.engine import NodeResult, apply_publish

    monkeypatch.setattr(
        "personalclaw.artifacts.registry.get_provider", lambda *a, **k: _FakeProvider()
    )
    node = Node.from_dict(
        {"kind": "stage", "id": "write", "config": {"prompt": "x", "publish": "Weekly digest"}}
    )
    return apply_publish(
        node, NodeResult(state=InstanceState.DONE, output="a body worth reading"), run_id=run_id
    )


def test_publishing_an_artifact_opens_an_outcome(home, monkeypatch):
    """Producer 1, driven through the real `publish:` seam. Publishing records what the run DID;
    the question records what it was FOR — and without it an artifact stream nobody reads is
    indistinguishable from a busy outbox."""
    run = _run()
    result = _publish(run.id, monkeypatch)
    assert result.published["slug"] == "weekly-digest"

    (question,) = journal_mod.ledger(run.id, kinds={journal_mod.PENDING_OUTCOME})
    assert question["producer"] == outcomes.PRODUCER_PUBLISH
    assert question["metric"] == "artifact.weekly-digest.consumed"
    assert question["metric_source"] == outcomes.SOURCE_MEMORY
    # the bet: a deliverable is for somebody, so one consumption is the baseline to beat
    assert question["baseline"] == 1.0
    assert question["horizon_secs"] > 0.0


def test_a_publish_with_no_run_opens_nothing(home, monkeypatch):
    """A run-less publish (a bare `apply_publish` call) has no ledger to write to. It must not
    invent one — a question in nobody's log can never be resolved."""
    _publish("", monkeypatch)
    assert journal_mod.ledger("", kinds={journal_mod.PENDING_OUTCOME}) == []


# Producer 2 — a gate escalation — is driven end to end against a real parked gate in
# `test_workflows_confirm_emission.py`, beside the `confirmation_pending` emission it rides.
