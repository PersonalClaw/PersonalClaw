"""PP-16 session 2 — the `research` kind's behaviour ARRIVES in `deep-research`, and the run path
cannot yet drive it.

The second port, in the shape session 1 established for `general`
(`tests/test_pp16_general_kind_as_run.py`). What is NOT the same is how much of the kind had to
arrive in the template: `general-project`'s root was already a judged work loop, so that session's
whole template diff was a convergence block. `deep-research` was a triage-and-branch pipeline with
**no judge, no `runtime_hints`, and a loop bounded by a cap that did not bind** — so for research
"the kind resolves to that template" was about as far from "the kind runs there" as it gets.

**Read this first: `research` is NOT in `PORTED_LOOP_KINDS`, and that is the session's finding.**
Everything the port owns is here and asserted — the convergence policy, a separate judge, the
`RESEARCH.md` deliverable, the breadth×depth sweep, the intake seam that lets a launch find the
template's own parameter name, and membership in the loop-kind contract suite. What is NOT here is
the flip, because driving the template measured that **the engine cannot run a `stage`-bodied loop
as a loop at all** — and all five templates the loop kinds resolve to are stage-bodied. Flipping
would replace a research loop that iterates on `loop/watchdog.py` with a run that executes one round
and escalates.

**The three defects, each pinned below with a control** (a zero that authorises nothing on its own):

1. `_reconcile_dispatched_stages` never advanced the iteration counter.
   :func:`test_a_stage_bodied_loop_advances_its_iteration_counter` — FIXED in this change, with the
   control being the same spec built from `infer` nodes, which always worked.
2. The double-execution claim is keyed on the NODE ID (`engine.claim_key`) and held for its 900s
   TTL on a successful spawn, so a loop re-running its own body is refused as a duplicate.
   :func:`test_a_stage_bodied_loop_cannot_re_execute_its_body` — NOT fixed here: the key's
   granularity is a `WORK-CONTAINERS` §1.5 security control, and narrowing it is an owner call, not
   a thing to improvise inside a template port.
3. A reconciled stage's output reaches the binding namespace as `{"result": "<raw text>"}` rather
   than in its declared schema, so a loop's `progress_field` is unreadable and a judge contract
   validates nothing. :func:`test_a_reconciled_stages_output_never_reaches_its_declared_shape` —
   NOT fixed here, same reason: it changes what every stage in the library returns.

Each of those three tests asserts the CURRENT behaviour and says in its own body what must change
when it is fixed, so the session that fixes one is forced to update the claim rather than leave a
stale docstring behind. That is the same discipline session 1 used for its `{{last.*}}` blocker.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import deliverable as deliverable_mod
from personalclaw.workflows import journal as J
from personalclaw.workflows import leases, loop_aliases, service, store, supervisor_policy
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.containers import Claim
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import Node, RunStatus, WorkflowRun
from personalclaw.workflows.supervisor_policy import (
    DONE_JUDGE_ASSESSMENT,
    DONE_ORCHESTRATED,
    KIND_CONVERGENCE,
    parse_supervisor_policy,
    policy_for_kind,
)
from personalclaw.workflows.tick import loop_should_continue
from personalclaw.workflows.validator import validate_spec

#: The kind this session ported the BEHAVIOUR of. Spelled once; every assertion below reads it.
KIND = "research"

#: The template `research` resolves to. Read from the alias table rather than restated, so a
#: renamed template moves this suite with it instead of silently testing the wrong spec.
TEMPLATE = loop_aliases.KIND_TO_TEMPLATE[KIND]

#: `research` is a VARIANT kind (`supervisor_policy._VARIANT_KINDS`), so its convergence row is
#: keyed by goal type. Derived through `convergence_key` rather than spelled `"research:open_ended"`
#: — the claim is that a run converges the way THIS KIND's loop does, and the kind's own default
#: variant is what decides which row that is.
CONVERGENCE_KEY = supervisor_policy.convergence_key(KIND)

#: Every kind `start_kind_run` still refuses. Derived by subtraction from BOTH tables, so the
#: session that flips a kind moves this set without editing it.
UNPORTED = tuple(sorted(set(loop_aliases.KIND_TO_TEMPLATE) - service.PORTED_LOOP_KINDS))

#: The loop node the convergence is declared on, and the two stages of its body.
LOOP_ID = "investigate"
SWEEP_ID = "sweep"

#: Bounded so a red (a run that never terminates) costs seconds rather than hanging the suite.
RUN_TIMEOUT = 30.0


# ── the fakes: a subagent manager and the one `infer` node's completion ──


class _Info:
    """The subset of `SubagentInfo` a stage completion is read from."""

    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _FakeSubagents:
    """Stands in for `SubagentManager` on `spawn` + `get`, exactly as `dispatch_stage` calls them.

    The result is chosen by reading the PROMPT, because `deep-research`'s three stages want three
    different JSON shapes. A fake that returned one canned string for all three would make the
    judge's schema unreachable and the sweep's progress field untestable.
    """

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}
        self.prompts: list[str] = []

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        self.prompts.append(prompt)
        if "You are verifying research you did not do" in prompt:
            payload: dict[str, Any] = {
                "reasoning": "re-fetched two cited URLs; both say what the report claims",
                "verdict": "PASS",
                "scores": {
                    "new sources were fetched and read this round": 2,
                    "every claim added cites the source it came from": 2,
                },
                "evidence_refs": ["RESEARCH.md", "https://example.invalid/a"],
                "proof": "web_fetch of https://example.invalid/a matched the quoted line",
                "shortfalls": [],
                "cannot_judge": "",
            }
        elif "Finish the report" in prompt:
            payload = {
                "answer": "the 3.2 release changed the default pool size",
                "confidence": "medium",
                "unknowns": ["whether the change was deliberate"],
            }
        else:
            payload = {
                "summary": "read the changelog and the dependency diff",
                "new_findings_count": 0,
                "sources_read": ["https://example.invalid/a"],
                "open_subtopics": [],
            }
        info = _Info(f"sub{len(self.prompts)}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        """Report done on the first lookup. Keyed on the LOOKUP, not on elapsed time: a fixed
        sleep would measure tick cadence and a frozen clock would make every tick read "not
        yet" — both pass against a controller that never reconciles at all."""
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info

    def sweeps(self) -> list[str]:
        """The sweep prompts actually dispatched — the count of rounds that really ran."""
        return [p for p in self.prompts if "Advance one round of deep research" in p]


class _FakeTriage:
    """The `completion` seam `EngineServices` already exposes, answering the ONE `infer` node.

    Session 1's suite faked only the subagent manager, because `general-project` is stages all the
    way down. `deep-research` opens with an `infer` classifier — the library's blessed opening shape
    and a rail in `test_workflows_bundled.py` — so the run needs a model answer before a branch can
    route. Faked here rather than routed around: making `triage` a stage to avoid a fake would break
    that rail and change the template to suit the test.

    Answers `investigation` so the branch takes the DEEPEST arm. The other two arms exist and route
    (their shapers are pure transforms), but the round loop only runs on this side of the graph, so
    a fake saying `lookup` would leave the ported loop unexercised while the run still went green.
    """

    def __init__(self, tier: str = "investigation") -> None:
        self.tier = tier

    async def __call__(
        self, prompt: str, *, use_case: str = "background", output_type: Any = None
    ) -> str:
        return json.dumps({"tier": self.tier, "why": "the answer has to be assembled"})


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every write under `tmp_path`. Nothing here may touch the real home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


def _template_spec() -> dict[str, Any]:
    """The spec production sees — through `read_template`, with macros expanded.

    NOT `json.load` of the file: a raw read skips macro expansion, so it is a different tree from
    the one the engine runs and an assertion over it can pass while the real spec is broken.
    """
    wf = read_template(TEMPLATE)
    assert wf is not None, f"{TEMPLATE} did not load through the bundled provider"
    return wf.to_dict()


def _node(spec: dict[str, Any], node_id: str) -> dict[str, Any]:
    """The raw node dict with `node_id`, anywhere in the tree (branch cases and loop bodies too)."""

    def walk(raw: Any) -> Any:
        if not isinstance(raw, dict):
            return None
        if raw.get("id") == node_id:
            return raw
        candidates = list(raw.get("children") or [])
        candidates += [raw.get("body"), raw.get("default")]
        candidates += list((raw.get("cases") or {}).values())
        for candidate in candidates:
            found = walk(candidate)
            if found is not None:
                return found
        return None

    found = walk(spec.get("root"))
    assert found is not None, f"{TEMPLATE} has no node {node_id!r}"
    return dict(found)


def _drive(spec: dict[str, Any] | None = None) -> tuple[RunStatus, str, _FakeSubagents]:
    """Drive a spec through a REAL `RunController`. Defaults to the REAL bundled template.

    Every declared input is supplied. `start_run` fills declared defaults via
    `_with_declared_defaults` and constructing the run directly skips that, so an omitted
    `exit_condition` would fail every stage on `unresolved reference at 'exit_condition'` — a
    test-construction artifact standing in front of the product behaviour under test.
    """
    spec = _template_spec() if spec is None else spec
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=str(spec.get("name") or TEMPLATE),
            inputs={
                "question": "why did our p99 latency double after the 3.2 release",
                "exit_condition": "every claim cites a fetched source",
                "output_manner": "a one-page brief",
                "source_budget": 0,
            },
        )
    )
    store.write_spec(run.id, spec)
    fake = _FakeSubagents()
    controller = RunController(
        run, spec, services=EngineServices(subagents=fake, completion=_FakeTriage())
    )
    status = asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    return status, run.id, fake


# ── what the port DELIVERS: the template really executes the research loop's nodes ──


def test_the_research_loops_own_nodes_execute_as_a_workflow_run() -> None:
    """Every node of the ported graph is reached and dispatched, on the run's OWN ledger.

    Asserted on the ledger rather than on a mapping table: `resolve_kind("research") ==
    "deep-research"` was already true before any of this and proves nothing, while the behaviour
    this replaces — read-time aliasing with no launch — produces no run and therefore no ledger at
    all, so every assertion here reds against it.

    The terminal status is asserted as ESCALATED, NOT complete, and the reason is
    :func:`test_a_stage_bodied_loop_cannot_re_execute_its_body`: round 1 runs end to end, rounds 2+
    are refused as duplicate executions, and the breaker correctly hands a loop that cannot make
    progress to a human. Asserting COMPLETE here would require either weakening that blocker or
    editing the template to dodge it, and both would report a working port that is not one.
    """
    status, run_id, fake = _drive()

    # 1. Terminal, not hung — the timeout would have surfaced as a non-terminal status.
    assert status is RunStatus.ESCALATED, (
        f"run ended {status}. COMPLETE here means the stage-loop defects below are fixed: update "
        "them and this assertion together"
    )

    # 2. The template's OWN nodes were reached, by id, across all three tiers of the graph: the
    #    classifier, the tier shaper the branch selected, both stages of the round loop, and the
    #    finaliser after it. Read through `journal_records`, not `ledger`: `step_started` is outside
    #    LEDGER_KINDS (only the OUTCOME kinds are in it), so `ledger(kinds={"step_started"})`
    #    returns [] — an empty list that reads as "no node ran" when the rows are right there.
    started = {
        str(e.get("node_id") or "") for e in J.journal_records(run_id, kinds={"step_started"})
    }
    assert {"triage", "shape_investigation", SWEEP_ID, "judge", "synthesize"} <= started, sorted(
        started
    )

    # 3. Real stages dispatched with their OWN prompts. Without this the run could have "reached"
    #    every node and failed each at bind time — scheduling, not execution.
    completed = {str(e.get("node_id") or "") for e in J.ledger(run_id, kinds={"step_completed"})}
    assert {SWEEP_ID, "judge", "synthesize"} <= completed, sorted(completed)
    assert any("You are verifying research you did not do" in p for p in fake.prompts)
    assert any("Finish the report" in p for p in fake.prompts)

    # 4. Round 1 failed nothing. Stated separately from the status because an escalated run could
    #    also have escalated out of a genuine failure.
    failures = [
        (e.get("node_id"), e.get("error")) for e in J.journal_records(run_id, kinds={"step_failed"})
    ]
    assert not failures, failures

    # 5. The run is journalled start-to-finish. `run_started`/`run_finished` are outside
    #    LEDGER_KINDS, so this reads the journal rather than the ledger mirror.
    assert J.journal_records(run_id, kinds={"run_started"}), "no run_started"
    assert J.journal_records(run_id, kinds={"run_finished"}), "no run_finished"


# ── DEFECT 1: the iteration counter, fixed here ──


def _synthetic_loop(body_kind: str) -> dict[str, Any]:
    """`sequence[transform, loop(until_dry, body=sequence[X, X])]` for `X` in {stage, infer}.

    The minimum shape that isolates the settle path: identical everywhere except the body's node
    KIND, which is what decides whether the completion is settled by `_apply` (an awaited dispatch)
    or by `_reconcile_dispatched_stages` (a spawned subagent).
    """
    node = {
        "kind": body_kind,
        "id": SWEEP_ID,
        "config": {
            "prompt": "Advance one round of deep research {{inputs.question}}",
            "schema": {"new_findings_count": "integer", "summary": "string"},
        },
    }
    second = {
        "kind": body_kind,
        "id": "second",
        "config": {"prompt": "close it {{inputs.question}}", "schema": {"verdict": "string"}},
    }
    return {
        "name": "stage-loop-probe",
        "inputs": {"question": {"type": "string", "required": True, "help": "h"}},
        "root": {
            "kind": "sequence",
            "id": "r",
            "children": [
                {"kind": "transform", "id": "entry", "config": {"expr": {"a": 1}}},
                {
                    "kind": "loop",
                    "id": LOOP_ID,
                    "config": {
                        "mode": "until_dry",
                        "streak": 2,
                        "progress_field": "new_findings_count",
                        "max_iterations": 4,
                        "timeout_stall_secs": 60,
                    },
                    "body": {"kind": "sequence", "id": "round", "children": [node, second]},
                },
            ],
        },
    }


def _iterations(run_id: str) -> list[str]:
    """The loop's own `iteration` outcomes. `iteration` is the journal kind — NOT `loop_iteration`,
    which matches nothing and reads as "the loop never advanced" against a loop that did."""
    return [str(e.get("outcome") or "") for e in J.journal_records(run_id, kinds={"iteration"})]


def test_a_stage_bodied_loop_advances_its_iteration_counter() -> None:
    """🔴 DEFECT 1, FIXED in this change: the loop counter was never advanced for a spawned stage.

    `_advance_loop` had exactly ONE call site — `_apply` — and `_apply` returns at its `RUNNING`
    branch for a spawned stage, handing the settle to `_reconcile_dispatched_stages`. That method
    set the state, journalled `step_completed`, recorded the effect and published — and returned
    without advancing anything. A loop whose body ENDED in a stage therefore finished round one and
    stopped, and the tick loop reported `run deadlocked: no runnable nodes and none in flight`.

    Same symptom as the container-body bug `_loop_parent`'s docstring records — "deadlock after
    exactly one iteration" — reached by the other of the two settle paths.

    The control is the same spec with `infer` bodies, which are settled by `_apply` and always
    worked. Without it, "the stage version now advances" could be a probe that advances everything.
    """
    _, stage_run, _ = _drive(_synthetic_loop("stage"))
    _, infer_run, _ = _drive(_synthetic_loop("infer"))

    assert _iterations(stage_run), (
        "a stage-bodied loop recorded no `iteration` at all — the counter is not being advanced "
        "from the stage reconciler again, and the run will deadlock after one round"
    )
    assert _iterations(infer_run), (
        "the CONTROL recorded no iteration either, so this test cannot tell a fixed stage path "
        "from a broken one"
    )


# ── DEFECT 2: the double-execution claim, NOT fixed here ──


def test_a_stage_bodied_loop_cannot_re_execute_its_body() -> None:
    """🔴 DEFECT 2, NOT FIXED — and it is why `research` is not in `PORTED_LOOP_KINDS`.

    `engine.dispatch_stage` takes `leases.acquire_claim(claim_key(run_id, node.id), holder)` before
    it spawns, and releases it on only two paths: at capacity, and on a rejected spawn. A SUCCESSFUL
    spawn holds it for the full `containers.DEFAULT_LEASE_SECS` (900s). The key is
    ``run_id:node_id`` — per NODE, not per node INSTANCE — and a loop re-runs the same node id every
    round. So round 2 of any stage-bodied loop is refused as a duplicate execution, lands
    `DEGRADED` with *"another worker holds the claim on this node"*, spawns nothing, and the breaker
    correctly trips on the unchanged output.

    Measured, and stated as the bound it is: **no `stage`-bodied loop in this engine can execute its
    body more than once inside 900 seconds.** All five templates the loop kinds resolve to are
    stage-bodied, so this is not a research finding — it bounds the whole per-kind port program.

    **Why this session did not fix it.** The claim is the `WORK-CONTAINERS` §1.5 double-execution
    control. Narrowing its key from the node to the node INSTANCE is very probably correct — the
    threat `claim_holder`'s docstring names (two co-tenant sessions in one gateway) collides on the
    same instance path either way — but changing the granularity of a security control is an owner
    decision, not a thing to improvise inside a template port.

    The control is the same drive with the claim granted every time: the sweep then dispatches once
    per round. Without it, "one sweep for eight iterations" could be a loop that never iterated —
    which is exactly what defect 1 looked like.
    """
    _, _, as_shipped = _drive()
    assert len(as_shipped.sweeps()) == 1, (
        f"the round loop dispatched {len(as_shipped.sweeps())} sweeps. More than one means the "
        "claim is no longer blocking re-execution — fix the docstring above, re-check whether "
        "`research` can join PORTED_LOOP_KINDS, and rewrite this assertion as the fixed behaviour"
    )

    granted = []

    def _always_grant(target: str, holder: str, **kw: Any) -> tuple[Claim, str]:
        granted.append(target)
        now = time.time()
        return Claim(holder=holder, expires_at=now + 900, taken_at=now), ""

    original = leases.acquire_claim
    leases.acquire_claim = _always_grant  # type: ignore[assignment]
    try:
        _, _, unblocked = _drive()
    finally:
        leases.acquire_claim = original  # type: ignore[assignment]

    assert len(unblocked.sweeps()) > 1, (
        "the CONTROL dispatched no more sweeps than the shipped path, so the claim is not what is "
        f"stopping the loop and the diagnosis above is wrong: {len(unblocked.sweeps())} sweep(s)"
    )
    # And the claim really is taken on the bare node id, which is the fixable part.
    assert any(t.endswith(f":{SWEEP_ID}") for t in granted), granted


# ── DEFECT 3: a reconciled stage's output shape, NOT fixed here ──


def test_a_reconciled_stages_output_never_reaches_its_declared_shape() -> None:
    """🔴 DEFECT 3, NOT FIXED: a spawned stage's output is not parsed into its declared schema.

    `_reconcile_dispatched_stages` stores ``{"result": str(info.result)}`` and puts that in the
    binding namespace. The declared `schema` is never applied, so two shipped mechanisms read
    nothing off a stage in a loop body:

    * a loop's `progress_field` — `_progress_value` looks for the field in the body's outputs, does
      not find it, and `_iteration_is_dry` falls back to the whole-output rule. The output is a
      non-empty dict every round, so the loop is permanently "not dry" and `until_dry` degenerates
      into `max_iterations`. `general-project`'s `meaningful_progress` is the same field in the same
      position, so this is not a research-specific gap.
    * a `judge_contract` stage — the contract cannot find the verdict in the raw text and produces
      an INVALID verdict from a judge that returned a clean PASS.

    Not fixed here because it changes what EVERY stage in the library returns to its downstream
    bindings, which is a contract change for 19 templates rather than a port detail.

    The control is the `infer` node in the same run, which IS parsed — so this measures the settle
    path rather than the schema mechanism.
    """
    spec = _template_spec()
    run = store.create(WorkflowRun(id="", workflow_name=TEMPLATE, inputs={"question": "q"}))
    store.write_spec(run.id, spec)
    fake = _FakeSubagents()
    controller = RunController(
        run, spec, services=EngineServices(subagents=fake, completion=_FakeTriage())
    )
    asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))

    # The CONTROL first: an awaited `infer` node's output IS in its declared shape.
    assert (controller._outputs.get("triage") or {}).get("tier") == "investigation", (
        "the control is broken: even an `infer` node's declared shape is missing, so the assertion "
        f"below measures nothing: {controller._outputs.get('triage')!r}"
    )

    # The defect: the sweep's declared `new_findings_count` is nowhere in the namespace, which is
    # why the loop's declared progress field cannot decide dryness.
    sweep_out = controller._outputs.get(SWEEP_ID)
    assert "new_findings_count" not in (sweep_out if isinstance(sweep_out, dict) else {}), (
        "a reconciled stage's declared schema now reaches the binding namespace. That fixes the "
        "`progress_field` half of this atom — update this test, and re-check whether the round "
        f"loop now ends on `dry_streak`: {sweep_out!r}"
    )


def test_a_binding_is_not_a_cap_which_is_why_the_round_loop_declares_a_literal() -> None:
    """🔴 The fourth defect, FIXED in the template: `max_iterations` as a binding did not bind.

    `deep-research` shipped `max_iterations: "{{inputs.rounds}}"`. Both readers of the field —
    `tick.loop_should_continue` and `resilience.check_breaker` — gate on `isinstance(cap, int)`, and
    a loop node's `config` is never resolved: `resolve_config` is called per NODE KIND in
    `engine.py`, and a loop is a container the controller advances itself. So the cap did not bind,
    and with no `progress_field` either the loop's only exit was something outside it.
    `template_lint`'s `WFL_TANGLED_LOOP` accepted the binding form on the stated grounds that it "is
    resolved at run start"; that claim was false and the branch is deleted.

    The positive control is the whole test: the SAME node with a literal cap must stop. Without it,
    "the binding form keeps going" could be a broken probe rather than a finding.
    """
    body = {"kind": "transform", "id": "b", "config": {"expr": "1"}}
    base: dict[str, Any] = {"mode": "until_dry", "streak": 2}
    bound = Node.from_dict(
        {
            "kind": "loop",
            "id": "l",
            "config": {**base, "max_iterations": "{{inputs.rounds}}"},
            "body": body,
        }
    )
    literal = Node.from_dict(
        {"kind": "loop", "id": "l", "config": {**base, "max_iterations": 3}, "body": body}
    )
    assert loop_should_continue(bound, iteration=99, last_output={"a": 1}, dry_streak=0) == (
        True,
        "",
    ), "a binding-shaped cap now binds — if the engine learned to resolve it, this rail is stale"
    assert loop_should_continue(literal, iteration=99, last_output={"a": 1}, dry_streak=0) == (
        False,
        "max_iterations",
    ), "the control is broken: a literal cap did not stop the loop, so the assertion above is empty"

    cap = (_node(_template_spec(), LOOP_ID).get("config") or {}).get("max_iterations")
    assert isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1, (
        f"{TEMPLATE}:{LOOP_ID} declares max_iterations={cap!r}, which the engine cannot read as a "
        "cap — the loop is unbounded again"
    )


# ── the intake seam: a launch finds the input the template actually declares ──


class _RecordingSupervisor:
    """Records what the service handed it and drives nothing.

    The injection point `start_run` already offers, used because the claim under test is the DOOR —
    kind in, run handed to the supervisor — and execution is proved separately above with a real
    controller. A supervisor that launched would re-drive the same run through a second code path
    and make a failure ambiguous between the two.
    """

    def __init__(self) -> None:
        self.launched: list[str] = []

    def controller(self, run_id: str) -> None:
        return None

    async def launch(self, run: Any, spec: dict[str, Any], *, depth: int = 0) -> None:
        self.launched.append(run.id)
        return None


@pytest.fixture
def bundled_provider() -> Any:
    """Register the bundled def provider for the duration, then put the registry back.

    `start_run` resolves a template NAME through `defs`, not through `read_template`, and only the
    gateway registers the bundled provider. Without this the service refuses with
    `WF_DEF_NOT_FOUND` — which reads as a missing TEMPLATE when the real cause is a missing
    PROVIDER. The registry is process-global, so it is unregistered again rather than left behind
    for whatever test the randomised order runs next.
    """
    from personalclaw.workflows.bundled_defs import PROVIDER_NAME, register_bundled_provider
    from personalclaw.workflows.defs import get_provider, unregister_provider

    preexisting = get_provider(PROVIDER_NAME) is not None
    register_bundled_provider()
    try:
        yield
    finally:
        if not preexisting:
            unregister_provider(PROVIDER_NAME)


def test_the_launch_door_fills_the_input_the_template_actually_declares(
    monkeypatch: pytest.MonkeyPatch, bundled_provider: Any
) -> None:
    """`start_kind_run` puts a research loop's task in `question`, not in `task`.

    This is the seam the port needed and session 1 did not. `general-project` happens to call its
    task `task`, so the door could spell the name as a constant and be right about one template.
    `deep-research` calls it `question` — measured, `design-project` calls it `brief` — so the
    constant was `general-project`'s vocabulary masquerading as the platform's, and flipping the
    frozenset alone would have produced `WF_RUN_MISSING_INPUTS` at every launch.

    Driven with `PORTED_LOOP_KINDS` widened to include `research`, because the frozenset is the ONLY
    thing still holding the kind back — the point is to show that the door works the moment the
    engine defects above are fixed, without pretending they already are.
    """
    monkeypatch.setattr(service, "PORTED_LOOP_KINDS", frozenset({*service.PORTED_LOOP_KINDS, KIND}))
    sup = _RecordingSupervisor()
    result = asyncio.run(
        service.start_kind_run(
            KIND,
            task="why did our p99 latency double after the 3.2 release",
            exit_condition="every claim cites a fetched source",
            supervisor=sup,
            skip_preflight=True,
        )
    )
    assert result.get("ok"), result
    run_id = str(result.get("run_id") or "")
    assert sup.launched == [run_id], f"the run was created but never launched: {sup.launched}"
    created = store.get(run_id)
    assert created is not None and created.workflow_name == TEMPLATE
    assert created.inputs.get("question") == "why did our p99 latency double after the 3.2 release"
    assert created.inputs.get("exit_condition") == "every claim cites a fetched source"
    # POSITIVE CONTROL for "the name was read, not guessed": `task` — the name the door used to
    # spell — is NOT on this run. Without it the two assertions above would also pass for a door
    # that sent both names and let the template discard one.
    assert "task" not in created.inputs, (
        "the launch still sends a literal `task` input, which `deep-research` does not declare and "
        f"silently discards: {sorted(created.inputs)}"
    )


def test_the_intake_marker_is_read_off_the_template_with_a_control() -> None:
    """The marker is LOAD-BEARING, not decoration.

    Without the control this is vacuous: `template_intake` returning `{"task": "question"}` could be
    a hard-coded table as easily as a read. So the SAME reader is run against the same spec with the
    marker stripped, and must find nothing.
    """
    spec = _template_spec()
    assert loop_aliases.template_intake(spec) == {
        "task": "question",
        "success_criteria": "exit_condition",
    }
    stripped = {
        name: {k: v for k, v in meta.items() if k != "loop_field"}
        for name, meta in (spec.get("inputs") or {}).items()
    }
    assert loop_aliases.template_intake({**spec, "inputs": stripped}) == {}, (
        "the reader answered without the markers — so the assertion above is not measuring the "
        "template's declaration"
    )


def test_a_ported_template_without_the_marker_is_refused_not_launched(
    monkeypatch: pytest.MonkeyPatch, bundled_provider: Any
) -> None:
    """The third refusal: a port that forgets the marker fails loudly at the door.

    The failure this prevents is the expensive one — a run that starts, spends a model call on every
    node, and hands its worker an unresolved reference where the question should have been.
    """
    monkeypatch.setattr(service, "PORTED_LOOP_KINDS", frozenset({*service.PORTED_LOOP_KINDS, KIND}))
    real = loop_aliases.template_intake
    monkeypatch.setattr(loop_aliases, "template_intake", lambda spec: {})
    result = asyncio.run(service.start_kind_run(KIND, task="x", skip_preflight=True))
    assert not result.get("ok"), result
    assert result["code"] == "WF_LOOP_KIND_NO_TASK_INPUT"
    assert result["template"] == TEMPLATE

    # The control: put the real reader back and the same call gets past this refusal.
    monkeypatch.setattr(loop_aliases, "template_intake", real)
    again = asyncio.run(
        service.start_kind_run(
            KIND, task="x", supervisor=_RecordingSupervisor(), skip_preflight=True
        )
    )
    assert again.get("ok"), again


def test_the_marker_vocabulary_is_closed_at_authoring_time() -> None:
    """The reader is tolerant on purpose, so the VALIDATOR is what must catch a typo.

    A mistyped `loop_field` is DROPPED by `template_intake`, which reads as "this template declares
    no task input" — indistinguishable from a template that never tried. That is the one failure
    mode this marker must not have: silent, and about whether the kind can launch at all.
    """
    base: dict[str, Any] = {
        "name": "intake-lint",
        "root": {"kind": "transform", "id": "t", "config": {"expr": "{{inputs.a}}"}},
    }
    bad = validate_spec({**base, "inputs": {"a": {"type": "string", "loop_field": "tsak"}}})
    assert "WF_INPUT_BAD_LOOP_FIELD" in [i.code for i in bad.issues]

    dupe = validate_spec(
        {
            **base,
            "inputs": {
                "a": {"type": "string", "loop_field": "task"},
                "b": {"type": "string", "loop_field": "task"},
            },
        }
    )
    assert "WF_INPUT_DUPLICATE_LOOP_FIELD" in [i.code for i in dupe.issues]

    # The control: the spelling the templates ship is accepted, so the two codes above are about the
    # typo and not about the field existing at all.
    good = validate_spec({**base, "inputs": {"a": {"type": "string", "loop_field": "task"}}})
    assert not [i.code for i in good.issues if i.code.startswith("WF_INPUT_")], [
        i.to_dict() for i in good.issues
    ]


def test_every_ported_kind_declares_its_task_input_and_only_loop_kinds_do() -> None:
    """The rail the NEXT port trips if it forgets the marker, in both directions.

    Widening `PORTED_LOOP_KINDS` is the whole ceremony of a port and the marker is the only other
    thing a launch needs, so the ported set is swept rather than a hand-listed pair. And the marker
    is confined to templates a loop kind resolves to: one on any other template would be inert at
    best, and at worst a claim that some template is a loop-kind descendant when it is not.
    """
    for kind in sorted(service.PORTED_LOOP_KINDS):
        template = loop_aliases.KIND_TO_TEMPLATE[kind]
        wf = read_template(template)
        assert wf is not None, f"{kind} is ported to {template!r}, which does not load"
        assert loop_aliases.template_intake(wf.to_dict()).get("task"), (
            f"{kind} is in PORTED_LOOP_KINDS but {template!r} declares no input marked "
            "`loop_field: task` — every launch of it would be refused"
        )

    kind_templates = set(loop_aliases.KIND_TO_TEMPLATE.values())
    marked = set()
    for name in template_names():
        wf = read_template(name)
        if wf is not None and loop_aliases.template_intake(wf.to_dict()):
            marked.add(name)
    assert marked <= kind_templates, (
        "a template no loop kind resolves to declares a `loop_field` marker: "
        f"{sorted(marked - kind_templates)}"
    )


def test_the_general_port_still_reads_its_own_input_names() -> None:
    """The kind ported BEFORE this one must be unaffected by the intake change.

    `start_kind_run` stopped spelling `task` as a constant. `general-project` still calls its task
    `task`, so the right outcome is that nothing changed for it — and the only way to know that is
    to read the markers it now declares rather than to assume the refactor was neutral.
    """
    assert loop_aliases.template_intake(read_template("general-project").to_dict()) == {
        "task": "task",
        "success_criteria": "exit_condition",
    }


# ── the convergence policy the port moved out of Python ──


def test_the_template_declared_convergence_reaches_the_resolved_policy() -> None:
    """The declaration is LOAD-BEARING, with a positive control.

    Without the control this is vacuous: `ConvergenceSpec()`'s default signal is `orchestrated`, so
    asserting only that the resolved signal is `judge_assessment` could pass for a reason unrelated
    to the template. So the same node, with the block stripped, must resolve to the default.
    """
    spec = _template_spec()
    raw = _node(spec, LOOP_ID)
    run = store.create(WorkflowRun(id="", workflow_name=TEMPLATE))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())

    resolved = controller._supervisor_policy(Node.from_dict(raw))
    assert resolved.convergence.signal == DONE_JUDGE_ASSESSMENT
    assert resolved.convergence.ground_truth_deliverable == "RESEARCH.md"

    stripped = {k: v for k, v in (raw.get("config") or {}).items() if k != "supervisor"}
    bare = controller._supervisor_policy(Node.from_dict({**raw, "config": stripped}))
    assert bare.convergence.signal == DONE_ORCHESTRATED, (
        "a node declaring no supervisor block resolved to something other than the default — the "
        "control is broken, so the assertion above proves nothing"
    )


def test_the_declared_block_and_the_kind_table_cannot_drift() -> None:
    """Two sources fill `convergence`, one per execution path, and they must agree.

    Seam 3's reason for a TABLE over template JSON stands on the loop side: the watchdog resolves a
    policy on every poll, and a declared table cannot go missing. The run path reads the template,
    where there is no per-poll read to lose. While both paths exist, a `research` loop and a
    `deep-research` run must converge identically — held field by field rather than trusted.
    """
    declared = parse_supervisor_policy(
        (_node(_template_spec(), LOOP_ID).get("config") or {}).get("supervisor")
    ).convergence
    assert dataclasses.asdict(declared) == dataclasses.asdict(KIND_CONVERGENCE[CONVERGENCE_KEY]), (
        f"the template's declared convergence and KIND_CONVERGENCE[{CONVERGENCE_KEY!r}] disagree — "
        "a `research` loop and a `deep-research` run would complete on different rules"
    )


def test_the_template_names_the_document_its_kind_declares() -> None:
    """`ground_truth_deliverable: RESEARCH.md` has to be a thing the template ASKS FOR.

    A convergence spec naming a document the graph never mentions is the stub `start_kind_run`
    exists to refuse: the run would converge on a file nothing wrote.
    `deliverable.instructed_by_spec` is the shipped reader of exactly this question, so the claim
    goes through it rather than through a private grep.

    The control is the other four templates the kinds resolve to: they still name nothing, which is
    what makes a `True` here a measurement of this port instead of a property of the reader.
    """
    name = KIND_CONVERGENCE[CONVERGENCE_KEY].ground_truth_deliverable
    assert name == "RESEARCH.md", name
    assert deliverable_mod.instructed_by_spec(_template_spec(), name) is True

    others = {
        template: deliverable_mod.instructed_by_spec(read_template(template).to_dict(), name)
        for kind, template in sorted(loop_aliases.KIND_TO_TEMPLATE.items())
        if kind != KIND
    }
    assert not any(others.values()), (
        "another loop-kind template now names RESEARCH.md too, so this rail no longer isolates the "
        f"research port: {others}"
    )


def test_the_judges_validated_verdict_is_bound_rather_than_discarded() -> None:
    """done_when: the judge stage's output is READ.

    WF2LOO-12 measured the failure this prevents — a contract-shaped verdict produced every
    iteration and thrown away. `deep-research` had no judge at all before this port; now it has one,
    and `synthesize` (the node after the loop) reads the verdict AND the shortfalls.

    Bound from OUTSIDE the loop deliberately. The three templates that carry a critique into the
    next iteration do it through `{{last.output.*}}`, which a loop body cannot bind on any iteration
    — the blocker session 1 recorded. Reading the last round's verdict after the loop needs no
    `last`, and `dep_edges_for_root` orders it through the enclosing sequence.

    Note what this does NOT claim: that the verdict RESOLVES to a real value. Defect 3 above means a
    reconciled stage's output is raw text, so the binding is correct and its value is not yet — the
    two are separate facts and only the first is this port's to deliver.
    """
    spec = _template_spec()
    prompt = str((_node(spec, "synthesize").get("config") or {}).get("prompt") or "")
    assert "{{nodes.judge.output.verdict}}" in prompt, prompt
    assert "{{nodes.judge.output.shortfalls}}" in prompt, prompt
    assert "{{last." not in json.dumps(
        spec
    ), f"{TEMPLATE} now references `last`, which a loop body cannot bind on any iteration"


# ── the kinds `start_kind_run` still refuses ──


@pytest.mark.parametrize("kind", UNPORTED)
def test_an_unported_kind_is_refused_rather_than_run_as_a_stub(kind: str) -> None:
    """The refusal that matters, because a caller will not expect it.

    All five kinds RESOLVE today, so resolution is not evidence that the template carries the kind's
    behaviour. Without this refusal a `code` loop would start a `code-project` run whose nodes hold
    none of `sdlc.py`'s 1,788 lines and report success.

    `research` is in this set for a DIFFERENT reason from the other three — its template does now
    carry the behaviour, and what it is waiting on is the engine. Refusing it is still the right
    answer: a run that executes one round and escalates is worse for the user than the loop path
    they get today.
    """
    result = asyncio.run(service.start_kind_run(kind, task="x"))
    assert not result.get("ok"), f"{kind} started a run without being ported: {result}"
    assert result["code"] == "WF_LOOP_KIND_NOT_PORTED"
    assert result["template"] == loop_aliases.KIND_TO_TEMPLATE[kind]
    assert result["ported"] == sorted(service.PORTED_LOOP_KINDS)


@pytest.mark.parametrize("kind", UNPORTED)
def test_an_unported_kind_keeps_its_read_time_resolution_and_table_policy(kind: str) -> None:
    """Every kind this session did not flip must run exactly as before.

    Both halves: `loop_aliases` still resolves the kind (the NOUN), and `policy_for_kind` still
    answers with the table row the loop watchdog reads (the SUPERVISOR). A session that landed one
    template by breaking four would pass a test that only checked `deep-research`.
    """
    assert loop_aliases.resolve_kind(kind), f"{kind} stopped resolving to a template"
    policy = policy_for_kind(kind)
    key = supervisor_policy.convergence_key(kind)
    assert dataclasses.asdict(policy.convergence) == dataclasses.asdict(
        KIND_CONVERGENCE[key]
    ), f"{kind}'s loop-path policy no longer matches its declared table row"


def test_research_is_still_refused_at_the_door_and_the_reason_is_recorded() -> None:
    """The session's own conclusion, asserted rather than left in a commit message.

    `research` is NOT ported, and the thing holding it is the engine rather than the template. This
    reds the moment someone widens the frozenset, which is the prompt to check that defects 2 and 3
    above are actually fixed first.
    """
    assert KIND not in service.PORTED_LOOP_KINDS, (
        "`research` was added to PORTED_LOOP_KINDS. Before that is correct, "
        "`test_a_stage_bodied_loop_cannot_re_execute_its_body` and "
        "`test_a_reconciled_stages_output_never_reaches_its_declared_shape` must both be fixed and "
        "rewritten — until then a research loop through this door runs one round and escalates"
    )
