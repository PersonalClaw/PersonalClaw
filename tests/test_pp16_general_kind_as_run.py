"""PP-16 session 1 — the `general` loop kind RUNS as a `WorkflowRun`.

The bridgehead for "a Loop becomes a WorkflowRun". Before this, `loop_aliases` could say which
template replaced a kind and **nothing acted on the answer**: its only non-test caller
(`deliverable.py:168`) walks the table forward to build a filename map, so a `general` loop still
became a `loops` row driven by `loop/watchdog.py`. Resolution was not execution.

Three claims are load-bearing here, and each has an easy false version:

1. **A `general` run really executes.** The false version asserts a mapping table — that
   `resolve_kind("general") == "general-project"` — which was already true and proves nothing.
   So `test_a_general_kind_run_executes_and_writes_its_own_ledger` drives the REAL bundled spec
   through a REAL `RunController` and reads the run's OWN ledger rows.
2. **The template's convergence reaches the engine's policy.** The false version asserts the JSON
   contains the block. That would pass with the parser deleted, because `POLICY_FIELDS` would
   still list it and the block would still be on disk. So the assertion goes through
   `RunController._supervisor_policy` and carries a POSITIVE CONTROL: the same node with the block
   removed must resolve to `orchestrated`, which is what the default was before this landed.
3. **The four un-ported kinds are untouched.** A session that breaks four working kinds to land
   one is a regression, not a bridgehead — so their read-time resolution and their table policy are
   both asserted, and the launch path must REFUSE them by name rather than run a stub.

**What this session deliberately does NOT do, railed rather than claimed.** The declared
convergence is resolved into the policy but has no consumer on the run path yet:
`loop/supervisor.done_signal` is the ONE evaluator and takes a `Loop`, reading `kind_config` and
`workspace_dir` off it — the `node_config` / `run_input` homes that the 33-column measurement says
require the loop's phases to already be a graph. `test_the_run_path_has_no_convergence_consumer_yet`
holds that gap in BOTH directions per the plan's criterion 9, so the session that closes it is
forced to update the claim instead of leaving a stale docstring behind.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import loop_aliases, service, store, supervisor_policy
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import Node, RunStatus, WorkflowRun
from personalclaw.workflows.supervisor_policy import (
    DONE_ORCHESTRATED,
    DONE_VERIFY_COMMAND,
    KIND_CONVERGENCE,
    parse_supervisor_policy,
    policy_for_kind,
)
from personalclaw.workflows.validator import validate_spec

#: The kind this session ported. Spelled once; every assertion below reads it, so porting the
#: next kind is a one-line change here plus its own cases.
PORTED = "general"

#: The template `general` resolves to. Read from the alias table rather than restated, so a
#: renamed template moves this suite with it instead of silently testing the wrong spec.
TEMPLATE = loop_aliases.KIND_TO_TEMPLATE[PORTED]

#: The four kinds session 1 did NOT port. Derived by subtraction, so a fifth kind appearing in
#: the alias table joins the "must still work" set automatically rather than being forgotten.
UNPORTED = tuple(sorted(set(loop_aliases.KIND_TO_TEMPLATE) - {PORTED}))

#: Bounded so a red (a run that never terminates) costs seconds rather than hanging the suite.
#: The fake subagent finishes on the controller's own reconciliation lookups, not on a sleep, so
#: a passing run ends on the first tick after the spawn reports done.
RUN_TIMEOUT = 20.0


# ── the fake subagent: the ONLY thing faked, and only on the two methods `dispatch_stage` uses ──


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

    The result is chosen by reading the PROMPT, because `general-project`'s two stages want
    different JSON shapes and the loop reads `meaningful_progress` out of the work stage's. A fake
    that returned one canned string for both would make the judge stage's schema unreachable and
    the loop's dry-out condition untestable.

    `meaningful_progress` is false from the first pass so the `until_dry` loop reaches its
    `streak: 2` and ends in two iterations. That is a real termination through the template's own
    declared condition — not `max_iterations`, which would also "finish" a loop whose progress
    field was never read at all.
    """

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}
        self.prompts: list[str] = []
        self.gets: list[str] = []

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        self.prompts.append(prompt)
        if "You are verifying work you did not do" in prompt:
            payload = {
                "reasoning": "re-ran the cited command; the artifact is present",
                "verdict": "PASS",
                "scores": {
                    "the step accomplished something real": 2,
                    "evidence is checkable": 2,
                },
                "evidence_refs": ["README.md"],
                "proof": "cat README.md printed the added line",
                "cannot_judge": "",
            }
        else:
            payload = {
                "summary": "wrote the line the task asked for",
                "meaningful_progress": False,
                "evidence": "README.md now contains it",
            }
        info = _Info(f"sub{len(self.prompts)}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        """Report done on the first lookup. Keyed on the LOOKUP, not on elapsed time: a fixed
        sleep would measure tick cadence and a frozen clock would make every tick read "not
        yet" — both pass against a controller that never reconciles at all."""
        self.gets.append(agent_id)
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


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


def _loop_node(spec: dict[str, Any]) -> Node:
    root = spec.get("root") or {}
    assert root.get("kind") == "loop", (
        f"{TEMPLATE}'s root is {root.get('kind')!r}, not a loop node — the convergence "
        "declaration below has no node to live on"
    )
    return Node.from_dict(root)


# ── claim 1: a general-kind run really executes ──


def _drive_the_template() -> tuple[RunStatus, str, _FakeSubagents]:
    """Drive the REAL bundled `general-project` spec through a REAL `RunController`.

    Both declared inputs are supplied. `start_run` fills a declared default via
    `_with_declared_defaults`, and constructing the run directly skips that — measured, omitting
    `exit_condition` fails every step on `unresolved reference at 'exit_condition'`, which would
    put a test-construction artifact in front of the product behaviour under test.
    """
    spec = _template_spec()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={
                "task": "add a line to README.md",
                "exit_condition": "README.md contains the line",
            },
        )
    )
    store.write_spec(run.id, spec)
    fake = _FakeSubagents()
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    status = asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    return status, run.id, fake


def test_a_general_kind_run_executes_and_writes_its_own_ledger() -> None:
    """THE clause: `general` runs through the engine as a `WorkflowRun`.

    Asserted on the run's OWN ledger, not on a mapping table. The behaviour this replaces —
    read-time alias resolution with no launch — produces no run and therefore no ledger at all,
    so every assertion here reds against it.

    `status is COMPLETE` IS asserted now, and the history of that line is the point. It began as
    "deliberately NOT asserted: the template still cannot complete", because a loop BODY received no
    `last` at all — so only iteration 0 had an honest value to read and every later one failed to
    bind. Three separate engine seams had to land for the template to finish, the last two in #3524:
    the stage reconciler advancing the iteration counter, `_context_for` handing a body its previous
    iteration (:func:`test_a_loop_body_reads_its_previous_iteration`), and a spawned stage's output
    reaching its declared `schema` at all. With those in, the loop ends on its OWN declared
    `until_dry` streak rather than burning its iteration budget, which is a stronger claim than a
    terminal state: `dry_streak` can only be reached by reading `meaningful_progress` out of the
    body's output, so a green here is also evidence the declared shape arrived.
    """
    status, run_id, fake = _drive_the_template()

    # 1. It COMPLETED. Not "reached some terminal state": `ESCALATED` and `FAILED` were both
    #    accepted here while the loop could not read its own previous iteration, and accepting them
    #    still would let this suite go green against a regression of exactly that.
    assert status is RunStatus.COMPLETE, f"run ended {status}"
    # And it ended on the TEMPLATE's own exit condition rather than on its iteration ceiling —
    # `max_iterations` is what a loop whose body fails every round reaches, so the two endings must
    # not be conflated (#3524's escalation-headline half).
    outcomes = [str(e.get("outcome") or "") for e in J.journal_records(run_id, kinds={"iteration"})]
    assert "dry_streak" in outcomes, f"the loop did not end on its declared condition: {outcomes}"
    assert not [
        o for o in outcomes if o.startswith("breaker:")
    ], f"the breaker tripped on a run that should not thrash: {outcomes}"

    # 2. The template's OWN nodes were reached, both of them, by id. This is the assertion that
    #    read-time aliasing could never satisfy: there was no run, so there was no node.
    #    Read through `journal_records`, not `ledger`: `step_started` is outside LEDGER_KINDS
    #    (only the OUTCOME kinds are in it), so `ledger(kinds={"step_started"})` returns [] — an
    #    empty list that reads as "no node ran" when the rows are right there.
    started = J.journal_records(run_id, kinds={"step_started"})
    reached = {str(e.get("node_id") or "") for e in started}
    assert {"work", "judge"} <= reached, f"expected both stages to start, got {sorted(reached)}"

    # 3. A stage really dispatched — the judge stage completed and spawned a subagent with its own
    #    prompt. Without this the run could have "reached" both nodes and failed both at bind time,
    #    which would prove scheduling but not execution.
    completed = {str(e.get("node_id") or "") for e in J.ledger(run_id, kinds={"step_completed"})}
    assert "judge" in completed, f"no stage completed; completed={sorted(completed)}"
    assert any(
        "You are verifying work you did not do" in p for p in fake.prompts
    ), f"the judge stage completed without receiving its own prompt: {fake.prompts}"

    # 4. The run is journalled start-to-finish. `run_started`/`run_finished` are outside
    #    LEDGER_KINDS, so this reads the journal rather than the ledger mirror.
    assert J.journal_records(run_id, kinds={"run_started"}), "no run_started"
    assert J.journal_records(run_id, kinds={"run_finished"}), "no run_finished"


def test_the_loop_bodys_first_iteration_binds() -> None:
    """The guarded `{{last.… | default(…)}}` idiom works, verified by EXECUTION not by text.

    This replaces `test_the_loop_body_cannot_read_its_previous_iteration`, whose claim was "a loop
    body cannot read ``last`` on ANY iteration". `bindings` keys its escape on a positive
    in-a-loop-body signal instead of on the root simply being absent, so iteration 0 resolves the
    documented default and both body stages run. Iterations 2+ are covered by
    :func:`test_a_loop_body_reads_its_previous_iteration`, which asserts the carried value rather
    than the default — so this test is specifically the FIRST pass, where the honest answer really
    is the default.

    **Why every assertion here is runtime.** The rail that certified this fixed once
    (`test_last_refs_carry_a_default`) scanned the template's JSON source text for
    the pipe and never ran it, so it could only confirm the idiom was PRESENT — and the idiom did
    not work, because `_walk_path` raised before any pipe. A source-text assertion is exactly what
    must not be trusted at this seam.

    **Still scoped to ITERATION 0's instances (`root.body@0.…`)**, now for a different reason than
    when it was written. The old reason was that a later iteration was DESIGNED to raise, so a
    run-wide assertion would have contradicted the design; #3524 removed that. The reason it stays
    scoped is that the claim in the name is about the first pass specifically — the run-wide version
    lives in :func:`test_a_loop_body_reads_its_previous_iteration`, and a test asserting both would
    no longer be able to say which of the two seams broke.
    """
    _, run_id, fake = _drive_the_template()

    unresolved = [
        e
        for e in J.journal_records(run_id, kinds={"step_failed"})
        if "unresolved reference" in str(e.get("error") or "")
        and str(e.get("instance_path") or "").startswith("root.body@0.")
    ]
    assert (
        not unresolved
    ), f"a reference still fails on the FIRST iteration: {[e.get('error') for e in unresolved]}"

    completed = {str(e.get("node_id") or "") for e in J.ledger(run_id, kinds={"step_completed"})}
    assert {
        "work",
        "judge",
    } <= completed, (
        f"the first iteration did not run both body stages; completed={sorted(completed)}"
    )

    # The PIPE ran, not merely the reference. "work completed" alone would also pass against a
    # resolver that rendered a silent empty string for the missing root — the `absent-is-not-zero`
    # failure — so the default's own text has to be in the prompt the stage dispatched.
    work = [p for p in fake.prompts if "Do the next meaningful step" in p]
    assert work, f"the work stage dispatched no prompt: {fake.prompts}"
    assert "(this is the first pass" in work[0], work[0][:400]


def test_a_loop_body_reads_its_previous_iteration() -> None:
    """The other half of the old blocker, now CLOSED (#3524) and pinned as the fixed behaviour.

    This replaces `test_a_loop_body_still_gets_no_real_previous_iteration`, whose claim was that
    `RunController._context_for` sets no `last_output`/`has_last` so a body prompt may never read
    `last`. It does now, and the contract that test said was missing is the one it named: what a
    loop ITERATION's output IS when the body is a container whose children emit different schemas.
    The answer is the body's outputs LAYERED in document order — this template reads `summary` from
    its worker and `verdict` from its judge in one prompt, so no single child's output could ever
    have been it (`RunController._last_output`).

    **Runtime, on the SECOND iteration's real prompt, not on a hand-built context.** A
    `_context_for` assertion would pass against a `_last_output` that returns the right shape from
    the wrong iteration, and the value that matters is the one the model is handed. So this asserts
    the previous iteration's own words are in iteration 1's `work` prompt, and that the first-pass
    default is NOT — which is what tells a carried value from a rescued absence.

    Three engine seams had to land together for this, and the third is why fixing `last` alone was
    not enough: a spawned stage's output was `{"result": "<raw text>"}` regardless of its declared
    `schema`, so wiring `last` only moved the failure from `unresolved reference at 'last'` to
    `unresolved reference at 'summary'`. `_settled_stage_output` carries that argument.
    """
    _, run_id, fake = _drive_the_template()

    work = [p for p in fake.prompts if "Do the next meaningful step" in p]
    assert len(work) >= 2, (
        "the loop never reached a second iteration, so this test cannot observe a carried `last` "
        f"at all: {len(work)} work prompt(s)"
    )
    second = work[1]
    assert "wrote the line the task asked for" in second, (
        "iteration 1's prompt does not carry iteration 0's `summary` — the worker's half of the "
        f"layered iteration output is missing: {second[:600]}"
    )
    assert "What the judge ruled on it: PASS" in second, (
        "iteration 1's prompt does not carry iteration 0's `verdict` — the JUDGE's half is "
        f"missing, which is the half no single child's output could supply: {second[:600]}"
    )
    assert "(this is the first pass" not in second, (
        "iteration 1 still rendered the first-pass default, so `last` resolved to an absence "
        f"rather than to the previous iteration: {second[:600]}"
    )

    # And nothing in the run failed on a reference, at ANY iteration. The narrower
    # `root.body@0.`-scoped version of this assertion in
    # `test_the_loop_bodys_first_iteration_binds` was correct while iterations 2+ were designed to
    # raise; now that they are not, run-wide is the honest scope.
    unresolved = [
        str(e.get("error") or "")
        for e in J.journal_records(run_id, kinds={"step_failed"})
        if "unresolved reference" in str(e.get("error") or "")
    ]
    assert not unresolved, f"a reference still fails somewhere in the run: {unresolved}"


#: The subagent manager's own sentence for a child it killed on its deadline, verbatim — the
#: controller files it unchanged as the failure's `cause_plain`, so this is also what a user reads.
REAPED = "Reaped after 900s (exceeded 900s deadline) [stage]"

WORK_PROMPT = "Do the next meaningful step"


class _ProseWorker(_FakeSubagents):
    """The work stage answers in PROSE, so its declared schema cannot be parsed.

    This was #3524's measured shape and it is now the CONTROL rather than the defect, which is the
    whole point of keeping it. Iteration 0 renders its documented default; from iteration 1 the
    output is the unstructured `{"result": "<text>"}` envelope, so `{{last.output.summary}}` reads a
    field a PRESENT `last` does not carry — and #3544's `_prior_cycle_field_miss` rescues exactly
    that, because the template already declares `| default(…)` for it. So the iterations no longer
    fail: the loop runs all six, `meaningful_progress` is never parseable out of prose so
    `until_dry` cannot fire, and it stops on `max_iterations` HONESTLY.

    Measured: `_iteration_failures` reports `(0, 6, '')` and the run journals ZERO `step_failed`
    rows. That is the negative direction of the test below — the same template, the same ceiling,
    and the reason must stay `max_iterations` — and it is what stops `iterations_failed` from being
    a label this suite would hang on any run that ran out of room.
    """

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        if WORK_PROMPT in prompt:
            self.prompts.append(prompt)
            info = _Info(f"sub{len(self.prompts)}", "I had a look around and things seem fine.")
            self.infos[info.id] = info
            return info
        return super().spawn(**kw)


class _ReapedWorker(_FakeSubagents):
    """The work stage's subagent is KILLED on its deadline, every iteration.

    The premise the test below needs, re-derived against post-#3544 `main`. The original premise was
    `_ProseWorker` — iterations failing on `unresolved reference at 'summary'` — and #3544 rescues
    that miss, so the loop stopped failing and started reaching its ceiling for real. Re-deriving
    rather than re-pointing the assertion: a reap is a failure mode #3544 has no opinion about, so
    the iterations genuinely fail and the escalation has something to be wrong about.

    It is also the honest shape for this claim. `general-project`'s three `last` reads all carry
    `| default(…)` and a stage that returns unparseable text keeps its `{"result": …}` envelope
    rather than failing, so **no binding failure is reachable in this template at all** now — the
    test asserts that too, so the premise cannot quietly drift back onto a rescued miss.

    The error is applied at LOOKUP, not at spawn. `dispatch_stage` reads `info.error` the moment
    `spawn` returns and files a non-empty one as a PERMISSION-class *spawn rejection* — a child that
    never started. A reap is a child that started, ran and was killed, which settles through
    `_reconcile_dispatched_stages` as `FailureClass.TIMEOUT` with `retries_exhausted`. Those are
    different rows with different `cause_plain` values (`spawn rejected: …` vs the sentence itself),
    and only the second is the run this escalation was measured on.
    """

    def __init__(self) -> None:
        super().__init__()
        self._reaped: set[str] = set()

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        if WORK_PROMPT not in prompt:
            return super().spawn(**kw)
        self.prompts.append(prompt)
        info = _Info(f"sub{len(self.prompts)}", "")
        self._reaped.add(info.id)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = super().get(agent_id)
        if info is not None and agent_id in self._reaped:
            info.error = REAPED
            info.result = ""
            info.reaped = True
        return info


def test_a_loop_that_spent_its_budget_failing_does_not_blame_its_ceiling() -> None:
    """The escalation names the real cause, not the budget it happened to reach (#3524).

    Measured shape: five of six iterations failed instantly on a binding and never called a model,
    and the banner read *"the loop reached its iteration ceiling at project. reached 6 iterations"*
    — which a reader takes as "my task was too big" and acts on by shrinking a task that was never
    the problem. The engine held both facts and surfaced the wrong one.

    Asserted on `run.attention`, the record the panel really renders (`attentionMeta.readAttention`
    → `EscalationPanel`), rather than on the helper that derives it: a `_iteration_failures` unit
    test would pass against a `_surface_loop` that ignored it.

    **The PREMISE was re-derived here, and the reason is worth stating rather than absorbing.** The
    failing iterations used to be produced by `_ProseWorker`, whose unstructured output made every
    `{{last.output.summary | default(…)}}` read raise `unresolved reference at 'summary'`. #3544
    rescues precisely that miss, so on current `main` that worker's loop reaches its ceiling with
    **zero** failed iterations — measured `(0, 6, '')` — and the old assertion failed in both
    directions at once: the reason really was `max_iterations`, honestly. Re-pointing the assertion
    at whatever now happens would have deleted the claim; instead the premise moved to a failure
    mode #3544 has no opinion about, a subagent killed on its deadline (`_ReapedWorker`). No binding
    failure is reachable in this template any more — all three `last` reads carry a default and an
    unparseable stage output keeps its envelope rather than failing — which is asserted below so the
    premise cannot drift back onto a rescued miss.

    **Both directions, in one test deliberately.** The claim is comparative — the reason must track
    whether the loop spent its budget WORKING — so the control is the same template hitting the
    same six-iteration ceiling with iterations that did not fail, and it must still read
    `max_iterations`. Split across two tests that discriminator can half-rot; here it is
    structural. (The clean drive elsewhere in this file is a third point: a schema-honouring worker
    COMPLETES on `dry_streak` and produces no escalation at all.)
    """

    def _drive(worker: _FakeSubagents) -> tuple[RunStatus, RunController, str]:
        spec = _template_spec()
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name=TEMPLATE,
                inputs={"task": "t", "exit_condition": "e"},
            )
        )
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices(subagents=worker))
        return asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT)), controller, run.id

    status, controller, run_id = _drive(_ReapedWorker())

    assert (
        status is RunStatus.ESCALATED
    ), f"the run did not escalate, so there is no banner: {status}"
    attention = controller.run.attention or {}
    assert attention.get("kind") == "escalation", attention

    # 1. The token, which is what picks the sentence the user reads.
    assert attention.get("reason") == "iterations_failed", (
        "the escalation still blames the budget it reached rather than the iterations that failed: "
        f"{attention.get('reason')!r} / {attention.get('detail')!r}"
    )

    # 2. The counts and the first failure's own words, so the detail is falsifiable rather than a
    #    second adjective. The original budget token stays in it — it is still true, and it is what
    #    a reader greps for.
    detail = str(attention.get("detail") or "")
    assert "iterations failed instead of finishing their work" in detail, detail
    assert (
        REAPED in detail
    ), f"the detail does not carry the failure the user cannot otherwise read: {detail}"
    assert "max_iterations" in detail, f"the budget token was dropped rather than kept: {detail}"

    # 3. The vacuity floor: the run really did fail most of its iterations. Without this, a detail
    #    naming failures could be describing a run that had none.
    rows = list(J.journal_records(run_id, kinds={"step_failed"}))
    failed = {str(e.get("instance_path") or "") for e in rows}
    assert (
        len(failed) >= 2
    ), f"fewer than two iterations failed, so the claim is not measured: {failed}"

    # 4. And the premise is the re-derived one, not the rescued one it replaced. If a binding miss
    #    ever fails here again, #3544's field-miss rescue has regressed and THAT is the finding —
    #    this test would otherwise absorb it and keep reporting green on the wrong premise.
    unresolved = [
        str(e.get("error") or "")
        for e in rows
        if "unresolved reference" in str(e.get("error") or "")
    ]
    assert not unresolved, (
        "an iteration failed on a binding reference, so this run is no longer measuring a reap — "
        f"#3544's field-miss rescue has regressed: {unresolved}"
    )

    # ── the other direction: the SAME ceiling, iterations that did not fail ──────────────────
    #
    # Without this the token above would be satisfied by a `_surface_loop` that had simply stopped
    # reading the budget reason at all and always said `iterations_failed`.
    ctl_status, ctl_controller, ctl_run_id = _drive(_ProseWorker())
    ctl_attention = ctl_controller.run.attention or {}

    assert ctl_status is RunStatus.ESCALATED, (
        "the control did not escalate, so it is not the same ending and cannot discriminate: "
        f"{ctl_status}"
    )
    assert not list(J.journal_records(ctl_run_id, kinds={"step_failed"})), (
        "the control's iterations FAILED, so it is a second copy of the case above rather than "
        "its opposite — re-derive it against a worker whose iterations complete"
    )
    assert ctl_attention.get("reason") == "max_iterations", (
        "a loop that spent its budget WITHOUT failing is now also reported as `iterations_failed`, "
        f"so the re-derivation is a relabel rather than a discrimination: {ctl_attention!r}"
    )


def test_a_tripped_breaker_reaches_the_timeline_a_user_can_read() -> None:
    """A tripped breaker is journaled; before #3524 no surface showed it.

    `controller._advance_loop` records a trip as an `iteration` row with
    `outcome="breaker:<reason>"`, and that was the ONLY record: `breaker_trip` is a kind only the
    LOOP noun writes (`introspection.RAIL_PRODUCERS` declares that asymmetry deliberately), and
    `iteration` was outside `service._TIMELINE_KINDS`. The node-inspect endpoint did return the row,
    but `web/src/pages/workflows/ledgerRowDetail.ts` projects only `kind`/`sha`/`impact`/
    `rationale`, so it rendered as the bare word `iteration`. An inert signal.

    **The filter is asserted in BOTH directions**, because allowlisting `iteration` wholesale is the
    wrong fix and would pass a one-directional test: an `until_cancelled` watcher writes one row per
    cycle for months, which is the noise the whitelist exists to keep out. So a `breaker:` row must
    arrive and a `continue` row must not.
    """
    from personalclaw.workflows.service import introspection_timeline

    rows = introspection_timeline(
        [
            {"kind": "iteration", "ts": "t1", "node_id": "project", "outcome": "continue"},
            {"kind": "iteration", "ts": "t2", "node_id": "project", "outcome": "dry_streak"},
            {
                "kind": "iteration",
                "ts": "t3",
                "node_id": "project",
                "outcome": "breaker:identical_output",
            },
        ]
    )
    assert [r["detail"] for r in rows] == ["breaker:identical_output"], (
        "the timeline must carry the tripped breaker and nothing else from a loop's per-round "
        f"bookkeeping: {rows}"
    )
    assert rows[0]["kind"] == "iteration" and rows[0]["node_id"] == "project", rows


@contextlib.contextmanager
def _bundled_provider() -> Any:
    """Register the bundled def provider for the duration, then put the registry back.

    `start_run` resolves a template NAME through `defs`, not through `read_template`, and only the
    gateway registers the bundled provider (`gateway.py:2766`). Without this the service refuses
    with `WF_DEF_NOT_FOUND` — which reads as a missing TEMPLATE when the real cause is a missing
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


class _RecordingSupervisor:
    """Records what the service handed it and drives nothing.

    The injection point `start_run` already offers, used here because the claim under test is the
    DOOR — kind in, run handed to the supervisor — and the execution claim is proved separately
    above with a real controller. A supervisor that launched would re-drive the same run through a
    second code path and make a failure ambiguous between the two.
    """

    def __init__(self) -> None:
        self.launched: list[str] = []

    def controller(self, run_id: str) -> None:
        return None

    async def launch(self, run: Any, spec: dict[str, Any], *, depth: int = 0) -> None:
        self.launched.append(run.id)
        return None


def test_the_kind_launch_path_starts_a_run_for_the_ported_kind() -> None:
    """`start_kind_run` is the door that was missing: kind in, live run out.

    Driven through the service (not the controller) because the claim is that a caller holding
    only a legacy kind name can start a run — which is what no code path could do before.

    `skip_preflight` because preflight checks credentials and model bindings, which this lane has
    none of; the door is what is under test, not the credential gate that already has its own suite.
    """
    sup = _RecordingSupervisor()
    with _bundled_provider():
        result = asyncio.run(
            service.start_kind_run(
                PORTED,
                task="add a line to README.md",
                exit_condition="README.md contains the line",
                supervisor=sup,
                skip_preflight=True,
            )
        )
    assert result.get("ok"), result
    run_id = str(result.get("run_id") or "")
    assert run_id, result
    # Handed to the SUPERVISOR, not just written to the store — an unlaunched row is not a run.
    assert sup.launched == [run_id], f"the run was created but never launched: {sup.launched}"
    created = store.get(run_id)
    assert created is not None, f"{run_id} was reported started but is not in the store"
    assert created.workflow_name == TEMPLATE
    # Both inputs reached the run as the template's DECLARED inputs, not as loop columns.
    assert created.inputs.get("task") == "add a line to README.md"
    assert created.inputs.get("exit_condition") == "README.md contains the line"


# ── claim 2: the template's convergence reaches the engine's policy ──


def test_the_template_declared_convergence_reaches_the_resolved_policy() -> None:
    """The declaration is LOAD-BEARING, with a positive control.

    Without the control this is vacuous: `ConvergenceSpec()`'s default signal is `orchestrated`,
    so asserting only that the resolved signal is `verify_command` could pass for a reason
    unrelated to the template. So the same node, with the block stripped, must resolve to the
    default — that difference is the parser doing work.
    """
    spec = _template_spec()
    node = _loop_node(spec)
    run = store.create(WorkflowRun(id="", workflow_name=TEMPLATE))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())

    resolved = controller._supervisor_policy(node)
    assert resolved.convergence.signal == DONE_VERIFY_COMMAND
    assert resolved.convergence.command_key == "verify_command"
    assert resolved.convergence.done_check_optional is True

    # The positive control: strip the declaration, resolve the SAME way, get the default.
    stripped = dict(node.config or {})
    stripped.pop("supervisor", None)
    bare = controller._supervisor_policy(Node.from_dict({**spec["root"], "config": stripped}))
    assert bare.convergence.signal == DONE_ORCHESTRATED, (
        "a node declaring no supervisor block resolved to something other than the default — "
        "the control is broken, so the assertion above proves nothing"
    )


def test_the_declared_block_and_the_kind_table_cannot_drift() -> None:
    """Two sources fill `convergence`, one per execution path, and they must agree.

    This is the rail the split is allowed to exist on. The loop watchdog resolves a policy on
    every poll, so seam 3's reason for a TABLE over template JSON stands: a declared table cannot
    go missing. The run path reads the template, where there is no per-poll read to lose. While
    both paths exist, a `general` loop and a `general` run must converge identically — and the
    only honest way to hold that is to compare them field by field rather than to trust it.
    """
    declared = parse_supervisor_policy(
        (_loop_node(_template_spec()).config or {}).get("supervisor")
    ).convergence
    tabled = KIND_CONVERGENCE[PORTED]
    assert dataclasses.asdict(declared) == dataclasses.asdict(tabled), (
        "the template's declared convergence and KIND_CONVERGENCE disagree — a `general` loop "
        "and a `general` run would complete on different rules"
    )


def test_the_template_still_validates_with_the_declaration_on_it() -> None:
    """A new authoring surface that fails its own validator ships a broken template."""
    res = validate_spec(_template_spec())
    codes = [str(getattr(e, "code", "") or "") for e in (getattr(res, "errors", None) or [])]
    assert not codes, f"{TEMPLATE} no longer validates: {codes}"


@pytest.mark.parametrize(
    "block, expected",
    [
        ({"convergence": "nope"}, "WF_SUPERVISOR_CONVERGENCE_NOT_OBJECT"),
        ({"convergence": {"signl": "never"}}, "WF_SUPERVISOR_UNKNOWN_CONVERGENCE_FIELD"),
        ({"convergence": {"signal": "whenever"}}, "WF_SUPERVISOR_BAD_DONE_SIGNAL"),
    ],
)
def test_a_malformed_convergence_block_is_an_authoring_error(block: dict, expected: str) -> None:
    """The parser is tolerant on purpose, so the VALIDATOR is what must catch a typo.

    A mistyped `signal` parses to the default — "this loop has no point-in-time done check" —
    which is a silent change to how the loop completes. That is the one failure mode this block
    must not have, so each malformed shape is asserted to produce its named code.
    """
    spec = {
        "name": "convergence-lint",
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "counted", "n": 1, "supervisor": block},
            "body": {"kind": "transform", "id": "b", "config": {"expr": "1"}},
        },
    }
    res = validate_spec(spec)
    codes = [str(getattr(e, "code", "") or "") for e in (getattr(res, "errors", None) or [])]
    assert expected in codes, f"expected {expected}, got {codes}"


# ── claim 3: the four un-ported kinds are untouched ──


@pytest.mark.parametrize("kind", UNPORTED)
def test_an_unported_kind_is_refused_rather_than_run_as_a_stub(kind: str) -> None:
    """The refusal that matters, because a caller will not expect it.

    All five kinds RESOLVE today, so resolution is not evidence that the template carries the
    kind's behaviour. Without this refusal an `sdlc` loop would start a `code-project` run whose
    nodes hold none of `sdlc.py`'s 1,788 lines and report success — the half-migration the
    clean-break tenet refuses, in its most expensive form.
    """
    result = asyncio.run(service.start_kind_run(kind, task="x"))
    assert not result.get("ok"), f"{kind} started a run without being ported: {result}"
    assert result["code"] == "WF_LOOP_KIND_NOT_PORTED"
    # The refusal names the template it WOULD have used, so the message is actionable.
    assert result["template"] == loop_aliases.KIND_TO_TEMPLATE[kind]
    # And it names what IS ported, so a caller learns the frontier instead of guessing.
    assert result["ported"] == [PORTED]


def test_an_unknown_kind_is_refused_without_guessing_a_template() -> None:
    """`loop_aliases`' own rule, held at the launch door: no default.

    "It ran something" is harder to debug than "it ran nothing and said why".
    """
    result = asyncio.run(service.start_kind_run("no-such-kind", task="x"))
    assert not result.get("ok")
    assert result["code"] == "WF_LOOP_KIND_UNKNOWN"
    assert "template" not in result, "the refusal leaked a guessed template"


@pytest.mark.parametrize("kind", UNPORTED)
def test_an_unported_kind_keeps_its_read_time_resolution_and_table_policy(kind: str) -> None:
    """The four kinds this session did not port must run exactly as before.

    Both halves: `loop_aliases` still resolves the kind (the NOUN), and `policy_for_kind` still
    answers with the table row the loop watchdog reads (the SUPERVISOR). A session that landed one
    kind by breaking four would pass a test that only checked `general`.
    """
    assert loop_aliases.resolve_kind(kind), f"{kind} stopped resolving to a template"
    policy = policy_for_kind(kind)
    key = supervisor_policy.convergence_key(kind)
    assert dataclasses.asdict(policy.convergence) == dataclasses.asdict(
        KIND_CONVERGENCE[key]
    ), f"{kind}'s loop-path policy no longer matches its declared table row"


# ── the residual, railed in both directions (the plan's criterion 9) ──


def test_the_run_path_has_no_convergence_consumer_yet() -> None:
    """What session 1 did NOT deliver, held so it cannot become a stale docstring.

    The template's convergence is RESOLVED into the policy (asserted above) but nothing on the run
    path READS `policy.convergence`: `loop/supervisor.py` is the ONE evaluator and its
    `done_signal` takes a `Loop`, reading `kind_config` and `workspace_dir` off it. Those are the
    `node_config` / `run_input` destinations the 33-column census says require the loop's phases to
    already be a graph — i.e. a later session's work, not a gap to paper over here.

    Railed in BOTH directions, which is the point:

    * if a `workflows/` module starts reading `convergence` while this test still claims none
      does, it reds — the claim became a lie;
    * if `loop/supervisor.py` stops being the sole reader, it reds too — the evaluator moved and
      the port's recipe changed.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    readers: dict[str, list[int]] = {}
    for path in sorted(src.rglob("*.py")):
        if path.name == "supervisor_policy.py":
            continue  # the declaration's own module
        # Parsed, not grepped. A substring scan for ".convergence" also matches the VALIDATOR's
        # error strings ("unknown supervisor.convergence field") — measured, it reported
        # `workflows/validator.py` as a reader — so the rail would fire on documentation and then
        # be relaxed to shut it up. An attribute ACCESS is the only thing that is actually a read.
        tree = ast.parse(path.read_text(encoding="utf-8"))
        lines = sorted(
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "convergence"
        )
        if lines:
            readers[str(path.relative_to(src))] = lines

    assert set(readers) == {"loop/supervisor.py"}, (
        "the set of modules reading `policy.convergence` changed. If a `workflows/` module now "
        "consumes it, the run path HAS a convergence consumer — update this test and the "
        "`convergence` field's docstring, which both still say it does not. Readers found: "
        f"{sorted(readers)}"
    )
