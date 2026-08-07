"""Node dispatchers — the only place in the engine that performs real work.

One function per node kind, each with the same contract: take a resolved node and a
binding context, do the work, return a `NodeResult`. Dispatchers do NOT write state and
do NOT decide what runs next — the controller owns both. Keeping that split means a
dispatcher can be tested by calling it, with no run, no lock, and no journal.

Two asymmetries here are deliberate and easy to get backwards:

**A null output is a value; an unresolvable reference is an error (WF2-R9).** A node that
legitimately produced nothing hands `None` downstream, and `filter`/`default` pipes exist
to absorb it. But a binding naming something that does not exist raises `BindingError` —
because the silent alternative is a prompt with an empty hole in it, which produces
confident nonsense that looks like a real answer. That failure must be loud.

**`stage` and `infer` are different kinds for a real reason.** A `stage` is a subagent
with tools and a session — expensive, capable, and able to spawn. An `infer` is exactly
one bounded model call with no tools and no session. Templates that just need a
classification should not pay for a subagent, and the lane accounting depends on
distinguishing them.

Model resolution goes through the `model_tiers` slot map: a node declares an intent
(`reasoning`/`standard`/`fast`), config maps intents to use cases, and the existing
use-case bridge resolves the actual model. A template never names a model — that is what
keeps a template portable across a user's provider setup.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows import longrun, ownership
from personalclaw.workflows.bindings import BindingContext, BindingError, resolve
from personalclaw.workflows.models import (
    Failure,
    FailureClass,
    GateKind,
    InstanceState,
    Node,
    NodeKind,
)
from personalclaw.workflows.verify import (
    Verdict,
    check_required_artifacts,
    parse_verdict,
    run_ladder,
)

logger = logging.getLogger(__name__)

#: Max nested workflow/stage depth (WF2-R21 sibling). Mirrors the `__hook_depth` pattern
#: at hooks.py:831 / invoke_agent_provider.py:66. Today's "no recursion" contract is
#: PROMPT-level only — this is the code check that did not previously exist.
MAX_WF_DEPTH = 3
WF_DEPTH_KEY = "__wf_depth"

#: node `model_tier` → model use case. A tier is an INTENT; the use-case bridge owns the
#: mapping to a real provider, so a template stays portable across provider setups.
DEFAULT_MODEL_TIERS = {
    "reasoning": "reasoning",
    "standard": "orchestration",
    "fast": "background",
}


@dataclass
class NodeResult:
    """What a dispatcher produces. `state` is advisory — the controller applies it, so a
    dispatcher cannot flip a node terminal on its own (WF2-R10 terminal-write ownership,
    and the engine-owned-completion rule from WF2-R3)."""

    state: InstanceState = InstanceState.DONE
    output: Any = None
    failure: Failure | None = None
    degraded_reason: str = ""
    tokens: int = 0
    model: str = ""
    provider: str = ""
    cost_usd: float = 0.0
    #: Edges this node considered and did NOT take. A `branch` declines every case it
    #: did not route to; the controller marks those targets SKIPPED so a downstream join
    #: sees a terminal predecessor rather than waiting forever (WF2-R18).
    declined_edges: list[str] = field(default_factory=list)
    #: Populated for wait/gate: when the controller should look at this node again.
    wake_at: float = 0.0
    #: The fully-resolved prompt, journaled for trajectory replay (§5 ledger).
    resolved_prompt: str = ""
    #: Typed human-input ask, for gates that need an answer.
    ask: dict[str, Any] | None = None
    #: The `publish:` outcome (S47): create / version / noop / error, with its reason. A DECLARED
    #: field rather than an ad-hoc attribute — an attribute set on the instance would work at
    #: runtime and never reach the journal, so the ledger would show a published artifact with no
    #: record of the publish.
    published: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        from personalclaw.workflows.models import SUCCESS_STATES

        return self.state in SUCCESS_STATES


def _fail(cls: FailureClass, cause: str, remediation: str = "", **kw: Any) -> NodeResult:
    return NodeResult(
        state=InstanceState.FAILED,
        failure=Failure(failure_class=cls, cause_plain=cause, remediation=remediation, **kw),
    )


async def _wait_with_progress(controller: Any, timeout: float, on_progress: Any) -> Any:
    """Wait for a child run, feeding the parent's stall clock while it works.

    The heartbeat is what makes `timeout_stall` mean "silent" rather than "slow": a nested run that
    legitimately takes ten minutes is progressing, and killing it as wedged would make nesting
    unusable for exactly the long-horizon work it exists for. The interval is well under any sane
    stall window, and each tick is one function call — the cost is nothing next to a child run.
    """
    if not callable(on_progress):
        return await controller.wait_for_terminal(timeout=timeout)

    task = asyncio.ensure_future(controller.wait_for_terminal(timeout=timeout))
    while not task.done():
        on_progress()
        # `asyncio.wait` rather than a sleep-then-check: it returns as soon as the child settles, so
        # a fast child is not padded by the heartbeat interval.
        await asyncio.wait({task}, timeout=_PROGRESS_HEARTBEAT_SECS)
    return task.result()


def _failed_with(cls: FailureClass, cause: str, remediation: str, output: Any) -> NodeResult:
    """A FAILED result that still carries an output.

    `_fail` forwards its kwargs to `Failure`, which has no output field — but a failed subworkflow
    must still hand back the child run id, or the user is told a nested run failed with no way to
    find it.
    """
    return NodeResult(
        state=InstanceState.FAILED,
        output=output,
        failure=Failure(failure_class=cls, cause_plain=cause, remediation=remediation),
    )


# ── binding helpers ──────────────────────────────────────────────────────────


def resolve_config(node: Node, ctx: BindingContext) -> tuple[dict[str, Any], Failure | None]:
    """Resolve every binding in a node's config.

    A `BindingError` becomes a USER failure rather than an exception: the spec is wrong,
    the run should say so precisely, and a traceback in a run log tells a non-developer
    nothing actionable.
    """
    try:
        return resolve(dict(node.config or {}), ctx), None
    except BindingError as exc:
        return {}, Failure(
            failure_class=FailureClass.USER,
            cause_plain=f"binding failed: {exc}",
            remediation=(
                "check the referenced node id and field exist, or add a `| default(...)` "
                "pipe if the value is genuinely optional"
            ),
        )


def resolve_use_case(node: Node, tiers: dict[str, str] | None = None) -> str:
    """Map a node's declared tier to a model use case."""
    table = dict(DEFAULT_MODEL_TIERS)
    table.update({str(k): str(v) for k, v in (tiers or {}).items()})
    tier = str((node.config or {}).get("model_tier", "standard") or "standard")
    return table.get(tier, "background")


def resolve_axis_model(use_case: str) -> str:
    """The concrete ``"Provider:model_id"`` ref the engine WOULD resolve for one axis.

    Reads the head of the active-selection CHAIN — the exact model
    `one_shot_completion` resolves for this use case — so a `cross_model` judge is
    validated against the model it will ACTUALLY run on, not a guess. Returns ``""``
    when nothing is bound (which the caller treats as an undeterminable family, so a
    cross-model gate fails closed rather than certifying against an unknown).

    Injected into `dispatch_gate` as `judge_model_resolver` so a test can pin a
    candidate family with no live provider.
    """
    try:
        from personalclaw.providers.use_cases import active_model_refs

        refs = active_model_refs(use_case)
    except Exception:  # noqa: BLE001 — an unresolvable axis is an undeterminable family
        return ""
    return str(refs[0]) if refs else ""


def _judge_pretier_screen(cfg: dict[str, Any]) -> NodeResult | None:
    """Run the free rule tier on a judge gate's declared `evidence`. Returns a NodeResult to
    SHORT-CIRCUIT the model call, or None to proceed to the judge (LOOPS-EVOLUTION criterion 2).

    🔴 Gated on the gate DECLARING `evidence`, and that guard is the whole reason this is additive.
    A judge that binds no evidence (every judge shipped before this) has nothing for the mechanical
    length check to measure — screening it unconditionally would reject it as "under 20 chars —
    nothing to judge" and turn a working gate into a permanent REJECT. So: no `evidence` key → no
    screen → identical behaviour to before. A gate opts INTO the saver by binding the deliverable it
    judges into `evidence` (e.g. `evidence: "{{nodes.deliverable.output.report}}"`).

    A rejection here is a USER failure carrying the pre-tier's own `failure_class` in the output, so
    the escalation ladder can route on WHY it was rejected (empty vs stubbed vs a worker give-up)
    rather than re-deriving it — the routing distinction the ladder was built for.
    """
    if "evidence" not in cfg:
        return None
    from personalclaw.workflows.judge_pretier import run_pretier

    evidence = cfg.get("evidence")
    text = evidence if isinstance(evidence, str) else ("" if evidence is None else str(evidence))

    def _int(key: str, default: int = 0) -> int:
        try:
            return int(cfg.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    # The existence gate (artifacts/commits/changed-files > 0) only makes sense when the template
    # supplies those counts; default it OFF so a text-only judge is not rejected for producing no
    # commits. `min_chars` is the author's knob for how much output is substantial enough to judge.
    result = run_pretier(
        worker_output=text,
        artifacts=_int("evidence_artifacts"),
        commits=_int("evidence_commits"),
        changed_files=_int("evidence_changed_files"),
        min_chars=_int("min_chars", 20) or 20,
        check_existence_gate=any(
            k in cfg for k in ("evidence_artifacts", "evidence_commits", "evidence_changed_files")
        ),
    )
    if not result.rejected:
        return None
    return NodeResult(
        state=InstanceState.FAILED,
        output={
            "verdict": Verdict.REJECT.value,
            "pretier": True,
            "failure_class": result.failure_class,
            "reason": result.reason,
            "checks_run": result.checks_run,
        },
        failure=Failure(
            failure_class=FailureClass.USER,
            cause_plain=f"pre-tier rejected before the judge: {result.reason}",
            remediation=(
                "the free rule tier proved the work unfinished (empty, stubbed, a tool error, or a "
                "worker give-up); fix the producing node — no model call was spent"
            ),
            recoverable=False,
        ),
    )


#: Ceiling on `judge_samples`. Each sample is a full reasoning-tier completion, so an author typo
#: (`judge_samples: 30`) would quietly cost 30× on a gate that runs every loop iteration. Five is
#: past any real use — the shipped template asks for 3 — so hitting this bound means a mistake.
MAX_JUDGE_SAMPLES = 5


def _judge_sample_count(cfg: dict[str, Any]) -> int:
    """How many independent samples this judge gate takes. Always ≥ 1.

    Absent/invalid → 1, which is the pre-S145 behaviour: a gate that never asked for sampling must
    not start paying for it. Clamped at `MAX_JUDGE_SAMPLES` — see that constant for why a typo
    here is expensive rather than merely wrong.
    """
    raw = cfg.get("judge_samples", 1)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 1
    if count < 1:
        return 1
    return min(count, MAX_JUDGE_SAMPLES)


def _aggregate_gate_verdicts(verdicts: list[Verdict]) -> Verdict | None:
    """Median-aggregate N sampled verdicts for this gate's vocabulary.

    The RULE is `judge_contract.aggregate_samples`'; the TYPE deliberately is not. See the call
    site for why sharing one function across two different `Verdict` enums would be S130's bug.

    Ordered exactly as the contract documents it:

    1. **Any ESCALATE wins.** An escalation names a contradiction the other samples did not see, and
       a contradiction is a fact rather than an opinion — outvoting it would discard the one sample
       that noticed.
    2. **A PASS needs the majority**, strictly (`2 of 3`, not `1 of 2`). This is the whole point: a
       terminal accept on a single sample was measured to be indistinguishable from noise.
    3. **Otherwise the majority rejection stands**, preferring REJECT over RETRY when the samples
       split — a REJECT is terminal and a RETRY spins, so the safe reading of a split is the
       one that stops and asks rather than the one that loops.
    """
    if not verdicts:
        return None
    if len(verdicts) == 1:
        return verdicts[0]
    if any(v is Verdict.ESCALATE for v in verdicts):
        return Verdict.ESCALATE
    passes = sum(1 for v in verdicts if v is Verdict.PASS)
    if passes * 2 > len(verdicts):
        return Verdict.PASS
    if any(v is Verdict.REJECT for v in verdicts):
        return Verdict.REJECT
    return Verdict.RETRY


# ── dispatchers ──────────────────────────────────────────────────────────────


async def dispatch_transform(node: Node, ctx: BindingContext) -> NodeResult:
    """Pure data reshaping — zero tokens. The `expr` binding IS the transform: there is
    no expression language beyond the closed pipe set, which is what keeps a spec from
    becoming an eval surface."""
    raw = (node.config or {}).get("expr")
    try:
        value = resolve(raw, ctx)
    except BindingError as exc:
        return _fail(
            FailureClass.USER,
            f"transform binding failed: {exc}",
            "check the referenced node id and field exist",
        )
    contract = (node.config or {}).get("output_contract")
    if isinstance(contract, dict):
        problem = check_output_contract(value, contract)
        if problem:
            return _fail(
                FailureClass.USER,
                f"output contract violated: {problem}",
                "adjust the transform expression or relax the contract",
            )
    return NodeResult(state=InstanceState.DONE, output=value)


async def dispatch_infer(
    node: Node,
    ctx: BindingContext,
    *,
    tiers: dict[str, str] | None = None,
    completion: Any = None,
) -> NodeResult:
    """ONE bounded model call — no tools, no session, no spawn.

    `completion` is injected so tests can drive this without a provider; production
    passes `llm_helpers.one_shot_completion`.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    prompt = str(cfg.get("prompt", "") or "")
    if not prompt.strip():
        return _fail(
            FailureClass.USER,
            "infer node has an empty prompt after binding",
            "check the prompt template and its bindings",
        )

    use_case = resolve_use_case(node, tiers)
    fn = completion
    if fn is None:
        from personalclaw.llm_helpers import one_shot_completion

        fn = one_shot_completion

    want_json = bool(cfg.get("schema")) or str(cfg.get("output", "")) == "json"
    try:
        text = await fn(prompt, use_case=use_case, output_type=dict if want_json else None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # provider/transport/contract failures
        return NodeResult(
            state=InstanceState.FAILED,
            failure=_classify_exception(exc),
            resolved_prompt=prompt,
        )

    output: Any = text
    if want_json:
        parsed = _parse_json_loose(text)
        if parsed is None:
            return NodeResult(
                state=InstanceState.FAILED,
                failure=Failure(
                    failure_class=FailureClass.PROTOCOL,
                    cause_plain="model output was not valid JSON",
                    remediation="add an explicit schema to the prompt, or split into a "
                    "produce-then-extract pair",
                ),
                resolved_prompt=prompt,
            )
        output = parsed
    return NodeResult(
        state=InstanceState.DONE,
        output=output,
        resolved_prompt=prompt,
        tokens=_estimate_tokens(prompt, text),
    )


async def dispatch_stage(
    node: Node,
    ctx: BindingContext,
    *,
    subagents: Any = None,
    depth: int = 0,
    run_id: str = "",
    cwd: str = "",
) -> NodeResult:
    """One subagent execution, with tools and a session.

    Spawns are `silent=True` and run-scoped: completions belong in the run journal, not
    injected into whatever chat session happened to start the run. Without `silent`, a
    background workflow would interrupt an unrelated conversation with stage results.

    Depth is enforced in CODE here (`MAX_WF_DEPTH`), which is new: the existing contract
    is a sentence in a system prompt, and a prompt is not an enforcement mechanism.
    """
    if depth >= MAX_WF_DEPTH:
        return _fail(
            FailureClass.PERMISSION,
            f"workflow nesting depth cap ({MAX_WF_DEPTH}) reached — not spawning",
            "flatten the workflow, or move the nested work into a separate run",
        )
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    prompt = str(cfg.get("prompt", "") or "")
    if not prompt.strip():
        return _fail(
            FailureClass.USER,
            "stage node has an empty prompt after binding",
            "check the prompt template and its bindings",
        )

    # A run that inherited a temporary/incognito origin skips its learning nodes OUTRIGHT
    # (WORK-CONTAINERS §5.1, S50). Skipping at the engine is the primary control: letting the node
    # run and trusting the persist provider's own gate would make correctness depend on every write
    # path checking a flag, and a write path added later would leak by default. DEGRADED rather than
    # FAILED — the node was deliberately not run, which is a success with a machine-readable
    # reason.
    skip, skip_why = _restriction_skip(cfg, run_id)
    if skip:
        return NodeResult(state=InstanceState.DEGRADED, output=None, degraded_reason=skip_why)

    if subagents is None:
        return _fail(
            FailureClass.INTERNAL,
            "subagent manager unavailable",
            "the gateway did not initialize the subagent service",
        )

    info = subagents.spawn(
        task=prompt,
        # The run OWNS this session (§5.1): `workflow:<run_id>:<node_id>`. Passed as the parent key
        # so the spawn's own audit + session plumbing attributes it to the run rather than to
        # whatever chat happened to start it.
        parent_session_key=ownership.owned_key(run_id, node.id or "node"),
        agent=str(cfg.get("agent", "") or ""),
        max_turns=int(cfg.get("max_turns", 0) or 0),
        cwd=cwd,
        silent=True,
        approval_mode=str(cfg.get("approval_mode", "") or "") or None,
    )
    if info is None:
        # At capacity. Not a failure: the node stays ready and the next tick retries.
        return NodeResult(
            state=InstanceState.READY,
            degraded_reason="subagent capacity reached; will retry",
            resolved_prompt=prompt,
        )
    if getattr(info, "error", ""):
        return NodeResult(
            state=InstanceState.FAILED,
            failure=Failure(
                failure_class=FailureClass.PERMISSION,
                cause_plain=f"spawn rejected: {info.error}",
                remediation="check subagent approval settings and cwd allow-roots",
            ),
            resolved_prompt=prompt,
        )
    return NodeResult(
        state=InstanceState.RUNNING,
        output={"subagent_id": info.id},
        resolved_prompt=prompt,
    )


async def dispatch_branch(node: Node, ctx: BindingContext) -> NodeResult:
    """Evaluate the selector, then record every edge the branch did NOT take.

    Declines are recorded, not inferred (WF2-R18). The frontier turns a declined edge's
    target into SKIPPED — terminal — which is what lets a downstream join proceed. Trying
    to infer "not taken" from "routed elsewhere" would also starve any sibling whose
    `needs` merely names this branch, since routing among cases says nothing about it.

    A resolved value with no matching case and no default is a routing failure, reported
    as one: falling through silently would make a spec that never ran its real work look
    like a clean pass.
    """
    from personalclaw.workflows.tick import _select_case, edge_key

    expr = str((node.config or {}).get("on", "") or "")
    if not expr:
        return _fail(
            FailureClass.USER,
            "branch node has no `on` binding",
            "add `config.on` naming the value to route on",
        )
    selected = _select_case(node, ctx)
    if selected is None:
        return _fail(
            FailureClass.USER,
            "branch selector matched no case and the node has no default",
            "add a `default` case, or declare `enum` so validation catches this earlier",
        )
    label, case_node = selected
    declined: list[str] = []
    if node.id:
        taken = id(case_node)
        for other in list(node.cases.values()) + (
            [node.default_case] if node.default_case is not None else []
        ):
            if other is not None and id(other) != taken and other.id:
                declined.append(edge_key(node.id, other.id))
    return NodeResult(
        state=InstanceState.DONE,
        output={"case": label},
        declined_edges=sorted(set(declined)),
    )


#: Nesting ceiling (WF2 §1). Three levels is deep enough for "a workflow that calls a workflow
#: that calls a leaf", and shallow enough that a runaway recursion is caught in three frames
#: rather than after the process has spawned hundreds of runs. A workflow that references itself
#: is the realistic way to hit this, and it is always a bug.
MAX_SUBWORKFLOW_DEPTH = 3

#: How often a long wait feeds the parent's stall clock. Well under any sane `timeout_stall`, so a
#: working child can never be mistaken for a silent one.
_PROGRESS_HEARTBEAT_SECS = 0.5


async def dispatch_subworkflow(
    node: Node,
    ctx: BindingContext,
    *,
    depth: int = 0,
    run_id: str = "",
    supervisor: Any = None,
    timeout: int = 60,
    on_progress: Any = None,
) -> NodeResult:
    """Run a named workflow as a CHILD run, and wait for it (WF2-R13).

    **A real child run, not an inlined subtree.** The child gets its own run id, its own journal,
    its own state map and its own terminal-status writer. That costs a row and a directory, and it
    buys the things that matter: the child can be rewound, resumed, forked and inspected on its
    own; a crash mid-child leaves a child run to adopt rather than a half-written parent; and the
    parent's journal stays readable instead of interleaving two graphs' events.

    **Genealogy is threaded, so a nested tree is queryable.** `parent_run_id` names the immediate
    parent and `root_run_id` names the top of the tree — both are needed: the parent answers "who
    spawned this?", and the root answers "show me everything this user request did", which is the
    query a widget and the ledger actually run.

    **Depth is capped at 3 and checked BEFORE anything is created.** A workflow that references
    itself would otherwise spawn runs until the process died, each one with a row and a directory
    to clean up. Refused as a USER failure with the ref named, because the fix is in the spec.

    **Waited on, not fired and forgotten.** A subworkflow node's whole purpose is to produce an
    output the parent binds to; returning `launched` (the `run-workflow` provider's contract) would
    make `{{nodes.child.output}}` resolve to nothing. The wait is bounded by the node's timeout,
    and a timeout leaves the child RUNNING — it is a real run and killing it would discard work
    the
    parent merely stopped waiting for.
    """
    cfg = node.config or {}
    ref = str(cfg.get("ref", "") or "").strip()
    if not ref:
        return _fail(
            FailureClass.USER,
            "subworkflow node has no `ref`",
            "set `config.ref` to a workflow definition name (optionally `name@version`)",
        )
    # `name@version` — the version is parsed off and ignored for resolution, because a def
    # provider serves one current version per name. Kept in the ref so a spec can RECORD which
    # version it was written against.
    name = ref.split("@", 1)[0]

    if depth + 1 > MAX_SUBWORKFLOW_DEPTH:
        return _fail(
            FailureClass.USER,
            f"subworkflow nesting would exceed depth {MAX_SUBWORKFLOW_DEPTH} at {ref!r}",
            "flatten one level, or check whether the workflow references itself",
        )
    if supervisor is None:
        return _fail(
            FailureClass.INTERNAL,
            "no workflow supervisor is available to run a subworkflow",
            "this is an engine wiring problem — the controller must pass its supervisor",
        )

    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows import store
    from personalclaw.workflows.models import (
        TERMINAL_RUN_STATUSES,
        OriginKind,
        RunOrigin,
        RunStatus,
        WorkflowRun,
    )

    definition = None
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:
            continue
        if found is not None:
            definition = found
            break
    if definition is None:
        return _fail(
            FailureClass.USER,
            f"no workflow definition named {name!r}",
            "check the name, or list the available definitions",
        )

    spec = definition if isinstance(definition, dict) else definition.to_dict()

    # Inputs are RESOLVED against the parent's context before the child is created, so the child
    # receives values rather than bindings it has no way to interpret — its own `{{nodes.…}}`
    # namespace is a different graph's.
    raw_inputs = cfg.get("inputs") if isinstance(cfg.get("inputs"), dict) else {}
    child_inputs: dict[str, Any] = {}
    for key, value in (raw_inputs or {}).items():
        try:
            child_inputs[str(key)] = resolve(value, ctx)
        except BindingError as exc:
            return _fail(
                FailureClass.USER,
                f"subworkflow input {key!r} did not resolve: {exc}",
                "check the referenced node id and field exist",
            )

    parent = store.get(run_id) if run_id else None
    child = store.create(
        WorkflowRun(
            id="",
            workflow_name=name,
            status=RunStatus.DRAFT,
            inputs=child_inputs,
            mode="background",
            parent_run_id=run_id or None,
            # The ROOT, not the parent: "everything this user request did" is the query that
            # matters, and at depth 3 the parent alone cannot answer it.
            root_run_id=(parent.root_run_id if parent else "") or run_id or "",
            project_id=parent.project_id if parent else "",
            origin=RunOrigin(kind=OriginKind.SUBAGENT_TOOL, trigger_id=node.id),
        )
    )
    store.write_spec(child.id, spec)

    try:
        controller = await supervisor.launch(child, spec, depth=depth + 1)
    except Exception as exc:
        return _failed_with(
            FailureClass.INTERNAL,
            f"could not start subworkflow {name!r}: {exc}",
            "check the gateway log for the child run's launch failure",
            {"child_run_id": child.id},
        )

    try:
        # A nested run is the clearest case of "slow but working": the child is ticking the whole
        # time, so the parent's stall clock must not read the wait as silence. One call before the
        # wait is NOT enough — the wait itself spans the window — so the clock is fed on a
        # heartbeat
        # for as long as the child is alive.
        status = await _wait_with_progress(controller, float(timeout or 0), on_progress)
    except Exception as exc:
        return _failed_with(
            FailureClass.INTERNAL,
            f"subworkflow {name!r} failed while running: {exc}",
            "inspect the child run directly",
            {"child_run_id": child.id},
        )

    # The child's own outputs, node-id keyed — what the parent's `{{nodes.child.output.x}}` reads.
    child_outputs: dict[str, Any] = {}
    try:
        for path, inst in store.read_state(child.id).items():
            if inst.output_ref:
                value = store.read_output(child.id, path)
                if value is not None:
                    child_outputs[path.split(".")[-1].split("#")[0].split("@")[0]] = value
    except Exception:
        logger.debug("subworkflow %s: could not collect child outputs", child.id, exc_info=True)

    payload: dict[str, Any] = {
        "child_run_id": child.id,
        "workflow": name,
        "status": status.value if hasattr(status, "value") else str(status),
        "outputs": child_outputs,
    }

    if status not in TERMINAL_RUN_STATUSES:
        # needs_input or a wait timeout: the child is alive and a human (or its own deadline) will
        # move it. DEGRADED rather than FAILED — the parent stopped waiting, the child did not
        # fail.
        return NodeResult(
            state=InstanceState.DEGRADED,
            output=payload,
            degraded_reason=f"subworkflow is still {payload['status']}",
        )
    if status == RunStatus.COMPLETE:
        return NodeResult(state=InstanceState.DONE, output=payload)
    return _failed_with(
        FailureClass.USER,
        f"subworkflow {name!r} ended {payload['status']}",
        f"inspect child run {child.id} for the failing node",
        payload,
    )


async def dispatch_action(
    node: Node,
    ctx: BindingContext,
    *,
    get_provider: Any = None,
    timeout: int = 60,
    run_id: str = "",
) -> NodeResult:
    """Dispatch to an action provider — zero tokens.

    The provider's `outcome` honesty contract is preserved: `"launched"` means background
    work STARTED, not that it succeeded, so it maps to DEGRADED with a reason rather than
    a clean DONE. Reporting it as success would make a fire-and-forget action look
    verified.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    name = str(cfg.get("provider", "") or "")
    if not name:
        return _fail(
            FailureClass.USER,
            "action node has no `provider`",
            "set `config.provider` to a registered action provider",
        )

    getter = get_provider
    if getter is None:
        from personalclaw.action_providers.registry import get_action_provider

        getter = get_action_provider
    provider = getter(name)
    if provider is None:
        return _fail(
            FailureClass.USER,
            f"unknown action provider {name!r}",
            "install the app that provides it, or pick a registered provider",
        )

    from personalclaw.action_providers.base import ActionContext

    action_config = dict(cfg.get("with") or cfg.get("config") or {})
    payload = dict(cfg.get("payload") or {})
    # Run/node provenance in the payload, so a provider can attribute what it wrote without
    # the template having to restate ids it cannot know. `knowledge-persist` auto-fills
    # `source_ref` from these, and `artifact_inspect` (WV-11) needs the run id to locate the
    # run-local `artifacts/` dir a `{{nodes.x.artifact}}` ref points into — without them a
    # persisted item is unattributed and an offloaded artifact is unreachable.
    payload.setdefault("node_id", getattr(node, "id", "") or "")
    if run_id:
        payload.setdefault("run_id", run_id)
    context = ActionContext(
        event="workflow_node", context=str(cfg.get("context", "") or ""), payload=payload
    )
    try:
        result = await provider.execute(action_config, context, timeout=timeout)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return NodeResult(state=InstanceState.FAILED, failure=_classify_exception(exc))

    output: Any = _action_output(result)
    contract = (node.config or {}).get("output_contract")
    if isinstance(contract, dict) and getattr(result, "success", False):
        problem = check_output_contract(output, contract)
        if problem:
            return _fail(
                FailureClass.PROTOCOL,
                f"output contract violated: {problem}",
                "fix the provider output or relax the node's output_contract",
            )

    if not getattr(result, "success", False):
        err = getattr(result, "error", "") or getattr(result, "stderr", "") or "action failed"
        return NodeResult(
            state=InstanceState.FAILED,
            output=output,
            failure=Failure(
                failure_class=FailureClass.TRANSIENT,
                cause_plain=str(err)[:500],
                remediation=_provider_fix(result),
                recoverable=True,
            ),
        )
    if getattr(result, "outcome", "") == "launched":
        return NodeResult(
            state=InstanceState.DEGRADED,
            output=output,
            degraded_reason="action started background work; completion not verified",
        )
    if getattr(result, "outcome", "") == "skip":
        return NodeResult(state=InstanceState.NO_CHANGE, output=output)
    return NodeResult(state=InstanceState.DONE, output=output)


async def dispatch_wait(node: Node, ctx: BindingContext, *, now: float) -> NodeResult:
    """Park until a deadline. Returns WAITING with a `wake_at`.

    Outgoing edges activate at WAIT-ENTRY, not on completion (WF2-R18). This is the
    subtle half of active-edge gating: a 3-way fan-out with one fast leg and two waiting
    legs would otherwise fire its join after the fast leg alone.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    until = cfg.get("until_ts")
    duration = cfg.get("duration_secs")
    seal = cfg.get("seal")

    if isinstance(seal, dict):
        # Buffer-seal (KNOWLEDGE-SYNTHESIS §4.3): a VOLUME trigger instead of a clock. Ready
        # now when the buffer is full, otherwise a short re-check wait — the stale-flush
        # deadline is the ceiling, so a trickle still synthesizes eventually.
        #
        # A quiet week costs zero model calls this way; wall-clock cadence has it exactly
        # backwards, paying for a synthesis of nothing every interval.
        return _dispatch_seal(seal, ctx, now=now)

    if isinstance(until, (int, float)) and until > 0:
        deadline = float(until)
    elif isinstance(duration, (int, float)) and duration > 0:
        # An adaptive cadence: a cycle may propose its own next delay, clamped. Unclamped, a
        # model asking for 2s spins the loop through its whole budget in an hour and one
        # asking for a week silently stops being a watcher — both look like a working run.
        deadline = now + float(_adaptive_duration(cfg, float(duration)))
    else:
        return _fail(
            FailureClass.USER,
            "wait node has no duration_secs, until_ts or seal",
            "set one of them",
        )
    if deadline <= now:
        return NodeResult(state=InstanceState.DONE, output={"waited": True})
    # WAITING is not terminal, so a downstream join behind this leg keeps waiting rather
    # than firing on a faster sibling — the wait-entry half of WF2-R18.
    return NodeResult(state=InstanceState.WAITING, wake_at=deadline)


def _adaptive_duration(cfg: dict[str, Any], configured: float) -> int:
    """The wait's duration after an optional model-proposed override, clamped.

    `adaptive` names the binding that carries the proposal (usually
    `{{last.output.next_cycle_delay_seconds}}`); `min_secs`/`max_secs` bound it. The
    template's own `duration_secs` is the fallback, so a garbage proposal keeps the declared
    cadence rather than falling to the floor — "the model returned nonsense" must not make the
    loop faster.
    """
    proposed = cfg.get("adaptive")
    if proposed is None:
        return int(configured)
    secs, reason = longrun.clamp_delay(
        proposed,
        default=int(configured),
        minimum=_int_or(cfg.get("min_secs"), longrun.MIN_ADAPTIVE_DELAY_SECS),
        maximum=_int_or(cfg.get("max_secs"), longrun.MAX_ADAPTIVE_DELAY_SECS),
    )
    if reason:
        logger.info("adaptive wait delay adjusted: %s -> %ss", reason, secs)
    return secs


def _int_or(raw: Any, fallback: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    return int(raw)


def _dispatch_seal(seal: dict[str, Any], ctx: BindingContext, *, now: float) -> NodeResult:
    """Buffer-seal wait: ready when the buffer fills, else a bounded re-check.

    The buffer contents come from a binding (`items`), so the seal is a pure function of what
    the watcher's sibling has produced — no engine-side buffer to persist, and therefore
    nothing to lose on a restart.
    """
    raw_items = seal.get("items")
    items: list[Any] = []
    if raw_items is not None:
        try:
            resolved = resolve(raw_items, ctx) if isinstance(raw_items, str) else raw_items
        except Exception:
            resolved = []
        items = resolved if isinstance(resolved, list) else []

    buffer = longrun.BufferState(
        items=items,
        seal_threshold=_int_or(seal.get("threshold"), 20),
        seal_tokens=_int_or(seal.get("tokens"), 0),
        flush_stale_after_secs=_int_or(seal.get("flush_stale_after_secs"), 3600),
        last_flush_at=float(_int_or(seal.get("last_flush_at"), 0)),
    )
    sealed, reason = buffer.should_seal(now=now)
    if sealed:
        return NodeResult(
            state=InstanceState.DONE,
            output={"waited": False, "sealed": True, "reason": reason, "count": len(items)},
        )
    # Not yet. Re-check on a bounded cadence rather than parking until the stale deadline: the
    # buffer fills from a SIBLING, so nothing about this node's own state would wake it.
    check_every = _int_or(seal.get("check_every_secs"), 60)
    return NodeResult(state=InstanceState.WAITING, wake_at=now + max(1, check_every))


async def dispatch_gate(
    node: Node,
    ctx: BindingContext,
    *,
    now: float,
    verify: Any = None,
    completion: Any = None,
    tiers: dict[str, str] | None = None,
    mode: str = "background",
    worker_model: str = "",
    judge_model_resolver: Any = None,
) -> NodeResult:
    """A checkpoint the engine — never the worker — resolves (WF2-R3).

    An `expression` gate is decided here. `approval` parks in WAITING with a typed ask
    payload. `verify_command`/`verify_script` run a deterministic validator through the
    injected `verify` callable. A stage may REQUEST completion; only this flips it.
    """
    cfg, failure = resolve_config(node, ctx)
    if failure:
        return NodeResult(state=InstanceState.FAILED, failure=failure)
    raw = str(cfg.get("kind", "") or "")
    try:
        kind = GateKind(raw)
    except ValueError:
        return _fail(
            FailureClass.USER,
            f"unknown gate kind {raw!r}",
            "use approval|verify_command|verify_script|event|expression",
        )

    if kind == GateKind.EXPRESSION:
        from personalclaw.workflows.tick import _truthy

        expr = (node.config or {}).get("expr")
        try:
            value = resolve(expr, ctx)
        except BindingError as exc:
            return _fail(
                FailureClass.USER,
                f"gate expression failed to resolve: {exc}",
                "check the referenced nodes exist",
            )
        passed = _truthy(value)
        return NodeResult(
            state=InstanceState.DONE if passed else InstanceState.FAILED,
            output={"passed": passed, "value": value},
            failure=(
                None
                if passed
                else Failure(
                    failure_class=FailureClass.USER,
                    cause_plain="gate expression evaluated false",
                    remediation="inspect the upstream node output the gate tests",
                )
            ),
        )

    if kind in (GateKind.VERIFY_COMMAND, GateKind.VERIFY_SCRIPT):
        block = cfg.get("verify")
        if not isinstance(block, dict):
            return _fail(
                FailureClass.USER,
                f"{raw} gate has no `verify` block",
                "add `config.verify` with a command/script",
            )
        if verify is None:
            return _fail(
                FailureClass.INTERNAL,
                "no verifier wired for this gate",
                "the engine did not provide a verify callable",
            )
        try:
            outcome = await verify(block)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return NodeResult(state=InstanceState.FAILED, failure=_classify_exception(exc))
        # Tristate, matching loop/gates.py: None means "could not determine", which is
        # NOT a pass — an unrunnable verifier must never certify work.
        if outcome is True:
            return NodeResult(state=InstanceState.DONE, output={"verified": True})
        if outcome is None:
            return _fail(
                FailureClass.INTERNAL,
                "verification could not be determined (verifier did not run)",
                "check the command exists and is executable in the run workspace",
            )
        return _fail(
            FailureClass.USER,
            "verification failed",
            "fix the reported problems and re-run this node",
        )

    if kind == GateKind.LADDER:
        criteria = cfg.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            return _fail(
                FailureClass.USER,
                "ladder gate has no `criteria`",
                "declare an ordered list of criteria with rung + threshold",
            )
        if verify is None:
            return _fail(
                FailureClass.INTERNAL,
                "no verifier wired for this gate",
                "the engine did not provide a verify callable",
            )
        # Each criterion is evaluated by the injected verifier; the RULES (order, no-skip,
        # no-averaging) live in run_ladder, which stays pure and unit-testable.
        evaluated: dict[str, Any] = {}
        for crit in criteria:
            name = str((crit or {}).get("name", "") or "criterion")
            try:
                evaluated[name] = await verify(crit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return NodeResult(state=InstanceState.FAILED, failure=_classify_exception(exc))
        ladder = run_ladder(criteria, evaluated)
        if ladder.passed:
            return NodeResult(state=InstanceState.DONE, output=ladder.to_dict())
        return NodeResult(
            state=InstanceState.FAILED,
            output=ladder.to_dict(),
            failure=Failure(
                failure_class=FailureClass.USER,
                cause_plain=f"verification ladder rejected at the {ladder.stopped_at} rung",
                remediation="fix the failing criterion; a hard failure is never averaged away",
            ),
        )

    if kind == GateKind.JUDGE:
        prompt = str(cfg.get("prompt", "") or "")
        if not prompt.strip():
            return _fail(
                FailureClass.USER,
                "judge gate has no `prompt`",
                "add the rubric the judge should apply",
            )

        # 🔴 THE FREE RULE TIER, BEFORE the model call (LOOPS-EVOLUTION §judge / criterion 2).
        # The plan is explicit: "A free rule tier runs BEFORE any LLM judge call … Anything
        # rule-solvable never reaches the probabilistic model. Loop judges run every cycle — this
        # is the single biggest token saver in the plan." `judge_pretier.run_pretier` implements
        # exactly that (mechanical length, failure-pattern regex, stub markers, structural checks)
        # and shipped in session 30 with **no caller** — measured: an empty artifact, a whitespace
        # artifact and a `TODO: implement` stub all reached the judge and spent a reasoning-tier
        # completion on output there was provably nothing to judge.
        #
        # Additive by construction: a judge gate that declares no `evidence` binding gets the same
        # behaviour as before (the screen sees empty text, and empty text is only rejected when the
        # author asked for it via `min_chars`/existence — see below). So this is a clean widening of
        # the gate, not a second judging path. The evidence a judge screens is whatever the template
        # binds into `evidence` (typically `{{nodes.deliverable.output.report}}`), because the gate
        # cannot otherwise see the worker output it is judging — it only has this resolved config.
        pre = _judge_pretier_screen(cfg)
        if pre is not None:
            return pre

        fn = completion
        if fn is None:
            from personalclaw.llm_helpers import one_shot_completion

            fn = one_shot_completion
        # A judge reasons, so it resolves on the reasoning tier unless told otherwise —
        # and the closed enum is demanded explicitly, because a judge that answers in
        # prose forces the scheduler to route on parsed sentiment.
        use_case = resolve_use_case(node, tiers) if node.config.get("model_tier") else "reasoning"

        # 🔴 CROSS-MODEL JUDGE ISOLATION (WF2LOO-11). `isolation: cross_model` demands a judge on a
        # DIFFERENT model FAMILY than the worker — a same-family "independent" judge shares the
        # blind spots it exists to catch. This is the seam `judge_actors.plan_judge_session` /
        # `validate_judge_model` were built for and had NO caller (S146): before it, a template
        # asking for cross-model independence silently got fresh-session isolation only, because the
        # gate was never told the worker's model and `one_shot_completion` resolved by use-case,
        # not by model. Now the engine resolves the concrete model this judge WOULD run on,
        # validates its family against the worker's, and PINS it so the judge provably runs it.
        #
        # Fail CLOSED. An unsatisfiable isolation guarantee must never certify work — the same rule
        # the `verify_command` tristate applies above (a verifier that could not run is not a pass).
        # Silently downgrading to fresh is the exact defect this atom fixes, so it is not an option.
        judge_model = ""
        if str(cfg.get("isolation", "") or "").strip().lower() == "cross_model":
            from personalclaw.workflows.judge_actors import plan_judge_session, validate_judge_model

            spec = plan_judge_session(isolation="cross_model", worker_model=worker_model)
            # A blank worker family is ALSO unsatisfiable: `validate_judge_model` compares the
            # candidate against `avoid_family`, and an empty avoid_family would accept any
            # determinable candidate — certifying "different from the worker" without knowing the
            # worker. Not knowing what to differ from is not proof of difference, so fail closed
            # here rather than letting the empty-string comparison read as a pass.
            if not spec.avoid_family:
                return _fail(
                    FailureClass.USER,
                    "cross_model judge isolation unsatisfiable: the worker's model family could "
                    "not be determined, so a different-family judge cannot be proven",
                    "bind a model for the worker tier so its family is known, or use "
                    "isolation: fresh",
                )
            resolve_candidate = judge_model_resolver or resolve_axis_model
            candidate = str(resolve_candidate(use_case) or "")
            # `_family_of` reads the family out of a bare id OR a "Provider:model_id" ref, so the
            # active-chain ref both the worker and the candidate carry validates directly.
            ok, reason = validate_judge_model(spec, candidate)
            if not ok:
                return _fail(
                    FailureClass.USER,
                    f"cross_model judge isolation unsatisfiable: {reason}",
                    "configure a different-family model for the judge tier, or use "
                    "isolation: fresh",
                )
            judge_model = candidate

        instruction = (
            f"{prompt}\n\nRespond with EXACTLY ONE word, one of: "
            "PASS, RETRY, ESCALATE, REJECT. No other text."
        )
        # 🔴 `judge_samples` was DECLARED by a shipped template and READ BY NOTHING (S145).
        # `goal-pursuit-open-ended`'s terminal `accept` gate carries `judge_samples: 3`, and its own
        # prompt tells the model why: "three independent samples of you are being asked — a single
        # judgement on a terminal accept was measured to be indistinguishable from noise." Measured
        # against the live gate: **one** sample was taken, and a model returning PASS,REJECT,REJECT
        # ACCEPTED the run on the first word. The majority verdict never happened.
        #
        # The aggregation rule is `judge_contract.aggregate_samples`', restated here rather than
        # imported, because that function types on `judge_contract.Verdict`
        # (PASS/REJECT/REPLAN/ESCALATE/NEEDS_INPUT) while this gate speaks `verify.Verdict`
        # (PASS/RETRY/ESCALATE/REJECT). Feeding one vocabulary's values to the other's aggregator is
        # exactly the cross-vocabulary defect S130 found in the fail-mode classifier — so the
        # RULE is shared and the TYPE is not.
        samples = _judge_sample_count(cfg)
        verdicts: list[Verdict] = []
        texts: list[str] = []
        # The pin rides along ONLY when cross_model resolved one — a non-cross gate calls exactly as
        # before (byte-for-byte), so the completion seam the whole loop library already injects is
        # untouched. A cross_model gate pins the validated different-family model.
        pin = {"model": judge_model} if judge_model else {}
        for _ in range(samples):
            try:
                text = await fn(instruction, use_case=use_case, output_type=None, **pin)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return NodeResult(
                    state=InstanceState.FAILED,
                    failure=_classify_exception(exc),
                    resolved_prompt=instruction,
                )
            texts.append(str(text))
            parsed = parse_verdict(text)
            if parsed is None:
                # Unparseable is PROTOCOL, and it fails the whole gate even mid-sample: a terminal
                # accept decided from 2 of 3 samples is a quieter version of the single-sample bug
                # this session exists to fix.
                verdict = None
                break
            verdicts.append(parsed)
        else:
            verdict = _aggregate_gate_verdicts(verdicts)
        text = texts[-1] if texts else ""
        # 🔴 Tokens are summed over EVERY sample, not just the last. Measured on my own first
        # draft: a `judge_samples: 3` gate reported 22 tokens where it had really spent ~66 —
        # so the loop breaker's `max_tokens` and the run's cost cap would under-count 3×
        # exactly where sampling makes a gate most expensive. A meter that reads low on the
        # expensive path is worse than no meter, because the cap silently stops binding.
        sampled_tokens = sum(_estimate_tokens(instruction, body) for body in texts)
        if verdict is None:
            # An unparseable verdict is a PROTOCOL failure, never a silent pass: guessing
            # would make a control-flow decision out of noise.
            return NodeResult(
                state=InstanceState.FAILED,
                failure=Failure(
                    failure_class=FailureClass.PROTOCOL,
                    cause_plain=f"judge returned no recognizable verdict: {str(text)[:120]}",
                    remediation="tighten the rubric, or use a ladder gate for a "
                    "deterministic check",
                ),
                resolved_prompt=instruction,
                tokens=sampled_tokens,
            )
        state = {
            Verdict.PASS: InstanceState.DONE,
            Verdict.RETRY: InstanceState.FAILED,
            Verdict.ESCALATE: InstanceState.ESCALATED,
            Verdict.REJECT: InstanceState.FAILED,
        }[verdict]
        failure = None
        if verdict in (Verdict.RETRY, Verdict.REJECT):
            failure = Failure(
                failure_class=(
                    FailureClass.TRANSIENT if verdict == Verdict.RETRY else FailureClass.USER
                ),
                cause_plain=f"judge returned {verdict.value}",
                remediation=(
                    "the engine will retry"
                    if verdict == Verdict.RETRY
                    else "address the judge's rubric and re-run the producing node"
                ),
                recoverable=verdict == Verdict.RETRY,
            )
        # Carry the evidence chain out on the node output so the controller can emit a
        # `judge_verdict` Run Ledger event at the settle (LOOPS-EVOLUTION R3, criterion 3).
        # Additive: existing readers of `output["verdict"]` are unaffected. `judge_evidence` holds
        # the per-sample verdicts + raw texts (the proof a reader replays); `judge_status` is
        # "discard" when a sample was outvoted by the median aggregation, else "kept".
        sample_values = [v.value for v in verdicts]
        judge_status = "kept" if not verdicts or verdict.value in sample_values else "discard"
        return NodeResult(
            state=state,
            output={
                "verdict": verdict.value,
                "judge_evidence": {
                    "samples": sample_values,
                    "texts": [t[:2000] for t in texts],
                    "sample_count": samples,
                    "aggregated": verdict.value,
                },
                "judge_status": judge_status,
            },
            failure=failure,
            resolved_prompt=instruction,
            tokens=sampled_tokens,
        )

    # approval / event: park for a human or an external signal. The deadline is
    # MODE-DEPENDENT (WF2-R7): a background run parked forever on an approval nobody is
    # watching is wedged, not waiting, so background gates time out fast and surface.
    from personalclaw.workflows.human_input import gate_timeout_secs

    timeout_secs = gate_timeout_secs(cfg, mode=mode)
    wake = now + float(timeout_secs) if timeout_secs > 0 else 0.0
    return NodeResult(
        state=InstanceState.WAITING,
        wake_at=wake,
        ask=_ask_payload(node, cfg),
    )


def _ask_payload(node: Node, cfg: dict[str, Any]) -> dict[str, Any]:
    """The typed human-input ask (WF2-R7). One renderer covers every gate, which is why
    the shape is fixed by `human_input.Ask` rather than left to each template."""
    from personalclaw.workflows.human_input import Ask

    return Ask.from_dict(
        {
            "kind": cfg.get("ask_kind", "approval"),
            "prompt": str(cfg.get("prompt", "") or cfg.get("message", "") or "Approval needed"),
            "fields": cfg.get("fields") or [],
            "choices": cfg.get("choices") or [],
            "node_id": node.id,
            "unattended_suppress": bool(cfg.get("unattended_suppress", False)),
        }
    ).to_dict()


def apply_artifact_gate(node: Node, result: NodeResult, workspace: Any) -> NodeResult:
    """Refuse a node's completion until its declared `required_artifacts` exist (WF2-R3).

    Applied at the dispatch seam rather than inside each producing dispatcher, so a new
    node kind inherits the gate instead of silently skipping it. A node that CLAIMS to have
    written files but did not is the single most common way agent-declared completion lies.
    """
    patterns = (node.config or {}).get("required_artifacts")
    if not isinstance(patterns, list) or not patterns:
        return result
    if result.state not in (InstanceState.DONE, InstanceState.DEGRADED):
        return result  # already failing; the artifact gate adds nothing
    if workspace is None:
        return NodeResult(
            state=InstanceState.FAILED,
            output=result.output,
            failure=Failure(
                failure_class=FailureClass.INTERNAL,
                cause_plain="required_artifacts declared but the run has no workspace",
                remediation="the engine did not provide a workspace path for this run",
            ),
        )
    from pathlib import Path

    check = check_required_artifacts([str(p) for p in patterns], Path(workspace))
    if check.satisfied:
        payload = result.output
        # The digests ride along so the ledger can later tell whether the artifact that
        # satisfied the gate is still the one on disk.
        if isinstance(payload, dict):
            payload = {**payload, "artifacts": check.digests}
        result.output = payload
        return result
    return NodeResult(
        state=InstanceState.FAILED,
        output=result.output,
        failure=Failure(
            failure_class=FailureClass.USER,
            cause_plain=("required artifacts missing: " + ", ".join(check.missing)),
            remediation="the node reported success without producing its declared files; "
            "check where it actually wrote them",
        ),
    )


def _restriction_skip(cfg: dict[str, Any], run_id: str) -> tuple[bool, str]:
    """Whether a restricted run must skip this node, resolved from the RUN's inherited mode.

    The mode lives on the run record, not on the node — a node cannot know whether the run it
    belongs
    to was launched from an incognito chat. Best-effort: if the run cannot be read, nothing is
    skipped, because a lookup failure must not silently stop doing the work the user asked for. The
    fail-closed direction in this feature is about the memory MODE (an unknown mode reads as
    restricted), not about whether the run executes.
    """
    if not run_id:
        return False, ""
    try:
        from personalclaw.workflows import store

        # `store.get`, not `store.load` — and the mode lives in the run's `extra` dict, not a
        # column.
        # Measured: an earlier version called `store.load()` and read `run.memory_mode`; NEITHER
        # exists, so the helper would have raised on every stage and the `except` would have
        # swallowed
        # it — an enforcement control that silently never fires, which is the exact class this
        # program keeps finding. `extra` is already persisted and round-tripped, so the mode
        # needs no
        # schema change.
        run = store.get(run_id)
        raw = (getattr(run, "extra", None) or {}).get(ownership.RUN_MODE_KEY, "") if run else ""
        mode = ownership.parse_mode(raw)
    except Exception:
        logger.debug("restriction lookup failed for run %s", run_id, exc_info=True)
        return False, ""
    return ownership.skips_node(cfg, mode)


def apply_publish(node: Node, result: NodeResult, *, run_id: str = "") -> NodeResult:
    """Publish a node's output as an Artifact when it declares `publish:` (WORK-CONTAINERS §2,
    S47).

    At the dispatch seam beside the artifact gate, so a new node kind inherits publishing
    rather than
    silently dropping a declared output.

    A MALFORMED declaration FAILS the node. The alternative — treating it as "no publish" —
    would let
    a node whose author declared a deliverable report success while producing nothing, which is the
    completion-lie class the artifact gate exists to catch. A declaration is a promise about output.

    A REGISTRY failure does not fail the node. The work happened; losing the copy is worth reporting
    on the result, not worth discarding a completed stage over. The distinction is deliberate: a bad
    declaration is the author's bug (fail loudly), a registry outage is the environment's (degrade
    honestly).
    """
    from personalclaw.workflows.publish import (
        PublishAction,
        flatten_lineage,
        parse_publish,
        upsert_plan,
    )

    cfg = node.config or {}
    if "publish" not in cfg:
        return result
    spec, error = parse_publish(cfg)
    if error:
        return NodeResult(
            state=InstanceState.FAILED,
            output=result.output,
            failure=Failure(
                failure_class=FailureClass.USER,
                cause_plain=f"invalid publish declaration: {error}",
                remediation=(
                    "fix the node's `publish:` block; it declares an output nothing produced"
                ),
            ),
        )
    if spec is None or result.state not in (InstanceState.DONE, InstanceState.DEGRADED):
        return result

    content = result.output if isinstance(result.output, str) else ""
    if not content and isinstance(result.output, dict):
        content = str(result.output.get("text") or result.output.get("output") or "")
    if not content.strip():
        # Nothing to publish is NOT an error: a node whose output is structured data the caller
        # binds elsewhere has still done its job. Recording it keeps the absence visible.
        return _with_publish(result, {"action": "noop", "reason": "node output was not text"})

    try:
        from personalclaw.artifacts.registry import get_provider as _artifact_provider

        provider = _artifact_provider()
        if provider is None or provider.readonly:
            # Guarded FIRST rather than mid-flow: the earlier shape reached the writer branches with
            # `provider` still possibly None, which typechecking caught. A publish path that could
            # dereference a missing provider would turn "no artifact store configured" into a
            # traceback on a completed stage.
            return _with_publish(
                result, {"action": "noop", "reason": "no writable artifact provider"}
            )
        existing = provider.find_similar(spec.artifact)
        previous = None
        if existing is not None:
            detail = provider.get(existing.slug)
            previous = getattr(detail, "content", None) if detail else None
        plan = upsert_plan(
            spec, content, existing_content=previous, run_id=run_id, node_id=node.id or ""
        )
        # The lineage and change note ride on the artifact's own EVENT metadata. Measured: without
        # this the plan computed a full run/node lineage and the artifact landed carrying none of it
        # — provenance computed and discarded, so "which run produced this" had no answer on disk.
        event_meta = {
            "run_id": run_id,
            "node_id": node.id or "",
            "change_note": plan.change_note,
            # Flattened to scalar keys: `clean_event_metadata` bounds event metadata to scalars, so
            # the nested dict was being stringified into an unparseable Python repr.
            **flatten_lineage(plan.lineage),
        }
        if plan.action is PublishAction.CREATE:
            created = provider.create(
                name=spec.artifact,
                content=content,
                kind=spec.kind,
                source="subagent",
                description=spec.description,
                actor="workflow",
                event_metadata=event_meta,
            )
            payload = {**plan.to_dict(), "slug": getattr(created, "slug", "")}
        elif plan.action is PublishAction.VERSION and existing is not None:
            updated = provider.update(
                existing.slug,
                content=content,
                snapshot=True,
                event_type="iterated",
                actor="workflow",
                event_metadata=event_meta,
            )
            payload = {
                **plan.to_dict(),
                "slug": getattr(updated, "slug", existing.slug if existing else ""),
            }
        else:
            payload = {**plan.to_dict(), "slug": existing.slug if existing else ""}
        return _with_publish(result, payload)
    except Exception as exc:
        logger.debug("publish failed for node %s", node.id, exc_info=True)
        return _with_publish(result, {"action": "error", "reason": f"{type(exc).__name__}: {exc}"})


def _with_publish(result: NodeResult, payload: dict[str, Any]) -> NodeResult:
    """Attach the publish outcome to the node's output without disturbing it.

    A string output stays reachable at its original binding path — wrapping it in a dict would
    break
    every `{{nodes.x.output}}` downstream, so publishing a node's output would change what its
    consumers read.
    """
    result.published = payload
    if isinstance(result.output, dict):
        # Mirrored into the output too, so a downstream `{{nodes.x.output.published.slug}}` binding
        # can reach it — the typed field is for the ledger, the mirror is for the graph.
        result.output = {**result.output, "published": payload}
    return result


# ── output contract (WF2-R8) ─────────────────────────────────────────────────


def check_output_contract(value: Any, contract: dict[str, Any]) -> str:
    """A ~0.3ms mechanical check before a node is marked complete.

    Runs BEFORE any `{{nodes.x.output}}` binding resolves, so malformed output never
    silently propagates through the graph. Returns "" when the value conforms, or a
    human-readable problem.
    """
    if contract.get("must_be_json"):
        if isinstance(value, str):
            if _parse_json_loose(value) is None:
                return "expected JSON, got unparseable text"
        elif not isinstance(value, (dict, list)):
            return f"expected JSON object/array, got {type(value).__name__}"

    required = contract.get("required_keys")
    if isinstance(required, list) and required:
        target = value
        if isinstance(target, str):
            target = _parse_json_loose(target)
        if not isinstance(target, dict):
            return "required_keys declared but output is not an object"
        missing = [k for k in required if str(k) not in target]
        if missing:
            return f"missing required keys: {', '.join(str(m) for m in missing)}"

    text = value if isinstance(value, str) else None
    if text is not None:
        min_len = contract.get("min_length")
        if isinstance(min_len, int) and len(text) < min_len:
            return f"output shorter than min_length {min_len}"
        max_len = contract.get("max_length")
        if isinstance(max_len, int) and len(text) > max_len:
            return f"output longer than max_length {max_len}"
        forbidden = contract.get("forbidden_phrases")
        if isinstance(forbidden, list):
            low = text.lower()
            hit = [p for p in forbidden if str(p).lower() in low]
            if hit:
                return f"output contains forbidden phrase: {hit[0]}"
    return ""


# ── helpers ──────────────────────────────────────────────────────────────────


def _parse_json_loose(text: Any) -> Any:
    """Parse JSON, stripping markdown fencing first.

    Fenced output is the dominant real-world format failure and stripping it fixes most
    cases with ZERO retries — measurably cheaper than a retry round-trip.
    """
    import json

    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pass
    # Last resort: the outermost {...} / [...] span. Last-match-wins on self-correction.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(raw[start : end + 1])
            except (TypeError, ValueError):
                continue
    return None


def _classify_exception(exc: BaseException) -> Failure:
    """Map an exception to the typed taxonomy. Only TRANSIENT/NETWORK are retryable, so
    this classification decides whether budget gets spent on a retry."""
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if isinstance(exc, TimeoutError) or "timeout" in low or "timed out" in low:
        return Failure(
            failure_class=FailureClass.TIMEOUT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="raise timeout_total, or split the node into smaller steps",
            recoverable=True,
        )
    if any(k in low for k in ("connection", "network", "dns", "unreachable", "socket")):
        return Failure(
            failure_class=FailureClass.NETWORK,
            cause_plain=f"{name}: {text}"[:500],
            remediation="check connectivity; the engine will retry",
            recoverable=True,
        )
    if any(
        k in low for k in ("permission", "forbidden", "unauthorized", "credential", "access denied")
    ):
        return Failure(
            failure_class=FailureClass.PERMISSION,
            cause_plain=f"{name}: {text}"[:500],
            remediation="check the credential in Settings → Providers, or the tool's "
            "approval policy",
        )
    if any(k in low for k in ("rate limit", "429", "throttl", "overloaded", "capacity")):
        return Failure(
            failure_class=FailureClass.TRANSIENT,
            cause_plain=f"{name}: {text}"[:500],
            remediation="the engine will back off and retry",
            recoverable=True,
        )
    if "outputcontract" in name.lower() or "schema" in low or "json" in low:
        return Failure(
            failure_class=FailureClass.PROTOCOL,
            cause_plain=f"{name}: {text}"[:500],
            remediation="tighten the schema in the prompt, or use produce-then-extract",
        )
    return Failure(
        failure_class=FailureClass.INTERNAL,
        cause_plain=f"{name}: {text}"[:500],
        remediation="check the gateway log for the full traceback",
    )


def _action_output(result: Any) -> Any:
    """A provider's canonical output. Prefers parsed JSON stdout (the BYOI contract is
    "stdout = one JSON object"), falling back to raw text."""
    stdout = getattr(result, "stdout", "") or ""
    parsed = _parse_json_loose(stdout)
    if parsed is not None:
        return parsed
    return {
        "stdout": stdout[:8000],
        "exit_code": int(getattr(result, "exit_code", 0) or 0),
        "outcome": getattr(result, "outcome", "") or "",
    }


def _provider_fix(result: Any) -> str:
    err = getattr(result, "agent_error", None)
    fix = getattr(err, "fix", "") if err else ""
    return str(fix) if fix else "check the action's configuration and the gateway log"


def _estimate_tokens(prompt: str, response: str) -> int:
    """A ~4-chars-per-token floor when the provider reported no usage.

    Deliberately an ESTIMATE and named as one: budgets treat it as a floor so an
    unreported call still costs something against the cap, rather than being free and
    letting an unmetered provider run away.
    """
    return max(1, (len(prompt) + len(response)) // 4)


# ── dispatch table ───────────────────────────────────────────────────────────


async def dispatch(
    node: Node,
    ctx: BindingContext,
    *,
    now: float = 0.0,
    subagents: Any = None,
    depth: int = 0,
    run_id: str = "",
    cwd: str = "",
    tiers: dict[str, str] | None = None,
    completion: Any = None,
    get_provider: Any = None,
    verify: Any = None,
    timeout: int = 60,
    mode: str = "background",
    #: The run supervisor, for `subworkflow` only — it is the one dispatcher that needs to
    #: CREATE and drive another run. Injected rather than imported so a test can nest without a
    #: gateway, and so the child is driven by the same supervisor that will adopt it on restart.
    supervisor: Any = None,
    #: `() -> None` — feeds the STALL clock (WF2-R5). A dispatcher that does bounded work in
    #: observable steps calls it; the controller resets `last_progress` on each call, which is what
    #: makes `timeout_stall` mean "silent" rather than merely "slow". Without it the two timeout
    #: knobs collapse into one and a steadily-working node is killed as wedged.
    on_progress: Any = None,
    #: The concrete model the run's workers resolve to — the family a `cross_model` judge gate must
    #: AVOID (WF2LOO-11). Threaded from the controller so the gate can demand a different family;
    #: only the JUDGE branch of `dispatch_gate` reads it, so a run with no cross_model gate is
    #: unaffected.
    worker_model: str = "",
) -> NodeResult:
    """Route one node to its dispatcher.

    A container reaching here is an engine bug, not a spec problem — the frontier is
    supposed to recurse into containers and only ever hand back leaves. It is reported as
    INTERNAL so the distinction stays visible in the ledger.
    """
    clock = now or time.time()
    result = await _dispatch_inner(
        node,
        ctx,
        now=clock,
        subagents=subagents,
        depth=depth,
        run_id=run_id,
        cwd=cwd,
        tiers=tiers,
        completion=completion,
        get_provider=get_provider,
        verify=verify,
        timeout=timeout,
        mode=mode,
        supervisor=supervisor,
        on_progress=on_progress,
        worker_model=worker_model,
    )
    # One seam, so a new node kind cannot silently skip the artifact gate.
    result = apply_artifact_gate(node, result, cwd or None)
    # The SAME seam for `publish:` (S47), for the same reason: a new node kind inherits the
    # publish path instead of quietly dropping a declared output. Ordered after the gate
    # deliberately — publishing the output of a node that failed its own artifact gate would
    # store a deliverable the run does not stand behind.
    return apply_publish(node, result, run_id=run_id)


async def _dispatch_inner(
    node: Node,
    ctx: BindingContext,
    *,
    now: float = 0.0,
    subagents: Any = None,
    depth: int = 0,
    run_id: str = "",
    cwd: str = "",
    tiers: dict[str, str] | None = None,
    completion: Any = None,
    get_provider: Any = None,
    verify: Any = None,
    timeout: int = 60,
    mode: str = "background",
    supervisor: Any = None,
    on_progress: Any = None,
    worker_model: str = "",
) -> NodeResult:
    kind = node.kind
    clock = now or time.time()
    if kind == NodeKind.TRANSFORM:
        return await dispatch_transform(node, ctx)
    if kind == NodeKind.INFER:
        return await dispatch_infer(node, ctx, tiers=tiers, completion=completion)
    if kind == NodeKind.STAGE:
        return await dispatch_stage(
            node, ctx, subagents=subagents, depth=depth, run_id=run_id, cwd=cwd
        )
    if kind == NodeKind.BRANCH:
        return await dispatch_branch(node, ctx)
    if kind == NodeKind.ACTION:
        return await dispatch_action(
            node, ctx, get_provider=get_provider, timeout=timeout, run_id=run_id
        )
    if kind == NodeKind.WAIT:
        return await dispatch_wait(node, ctx, now=clock)
    if kind == NodeKind.GATE:
        return await dispatch_gate(
            node,
            ctx,
            now=clock,
            verify=verify,
            completion=completion,
            tiers=tiers,
            mode=mode,
            worker_model=worker_model,
        )
    if kind == NodeKind.SUBWORKFLOW:
        return await dispatch_subworkflow(
            node,
            ctx,
            depth=depth,
            run_id=run_id,
            supervisor=supervisor,
            timeout=timeout,
            on_progress=on_progress,
        )
    return _fail(
        FailureClass.INTERNAL,
        f"{kind.value} is a container and has no dispatcher",
        "this is an engine bug — the frontier should never return a container",
    )
