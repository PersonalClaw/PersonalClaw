"""The RUN_END learner — a terminal run mines its own Run Ledger for lessons (WF2LEA-4).

LEARNING-FLYWHEEL §3.3 / §7 step 5, the RUN_END cadence. This suite covers the clauses
WF2LEA-4's `done_when` names for the run-end spoke, each against the REAL
`MemoryService`/`VectorMemoryStore`, the REAL Run Ledger (`Journal` over `store`), and the
REAL proposal store (monkeypatched to a tmp home) — not hand-built state:

* the spoke is INERT unless a memory service with a live vector store is injected — the exact
  guard `self_model_observer.observe_turn` uses, so every terminal-run controller test writes
  nothing and never touches the real home;
* a terminal failure files a `lesson_batch` PROPOSAL (never a live lesson) carrying an R8
  failure CAPSULE — repro command, signature, forbidden success modes, bounded evidence —
  keyed by `(template, mode, signature)` so the same mechanism failing twice is ONE proposal;
* the §3.3 environment deny-filter drops world-condition failures before they can teach the
  agent to refuse a valid action;
* a procedural prior is recorded per failed step even when the lesson proposal is
  quota-suppressed;
* the per-run quota bounds how many proposals one pass files.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from personalclaw.learning import proposals as P
from personalclaw.learning import run_end
from personalclaw.memory_service import MemoryService
from personalclaw.vector_memory import VectorMemoryStore
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import Failure, FailureClass, RunStatus, WorkflowRun


@pytest.fixture
def svc():
    store = VectorMemoryStore(db_path=Path(tempfile.mkdtemp()) / "m.db")
    store.init()
    return MemoryService.over_vector_store(store)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the proposal store + workflows store + inbox side effects at a tmp home.

    `personalclaw.learning.proposals` resolves `config_dir` inside its functions, but
    `personalclaw.workflows.store` binds it at module import — so patching the loader symbol
    alone leaves the workflows store on the real `~/.personalclaw`. `config_dir()` re-reads
    `PERSONALCLAW_HOME` live every call, so setting the env var isolates the whole write path.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_surface_in_inbox", lambda prop: None)
    monkeypatch.setattr(P, "_resolve_inbox_item", lambda pid, status: None)
    return tmp_path


def _terminal_run(name: str = "daily-report") -> WorkflowRun:
    run = store_mod.create(WorkflowRun(id="", workflow_name=name))
    run.status = RunStatus.FAILED
    return store_mod.save(run)


def _fail(
    run: WorkflowRun,
    node: str,
    text: str,
    *,
    attempt: int = 1,
    exhausted: bool = True,
    fc: FailureClass = FailureClass.INTERNAL,
) -> None:
    journal_mod.Journal(run.id).step_failed(
        f"root.{node}",
        node,
        epoch=1,
        failure=Failure(failure_class=fc, cause_plain=text),
        attempt=attempt,
        retries_exhausted=exhausted,
    )


# ── inert without a live vector store (the controller-test guarantee) ──


def test_no_vector_store_is_a_noop(home):
    """A null-memory service records nothing and does not raise — this is what makes every
    terminal-run controller test (which injects no memory) write nothing to the real home."""
    run = _terminal_run()
    _fail(run, "transform", "AttributeError: NoneType has no attribute 'foo'")
    assert run_end.capture(run, MemoryService.over_vector_store(None), journal=journal_mod) == {
        "proposed": 0,
        "procedural": 0,
        "filtered": 0,
        "skipped": 0,
    }
    assert P.list_pending(kind=P.Kind.LESSON_BATCH.value) == []


def test_none_service_is_a_noop(home):
    run = _terminal_run()
    _fail(run, "transform", "TypeError: unsupported operand")
    assert run_end.capture(run, None, journal=journal_mod)["proposed"] == 0


def test_a_run_with_no_failures_proposes_nothing(svc, home):
    run = _terminal_run()  # terminal, but no step_failed events
    assert run_end.capture(run, svc, journal=journal_mod)["proposed"] == 0


# ── a terminal failure files a lesson PROPOSAL with an R8 capsule ──


def test_a_terminal_failure_files_a_lesson_proposal(svc, home):
    """§3.3: propose, never install. A worthy terminal failure files ONE `lesson_batch`
    proposal into the shared human-gated queue, tagged for the run-end cadence."""
    run = _terminal_run()
    _fail(run, "transform", "AttributeError: NoneType has no attribute 'foo'")
    report = run_end.capture(run, svc, journal=journal_mod)
    assert report["proposed"] == 1
    (prop,) = P.list_pending(kind=P.Kind.LESSON_BATCH.value)
    assert prop.source_cadence == "run_end"
    assert prop.run_id == run.id
    assert "run_end" in prop.tags and "workflow_run" in prop.tags


def test_the_proposal_body_carries_an_R8_capsule(svc, home):
    """LEARN-R8d: the proposal body embeds a checkable capsule — repro command, signature,
    forbidden success modes — so a later replay can verify the lesson still applies rather
    than re-reading prose."""
    run = _terminal_run()
    _fail(run, "transform", "AttributeError: NoneType has no attribute 'foo'")
    run_end.capture(run, svc, journal=journal_mod)
    (prop,) = P.list_pending(kind=P.Kind.LESSON_BATCH.value)
    assert "Failure capsule" in prop.body
    assert f'workflow_start(name="diagnose-run", inputs={{"run_id": "{run.id}"}})' in prop.body
    assert "must NOT be called success" in prop.body
    # the driving evidence is FENCED, never executable
    assert prop.source_excerpt


def test_the_key_is_template_mode_signature(svc, home):
    """LEARN-R8a/b: the lesson is keyed by `(template, failure_mode, signature)` so it can be
    re-injected on future runs of the SAME template."""
    from personalclaw.learning.detectors import (
        LessonKey,
        classify_failure,
        dedupe_signature,
    )

    text = "ValueError: invalid json payload: unexpected field bar"
    run = _terminal_run("etl")
    _fail(run, "load", text)
    run_end.capture(run, svc, journal=journal_mod)
    (prop,) = P.list_pending(kind=P.Kind.LESSON_BATCH.value)
    expected = LessonKey(
        template="etl", mode=classify_failure(text), signature=dedupe_signature(text)
    ).key
    assert prop.target == expected


# ── one mechanism failing twice is ONE proposal ──


def test_retries_of_one_node_collapse_to_one_proposal(svc, home):
    """`_terminal_failures` keys by node, so a step that failed on every retry contributes ONE
    failure, and the `(template, mode, signature)` key dedups it to a single proposal."""
    run = _terminal_run()
    _fail(run, "transform", "AttributeError: NoneType 'foo'", attempt=1, exhausted=False)
    _fail(run, "transform", "AttributeError: NoneType 'foo'", attempt=2, exhausted=False)
    _fail(run, "transform", "AttributeError: NoneType 'foo'", attempt=3, exhausted=True)
    report = run_end.capture(run, svc, journal=journal_mod)
    assert report["proposed"] == 1
    assert len(P.list_pending(kind=P.Kind.LESSON_BATCH.value)) == 1


# ── the environment deny-filter ──


def test_an_environment_failure_is_filtered_not_proposed(svc, home):
    """§3.3's absolute guardrail: a world condition (a refused connection) must never become a
    lesson, or the agent learns to refuse a valid action later."""
    run = _terminal_run()
    _fail(run, "fetch", "connection refused: could not reach host", fc=FailureClass.NETWORK)
    report = run_end.capture(run, svc, journal=journal_mod)
    assert report["filtered"] == 1
    assert report["proposed"] == 0
    assert P.list_pending(kind=P.Kind.LESSON_BATCH.value) == []


def test_worthy_and_environment_failures_split(svc, home):
    """A run with one real code failure and one network failure files exactly one proposal and
    counts the network one as filtered."""
    run = _terminal_run()
    _fail(run, "transform", "AttributeError: NoneType 'foo'")
    _fail(run, "fetch", "ECONNRESET", fc=FailureClass.NETWORK)
    report = run_end.capture(run, svc, journal=journal_mod)
    assert report["proposed"] == 1
    assert report["filtered"] == 1


# ── procedural prior recorded per failed step ──


def test_a_procedural_prior_is_recorded_for_a_worthy_failure(svc, home):
    """§3.3: a `record_procedural(tool="workflow:<template>/<step>", outcome="failed")` prior is
    recorded so the existing ≥3-failure synthesis surfaces it next time the template is planned."""
    run = _terminal_run("nightly")
    _fail(run, "transform", "AttributeError: NoneType 'foo'")
    report = run_end.capture(run, svc, journal=journal_mod)
    assert report["procedural"] == 1
    key = svc._procedural_key("workflow:nightly/transform", "workflow:nightly/transform", "failed")
    assert svc.get_record(key) is not None


# ── the per-run quota ──


def test_the_per_run_quota_bounds_the_proposals(svc, home, monkeypatch):
    """A pass that files twenty proposals is unreadable, not thorough: the per-run quota caps how
    many one terminal run may file, and the overflow is counted as skipped, not silently dropped."""
    monkeypatch.setattr(P, "quota_remaining", lambda filed, quota=None: max(0, 2 - filed))
    run = _terminal_run("wide")
    # five distinct nodes with distinct code failures → five distinct signatures → five keys.
    # ALPHABETIC attribute names, not attr0..attr4: the signature strips digits, so numbered
    # names would collapse to one key and dedup to a single proposal, masking the quota path.
    for name in ("foo", "bar", "baz", "qux", "quux"):
        _fail(run, f"node_{name}", f"AttributeError: NoneType has no attribute '{name}'")
    report = run_end.capture(run, svc, journal=journal_mod)
    assert report["proposed"] == 2
    assert report["skipped"] == 3
    assert len(P.list_pending(kind=P.Kind.LESSON_BATCH.value)) == 2


# ── best-effort: capture never raises into the terminal writer ──


def test_capture_never_raises_on_a_bad_ledger(svc, home, monkeypatch):
    """`_finish` is the single terminal writer that must not fail, so a ledger read error yields an
    empty report rather than an exception."""

    def _boom(*a, **k):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(journal_mod, "ledger", _boom)
    run = _terminal_run()
    assert run_end.capture(run, svc, journal=journal_mod)["proposed"] == 0
