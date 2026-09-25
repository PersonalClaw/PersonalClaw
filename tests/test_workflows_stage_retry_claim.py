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

from personalclaw.workflows import engine
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


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real `RunController` over a real spec, with only the subagent manager faked.

    The leases dir is redirected at `tmp_path` so a claim in this test cannot be seen by — or
    leak into — the real home.
    """
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    def _build(**kw: Any) -> tuple[RunController, _FakeSubagents, WorkflowRun]:
        fake = _FakeSubagents(**kw)
        spec = _spec()
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
    """
    controller, fake, run = wired(errors=(REAPED, ""))
    acquired: list[tuple[str, str, bool]] = []
    real_acquire = leases.acquire_claim

    def _recording(target_id: str, holder: str, **kw: Any):
        granted, reason = real_acquire(target_id, holder, **kw)
        acquired.append((target_id, holder, granted is not None))
        return granted, reason

    monkeypatch.setattr(leases, "acquire_claim", _recording)

    async def _go() -> tuple[RunStatus, RunStatus]:
        first = await controller.run_to_completion(timeout=RUN_TIMEOUT)
        inst = controller.instances[STAGE_PATH]
        assert inst.state is InstanceState.FAILED, (
            f"the premise failed: the first attempt is {inst.state.value}, not FAILED, so this "
            "test is not measuring a retry of a failed stage"
        )
        assert len(fake.spawns) == 1, f"the premise failed: {len(fake.spawns)} spawns, not 1"
        assert leases.read_claim(claim_key(run.id, "work")) is None, (
            "the failed attempt is still holding its claim — this is #3533 itself, and every "
            "assertion below would be measuring the unfixed code"
        )
        assert _rewind(controller)["ok"], "the rewind was refused, so no retry was requested"
        return first, await controller.run_to_completion(timeout=RUN_TIMEOUT)

    first, second = asyncio.run(_go())
    inst = controller.instances[STAGE_PATH]

    assert first is RunStatus.FAILED, f"the first attempt did not fail (status={first.value})"
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
    assert targets == {claim_key(run.id, "work")}, (
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


def test_a_stage_that_SUCCEEDS_still_holds_its_claim(wired) -> None:
    """🔴 The scope boundary, pinned: the release is on the FAILED settle ONLY, deliberately.

    The symptom a retained DONE claim causes is a different one — a stage-bodied LOOP is refused its
    own next round, because every round re-runs the same node id — and it is fixed by keying the
    claim per node INSTANCE (#3531 / #3524), not by shortening the window here. Two mechanisms for
    one symptom is the dual path this project does not keep.

    **Releasing on both outcomes also clears that bound, and it was measured rather than reasoned
    about**: with the release moved out of the `if error:` branch, `deep-research`'s round loop goes
    from **1** sweep dispatch to **8**, which reds
    `test_pp16_research_kind_as_run.py::test_a_stage_bodied_loop_cannot_re_execute_its_body` — a pin
    #3531 has already rewritten. So the narrow release is not caution about an unknown; it is
    declining to fix a second issue's defect a second way.

    A re-run of the same SUCCEEDED instance is intercepted before the claim is consulted anyway.
    Measured on a `rewind` of a completed stage: the node lands **BLOCKED** on the committed-effects
    redo gate (`effects.redo_blocked` — a `stage` is `_commits_effects`), and a rewind that does not
    bump the epoch is served from the WF2-A1 resume cache in `_launch` instead of dispatching.

    **If you widen the release to both branches, this test is the one that must change**: rewrite it
    as the new behaviour and check `test_pp16_research_kind_as_run.py` in the same commit.
    """
    controller, fake, run = wired(errors=("",))
    status = asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    inst = controller.instances[STAGE_PATH]

    assert status is RunStatus.COMPLETE, f"the premise failed: status={status.value}"
    assert inst.state is InstanceState.DONE, f"the premise failed: {inst.state.value}"
    assert len(fake.spawns) == 1, f"the premise failed: {len(fake.spawns)} spawns"
    held = leases.read_claim(claim_key(run.id, "work"))
    assert held is not None and held.holder == inst.claim_holder, (
        "a SUCCEEDED stage no longer holds its claim. That is a defensible change and not this "
        "issue's: read this docstring, then update `test_pp16_research_kind_as_run.py`'s "
        f"stage-bodied-loop pin in the same commit. (claim={held}, recorded={inst.claim_holder!r})"
    )


# ── direction 2: a CONCURRENT second execution is still REFUSED ──────────────


def test_a_concurrent_second_execution_of_the_same_instance_is_still_refused(wired) -> None:
    """The invariant the claim exists for, which the fix must not trade away.

    The premise is a LIVE attempt: the stage is RUNNING, its subagent has not reported, and the
    controller therefore has not settled it. A second execution of that same instance in that
    window is the thing §1.5 is about, and it must still be turned away — and the winner must
    still hold its claim afterwards, because a release by a non-holder would let the loser steal
    the work by releasing first.
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
        # A second worker arriving at the SAME instance while the first is still executing.
        return await engine.dispatch_stage(
            _stage_node(), BindingContext(), subagents=fake, run_id=run.id
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
    target = claim_key("run-1", "work")
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

    key = claim_key("run-1", "work")
    assert key == claim_key("run-1", "work"), "the claim key is not a pure function of its inputs"
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
    """
    inst = NodeInstance(
        path=STAGE_PATH,
        state=InstanceState.RUNNING,
        claim_target="run-1:work",
        claim_holder="workflow:run-1:work#deadbeefcafe",
    )
    back = NodeInstance.from_dict(inst.to_dict())
    assert back.claim_target == "run-1:work", inst.to_dict()
    assert back.claim_holder == "workflow:run-1:work#deadbeefcafe", inst.to_dict()


# ── the spawn that never returns a result ────────────────────────────────────


def test_a_spawn_that_raises_releases_the_claim_it_took(tmp_path: Path, monkeypatch) -> None:
    """The one failure that carries the claim identity NOWHERE.

    Every other way `dispatch_stage` declines returns a `NodeResult`, so the holder can travel to
    the controller. A raising `spawn` returns nothing, so unless the claim is dropped on the way
    out there is no code anywhere that could ever release it — the node would refuse its own
    retry for the full TTL, which is #3533 wearing an exception.
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
                _stage_node(), BindingContext(), subagents=_Boom(), run_id="run-1"
            )
        )

    target = claim_key("run-1", "work")
    assert [t for t, _ in acquired] == [
        target
    ], f"the raising path never took a claim ({acquired}), so the assertion below is vacuous"
    assert leases.read_claim(target) is None, (
        "a spawn that raised left its claim behind, and nothing holds the holder that could "
        "release it"
    )
