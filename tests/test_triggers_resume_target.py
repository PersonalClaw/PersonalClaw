"""A scheduled trigger that RESUMES a parked run instead of starting a new one.

🔴 **What was measured before this was written.** `wakeup.resume_for`, `WakeKind.RESUME`,
`Disposition.REQUEUED`, `dispatch.droppable` and `wakeup.retry_queue` were all shipped, documented
and unit tested — and `resume_for` had **zero production callers**. Only `wakeup_for` was reachable,
from `dispatch_fires`, and it always built a `wake`. So §3's own documented fire-path step (`resolve
def / **resume target**`, in `firepath`'s header) had no producer, and `WF2LOO-9`'s
`goal-pursuit-monitor` clause was blocked on a resume target that could not be declared.

**And the consumer half was worse than absent.** `executor.drain` never dispatches on
`Wakeup.kind` — `grep -n kind executor.py` returns exactly one hit, inside a prose docstring. So a
resume queued onto a session inbox is drained and handed to the trigger's ordinary action runner: a
resume that reports success while doing entirely different work, unattended. That is why a resume
target never reaches `deliver()` at all (`Disposition.RESUME_TARGET`) and the loop applies it to the
run directly.

**The bar this file holds itself to.** "A scheduled fire that reaches a mailbox nobody opens is
silently discarded", and every run in this subsystem's history had been started by hand. So the
central test does not assert that a fire was enqueued, or that `resume_run` was called. It builds a
REAL run, drives it to a REAL gate, fires a REAL trigger through `_execute_delivery`, and then reads
**the downstream node's output out of the run's own store** — a value that cannot exist unless the
run actually carried on past the gate.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.triggers import loop as tl
from personalclaw.triggers import wakeup as W
from personalclaw.triggers.models import Outcome, parse_trigger
from personalclaw.workflows import store as wstore
from personalclaw.workflows.controller import EngineServices
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.native_defs import register_native_provider
from personalclaw.workflows.watchdog import WorkflowWatchdog

NOW = 1_700_000_000.0

#: A run that PARKS on an approval gate and has an un-run node behind it. The node behind the gate
#: is the whole proof: its output cannot exist unless the resume actually moved the run forward.
GATED_SPEC = {
    "name": "gated",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "gate", "id": "approve", "config": {"kind": "approval", "prompt": "ok?"}},
            {"kind": "transform", "id": "after", "config": {"expr": "the run carried on"}},
        ],
    },
}

#: Where `store.read_output` finds that node's value.
AFTER_PATH = "root.children[1]"


class _Trigger:
    """The two fields `wakeup_for` reads, plus the id the claim release needs."""

    def __init__(self, workflow: dict, *, trigger_id: str = "schedule:j1") -> None:
        self.id = trigger_id
        self.workflow = workflow
        self.session = "fresh"
        self.kind = "clock"


class _Fire:
    """S88's `DueFire`, reduced to what the dispatcher reads."""

    def __init__(self, trigger: _Trigger) -> None:
        self.trigger = trigger
        self.scheduled_for = NOW - 10
        self.reason = ""


def _resume_trigger(run_id: str, **extra) -> _Trigger:
    return _Trigger({"resume": {"run_id": run_id, **extra}})


# ══════════════════════════════════════════════════════════════════════════════
# the pure half: what a trigger declares, and what the dispatcher makes of it
# ══════════════════════════════════════════════════════════════════════════════


def test_a_trigger_with_NO_resume_target_still_builds_a_droppable_wake():
    """🟢 **THE VACUITY PARTNER for every mutation in this file.** Every trigger authored before a
    resume target existed must behave exactly as before — a `wake`, droppable, so `overlap: skip`
    keeps working. A mutation that breaks the resume path must leave this green, or the red it
    produced proves nothing about the resume path specifically."""
    wakeup = W.wakeup_for(_Fire(_Trigger({"inline": {"provider": "notify"}})), seq=1, now=NOW)
    assert wakeup.kind == W.WakeKind.WAKE.value
    assert wakeup.droppable is True
    assert wakeup.payload["trigger_id"] == "schedule:j1"


def test_a_declared_resume_target_builds_a_NON_droppable_resume():
    """§3.2: "overlap guards must never eat gate answers intended for parked runs." The trigger has
    asked something — it named a parked run — so the fire is no longer droppable."""
    wakeup = W.wakeup_for(_Fire(_resume_trigger("run-7")), seq=1, now=NOW)
    assert wakeup.kind == W.WakeKind.RESUME.value
    assert wakeup.droppable is False
    assert wakeup.payload["run_id"] == "run-7"


def test_the_resume_names_NO_session_because_it_targets_a_RUN():
    """`resume_for`'s contract is that `session_key` names "the session that PARKED". For a
    run-targeted resume that is the run's own session, which the dispatcher cannot derive — and does
    not need, because this wakeup never enters an inbox. Empty is the honest value; deriving the
    TRIGGER's key would name a session that provably did not park this run."""
    assert W.wakeup_for(_Fire(_resume_trigger("run-7")), seq=1, now=NOW).session_key == ""


def test_a_resume_target_NEVER_reaches_the_session_inbox():
    """🔴 THE MEASURED TRAP. `executor.drain` never dispatches on `Wakeup.kind`, so a resume queued
    onto an inbox is drained and executed as the trigger's ordinary ACTION — a resume that looks
    like it worked. `RESUME_TARGET` is what keeps it out of `deliver()` entirely."""
    enqueued: list = []

    class _Manager:
        _sessions: dict = {}

        def enqueue(self, *a, **kw):
            enqueued.append((a, kw))
            return True

    deliveries = W.dispatch_fires(_Manager(), [_Fire(_resume_trigger("run-7"))], now=NOW)
    assert [d.disposition for d in deliveries] == [W.Disposition.RESUME_TARGET.value]
    assert enqueued == [], "a resume target must never be queued onto a session inbox"
    assert deliveries[0].delivered is False
    assert (
        deliveries[0].needs_retry is False
    ), "a resume target is applied, not retried into an inbox"


def test_a_mixed_batch_keeps_ONE_delivery_per_FIRE_and_its_order():
    """One result per input, always — a caller diffing counts to find what happened would be doing
    the dispatcher's job. The wake half must still go through `deliver`."""
    fires = [
        _Fire(_Trigger({"inline": {}}, trigger_id="a")),
        _Fire(_resume_trigger("run-7")),
        _Fire(_Trigger({"inline": {}}, trigger_id="c")),
    ]
    deliveries = W.dispatch_fires(None, fires, now=NOW)
    assert [d.disposition for d in deliveries] == [
        W.Disposition.NO_SESSION.value,
        W.Disposition.RESUME_TARGET.value,
        W.Disposition.NO_SESSION.value,
    ]
    assert [d.wakeup.seq for d in deliveries] == [1, 2, 3]


def test_the_new_disposition_is_counted_by_the_SHIPPED_summary():
    """`summary()` keys every `Disposition` member at zero so a surface renders a fixed set of chips
    instead of discovering dispositions at runtime. A new member that the summary did not carry
    would be invisible in the runs inbox."""
    counts = W.summary(W.dispatch_fires(None, [_Fire(_resume_trigger("r"))], now=NOW))
    assert counts["by_disposition"][W.Disposition.RESUME_TARGET.value] == 1
    assert counts["delivered"] == 0
    assert counts["retry"] == 0


# ── `resume_target_of`: the normalized shape every consumer reads ──


@pytest.mark.parametrize(
    "workflow",
    [
        {},
        {"inline": {"provider": "notify"}},
        {"resume": None},
        {"resume": "run-7"},
        {"resume": []},
        {"resume": {}},
        {"resume": {"run_id": ""}},
        {"resume": {"run_id": "   "}},
    ],
)
def test_a_target_that_names_no_run_is_NO_target(workflow):
    """Fail-OPEN here, deliberately and in exactly one place: a `resume` block with no run id names
    nothing, so the trigger falls back to its ordinary new-run wake. An authoring slip that
    silently DISABLED a trigger's normal fire would be far harder to find than one that fires
    normally — and `models._resume_target_issues` tells the author at save time, where the mistake
    was made."""
    assert W.resume_target_of(_Trigger(workflow)) == {}
    assert W.wakeup_for(_Fire(_Trigger(workflow)), now=NOW).kind == W.WakeKind.WAKE.value


def test_a_missing_answer_key_means_CLEAR_THE_PAUSE_not_answer_a_gate():
    """The safe default for an unattended fire. A monitor says "carry on"; auto-approving a gate is
    something an author has to write down."""
    target = W.resume_target_of(_resume_trigger("run-7"))
    assert target["answers_gate"] is False
    assert target["gate_answer"] is None


@pytest.mark.parametrize("answer", [False, None, 0, "", {"revise": {}}])
def test_answer_PRESENCE_is_what_counts_never_its_truthiness(answer):
    """🔴 `answer: false` and `answer: null` are both legitimate gate answers — `false` is a
    REJECTION, the single most consequential answer in the vocabulary. A truthiness test would read
    both as "no answer given" and silently convert a rejection into "clear the pause and carry
    on", which is the opposite of what the author wrote."""
    target = W.resume_target_of(_resume_trigger("run-7", answer=answer))
    assert target["answers_gate"] is True
    assert target["gate_answer"] == answer


def test_the_target_carries_its_scope_and_token_through():
    target = W.resume_target_of(_resume_trigger("run-7", project_id="p1", resume_token="tok"))
    assert target["run_id"] == "run-7"
    assert target["project_id"] == "p1"
    assert target["resume_token"] == "tok"


# ── validation: the author is told at SAVE time ──


def _issues(workflow: dict) -> list:
    _, issues = parse_trigger(
        {
            "id": "j1",
            "name": "j",
            "kind": "clock",
            "spec": {"kind": "interval", "interval_secs": 3600},
            "workflow": workflow,
        }
    )
    return issues


def test_a_resume_block_with_no_run_id_is_an_ERROR_at_save_time():
    """The runtime is fail-open on this by design, so validation is the ONLY thing that can tell the
    author. Without it a mistyped target is a trigger that quietly does the wrong thing forever."""
    issues = _issues({"resume": {}})
    assert any(i.path == "workflow.resume.run_id" and i.severity == "error" for i in issues)


def test_a_non_object_resume_target_is_an_ERROR():
    issues = _issues({"resume": "run-7"})
    assert any(i.path == "workflow.resume" and i.severity == "error" for i in issues)


def test_a_near_miss_field_gets_the_key_the_author_MEANT():
    """`Issue.closest` is the point of the record: an agent that wrote `runid` should be told which
    key it meant, not that its trigger is invalid."""
    issues = _issues({"resume": {"run_id": "r", "runid": "r"}})
    near = [i for i in issues if i.path == "workflow.resume.runid"]
    assert near and near[0].closest == "run_id"


@pytest.mark.parametrize("action_key", ["inline", "provider", "ref"])
def test_declaring_BOTH_a_resume_target_and_an_action_is_an_ERROR(action_key):
    """🔴 Not a precedence rule. `wakeup_for` picks the resume and the configured action NEVER
    runs — a trigger whose action is silently ignored is the "mechanism present but inert" shape
    this codebase keeps paying for. Only the author can say which they meant."""
    issues = _issues({"resume": {"run_id": "r"}, action_key: {"provider": "notify"}})
    both = [i for i in issues if i.path == "workflow.resume" and i.severity == "error"]
    assert both, f"declaring resume + {action_key} must be refused"
    assert action_key in both[0].message


def test_a_WELL_FORMED_resume_target_raises_no_issues():
    """The vacuity partner for the validation legs: a rule that refused everything would pass every
    test above while making the feature unusable."""
    issues = _issues({"resume": {"run_id": "r", "project_id": "p", "answer": True}})
    assert [i.to_dict() for i in issues] == []


# ══════════════════════════════════════════════════════════════════════════════
# the end-to-end half: the run ACTUALLY resumes
# ══════════════════════════════════════════════════════════════════════════════

pytestmark_asyncio = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """An isolated workflow home.

    `workflows.store.config_dir` is patched where the STORE imports it, not only on
    `config.loader`: the store binds `config_dir` at module level, and patching only the loader
    leaves `store.save()` writing into the real `~/.personalclaw` — this program has paid for that
    already, and `ALLOWED_RESIDUE` is `frozenset()`.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    from personalclaw.workflows import defs as defs_mod

    saved = dict(defs_mod._providers)
    defs_mod._providers.clear()
    register_native_provider()
    try:
        yield home
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(saved)


async def _parked_run(spec: dict = GATED_SPEC):
    """A run driven to a REAL gate through the REAL supervisor. Returns `(run, watchdog)`.

    The real `WorkflowWatchdog` because `resume_run` requires a LIVE controller by contract, and a
    hand-rolled registry would drift from that contract exactly where this suite should be checking
    it. `launch` both creates and registers the controller, which is what production does.
    """
    run = wstore.create(WorkflowRun(id="", workflow_name=str(spec.get("name", "wf"))))
    wstore.write_spec(run.id, spec)
    watchdog = WorkflowWatchdog(None, EngineServices())
    controller = await watchdog.launch(run, spec)
    assert await controller.wait_for_terminal(timeout=15) == RunStatus.NEEDS_INPUT
    return run, watchdog


def _attach(monkeypatch, watchdog) -> None:
    """Publish the supervisor the way the GATEWAY does, not by patching the loop's accessor.

    🔴 This is load-bearing. `service._live(run_id, None)` returns None unconditionally, so a
    supervisor-less `resume_run` can only ever answer `WF_RUN_NOT_LIVE` — the resume path would be
    inert and every test here would pass on a `deferred` outcome that never touched a run. Going
    through `ActionServices.workflows` proves the accessor the gateway actually populates
    (`gateway.py`: `svc.workflows = self.workflow_watchdog`) is the one the loop reads.
    """
    from personalclaw.action_providers import services as svc_mod

    services = svc_mod.get_action_services()
    if services is None:
        services = svc_mod.ActionServices(state=None, spawn_background=lambda _coro: None)
        monkeypatch.setattr(svc_mod, "get_action_services", lambda: services)
    monkeypatch.setattr(services, "workflows", watchdog, raising=False)


async def _fire(run_id: str, **extra):
    """Dispatch and execute ONE scheduled fire whose trigger targets `run_id`.

    Drives the REAL seam — `dispatch_fires` then `_execute_delivery` — rather than calling
    `_apply_resume` directly, so a break anywhere in the chain (a disposition that routes to
    `drain`, a kind branch placed after the disposition branches) shows up as a red here.
    """
    fire = _Fire(_resume_trigger(run_id, **extra))
    (delivery,) = W.dispatch_fires(None, [fire], now=NOW)
    outcomes = await tl._execute_delivery(delivery, _never_runs, sessions=None, now=NOW)
    assert len(outcomes) == 1, outcomes
    return outcomes[0]


async def _never_runs(_payload):
    """The trigger's ordinary action runner. A resume must NEVER reach it.

    Raising rather than recording: if a resume ever routes through `drain`/`run_one`, the runner is
    where it lands, and `run_one` would swallow the raise into a `failed` outcome — so the assertion
    lives in the outcome, and this makes that outcome unmistakable.
    """
    raise AssertionError("a resume target must never execute the trigger's ordinary action")


@pytest.mark.anyio
async def test_THE_RUN_ACTUALLY_RESUMES(isolated, monkeypatch):
    """🔴 **THE CENTRAL PROOF, read from the run's own state.**

    Not "a fire was enqueued", not "resume_run was called" — the node BEHIND the gate produced its
    output. That value cannot exist unless the trigger's fire moved this run past a gate it was
    genuinely parked on. A whole trigger subsystem shipped here delivering into a mailbox nobody
    opened, and every run in its history had been started by hand; this is the assertion that
    would have caught it.
    """
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)

    from personalclaw.workflows.human_input import list_continuations

    assert list_continuations(run.id), "the gate did not mint a continuation to resume"
    assert wstore.get(run.id).status == RunStatus.NEEDS_INPUT

    outcome = await _fire(run.id, answer=True)
    assert outcome.outcome == Outcome.RAN.value, outcome.reason
    assert outcome.run_id == run.id

    controller = watchdog.controller(run.id)
    assert await controller.wait_for_terminal(timeout=20) == RunStatus.COMPLETE
    # The proof: a value that only exists if the run carried on past the gate.
    assert wstore.read_output(run.id, AFTER_PATH) == "the run carried on"
    assert wstore.get(run.id).status == RunStatus.COMPLETE


@pytest.mark.anyio
async def test_a_resume_CONSUMES_the_gate_token(isolated, monkeypatch):
    """The complement, observed on the continuation store rather than on the output: the token was
    CLAIMED, which is what makes the answer single-use.

    Asserted on the FILES rather than on `list_continuations`, because that function reports only
    PENDING gates — see `test_list_continuations_EXCLUDES_a_claimed_gate`. The claim is a move
    into the `claimed/` subdirectory, so "consumed" means the bare token file is gone from the
    pending directory and the audit copy exists under `claimed/`.
    """
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)

    from personalclaw.workflows.human_input import _claimed_dir, _dir, list_continuations

    token = list_continuations(run.id)[0].token
    assert (_dir(run.id) / f"{token}.json").is_file()

    assert (await _fire(run.id, answer=True)).outcome == Outcome.RAN.value

    assert not (_dir(run.id) / f"{token}.json").exists(), "the gate answer was not claimed"
    assert (_claimed_dir(run.id) / f"{token}.json").is_file(), "the claim left no audit trail"


@pytest.mark.anyio
async def test_list_continuations_EXCLUDES_a_claimed_gate(isolated, monkeypatch):
    """🔴 **The INVERSE of a defect pin this file used to carry.**

    `consume_continuation` used to claim a token by renaming it to a `<token>.claimed.json`
    SIBLING, which `list_continuations`' `*.json` glob still matched — so an already-answered
    gate kept reading as pending, and this file pinned that defect as current behaviour. The
    claim is now a move into the `claimed/` subdirectory, which the non-recursive glob cannot
    match, so the pin inverts: an answered gate must vanish from the pending listing.

    Why THIS seam cares (the reason the original pin lived here): `loop._RESUME_NOT_YET` maps
    `WF_NO_PENDING_GATE` to DEFERRED. With the claimed record excluded, a fire on a run whose
    only gate was already answered reaches exactly that branch — postponed to the trigger's next
    cadence — instead of REFUSING with `WF_RESUME_UNKNOWN_TOKEN` about a token nobody named.
    `test_a_run_with_NO_PENDING_GATE_is_DEFERRED_not_refused` drives that mapping; this test
    proves the state it needs is the one an answered gate actually leaves behind.
    """
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)
    from personalclaw.workflows.human_input import list_continuations

    assert (await _fire(run.id, answer=True)).outcome == Outcome.RAN.value
    assert list_continuations(run.id) == [], (
        "a claimed gate is reading as pending again — the token-less resolver, the ambiguity "
        "check, and the DEFERRED mapping in loop._RESUME_NOT_YET all mis-answer on that state"
    )


@pytest.mark.anyio
async def test_TWO_fires_resume_the_run_ONCE(isolated, monkeypatch):
    """🔴 **IDEMPOTENCE, and it is INHERITED rather than re-implemented.**

    `human_input.consume_continuation` claims the token with `os.rename` BEFORE reading it, so
    exactly one caller can ever see the payload — measured, against a read-then-unlink version that
    let multiple callers through in 36 of 40 races. A SEQUENTIAL second token-less fire never even
    reaches the claim: the answered gate is no longer listed as pending, so it reports
    `WF_NO_PENDING_GATE` and is DEFERRED to the trigger's next cadence — where the then-terminal
    run REFUSES it ("has finished", pinned below). Only a genuine race on one token produces a
    claim LOSER, which is REFUSED (`WF_RESUME_ALREADY_USED`/`WF_RESUME_UNKNOWN_TOKEN`) — the
    CONCURRENT test drives that shape. Neither is ever silently counted as a success.

    So `concurrency.single_flight` is deliberately NOT wrapped around this: it is the weaker guard
    (advisory, non-blocking, released the instant the block ends) and the authoritative claim
    already lives one layer down. A second lock would be two mechanisms disagreeing about who won.
    """
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)

    first = await _fire(run.id, answer=True)
    second = await _fire(run.id, answer=True)

    assert first.outcome == Outcome.RAN.value, first.reason
    assert second.outcome != Outcome.RAN.value, "the second fire re-answered one gate"
    assert second.outcome == Outcome.DEFERRED.value, second.outcome
    assert second.reported == "WF_NO_PENDING_GATE", second.reported
    assert second.reason, "a deferral must carry a reason"

    controller = watchdog.controller(run.id)
    assert await controller.wait_for_terminal(timeout=20) == RunStatus.COMPLETE
    assert wstore.read_output(run.id, AFTER_PATH) == "the run carried on"


@pytest.mark.anyio
async def test_two_CONCURRENT_fires_resume_the_run_once(isolated, monkeypatch):
    """The same guarantee under a real race rather than in sequence — the shape a retry storm and a
    double-scheduled tick actually take."""
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)

    outcomes = await asyncio.gather(_fire(run.id, answer=True), _fire(run.id, answer=True))
    ran = [o for o in outcomes if o.outcome == Outcome.RAN.value]
    assert len(ran) == 1, [o.outcome for o in outcomes]
    assert all(o.reason for o in outcomes if o.outcome != Outcome.RAN.value)


# ── the missing-target dispositions: fail-CLOSED, and LEGIBLE ──


@pytest.mark.anyio
async def test_a_GONE_target_is_REFUSED_with_a_reason_that_names_the_run(isolated, monkeypatch):
    """🔴 **FAIL-CLOSED, and the direction is not symmetric.** This fires unattended. Fail-open
    would mean "the target is gone, so start a new run instead" — running work the author never
    asked for, on a schedule, with nobody watching, potentially mutating. Refusing costs one
    automation that was already broken.

    And it must SAY so: "a trigger that silently does nothing every hour is worse than one that
    says its target is gone". `Outcome.REFUSED` carries a mandatory reason
    (`models.require_reason`), and the reason names the run id so the fix is findable.
    """
    _attach(monkeypatch, WorkflowWatchdog(None, EngineServices()))
    outcome = await _fire("run-that-never-existed")
    assert outcome.outcome == Outcome.REFUSED.value
    assert "run-that-never-existed" in outcome.reason
    assert "no longer exists" in outcome.reason


@pytest.mark.anyio
async def test_a_FINISHED_target_is_REFUSED_not_restarted(isolated, monkeypatch):
    """A run is ONE attempt — `RESUMABLE_ENDED_RUN_STATUSES` is deliberately empty — so every way it
    can stop is a way it stops for good. Reusing the shipped `is_terminal` rather than re-deriving
    the status set keeps this honest as that vocabulary changes."""
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)
    run.status = RunStatus.COMPLETE
    wstore.save(run)
    assert wstore.get(run.id).is_terminal is True

    outcome = await _fire(run.id, answer=True)
    assert outcome.outcome == Outcome.REFUSED.value
    assert "has finished" in outcome.reason
    # And it did NOT reach the gate machinery: the continuation is untouched.
    from personalclaw.workflows.human_input import list_continuations

    assert len(list_continuations(run.id)) == 1


@pytest.mark.anyio
async def test_a_FOREIGN_target_is_REFUSED_when_the_project_disagrees(isolated, monkeypatch):
    """A run id is not unique to a project's intent — ids are reused across a restore and a fork —
    and resuming a stranger's run unattended is the one outcome worth refusing on a merely
    SUSPICIOUS signal."""
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)
    run.project_id = "project-a"
    wstore.save(run)

    outcome = await _fire(run.id, answer=True, project_id="project-b")
    assert outcome.outcome == Outcome.REFUSED.value
    assert "project-b" in outcome.reason and "project-a" in outcome.reason


@pytest.mark.anyio
async def test_a_MATCHING_project_is_allowed_through(isolated, monkeypatch):
    """🟢 The vacuity partner for the scope check: a guard that refused every project would pass the
    test above while making the field unusable."""
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)
    run.project_id = "project-a"
    wstore.save(run)

    outcome = await _fire(run.id, answer=True, project_id="project-a")
    assert outcome.outcome == Outcome.RAN.value, outcome.reason


@pytest.mark.anyio
async def test_NO_declared_project_does_not_check_the_project(isolated, monkeypatch):
    """The check is opt-in: a target that declares no project cannot be compared against one, and
    inventing a comparison would refuse every run authored before the field existed."""
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)
    run.project_id = "project-a"
    wstore.save(run)

    assert (await _fire(run.id, answer=True)).outcome == Outcome.RAN.value


@pytest.mark.anyio
async def test_a_run_with_NO_PENDING_GATE_is_DEFERRED_not_refused(isolated, monkeypatch):
    """A state a parked run LEAVES on its own, so it is postponed rather than refused — and
    re-evaluated on the trigger's next scheduled fire. No retry queue: a scheduled trigger's own
    cadence IS the retry, and `pending_resumes` feeds `deliver_all`, which would put the resume back
    onto the inbox this path exists to avoid."""
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)
    from personalclaw.workflows.human_input import _dir

    # Cleared by unlinking the files directly. `consume_continuation` now reaches this state on
    # its own (a claimed record moves under `claimed/` and stops being listed — see
    # `test_list_continuations_EXCLUDES_a_claimed_gate`), but constructing it by hand keeps this
    # test about the DEFERRED branch alone, independent of the claim mechanics.
    for path in _dir(run.id).glob("*.json"):
        path.unlink()

    outcome = await _fire(run.id, answer=True)
    assert outcome.outcome == Outcome.DEFERRED.value, outcome.reason
    assert outcome.reported == "WF_NO_PENDING_GATE"
    assert "not ready yet" in outcome.reason


@pytest.mark.anyio
async def test_NO_supervisor_is_DEFERRED_and_says_why(isolated, monkeypatch):
    """The window before the gateway attaches the watchdog, and every process that has no workflow
    engine at all (a CLI tick, `automation doctor`). `WF_RUN_NOT_LIVE` is transient because the
    watchdog ADOPTS parked runs on its poll — `store.active_runs()` includes `needs_input` — so it
    means "not adopted yet", never "gone"."""
    run, _ = await _parked_run()
    monkeypatch.setattr(tl, "_supervisor", lambda: None)

    outcome = await _fire(run.id, answer=True)
    assert outcome.outcome == Outcome.DEFERRED.value
    assert outcome.reported == "WF_RUN_NOT_LIVE"


@pytest.mark.anyio
async def test_a_pause_is_CLEARED_when_no_answer_is_declared(isolated, monkeypatch):
    """The safe unattended default: `resume_run`'s token-less, answer-less path clears
    `pause_requested` and does NOT answer a gate. A `goal-pursuit-monitor` says "carry on"; it does
    not hold an approval."""
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)
    fresh = wstore.get(run.id)
    fresh.extra["pause_requested"] = True
    wstore.save(fresh)

    outcome = await _fire(run.id)  # no `answer` key at all
    assert outcome.outcome == Outcome.RAN.value, outcome.reason
    assert "pause_requested" not in wstore.get(run.id).extra
    # The gate is UNTOUCHED — clearing a pause is not answering a question.
    from personalclaw.workflows.human_input import list_continuations

    assert len(list_continuations(run.id)) == 1


# ── survivability: this runs inside the one clock loop for the whole machine ──


@pytest.mark.anyio
async def test_an_UNREADABLE_store_is_a_FAILURE_not_a_refusal(isolated, monkeypatch):
    """A refusal says "your target is wrong"; a raise says "we could not tell". Reporting the second
    as the first would tell a user to fix an automation that was fine."""
    monkeypatch.setattr(wstore, "get", lambda _rid: (_ for _ in ()).throw(OSError("disk")))
    _attach(monkeypatch, WorkflowWatchdog(None, EngineServices()))
    outcome = await _fire("run-7")
    assert outcome.outcome == Outcome.FAILED.value
    assert "could not be read" in outcome.reason


@pytest.mark.anyio
async def test_a_RAISING_resume_never_propagates_into_the_tick(isolated, monkeypatch):
    """One trigger's resume must not take the clock loop for every automation on the machine."""
    from personalclaw.workflows import service as wfs

    _attach(monkeypatch, WorkflowWatchdog(None, EngineServices()))
    run, _ = await _parked_run()
    monkeypatch.setattr(
        wfs, "resume_run", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    outcome = await _fire(run.id, answer=True)
    assert outcome.outcome == Outcome.FAILED.value
    assert "boom" in outcome.reason


@pytest.mark.anyio
async def test_every_resume_path_RELEASES_the_trigger_claim(isolated, monkeypatch):
    """🔴 S97's defect, on a new path. `tick` persists a claim per fire so `overlap` can enforce, and
    `run_one`'s `finally` is the only other release — which a resume never reaches. Without the
    release a resume-target trigger reports `is_running` for the claim's full 3600s after its first
    fire, records `skipped_overlap` on every later tick, and answers `409 already running` to a
    manual Run for an hour."""
    from personalclaw.triggers import executor as ex

    released: list[str] = []
    monkeypatch.setattr(ex, "release_claim_for", lambda tid, **kw: released.append(tid) or True)
    _attach(monkeypatch, WorkflowWatchdog(None, EngineServices()))

    await _fire("gone")  # the refusal path
    run, watchdog = await _parked_run()
    _attach(monkeypatch, watchdog)
    await _fire(run.id, answer=True)  # the success path

    assert released == ["schedule:j1", "schedule:j1"], released
