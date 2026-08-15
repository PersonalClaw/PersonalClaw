"""PP-5 — loops emit the platform ledger, closing the flywheel's loop blind spot.

The acceptance bar is not "the rows exist": it is the flywheel producing a PROPOSAL from loop
evidence where before it saw nothing, the loop's trajectory being reconstructable FROM THE LEDGER
ALONE (the old findings/verdicts file store gone, not dual-written), and a supervisor assessment
landing as a `judge_verdict` in the reconciled `JudgeVerdict` vocabulary (WF2LOO-16), not a fifth
dialect.

Every test drives loop cycles through the REAL emit path — `store.record_cycle_findings`
(the worker's file → `step_started`/`step_completed`) and `store.write_verdict`
(→ `judge_verdict`) — under a tmp `PERSONALCLAW_HOME`, because the proposal/staging stores bind
`config_dir` lazily and the env var is what actually isolates the write path.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.learning import loop_end
from personalclaw.learning import proposals as P
from personalclaw.loop import journal as loop_journal
from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate the loop store, proposal store and staging ledger to a tmp home.

    `config_dir()` re-reads `PERSONALCLAW_HOME` on every call, so the env var isolates the write
    path even for stores that bound the loader symbol at import; the loop store's own bound symbol
    is patched too. The staging singleton is reset so a per-test miss count does not accumulate.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    from personalclaw.learning import staging as staging_mod

    monkeypatch.setattr(staging_mod, "_INSTANCE", None)
    yield tmp_path
    staging_mod._INSTANCE = None


def _loop(kind: str = "code", task: str = "add oauth login", complete: bool = True) -> Loop:
    cfg = {"code": {"entry_stage": "design"}, "goal": {"goal_type": "open_ended"}}.get(kind, {})
    loop = store.create(Loop(id="", name="L", kind=kind, task=task, kind_config=cfg))
    if complete:
        store.update_status(loop.id, LoopStatus.COMPLETE)
    return store.get(loop.id)


def _write_finding(loop_id: str, cycle: int, stage: str, **extra) -> None:
    """Write ONE worker finding file — the worker's per-cycle deliverable (unchanged interface)."""
    d = store.loop_dir(loop_id)
    (d / "findings" / f"cycle_{cycle:03d}.json").write_text(
        json.dumps({"cycle": cycle, "stage": stage, "summary": f"{stage} work", **extra})
    )


# ── the headline: the flywheel produces a proposal from loop evidence ──


def test_flywheel_produces_a_proposal_from_loop_evidence(home):
    """Before PP-5 a loop's findings were files the flywheel could not read; after, its cycles are
    on the ledger and `learning.mining` produces a TEMPLATE proposal from them.

    The before/after is in ONE test on purpose: the worker files exist the whole time; what changes
    is whether they were ingested into the ledger the mining reader reads.
    """
    from personalclaw.learning import mining

    loops = [_loop("code") for _ in range(3)]
    for loop in loops:
        _write_finding(loop.id, 1, "implement")
        _write_finding(loop.id, 2, "validate")

    # BEFORE: the worker files exist, but nothing is on the ledger — the blind spot. Mining sees
    # no completed steps for any loop, so it produces nothing.
    traces_before, _miss = mining.positive_path_candidates(
        workflow_name="loop:code", journal=loop_journal, store=loop_end._LoopRunStore()
    )
    assert traces_before == []
    assert not P.list_pending(kind=P.Kind.TEMPLATE.value)

    # THE PP-5 EMIT: ingest each loop's cycles into the ledger (`step_started`/`step_completed`).
    for loop in loops:
        assert store.record_cycle_findings(loop.id) == 2

    # AFTER: three loops took the same named path — a procedure worth naming. The flywheel files it.
    after = loop_end.capture(loops[0], service=None)
    assert after["proposed"] >= 1, "the flywheel mined no proposal from loop evidence"
    pending = P.list_pending(kind=P.Kind.TEMPLATE.value)
    assert pending, "no TEMPLATE proposal reached the human-gated queue"
    assert any("implement" in p.title or "implement" in p.body for p in pending)


def test_mining_reads_loop_steps_off_the_ledger(home):
    """`learning.mining` handed the loop journal reads a loop's completed steps — the reader
    parity that makes a loop a first-class mining subject."""
    from personalclaw.learning import mining

    loop = _loop("code")
    _write_finding(loop.id, 1, "implement")
    _write_finding(loop.id, 2, "validate")

    # Before ingest: no steps on the ledger → an empty "did".
    empty = mining.invert_intent(loop_end._LoopRunView.of(loop), journal=loop_journal)
    assert "no steps completed" in empty.did

    store.record_cycle_findings(loop.id)
    inv = mining.invert_intent(loop_end._LoopRunView.of(loop), journal=loop_journal)
    assert "implement" in inv.did and "validate" in inv.did


# ── read the trajectory back FROM THE LEDGER ALONE ──


def test_trajectory_reconstructs_from_the_ledger_alone(home):
    """The findings/verdicts a reader sees are PROJECTIONS over the ledger, faithful to what the
    loop recorded — and they survive the raw files being deleted, proving the second store is gone.
    """
    loop = _loop("code")
    _write_finding(loop.id, 1, "implement", key_insight="wired the button")
    _write_finding(loop.id, 2, "validate", key_insight="tests pass")
    store.record_cycle_findings(loop.id)
    store.write_verdict(
        loop.id, 2, {"cycle": 2, "verdict": "PASS", "done": True, "quality_score": 4}
    )

    findings = store.get_findings(loop.id)
    assert [f["stage"] for f in findings] == ["implement", "validate"]
    assert findings[0]["key_insight"] == "wired the button"
    # No projection carries the ingest bookkeeping key.
    assert all("_source_file" not in f for f in findings)

    verdicts = store.get_verdicts(loop.id)
    assert len(verdicts) == 1 and verdicts[0]["cycle"] == 2 and verdicts[0]["done"] is True

    # The second store is GONE: delete the worker's raw files and the verdicts dir; the projection
    # is unchanged because it reads the ledger, not the files.
    import shutil

    shutil.rmtree(store.loop_dir(loop.id) / "findings")
    assert not (store.loop_dir(loop.id) / "verdicts").exists()
    assert [f["stage"] for f in store.get_findings(loop.id)] == ["implement", "validate"]
    assert store.get_verdicts(loop.id)[0]["done"] is True

    # The full journal carries the whole trajectory in order, step_started markers included.
    journal_seq = [e["kind"] for e in store.read_jsonl(loop.id, loop_journal.JOURNAL_FILE)]
    assert journal_seq == [
        loop_journal.STEP_STARTED,
        loop_journal.STEP_COMPLETED,
        loop_journal.STEP_STARTED,
        loop_journal.STEP_COMPLETED,
        loop_journal.JUDGE_VERDICT,
    ]
    # The durable ledger mirror (events.jsonl) carries the LEDGER_KINDS the flywheel reads —
    # step_completed + judge_verdict — exactly as the workflow ledger does (step_started is a
    # journal-only progress marker, not a durable ledger kind).
    events_seq = [e["kind"] for e in loop_journal.ledger(loop.id)]
    assert events_seq == [
        loop_journal.STEP_COMPLETED,
        loop_journal.STEP_COMPLETED,
        loop_journal.JUDGE_VERDICT,
    ]


# ── a supervisor assessment lands in the reconciled vocabulary ──


def test_judge_verdict_carries_the_reconciled_vocabulary(home):
    """A supervisor assessment is a `judge_verdict` carrying the reconciled `JudgeVerdict` shape
    (WF2LOO-16), not a loop-local dialect."""
    from personalclaw.workflows.judge_contract import JudgeVerdict, Verdict

    loop = _loop("goal")
    verdict = JudgeVerdict(
        verdict=Verdict.PASS, done_reason="goal met", marginal_value=3.0, quality_score=4.0
    )
    store.write_verdict(loop.id, 1, {"cycle": 1, **verdict.to_dict()})

    events = loop_journal.ledger(loop.id, kinds={loop_journal.JUDGE_VERDICT})
    assert len(events) == 1
    ev = events[0]
    # The reconciled keys are on the ledger record at top level — the same words a workflow verdict
    # carries, so the flywheel reads one vocabulary.
    for key in ("verdict", "done", "marginal_value", "quality_score", "done_reason"):
        assert key in ev, f"reconciled key {key!r} missing from the judge_verdict event"
    assert ev["verdict"] == Verdict.PASS.value
    assert ev["done"] is True
    assert ev["marginal_value"] == 3.0


# ── all four emit points land the right kind ──


def test_the_four_emit_points(home):
    """A cycle → step_started/step_completed, an assessment → judge_verdict, a stall →
    breaker_trip, a reap → watcher_reaped."""
    loop = _loop("code")

    _write_finding(loop.id, 1, "implement")
    store.record_cycle_findings(loop.id)
    store.write_verdict(loop.id, 1, {"cycle": 1, "verdict": "RETRY", "done": False})
    store.record_breaker_trip(loop.id, 1, "the cycle report has not changed")
    store.record_watcher_reaped(loop.id, cycles=1, reason="worker process lost to restart")

    # The durable ledger mirror (events.jsonl) carries the four LEDGER_KINDS.
    events_kinds = {e["kind"] for e in loop_journal.ledger(loop.id)}
    assert events_kinds == {
        loop_journal.STEP_COMPLETED,
        loop_journal.JUDGE_VERDICT,
        loop_journal.BREAKER_TRIP,
        loop_journal.WATCHER_REAPED,
    }
    # step_started is emitted too — a journal-only progress marker, like the workflow engine's.
    journal_kinds = {e["kind"] for e in store.read_jsonl(loop.id, loop_journal.JOURNAL_FILE)}
    assert loop_journal.STEP_STARTED in journal_kinds
    # The breaker/reaper events carry their reason so a refiner can tell WHY the run was cut off.
    trip = loop_journal.ledger(loop.id, kinds={loop_journal.BREAKER_TRIP})[0]
    assert trip["reason"] == "the cycle report has not changed" and trip["cycle"] == 1
    reap = loop_journal.ledger(loop.id, kinds={loop_journal.WATCHER_REAPED})[0]
    assert reap["cycles"] == 1


def test_event_ids_are_stable_across_reopen(home):
    """`LoopJournal.open` recovers `seq`, so a second emit (a later poll, a restart) continues the
    sequence rather than re-minting event ids the file already holds."""
    loop = _loop("code")
    _write_finding(loop.id, 1, "implement")
    store.record_cycle_findings(loop.id)
    _write_finding(loop.id, 2, "validate")
    store.record_cycle_findings(loop.id)

    ids = [e["event_id"] for e in loop_journal.ledger(loop.id)]
    assert len(ids) == len(set(ids)), "duplicate event ids — seq was not recovered on reopen"
    seqs = [e["seq"] for e in store.read_jsonl(loop.id, loop_journal.JOURNAL_FILE)]
    assert seqs == sorted(seqs) and seqs[0] == 1
