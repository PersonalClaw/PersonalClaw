"""Predict-then-verify, WIRED — the curator grades what a human accepted.

`accountability.py` decides the five-way verdict from before/after failure rates; this suite covers
the ORCHESTRATION that makes the verdict real — the half that reads the Run Ledger, bridges the
accept→observe→grade gap, and files the revert. Everything runs against the REAL proposal store, the
REAL Run Ledger (`Journal` over `store`), and the REAL config loader, all repointed at a tmp home —
not hand-built state, so a mismatch between what `proposals.accept` persists and what
`grade_accepted_changes` reads shows up here rather than in production.

The properties covered:

* accepting a proposal SNAPSHOTS the bet (target + predicted_fixes + before-rates) the instant
  before `accept` unlinks the proposal file — the only moment it is still knowable;
* the curator grades every recorded change with ≥MIN_RUNS post-acceptance runs of its target,
  computing after-rates from the ledger and calling `accountability.attribute`;
* a HARMFUL verdict auto-files a revert PROPOSAL through the shared queue (never applied);
* an EFFECTIVE/INEFFECTIVE/MIXED verdict files NO revert;
* grading is idempotent, inert-by-data, and gated on `learning.attribution_enabled`.
"""

from __future__ import annotations

import json

import pytest
from table_rows import row

from personalclaw.learning import accountability
from personalclaw.learning import attribution as A
from personalclaw.learning import proposals as P
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import Failure, FailureClass, RunStatus, WorkflowRun
from personalclaw.workflows.step_usage import NOTHING_SENT


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
        usage=NOTHING_SENT,
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


# ── the module is no longer inert (the end-to-end check) ──
#
# The headline: `accountability.py` had ZERO production importers. `attribution` is that importer,
# the curator tick (`learning/curator_tick.py`) runs its grading, and the memory consolidation in
# `history.py` runs the tick. Each link is driven here, with `accountability.attribute` watched and
# still deciding every verdict.


def _watch_attribute(monkeypatch) -> list[tuple[dict, object]]:
    """Every call grading makes to `accountability.attribute`, with what it answered."""
    real = accountability.attribute
    calls: list[tuple[dict, object]] = []

    def watched(**kw):
        answer = real(**kw)
        calls.append((kw, answer))
        return answer

    monkeypatch.setattr(accountability, "attribute", watched)
    return calls


def _accepted_then(kind: str, runs: int, target: str = "nightly") -> None:
    """A schema failure of *target*, a change accepted that predicts its fix, then *runs* runs of
    *target*: clean, or harmful (the schema failure still there and a new code failure beside it).
    """
    before = _run(target, status=RunStatus.FAILED)
    _fail(before, "load", _SCHEMA)
    _accept_a_change(target, ["schema_violation"])
    for _ in range(runs):
        if kind == "clean":
            _run(target)
        else:
            r = _run(target, status=RunStatus.FAILED)
            _fail(r, "load", _SCHEMA)
            _fail(r, "transform", _CODE)


def test_the_curator_tick_grades_every_accepted_change(home, monkeypatch, subtests):
    """`run_curator_tick` grades each accepted change whose runs since are in, by what
    `accountability.attribute` answers for the change's bet and those runs, files the revert a
    HARMFUL verdict owes, and counts what it graded in its summary. Too few runs since leave a
    change PENDING for a later tick, and a curator turned off grades nothing."""
    from personalclaw.learning.curator_tick import run_curator_tick

    calls = _watch_attribute(monkeypatch)
    harm, clean = "harmful", "clean"
    # case, the changes (target, the runs after acceptance: clean or harmful and how many, the
    # verdict after the tick), the config's `learning` section, the tick's note on the grading
    table = [
        ("a change that did harm", [("nightly", harm, 3, "HARMFUL")], {}, "graded=1 reverts=1"),
        ("a change that delivered", [("nightly", clean, 3, "EFFECTIVE")], {}, "graded=1 reverts=0"),
        ("too few runs since", [("nightly", clean, 2, "PENDING")], {}, None),
        (
            "one change ready and one not",
            [("nightly", harm, 3, "HARMFUL"), ("weekly", clean, 1, "PENDING")],
            {},
            "graded=1 reverts=1",
        ),
        (
            "the curator turned off",
            [("nightly", harm, 3, "PENDING")],
            {"curator_enabled": False},
            None,
        ),
    ]
    assert len(table) == 5
    for i, (case, changes, learning, note) in enumerate(table):
        with row(subtests, case=case):
            here = home / f"row-{i}"
            here.mkdir()
            monkeypatch.setenv("PERSONALCLAW_HOME", str(here))
            monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda here=here: here)
            (here / "config.json").write_text(json.dumps({"learning": learning}), encoding="utf-8")
            calls.clear()
            for target, kind, runs, _verdict in changes:
                _accepted_then(kind, runs, target)

            summary = run_curator_tick(None)

            recs = {rec.target: rec for rec in A._all()}
            got = {t: (rec.verdict, rec.resolved) for t, rec in recs.items()}
            want = {t: (verdict, verdict != "PENDING") for t, _k, _r, verdict in changes}
            assert got == want, case
            reverts = [p.id for p in P.list_pending(kind=P.Kind.RETIREMENT.value)]
            owed = [recs[t].revert_proposal_id for t, _k, _r, v in changes if v == "HARMFUL"]
            assert reverts == owed and all(owed), f"{case}: reverts {reverts}, owed {owed}"
            if note is None:
                assert "attribution" not in summary, f"{case}: the tick says {summary!r}"
            else:
                assert f"attribution {note}" in summary.split("; "), f"{case}: {summary!r}"
            if learning.get("curator_enabled") is False:
                assert summary == "" and calls == [], f"{case}: {summary!r}, {len(calls)} gradings"
                continue
            assert len(calls) == len(changes), f"{case}: graded {len(calls)} times"
            asked = sorted((kw["predicted_fixes"], kw["outcome"].runs_after) for kw, _a in calls)
            assert asked == sorted(
                (["schema_violation"], runs) for _t, _k, runs, _v in changes
            ), f"{case}: {asked}"
            answered = sorted((answer.verdict, answer.reason) for _kw, answer in calls)
            assert answered == sorted((rec.verdict, rec.reason) for rec in recs.values()), case


@pytest.mark.asyncio
async def test_the_memory_consolidation_runs_the_tick_that_grades(home, monkeypatch):
    """The cadence the tick runs on: a history consolidation's maintenance. Driven through the real
    `HistoryConsolidator._consolidate_locked`, with a model that answers one line and a memory
    service whose own upkeep does nothing, the pass reaches its maintenance, and the curator tick
    there grades the change its runs since show harmful and files the revert."""
    from unittest.mock import MagicMock

    from personalclaw.history import HistoryConsolidator

    class _QuietService:
        has_vector = False

        def __getattr__(self, _name):
            return lambda *_a, **_k: 0

    calls = _watch_attribute(monkeypatch)
    _accepted_then("harmful", 3)
    (rec,) = A._all()
    assert (rec.verdict, rec.resolved) == ("PENDING", False), "graded before any pass"

    rows = [{"role": "user", "content": "hi", "ts": "2026-08-26"}]
    log = MagicMock()
    log.get_unconsolidated = MagicMock(return_value=(rows, 1))
    log.read_messages = MagicMock(return_value=rows)
    log.get_metadata = MagicMock(return_value={})
    memory = MagicMock()
    memory.read_preferences = MagicMock(return_value="")
    memory.read_projects = MagicMock(return_value="")
    consolidator = HistoryConsolidator(log=log, memory=memory, vector_store=None, migrated=True)
    consolidator._memory_service = _QuietService()  # type: ignore[assignment]

    async def _one_line(_prompt: str, _chat_key: str, **_kw: object):
        return {"history_entry": "one line"}

    consolidator._call_llm = _one_line  # type: ignore[assignment]

    await consolidator._consolidate_locked("k", include_history=True)

    log.mark_consolidated.assert_called_once_with("k", 1)  # the pass ran to its end
    (rec,) = A._all()
    assert (rec.verdict, rec.resolved) == ("HARMFUL", True)
    (rev,) = P.list_pending(kind=P.Kind.RETIREMENT.value)
    assert rev.id == rec.revert_proposal_id and "nightly" in rev.title
    assert A.verdict_history() == [("refiner", "HARMFUL")]
    assert len(calls) == 1 and calls[0][0]["outcome"].runs_after == 3


def test_a_harmful_verdict_also_revokes_standing_autonomy_grants(home, monkeypatch):
    """The revert proposal retires the CHANGE; the revocation retires the
    AUTONOMY. Same drive as the revert test — the revoker is spied, its own semantics
    live in test_guardrails_revocation.py."""
    calls: list[dict] = []
    monkeypatch.setattr(
        "personalclaw.guardrails.ladder.revoke_granted_scopes",
        lambda **kw: calls.append(kw) or [],
    )
    b1 = _run("nightly", status=RunStatus.FAILED)
    _fail(b1, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    for _ in range(3):
        r = _run("nightly", status=RunStatus.FAILED)
        _fail(r, "load", _SCHEMA)
        _fail(r, "transform", _CODE)

    report = A.grade_accepted_changes()

    assert report["harmful"] == 1
    assert len(calls) == 1, "one HARMFUL verdict, one revocation pass"
    assert "nightly" in calls[0]["cause"], "the cause names the change that did damage"
    assert calls[0]["evidence_id"].startswith("attribution:")


def test_an_effective_verdict_revokes_nothing(home, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        "personalclaw.guardrails.ladder.revoke_granted_scopes",
        lambda **kw: calls.append(kw) or [],
    )
    b1 = _run("nightly", status=RunStatus.FAILED)
    _fail(b1, "load", _SCHEMA)
    _accept_a_change("nightly", ["schema_violation"])
    for _ in range(3):
        _run("nightly", status=RunStatus.COMPLETE)

    report = A.grade_accepted_changes()

    assert report["effective"] == 1 and calls == []
