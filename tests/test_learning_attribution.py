"""Predict-then-verify, WIRED — the curator grades what a human accepted (WF2LEA-5 / §3.1).

`accountability.py` decides the five-way verdict from before/after failure rates; this suite covers
the ORCHESTRATION that makes criterion 9 real — the half that reads the Run Ledger, bridges the
accept→observe→grade gap, and files the revert. Everything runs against the REAL proposal store, the
REAL Run Ledger (`Journal` over `store`), and the REAL config loader, all repointed at a tmp home —
not hand-built state, so a mismatch between what `proposals.accept` persists and what
`grade_accepted_changes` reads shows up here rather than in production.

The clauses WF2LEA-5's `done_when` names:

* accepting a proposal SNAPSHOTS the bet (target + predicted_fixes + before-rates) the instant
  before `accept` unlinks the proposal file — the only moment it is still knowable;
* the curator grades every recorded change with ≥MIN_RUNS post-acceptance runs of its target,
  computing after-rates from the ledger and calling `accountability.attribute`;
* a HARMFUL verdict auto-files a revert PROPOSAL through the shared queue (never applied);
* an EFFECTIVE/INEFFECTIVE/MIXED verdict files NO revert;
* grading is idempotent, inert-by-data, and gated on `learning.attribution_enabled`.
"""

from __future__ import annotations

import pytest

from personalclaw.learning import accountability
from personalclaw.learning import attribution as A
from personalclaw.learning import proposals as P
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import Failure, FailureClass, RunStatus, WorkflowRun


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Repoint the proposal store, attribution store, workflows store, and inbox at a tmp home.

    `proposals`/`attribution` resolve `config_dir` inside their functions, but `workflows.store`
    binds it at import — so, like the run-end suite, setting `PERSONALCLAW_HOME` (re-read live by
    `config_dir()`) is what actually isolates the whole write path. The inbox side-effects are
    neutralized so an accept does not try to resolve a real inbox item.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    return tmp_path


def _run(name: str, *, status: RunStatus = RunStatus.COMPLETE) -> WorkflowRun:
    run = store_mod.create(WorkflowRun(id="", workflow_name=name))
    run.status = status
    return store_mod.save(run)


def _fail(run: WorkflowRun, node: str, text: str, *, exhausted: bool = True) -> None:
    journal_mod.Journal(run.id).step_failed(
        f"root.{node}",
        node,
        epoch=1,
        failure=Failure(failure_class=FailureClass.INTERNAL, cause_plain=text),
        attempt=1,
        retries_exhausted=exhausted,
    )


def _accept_a_change(target: str, predicted: list[str]) -> P.Proposal:
    """File and accept a proposal for `target` predicting `predicted` fixes → returns the accepted
    proposal. The accept path is what records the attribution snapshot."""
    manifest = P.ChangeManifest(
        component=target,
        failure_pattern="x",
        evidence_refs=["r"],
        root_cause="x",
        targeted_fix="x",
        predicted_fixes=predicted,
    )
    verdict, prop = P.enqueue(
        kind=P.Kind.TEMPLATE_DIFF.value,
        title=f"tune {target}",
        body="a change with predictions",
        target=target,
        provenance="refiner",
        change_manifest=manifest,
        evidence_refs=["r1"],
        evidence_strength="correlated",
        confidence=0.6,
        occurrences=3,
        min_evidence=1,
    )
    assert prop is not None, verdict
    return P.accept(prop.id, actor="user")


_SCHEMA = "ValueError: schema validation failed: unexpected field bar"
_CODE = "AttributeError: NoneType has no attribute foo"


# ── accept-time: the bet is snapshotted ──


def test_accepting_a_change_records_it_for_grading(home):
    """The instant a human accepts, the target + predicted_fixes + before-rates are frozen — the
    only moment they are knowable, since `accept` unlinks the proposal two lines later."""
    before = _run("nightly", status=RunStatus.FAILED)
    _fail(before, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])

    (rec,) = A._all()
    assert rec.target == "nightly"
    assert rec.predicted_fixes == ["schema_violation"]
    assert rec.before == {"schema_violation": 1.0}
    assert before.id in rec.baseline_run_ids
    assert rec.source == "refiner"
    assert not rec.resolved and rec.verdict == "PENDING"


def test_a_change_with_no_target_is_not_recorded(home):
    """Nothing to scope failure rates to → nothing to attribute → a record that could only ever be
    INEFFECTIVE is noise, so it is never written."""
    verdict, prop = P.enqueue(
        kind=P.Kind.LESSON_BATCH.value,
        title="a targetless lesson",
        body="body",
        target="",
        provenance="inferred",
        occurrences=1,
        min_evidence=1,
    )
    assert prop is not None
    P.accept(prop.id, actor="user")
    assert A._all() == []


def test_recording_is_off_when_attribution_disabled(home, monkeypatch):
    from personalclaw.config.loader import AppConfig

    base = AppConfig.load()
    monkeypatch.setattr(A, "_attribution_enabled", lambda: False)
    _run("nightly", status=RunStatus.FAILED)
    _accept_a_change("nightly", ["schema_violation"])
    assert A._all() == []
    assert base.learning.attribution_enabled is True  # default stays on


# ── grading: the verdict ladder end to end ──


def test_pending_until_enough_post_acceptance_runs(home):
    before = _run("nightly", status=RunStatus.FAILED)
    _fail(before, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    # only two clean runs since acceptance — below MIN_RUNS
    for _ in range(2):
        _run("nightly")
    report = A.grade_accepted_changes()
    assert report["graded"] == 0 and report["pending"] == 1
    (rec,) = A._all()
    assert rec.verdict == "PENDING" and not rec.resolved


def test_a_change_that_delivered_is_EFFECTIVE_and_files_no_revert(home):
    """Predicted schema failures, and after acceptance the schema rate fell to zero over enough
    runs with nothing new breaking → EFFECTIVE, no revert."""
    before = _run("nightly", status=RunStatus.FAILED)
    _fail(before, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    for _ in range(3):  # clean runs after acceptance
        _run("nightly")
    report = A.grade_accepted_changes()
    assert report["graded"] == 1 and report["effective"] == 1 and report["reverts"] == 0
    (rec,) = A._all()
    assert rec.verdict == accountability.Verdict.EFFECTIVE.value
    assert rec.resolved and rec.after == {}
    assert P.list_pending(kind=P.Kind.RETIREMENT.value) == []


def test_a_harmful_change_auto_files_a_revert_that_names_what_broke(home):
    """Predicted a schema fix that never landed, and a NEW code failure appeared after acceptance →
    HARMFUL → a revert PROPOSAL is filed naming the regression."""
    b1 = _run("nightly", status=RunStatus.FAILED)
    _fail(b1, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    # after acceptance: schema still failing AND a brand-new code regression
    for _ in range(3):
        r = _run("nightly", status=RunStatus.FAILED)
        _fail(r, "load", _SCHEMA)
        _fail(r, "transform", _CODE)

    report = A.grade_accepted_changes()
    assert report["harmful"] == 1 and report["reverts"] == 1
    (rev,) = P.list_pending(kind=P.Kind.RETIREMENT.value)
    assert "nightly" in rev.title
    assert "code" in rev.body  # the unattributed regression is named
    assert rev.provenance == "inferred"
    (rec,) = A._all()
    assert rec.verdict == accountability.Verdict.HARMFUL.value
    assert rec.revert_proposal_id == rev.id


def test_a_mixed_change_files_no_revert(home):
    """A predicted fix landed AND something new regressed → MIXED: the change did something wanted,
    so reverting is the user's call, not automatic."""
    before = _run("nightly", status=RunStatus.FAILED)
    _fail(before, "load", _SCHEMA)  # baseline has schema only, no code
    _accept_a_change("nightly", ["schema_violation"])
    # after: schema is gone (predicted fix landed) but a brand-new code regression appeared
    for _ in range(3):
        r = _run("nightly", status=RunStatus.FAILED)
        _fail(r, "transform", _CODE)

    report = A.grade_accepted_changes()
    assert report["mixed"] == 1 and report["reverts"] == 0
    (rec,) = A._all()
    assert rec.verdict == accountability.Verdict.MIXED.value
    assert P.list_pending(kind=P.Kind.RETIREMENT.value) == []


# ── idempotency, scope, gating ──


def test_grading_is_idempotent(home):
    """A resolved record is skipped on the next tick, so a HARMFUL revert is filed exactly once."""
    b1 = _run("nightly", status=RunStatus.FAILED)
    _fail(b1, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    for _ in range(3):  # predicted schema fix never landed AND code regressed → HARMFUL
        r = _run("nightly", status=RunStatus.FAILED)
        _fail(r, "load", _SCHEMA)
        _fail(r, "transform", _CODE)

    first = A.grade_accepted_changes()
    second = A.grade_accepted_changes()
    assert first["graded"] == 1 and second["graded"] == 0
    assert len(P.list_pending(kind=P.Kind.RETIREMENT.value)) == 1


def test_only_the_targets_own_runs_are_scored(home):
    """Failure rates are scoped by `workflow_name == target`: a regression in an UNRELATED template
    after acceptance must not make this change look harmful."""
    b1 = _run("nightly", status=RunStatus.FAILED)
    _fail(b1, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    for _ in range(3):  # nightly is now clean
        _run("nightly")
    for _ in range(3):  # a DIFFERENT template is on fire
        r = _run("other-template", status=RunStatus.FAILED)
        _fail(r, "x", _CODE)

    report = A.grade_accepted_changes()
    assert report["effective"] == 1 and report["reverts"] == 0


def test_non_terminal_runs_are_not_counted_as_evidence(home):
    """A still-running post-acceptance run has not failed or passed yet, so it is not enough
    evidence and the change stays PENDING."""
    before = _run("nightly", status=RunStatus.FAILED)
    _fail(before, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    for _ in range(5):
        _run("nightly", status=RunStatus.RUNNING)  # non-terminal
    report = A.grade_accepted_changes()
    assert report["pending"] == 1 and report["graded"] == 0


def test_inert_by_data_when_nothing_accepted(home):
    assert A.grade_accepted_changes() == {
        "graded": 0,
        "pending": 0,
        "harmful": 0,
        "effective": 0,
        "mixed": 0,
        "ineffective": 0,
        "partial": 0,
        "reverts": 0,
    }


def test_grading_is_off_when_attribution_disabled(home, monkeypatch):
    b1 = _run("nightly", status=RunStatus.FAILED)
    _fail(b1, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    for _ in range(3):
        r = _run("nightly", status=RunStatus.FAILED)
        _fail(r, "transform", _CODE)
    monkeypatch.setattr(A, "_attribution_enabled", lambda: False)
    assert A.grade_accepted_changes()["graded"] == 0


# ── the trust readout ──


def test_verdict_history_feeds_proposer_trust(home):
    b1 = _run("nightly", status=RunStatus.FAILED)
    _fail(b1, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    for _ in range(3):  # predicted fix never landed + code regressed → HARMFUL
        r = _run("nightly", status=RunStatus.FAILED)
        _fail(r, "load", _SCHEMA)
        _fail(r, "transform", _CODE)
    A.grade_accepted_changes()

    assert A.verdict_history() == [("refiner", accountability.Verdict.HARMFUL.value)]
    trust = A.proposer_trust_report()
    assert trust[0]["source"] == "refiner"
    assert trust[0]["harm_rate"] == 1.0


# ── the module is no longer inert (criterion 9's end-to-end check) ──


def test_accountability_now_has_a_production_importer():
    """WF2LEA-5's headline: `accountability.py` had ZERO production importers. `attribution` is that
    importer, and it is itself wired into the curator tick in `history.py`."""
    import inspect

    src = inspect.getsource(A)
    assert "accountability" in src
    hist = inspect.getsource(__import__("personalclaw.history", fromlist=["_x"]))
    assert "attribution.grade_accepted_changes" in hist
