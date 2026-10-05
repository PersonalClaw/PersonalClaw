"""A stage's no-double-execution claim belongs to ONE ATTEMPT, not to the node forever (#3533).

`engine.dispatch_stage` takes `leases.acquire_claim` before it spawns, and the spawn is still
live when it returns — so the claim has to outlive the dispatcher. Until this change its ONLY way
out was the 900s TTL, which means a stage instance that settled FAILED went on holding a claim
against itself, and the retry of that instance met its own lease.

**Measured on unmodified `main`**, by reaping a stage and then rewinding the node (the ordinary
way a user retries a failed step):

    ATTEMPT 1: status=failed state=failed spawns=1
    CLAIM after the FAILED settle: target='2c1029f4:work'
        claim=Claim(holder='workflow:2c1029f4:work#a72e3341191c', expires_at=…, renewals=0)
    REWIND: ok=True issues=[]
    ATTEMPT 2: status=complete state=degraded spawns=1
        degraded='another worker holds the claim on this node
                  (held by workflow:2c1029f4:work#a72e3341191c for another 899s)
                  — not executing twice'

Note the second line of the symptom, which is worse than the refusal: DEGRADED is a SUCCESS state,
so the run reported **COMPLETE** having re-run nothing. The user asks for a retry, waits, and is
told the run finished.

**Keying the claim more finely cannot fix this** — the retry IS the same instance, so any key that
identifies the instance collides with itself. (#3531 makes the key per-INSTANCE, which fixes a
loop's *iterations*; it does not touch this.) The identity that has to be per-attempt is the
HOLDER, and it travels out on `NodeResult` so the settle path can give it back.

**Both directions are asserted here, because without the second this is a hole rather than a fix:**

* a retry after a FAILED attempt PROCEEDS —
  `test_a_rewound_stage_retries_after_its_first_attempt_FAILED`;
* a genuinely CONCURRENT second execution of the same instance is still REFUSED —
  `test_a_concurrent_second_execution_of_the_same_instance_is_still_refused`.

**And the attempt identity stays out of the lease PATH.** `leases._lease_path` truncates to 64
characters and collapses `.`, `@`, `[` and `#` to `_`, so two distinct keys can alias into one
file — and an aliased lease is indistinguishable from a correctly-refused duplicate. The holder is
a VALUE inside the claim file, never a path component, which is what
`test_the_attempt_identity_stays_out_of_the_lease_path` pins (with a control proving the collapse
is real, so the assertion is not vacuous).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from personalclaw.approval_answer import YOU
from personalclaw.workflows import engine
from personalclaw.workflows import human_input as HI
from personalclaw.workflows import journal as J
from personalclaw.workflows import leases, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import claim_holder, claim_key, release_execution_claim
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeInstance,
    RunStatus,
    WorkflowRun,
)

STAGE_PATH = "root.children[0]"
#: Bounded so a red costs seconds. Nothing here waits on wall-clock progress: the fake reports
#: its verdict on the first lookup, so a passing run terminates on the next tick.
RUN_TIMEOUT = 6.0
#: For the one test whose premise is a stage that NEVER settles, where the run cannot terminate
#: and the timeout is the exit. Short, because the assertion is about the claim, not the clock.
LIVE_TIMEOUT = 1.0

REAPED = "Reaped after 900s (exceeded 900s deadline) [stage]"


class _Info:
    """The subset of `SubagentInfo` a completion is read from (``subagent.py:306``)."""

    def __init__(self, agent_id: str, *, error: str, settles: bool = True) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""
        self._error = error
        self._settles = settles


class _FakeSubagents:
    """`SubagentManager` on exactly the two methods this path uses: `spawn` and `get`.

    `errors` is read per spawn, so one fake can answer "attempt 1 was reaped, attempt 2 worked" —
    which is the whole shape of a retry and cannot be expressed by a single flag.
    """

    def __init__(self, *, errors: tuple[str, ...] = ("",), settles: bool = True) -> None:
        self.infos: dict[str, _Info] = {}
        self.spawns: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self._errors = errors
        self._settles = settles

    def spawn(self, **kw: Any) -> _Info:
        n = len(self.spawns)
        error = self._errors[n] if n < len(self._errors) else self._errors[-1]
        info = _Info(f"sub{n + 1}", error=error, settles=self._settles)
        self.spawns.append(kw)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        self.gets.append(agent_id)
        info = self.infos.get(agent_id)
        if info is None or not info._settles:
            return info
        info.done = True
        info.error = info._error
        info.result = "" if info._error else "the stage's answer"
        info.reaped = bool(info._error)
        return info


def _spec() -> dict[str, Any]:
    return {
        "name": "stage-retry",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "work", "config": {"prompt": "do the thing"}}],
        },
    }


def _gated_spec() -> dict[str, Any]:
    """The stage beside a gate that waits indefinitely. A stage that FAILS then leaves the run LIVE
    — parked on the gate, since a `parallel` settles once every child has — which is where a user
    rewinds a failed step. A run that FAILED is one attempt and refuses the edit; a retry of it is
    a fork."""
    return {
        "name": "stage-retry-gated",
        "root": {
            "kind": "parallel",
            "id": "root",
            "children": [
                {"kind": "stage", "id": "work", "config": {"prompt": "do the thing"}},
                {
                    "kind": "gate",
                    "id": "hold",
                    "config": {"kind": "approval", "prompt": "hold", "timeout_secs": 0},
                },
            ],
        },
    }


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real `RunController` over a real spec, with only the subagent manager faked.

    The leases dir is redirected at `tmp_path` so a claim in this test cannot be seen by — or
    leak into — the real home.
    """
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    def _build(
        *, gated: bool = False, **kw: Any
    ) -> tuple[RunController, _FakeSubagents, WorkflowRun]:
        fake = _FakeSubagents(**kw)
        spec = _gated_spec() if gated else _spec()
        run = store.create(WorkflowRun(id="", workflow_name="stage-retry"))
        store.write_spec(run.id, spec)
        controller = RunController(
            run, spec, services=EngineServices(subagents=fake, cwd=str(tmp_path))
        )
        return controller, fake, run

    return _build


def _rewind(controller: RunController) -> dict[str, Any]:
    """Retry the stage the way a user does: rewind the node and let the loop re-run it."""
    return controller.submit_mutation(
        [{"op": "rewind", "node_id": "work", "force": True}], actor="user", confirm=True
    )


def _stage_node() -> Node:
    return Node.from_dict(_spec()["root"]["children"][0])


# ── direction 1: a retry after a settled attempt PROCEEDS ────────────────────


def test_a_rewound_stage_retries_after_its_first_attempt_FAILED(wired, monkeypatch) -> None:
    """THE defect. On `main` the second drive spawns nothing and the node lands DEGRADED.

    Three independent observables, because no one of them is enough on its own: the SPAWN COUNT (a
    refusal never reaches the subagent manager), the node's STATE (a refusal is DEGRADED, which the
    run counts as a success), and the ACQUISITIONS — two grants on the SAME target with DISTINCT
    holders. That last one is what separates this fix from two wrong ways to pass: widening the key
    so the retry claims something else, and reusing the holder so the retry RENEWS. `claim_holder`'s
    docstring records the second as measured: a stable holder let BOTH executions through while a
    lease file sat there looking like protection.

    **The claim COORDINATE moved in #3524 and this test was reading the old one**, which is worse
    than a plain red: `claim_key` now takes the node INSTANCE path, so `claim_key(run.id, "work")`
    digests a string no run ever claims, and the premise assertion below ("the failed attempt is no
    longer holding its claim") read a key that is unconditionally absent — it passed by looking in
    the wrong place. The target is now taken from the recorder, so the release is asserted against
    the key the run demonstrably took.
    """
    controller, fake, run = wired(errors=(REAPED, ""), gated=True)
    acquired: list[tuple[str, str, bool]] = []
    real_acquire = leases.acquire_claim

    def _recording(target_id: str, holder: str, **kw: Any):
        granted, reason = real_acquire(target_id, holder, **kw)
        acquired.append((target_id, holder, granted is not None))
        return granted, reason

    monkeypatch.setattr(leases, "acquire_claim", _recording)

    async def _go() -> tuple[RunStatus, RunStatus]:
        # The stage fails and the run stays live, parked on the gate beside it.
        first = await controller.run_to_completion(timeout=RUN_TIMEOUT)
        inst = controller.instances[STAGE_PATH]
        assert inst.state is InstanceState.FAILED, (
            f"the premise failed: the first attempt is {inst.state.value}, not FAILED, so this "
            "test is not measuring a retry of a failed stage"
        )
        assert len(fake.spawns) == 1, f"the premise failed: {len(fake.spawns)} spawns, not 1"
        # The target is READ BACK from the recorder rather than recomputed from a coordinate this
        # test chose. `inst.claim_target` is cleared by the release itself, so the acquisition is
        # the only place the live key survives — and reading it here is what stops the assertion
        # below from being satisfied by a key the run never touched.
        assert len(acquired) == 1, f"the first attempt took {len(acquired)} claims, not 1"
        first_target = acquired[0][0]
        assert first_target == claim_key(run.id, STAGE_PATH), (
            "the claim is not keyed on the node INSTANCE path any more, so this test is measuring "
            f"a coordinate the run does not use (took {first_target!r}; the instance key is "
            f"{claim_key(run.id, STAGE_PATH)!r}, the pre-#3524 node-id key was "
            f"{claim_key(run.id, 'work')!r})"
        )
        assert leases.read_claim(first_target) is None, (
            "the failed attempt is still holding its claim — this is #3533 itself, and every "
            "assertion below would be measuring the unfixed code"
        )
        assert _rewind(controller)["ok"], "the rewind was refused, so no retry was requested"
        # The parked run applies the rewind at once: the stage re-runs, and the run waits on its
        # gate again. Answering the gate is what then lets it complete.
        await asyncio.wait_for(controller._terminal.wait(), timeout=RUN_TIMEOUT)
        pending = HI.list_continuations(run.id)
        assert len(pending) == 1, f"the gate is not waiting to be answered: {pending}"
        assert controller.resume(pending[0].token, True, by=YOU)["ok"]
        return first, await controller.run_to_completion(timeout=RUN_TIMEOUT)

    first, second = asyncio.run(_go())
    inst = controller.instances[STAGE_PATH]

    assert (
        first is RunStatus.NEEDS_INPUT
    ), f"the run did not stay live on its gate after the stage failed (status={first.value})"
    assert len(fake.spawns) == 2, (
        f"the retry never reached the subagent manager ({len(fake.spawns)} spawn(s)) — it was "
        f"refused by its own claim: {inst.degraded_reason!r}"
    )
    assert (
        inst.state is InstanceState.DONE
    ), f"the retried stage is {inst.state.value}, not DONE: {inst.degraded_reason!r}"
    assert "holds the claim" not in inst.degraded_reason, inst.degraded_reason
    assert second is RunStatus.COMPLETE, f"the retry did not complete (status={second.value})"

    assert len(acquired) == 2, f"expected one acquisition per attempt, got {acquired}"
    targets = {t for t, _, _ in acquired}
    assert targets == {claim_key(run.id, STAGE_PATH)}, (
        f"the retry claimed a DIFFERENT target ({targets}) — the fix widened the key instead of "
        "releasing the attempt's claim, which would let two concurrent workers past as well"
    )
    holders = [h for _, h, _ in acquired]
    assert holders[0] != holders[1], (
        "both attempts used the same holder, so the second was a RENEWAL rather than a fresh "
        "claim — the guard passes both executions through in that shape"
    )
    assert all(
        granted for _, _, granted in acquired
    ), f"an attempt was refused its claim: {acquired}"

    # And from the RUN LEDGER, not only from the fake: two dispatches, one failure, one completion.
    # The fake's counters are this test's own bookkeeping; the ledger is the run's durable record
    # and the thing a user reads, so a fix that satisfied the counters and left the ledger showing
    # one dispatch would be the "every layer reports success" failure this engine keeps producing.
    started = [
        e
        for e in J.journal_records(run.id, kinds={"step_started"})
        if str(e.get("node_id") or "") == "work"
    ]
    assert (
        len(started) == 2
    ), f"the ledger records {len(started)} dispatch(es) of the stage: {started}"
    failed = [
        e
        for e in J.journal_records(run.id, kinds={"step_failed"})
        if str(e.get("node_id") or "") == "work"
    ]
    completed = [
        e
        for e in J.ledger(run.id, kinds={"step_completed"})
        if str(e.get("node_id") or "") == "work"
    ]
    assert len(failed) == 1 and len(completed) == 1, (
        f"the ledger does not read as one failed attempt then one that worked: "
        f"failed={len(failed)} completed={len(completed)}"
    )


def _rewind_as_the_run_page_does(controller: RunController) -> dict[str, Any]:
    """`POST …/rewind {node_id, confirm_cascade: true}`, through `service._reentry`: the run page's
    Re-run and the Inbox's "Run this step again" after the owner confirmed the re-run."""
    return controller.submit_mutation(
        [{"op": "rewind", "node_id": "work", "redo_effects": False, "force": False}],
        actor="chat",
        confirm=True,
    )


def test_a_rewind_the_owner_confirmed_reruns_a_stage_that_SUCCEEDED(wired) -> None:
    """A settled attempt's claim goes back when a rewind resets it, whichever way it settled.

    The success settle keeps its claim (below), and a confirmed rewind of that DONE stage is the
    same instance asking to run again, so it met its own lease: `another worker holds the claim on
    this node (held by … for another 774s) — not executing twice`, DEGRADED, which the run counts as
    a success. Driven that way on a live gateway: the owner confirmed "Re-run "draft"?", the page
    said the step was running again, and nothing ran — for the claim's whole 900s TTL, which is
    exactly when a step is re-run (its approval had just gone unanswered).

    The committed-effects redo gate does not intercept it: the owner's confirm IS that gate's
    answer. What the reset gives back is only a SETTLED attempt's claim; a live one keeps it
    (`test_a_rewind_of_a_live_stage_keeps_its_claim`).
    """
    controller, fake, run = wired(errors=("", ""), gated=True)

    async def _go() -> tuple[RunStatus, RunStatus]:
        first = await controller.run_to_completion(timeout=RUN_TIMEOUT)
        inst = controller.instances[STAGE_PATH]
        assert inst.state is InstanceState.DONE, f"the premise failed: {inst.state.value}"
        assert len(fake.spawns) == 1, f"the premise failed: {len(fake.spawns)} spawns"
        held = leases.read_claim(inst.claim_target)
        assert held is not None, "the premise failed: the succeeded stage holds no claim"
        assert _rewind_as_the_run_page_does(controller)["ok"], "the rewind was refused"
        await asyncio.wait_for(controller._terminal.wait(), timeout=RUN_TIMEOUT)
        pending = HI.list_continuations(run.id)
        assert len(pending) == 1, f"the gate is not waiting to be answered: {pending}"
        assert controller.resume(pending[0].token, True, by=YOU)["ok"]
        return first, await controller.run_to_completion(timeout=RUN_TIMEOUT)

    first, second = asyncio.run(_go())
    inst = controller.instances[STAGE_PATH]

    assert first is RunStatus.NEEDS_INPUT, f"the run did not stay live on its gate: {first.value}"
    assert len(fake.spawns) == 2, (
        f"the confirmed re-run never reached the subagent manager ({len(fake.spawns)} spawn(s)) — "
        f"it was refused by its own claim: {inst.degraded_reason!r}"
    )
    assert inst.state is InstanceState.DONE, f"{inst.state.value}: {inst.degraded_reason!r}"
    assert second is RunStatus.COMPLETE, f"the re-run did not complete (status={second.value})"
    started = [
        e
        for e in J.journal_records(run.id, kinds={"step_started"})
        if str(e.get("node_id") or "") == "work"
    ]
    assert len(started) == 2, f"the ledger records {len(started)} dispatch(es): {started}"


def test_a_rewind_of_a_live_stage_keeps_its_claim(wired) -> None:
    """The other direction: a rewind of a stage whose subagent is still running resets the node but
    not the attempt, which is still executing. Its claim stays, so the re-dispatch is refused as a
    second, concurrent execution — the invariant the claim exists for."""
    controller, fake, run = wired(settles=False, gated=True)

    async def _go() -> None:
        await controller.run_to_completion(timeout=LIVE_TIMEOUT)
        inst = controller.instances[STAGE_PATH]
        assert inst.state is InstanceState.RUNNING, f"the premise failed: {inst.state.value}"
        live = leases.read_claim(inst.claim_target)
        assert live is not None and live.holder == inst.claim_holder, f"no live claim: {live}"
        assert _rewind_as_the_run_page_does(controller)["ok"], "the rewind was refused"
        await controller.run_to_completion(timeout=LIVE_TIMEOUT)

    live_target = claim_key(run.id, STAGE_PATH)
    asyncio.run(_go())
    still = leases.read_claim(live_target)
    assert still is not None, "a rewind released the claim of an attempt that is still running"
    assert (
        len(fake.spawns) == 1
    ), f"the rewind let a second execution start beside the live one ({len(fake.spawns)} spawns)"


def test_a_stage_that_SUCCEEDS_still_holds_its_claim(wired) -> None:
    """🔴 The scope boundary, pinned: the release is on the FAILED settle ONLY, deliberately.

    The symptom a retained DONE claim causes is a different one — a stage-bodied LOOP is refused its
    own next round, because every round re-runs the same node id — and it is fixed by keying the
    claim per node INSTANCE (#3531 / #3524), not by shortening the window here. Two mechanisms for
    one symptom is the dual path this project does not keep.

    **Releasing on both outcomes also clears that bound, and it was measured rather than reasoned
    about**: with the release moved out of the `if error:` branch, `deep-research`'s round loop goes
    from **1** sweep dispatch to **8**, which reds
    `test_research_kind_as_run.py::test_a_stage_bodied_loop_cannot_re_execute_its_body` — a pin
    #3531 has already rewritten. So the narrow release is not caution about an unknown; it is
    declining to fix a second issue's defect a second way.

    A re-run of the same SUCCEEDED instance is a rewind, and the rewind gives the settled attempt's
    claim back when it resets the node (`mid_flight._apply_reentry`,
    `test_a_rewind_the_owner_confirmed_reruns_a_stage_that_SUCCEEDED`). This used to say such a
    re-run is intercepted before the claim is consulted — by the committed-effects redo gate, then
    the resume cache. A rewind the owner CONFIRMED is not: the confirm is that gate's answer,
    and the reset clears the cache entry, so it dispatched and met this claim.

    **If you widen the release to both branches, this test is the one that must change**: rewrite it
    as the new behaviour and check `test_research_kind_as_run.py` in the same commit.

    The retained target is read from `inst.claim_target` — the success branch is the one that keeps
    it — and pinned against `claim_key(run.id, STAGE_PATH)` in the same breath, so the test asserts
    the claim is held AND that it is held at the instance coordinate #3524 moved it to. Reading a
    recomputed node-id key here fails open the other way: absent, therefore "no claim".
    """
    controller, fake, run = wired(errors=("",))
    status = asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    inst = controller.instances[STAGE_PATH]

    assert status is RunStatus.COMPLETE, f"the premise failed: status={status.value}"
    assert inst.state is InstanceState.DONE, f"the premise failed: {inst.state.value}"
    assert len(fake.spawns) == 1, f"the premise failed: {len(fake.spawns)} spawns"
    assert inst.claim_target == claim_key(run.id, STAGE_PATH), (
        "the succeeded stage recorded no instance-keyed claim target, so the read below would be "
        f"looking in the wrong place ({inst.claim_target!r} vs {claim_key(run.id, STAGE_PATH)!r})"
    )
    held = leases.read_claim(inst.claim_target)
    assert held is not None and held.holder == inst.claim_holder, (
        "a SUCCEEDED stage no longer holds its claim. That is a defensible change and not this "
        "issue's: read this docstring, then update `test_research_kind_as_run.py`'s "
        f"stage-bodied-loop pin in the same commit. (claim={held}, recorded={inst.claim_holder!r})"
    )


# ── direction 2: a CONCURRENT second execution is still REFUSED ──────────────


def test_a_concurrent_second_execution_of_the_same_instance_is_still_refused(wired) -> None:
    """The invariant the claim exists for, which the fix must not trade away.

    The premise is a LIVE attempt: the stage is RUNNING, its subagent has not reported, and the
    controller therefore has not settled it. A second execution of that same instance in that
    window is the thing the claim is for, and it must still be turned away — and the winner must
    still hold its claim afterwards, because a release by a non-holder would let the loser steal
    the work by releasing first.

    The second worker is dispatched WITH `instance_path=STAGE_PATH`, because that is what makes it
    concurrent with the first: #3524 keys the claim on the instance path, so a dispatch omitting it
    is turned away by the fail-closed guard (`FailureClass.INTERNAL`, "not spawning") BEFORE the
    claim is consulted. That is a refusal for the wrong reason, and taking it as this assertion's
    green would let the lease itself rot unobserved.
    """
    controller, fake, run = wired(settles=False)

    async def _go() -> engine.NodeResult:
        await controller.run_to_completion(timeout=LIVE_TIMEOUT)
        inst = controller.instances[STAGE_PATH]
        assert inst.state is InstanceState.RUNNING, (
            f"the premise failed: the stage is {inst.state.value}, not RUNNING — there is no "
            "live attempt to be concurrent with"
        )
        assert inst.claim_target and inst.claim_holder, "the live attempt recorded no claim"
        held = leases.read_claim(inst.claim_target)
        assert (
            held is not None and held.holder == inst.claim_holder
        ), f"the live attempt's claim is not on disk: {held}"
        # A second worker arriving at the SAME instance while the first is still executing. The
        # instance path is what "the same instance" means now, so it travels with the dispatch.
        return await engine.dispatch_stage(
            _stage_node(),
            BindingContext(),
            subagents=fake,
            run_id=run.id,
            instance_path=STAGE_PATH,
        )

    second = asyncio.run(_go())
    inst = controller.instances[STAGE_PATH]

    assert second.state is InstanceState.DEGRADED, (
        f"a concurrent second execution of a live instance was ALLOWED ({second.state.value}) — "
        "the no-double-execution claim has been traded away"
    )
    assert "holds the claim" in second.degraded_reason, second.degraded_reason
    assert len(fake.spawns) == 1, (
        f"the refused execution spawned anyway ({len(fake.spawns)} spawns) — a recorded claim "
        "that does not prevent the second spawn is worse than no claim"
    )
    still = leases.read_claim(inst.claim_target)
    assert (
        still is not None and still.holder == inst.claim_holder
    ), f"the refused worker took or dropped the live holder's claim: {still}"


def test_only_the_recorded_holder_can_release_a_claim(wired, tmp_path: Path) -> None:
    """What makes a per-attempt release SAFE: it names the holder, and only that holder may.

    So carrying the identity per attempt cannot become a way for one attempt to free another's
    claim — including the retry freeing a claim taken by a genuinely concurrent worker.
    """
    # The two helpers take DIFFERENT coordinates since #3524 and it is worth spelling out where
    # they sit side by side: the key is the node INSTANCE path, the holder is the node id plus a
    # per-attempt nonce. Passing a node id as the key still returns a well-formed digest, so the
    # mix-up is silent — it just names a unit of work no run ever claims.
    target = claim_key("run-1", STAGE_PATH)
    mine = claim_holder("run-1", "work")
    theirs = claim_holder("run-1", "work")
    assert mine != theirs, "`claim_holder` is no longer unique per attempt — the premise is gone"

    granted, reason = leases.acquire_claim(target, mine)
    assert granted is not None, f"the setup could not take a claim: {reason}"

    release_execution_claim(target, theirs)
    survived = leases.read_claim(target)
    assert (
        survived is not None and survived.holder == mine
    ), f"a foreign holder released someone else's claim: {survived}"

    release_execution_claim(target, mine)
    assert leases.read_claim(target) is None, "the recorded holder could not release its own claim"


# ── the lease-path trap (#3531's finding) ────────────────────────────────────


def test_the_attempt_identity_stays_out_of_the_lease_path() -> None:
    """The per-attempt identity is a claim-file VALUE, never part of the file NAME.

    `leases._lease_path` truncates to 64 characters and collapses `.`, `@`, `[` and `#` to `_`, so
    anything folded into the key can alias two distinct claims into one file — and an aliased lease
    is indistinguishable from a correctly-refused duplicate, which is the worst available failure
    here. This is why the fix is a per-attempt HOLDER and not a finer key.

    The collapse control runs FIRST: without it, "the holder does not appear in the path" would
    also pass against a `_lease_path` that had stopped mangling anything at all.
    """
    collapsed = {
        leases._lease_path("root.body@1.children[1]").name,
        leases._lease_path("root.body#1.children[1]").name,
    }
    assert len(collapsed) == 1, (
        f"`_lease_path` no longer collapses `@`/`#`/`.`/`[` ({collapsed}) — the aliasing this "
        "test guards against has changed shape, so re-derive the guard rather than deleting it"
    )
    long_key = "x" * 80
    assert len(leases._lease_path(long_key).stem) == 64, "`_lease_path` no longer truncates"

    key = claim_key("run-1", STAGE_PATH)
    assert key == claim_key(
        "run-1", STAGE_PATH
    ), "the claim key is not a pure function of its inputs"
    holders = {claim_holder("run-1", "work") for _ in range(5)}
    assert len(holders) == 5, "the holder is not per-attempt, so a retry would RENEW, not refuse"

    name = leases._lease_path(key).name
    for holder in holders:
        assert holder not in name, (
            f"the per-attempt holder {holder!r} reached the lease PATH ({name!r}) — it would be "
            "truncated and collapsed there, and two attempts could alias onto one file"
        )


def test_the_recorded_claim_survives_a_state_round_trip() -> None:
    """The lease FILE outlives the process, so the identity that can release it must too.

    Persisted for the same reason `subagent_id` is: a restarted gateway re-adopting this run is
    the only thing that can give the claim back before its TTL.

    The target is the REAL `claim_key` output rather than a hand-written `run-1:work`, because that
    literal stopped being a shape the engine produces in #3524 — a round-trip test carrying an
    obsolete example teaches the wrong key to whoever copies it next.
    """
    target = claim_key("run-1", STAGE_PATH)
    inst = NodeInstance(
        path=STAGE_PATH,
        state=InstanceState.RUNNING,
        claim_target=target,
        claim_holder="workflow:run-1:work#deadbeefcafe",
    )
    back = NodeInstance.from_dict(inst.to_dict())
    assert back.claim_target == target, inst.to_dict()
    assert back.claim_holder == "workflow:run-1:work#deadbeefcafe", inst.to_dict()


# ── the spawn that never returns a result ────────────────────────────────────


def test_a_spawn_that_raises_releases_the_claim_it_took(tmp_path: Path, monkeypatch) -> None:
    """The one failure that carries the claim identity NOWHERE.

    Every other way `dispatch_stage` declines returns a `NodeResult`, so the holder can travel to
    the controller. A raising `spawn` returns nothing, so unless the claim is dropped on the way
    out there is no code anywhere that could ever release it — the node would refuse its own
    retry for the full TTL, which is #3533 wearing an exception.

    `instance_path` travels with the dispatch for the reason the concurrency test above records: it
    is the claim's key since #3524, and without it the fail-closed guard returns before `spawn` is
    ever called — so the premise (a spawn that RAISES) would not be reached and the test would red
    on `DID NOT RAISE` rather than on the claim it is about.
    """
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    class _Boom:
        def spawn(self, **kw: Any) -> Any:
            raise RuntimeError("the session store was unreachable")

    # The CONTROL, recorded on the way in: "no claim on disk afterwards" would also be satisfied
    # by a path that never claimed at all, so the acquisition itself is observed.
    acquired: list[tuple[str, str]] = []
    real_acquire = leases.acquire_claim

    def _recording(target_id: str, holder: str, **kw: Any):
        acquired.append((target_id, holder))
        return real_acquire(target_id, holder, **kw)

    monkeypatch.setattr(leases, "acquire_claim", _recording)

    with pytest.raises(RuntimeError, match="session store"):
        asyncio.run(
            engine.dispatch_stage(
                _stage_node(),
                BindingContext(),
                subagents=_Boom(),
                run_id="run-1",
                instance_path=STAGE_PATH,
            )
        )

    target = claim_key("run-1", STAGE_PATH)
    assert [t for t, _ in acquired] == [
        target
    ], f"the raising path never took a claim ({acquired}), so the assertion below is vacuous"
    assert leases.read_claim(target) is None, (
        "a spawn that raised left its claim behind, and nothing holds the holder that could "
        "release it"
    )
