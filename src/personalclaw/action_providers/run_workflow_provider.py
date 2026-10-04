"""``run-workflow`` action provider — start a v2 workflow run from a trigger.

Deleted with the old workflow feature (WORKFLOWS-V2 Phase 1) and re-registered here
against the v2 engine. It is re-added to ``ALLOWED_HOOK_PROVIDERS`` **in the same
commit**: a provider registered in one set but not the other is exactly the mismatch
that lets a trigger validate, save, and then fail at fire time with nothing actionable.

``action_config`` shape::

    {
        "workflow": "triage-inbox",     # required: a saved def name
        "inputs": {"since": "1h"},      # optional: run inputs
        "mode": "background",           # background (default) | blocking
        "project_id": "...",            # optional project binding (an automation's own)
        "idempotency_key": "..."        # optional caller dedupe key
    }

**A workflow step's run is in the step's own project.** An automation's action names the project
its run belongs to, as the owner set it up and allowed it. A workflow step's arguments may not: a
template can be written or edited by a model, and a run in another project reads that project's
secrets. So a step starts its run in its own run's project (``ActionContext.project_id``, which
its dispatch states), and a step that names one is refused before anything is created, in the
words its template is refused with when it is saved (``workflows.step_arguments``).

**A workflow step's run is its run's work.** It records the run it was started from
(``parent_run_id``, the tree's ``root_run_id``, and the step, ``spawned_by_node_id``) and keeps what
that run keeps (``ownership.inherited_extra``), as a subworkflow node's child does: a run an
Incognito or Temporary chat started starts only a run that keeps nothing as the chat does, on the
chat's model, and a Temporary chat's ends with it (``workflows.temporary_runs``). A step whose run
cannot be read starts nothing, since what that run keeps cannot be said.

**`outcome: "launched"`, not success.** A background run has only STARTED when this
returns; its real outcome lands in the run's own ledger. Reporting it as plain success
would make an unverified run look verified — the honesty contract `ActionResult.outcome`
exists for. The result names the run (`work_id`, launched or queued), so the trigger's own run
says how it went when it ends and counts toward its pause (`triggers.settle`).

**It runs the version of the workflow its automation was allowed** (`workflows.automation_version`).
The owner's Allow of the automation records the version of the workflow it was given for
(`triggers.grants`), and a fire runs that version, or a newer one she saved herself in the
workflow's editor, whatever else saved one since: an agent's tool, a sync, an import or an app. A
version it may run that is no longer kept is refused, in words, and nothing starts. The run carries
the versions of the workflows its steps may start, allowed with it, and a step of such a run that
starts a workflow here starts the version allowed with it, by the same rule. A run no automation
its owner allowed started — the Run button's, an agent's, a step of one of those — runs the
workflow as it is.

**Its inputs are checked where the trigger is SAVED, and again when it fires.** `config_problem`
asks the question `service.start_run` asks before it spends anything — the workflow exists, every
required input is given and every input is its declared type (`contracts.start_problem`) — so a
trigger that could never start its workflow is refused in the form that authored it rather than
failing at every fire. A fire checks again, because the workflow can change after the trigger was
saved, and starts the run with the inputs coerced and the declared defaults applied, as the Run
button does.

**A secret its inputs name is handed to the run as the reference** (`hands_config_to_a_run`). The
run's record keeps its inputs, its ledger opens with them, the run list serves them and a preview
reports them: filled in by the dispatch, every one of those held the secret's value, and a model
step reading the input was sent it. The run fills the reference where one of its steps uses the
input (`workflows.input_secrets`).

**`on_overlap` is honoured here**, not left to the caller. A per-minute trigger against
a ten-minute workflow must not stack runs, and the def's declared policy (`skip` by
default) is the single place that decision belongs. The decision itself lives in
`workflows.overlap.decide` — exhaustive over the policy enum, with a raising tail —
because `queue` previously matched no branch here and fell through to "start now", which
is the opposite of what its name promises.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
    is_workflow_step,
)

logger = logging.getLogger(__name__)


class RunWorkflowActionProvider(ActionProvider):
    """Start a workflow run. Never drives it — execution is engine-owned."""

    @property
    def name(self) -> str:
        return "run-workflow"

    @property
    def display_name(self) -> str:
        return "Run workflow"

    @property
    def supports_dry_run(self) -> bool:
        """A workflow run is spawn-based, so an observe-mode preview is meaningful."""
        return True

    @property
    def hands_config_to_a_run(self) -> bool:
        """Its ``inputs`` are the run's: a ``{{secret:…}}`` in them is handed on as the reference,
        and the run fills it where a step uses it (``workflows.input_secrets``)."""
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        import json
        import time

        started = time.monotonic()
        name = str((action_config or {}).get("workflow", "") or "").strip()
        if not name:
            return ActionResult(
                success=False,
                error="run-workflow requires a `workflow` name",
                stderr="no workflow named in the action config",
            )
        # The project the run is in: a workflow step's own, as its dispatch says, else the one an
        # automation's action names. Asked before anything is looked up or created.
        project = str((action_config or {}).get("project_id", "") or "")
        if is_workflow_step(ctx):
            from personalclaw.workflows.step_arguments import dispatch_refusal

            if refused := dispatch_refusal(self.name, action_config or {}):
                return ActionResult(
                    success=False,
                    error=refused,
                    stderr="a workflow step names the project of the run it starts",
                    failure_class="user",
                )
            project = str(getattr(ctx, "project_id", "") or "")
            parent = _run_of_step(ctx)
            if parent is None:
                return ActionResult(
                    success=False,
                    error=(
                        "run-workflow: the run this step belongs to could not be read, so the run "
                        "it would start could not keep what that run keeps; nothing was started"
                    ),
                    stderr="the step's own run could not be read",
                )
        else:
            parent = None

        try:
            from personalclaw.workflows import overlap as overlap_mod
            from personalclaw.workflows import ownership, store
            from personalclaw.workflows.effects import START_DEDUPE
            from personalclaw.workflows.models import (
                OriginKind,
                OverlapPolicy,
                RunOrigin,
                RunStatus,
                WorkflowRun,
                run_work_id,
            )
        except Exception as exc:  # the engine should always import; be explicit if not
            return ActionResult(
                success=False,
                error=f"workflow engine unavailable: {exc}",
                stderr="could not import the v2 workflow engine",
            )

        # Caller dedupe: a retried tool/trigger dispatch with the same key gets
        # the EXISTING run back rather than minting a second one doing the same work.
        caller_key = str((action_config or {}).get("idempotency_key", "") or "")
        if caller_key:
            existing = START_DEDUPE.lookup(caller_key)
            if existing:
                return ActionResult(
                    success=True,
                    outcome="launched",
                    stdout=json.dumps({"run_id": existing, "deduped": True}),
                    duration_ms=int((time.monotonic() - started) * 1000),
                    summary=(
                        f"This request already started “{name}” as run {existing}, so no second "
                        "run began."
                    ),
                )

        from personalclaw.triggers.grants import allowed_for_fire
        from personalclaw.workflows import automation_version

        now = await automation_version.current(name)
        if now is None:
            return ActionResult(
                success=False,
                error=f"unknown workflow {name!r}",
                stderr="no workflow definition by that name is registered",
            )
        if not now.spec.get("root"):
            return ActionResult(
                success=False,
                error=f"workflow {name!r} has no usable spec",
                stderr="the definition carries no root node",
            )
        # The version this fire may run: the one its automation was allowed, or a newer one its
        # owner saved herself; for a step of a run such an automation started, the version allowed
        # with it. Read as things are now, and refused in words when it cannot be run, rather than
        # run as some other version. The run carries on the versions its own steps may start.
        bound = None
        if is_workflow_step(ctx):
            parent_id = str((getattr(ctx, "payload", None) or {}).get("run_id") or "")
            bound = automation_version.bound_in(store.get(parent_id) if parent_id else None)
        carried: dict[str, Any] | None = None
        fire: automation_version.Runs | None
        if bound is not None:
            refused = ""
            fire = automation_version.step_runs(bound, now)
            if fire.spec is not None:
                carried = automation_version.step_versions(bound, name, fire)
        else:
            allowed, refused = allowed_for_fire(str(getattr(ctx, "trigger_id", "") or ""), now)
            fire = automation_version.runs(allowed, now) if not refused else None
            if allowed is not None and fire is not None and fire.spec is not None:
                carried = automation_version.step_versions(
                    automation_version.bound_of(allowed), name, fire
                )
        if fire is None or fire.spec is None:
            why = refused or (fire.problem if fire is not None else "")
            return ActionResult(
                success=False,
                error=f"workflow {name!r}: {why}",
                stderr="the version of the workflow this automation may run cannot be run",
                failure_class="user",
            )
        spec = fire.spec
        from personalclaw.workflows.contracts import start_problem
        from personalclaw.workflows.service import with_declared_defaults

        provided = (action_config or {}).get("inputs") or {}
        if not isinstance(provided, dict):
            provided = {}
        coerced, problem = start_problem(spec, provided, name=name)
        if problem:
            # The workflow changed after this trigger was saved: the same sentence the form gave,
            # at the fire, and a USER failure — a retry sends the same inputs.
            return ActionResult(
                success=False,
                error=f"workflow {name!r}: {problem}",
                stderr="the trigger's inputs no longer start this workflow; edit the trigger",
                failure_class="user",
            )
        run_inputs = with_declared_defaults(spec, coerced)
        # A secret its inputs name is handed on as the reference, never the value: the run keeps
        # its inputs on its record and fills the reference where a step uses it. The run reads
        # only the ones its dispatch names as the author's (`ActionContext.secret_references`),
        # checked here against the secrets that run reads; any other text that reads as one is
        # text the run was given.
        from personalclaw.triggers.secrets import UnresolvedSecret, check
        from personalclaw.workflows import input_secrets

        kept = set(getattr(ctx, "secret_references", ()) or ())
        handed = sorted(set(input_secrets.references_in(coerced)) & kept)
        try:
            check(handed, project_id=project)
        except UnresolvedSecret as missing:
            return ActionResult(
                success=False,
                error=f"workflow {name!r}: {missing}",
                stderr="a secret its inputs name is not stored where the run reads it",
                failure_class="user",
            )

        # on_overlap — the policy of the version that runs, applied before a second run exists. The
        # branch lives in `overlap.decide`, exhaustive over the enum with a raising tail.
        overlap = _overlap_of(spec, OverlapPolicy)
        active = [r for r in store.active_runs() if r.workflow_name == name]
        queued = overlap_mod.queued_runs(name)
        action = overlap_mod.decide(overlap, active=len(active), queued=len(queued))
        Act = overlap_mod.OverlapAction

        if action == Act.SKIP:
            return ActionResult(
                success=True,
                outcome="skip",
                stdout=json.dumps(
                    {"skipped": True, "reason": "already running", "run_id": active[0].id}
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
                summary=(
                    f"Did not start “{name}”: run {active[0].id} is still going, and this "
                    "workflow skips a start while one is."
                ),
            )

        if (action_config or {}).get("dry_run"):
            # Honest preview: nothing is created — checked BEFORE the queue path, because a
            # preview that persisted a queued run would be a write. It names the DECIDED
            # action, so a dry run against a busy def says "would queue", not "would start".
            # The engine's own preflight is what would validate inputs, and claiming more
            # than that here would be a lie.
            return ActionResult(
                success=True,
                outcome="skip",
                stdout=json.dumps(
                    {
                        "dry_run": True,
                        "would": action.value,
                        "would_start": name,
                        "version": fire.version,
                        "inputs": run_inputs,
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        if action == Act.DROP:
            # The cap refused this start. Loud in BOTH places: a truncation that reported
            # "queued" would be the same lie as the concurrent start this change replaced.
            logger.warning(
                "run-workflow: dropped a queued start for %s — the queue is already %d deep "
                "(max %d); the pending run is %s",
                name,
                len(queued),
                overlap_mod.MAX_QUEUE_DEPTH,
                queued[0].id,
            )
            return ActionResult(
                success=True,
                outcome="skip",
                stdout=json.dumps(
                    {
                        "dropped": True,
                        "reason": "queue_full",
                        "queue_depth": len(queued),
                        "max_queue_depth": overlap_mod.MAX_QUEUE_DEPTH,
                        "queued_run_id": queued[0].id,
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
                summary=(
                    f"Did not start “{name}”: its queue is full, and run {queued[0].id} is "
                    "already waiting to start."
                ),
            )

        if action not in (Act.QUEUE, Act.START, Act.CANCEL_THEN_START):
            # Refuse before anything is created. The dangerous default at this call site is
            # "fall through and launch" — which is exactly what `queue` used to do.
            raise AssertionError(f"run-workflow has no branch for OverlapAction.{action.name}")

        from personalclaw.triggers.chain import CHAIN_EXTRA_KEY, carried_chain

        extra = overlap_mod.queued_extra() if action == Act.QUEUE else {}
        # A chained fire's chain rides the run it starts, so the run's end continues it
        # (`triggers.chain`): a loop through a workflow run is still refused as a loop.
        chain_state = carried_chain(getattr(ctx, "payload", None))
        if chain_state:
            extra = {**extra, CHAIN_EXTRA_KEY: chain_state}
        # Which inputs the run was handed a reference in, so it fills them where they are used.
        extra = input_secrets.stamp(extra, coerced, handed)
        if carried is not None:
            extra = automation_version.stamp(extra, carried)
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name=name,
                status=RunStatus.DRAFT,
                # The version it executes, which is the one its automation may run: a run is
                # traced to the spec it read, an older version included.
                spec_version=fire.version,
                inputs=run_inputs,
                mode=str((action_config or {}).get("mode", "background") or "background"),
                project_id=project,
                # The trigger whose fire this is, so the run says how it went on the trigger's
                # route when it ends (`run_finish.report_to_its_trigger`). This read the event's
                # CONTEXT text, which is a file path or a message and never the trigger's id.
                origin=RunOrigin(kind=OriginKind.HOOK, trigger_id=ctx.trigger_id),
                # A step's run is its run's work: one tree, the step that started it, and what
                # the run keeps (a Temporary or Incognito origin's mode, the model its work stays
                # on, the app whose work it is).
                parent_run_id=parent.id if parent is not None else None,
                root_run_id=(parent.root_run_id or parent.id) if parent is not None else "",
                spawned_by_node_id=_node_of_step(ctx) if parent is not None else None,
                # The queued marker goes in the SAME insert as the row — marking after
                # `create` would leave a window in which the row is an ordinary DRAFT.
                extra={**ownership.inherited_extra(parent), **extra} if parent else extra,
            )
        )
        store.write_spec(run.id, spec)
        if caller_key:
            START_DEDUPE.remember(caller_key, run.id)

        if action == Act.QUEUE:
            # Persisted, not launched, and NAMED. The drain (`overlap.drain`, called from
            # the single terminal writer and from the watchdog poll) starts it when the
            # prior ends. `outcome="queued"` and not "skip": a durable run record exists,
            # so reporting it as a no-op skip would under-report real state; and not
            # "launched": nothing is running.
            return ActionResult(
                success=True,
                outcome="queued",
                stdout=json.dumps(
                    {
                        "run_id": run.id,
                        "workflow": name,
                        "queued": True,
                        "started": False,
                        "behind": [r.id for r in active],
                    }
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
                summary=(
                    f"Queued “{name}” as run {run.id}; it starts when {_ahead(active)} "
                    f"{'ends' if len(active) == 1 else 'end'}."
                ),
                # The run, so the fire's row says how it went once it has run (`triggers.settle`).
                work_id=run_work_id(run.id),
            )

        if action == Act.CANCEL_THEN_START:
            for prior in active:
                store.request_cancel(prior.id)

        launched = await _launch(run, spec)
        if not launched:
            return ActionResult(
                success=False,
                error="no workflow supervisor available to start the run",
                stderr=f"run {run.id} was created but not started",
                stdout=json.dumps({"run_id": run.id, "started": False}),
            )

        # The row's line says it STARTED — the same claim `launched` makes, and no stronger.
        said = f"Started “{name}” as run {run.id}"
        if action == Act.CANCEL_THEN_START and active:
            said += f", after asking {_ahead(active)} to stop"
        return ActionResult(
            success=True,
            # "launched", never plain success: the run has STARTED, and its real outcome
            # lands in its own ledger. Anything stronger would report unverified work as
            # verified.
            outcome="launched",
            stdout=json.dumps({"run_id": run.id, "workflow": name, "started": True}),
            duration_ms=int((time.monotonic() - started) * 1000),
            summary=f"{said}.",
            # The run, so the fire's row says how it went when it ends (`triggers.settle`).
            work_id=run_work_id(run.id),
        )


def _run_of_step(ctx: Any) -> Any:
    """The run whose step *ctx* dispatches, or ``None`` when it cannot be read. Named by the
    dispatch (``payload["run_id"]``, which the step's own payload cannot set)."""
    from personalclaw.workflows import store

    run_id = str((getattr(ctx, "payload", None) or {}).get("run_id", "") or "")
    try:
        return store.get(run_id) if run_id else None
    except Exception:  # noqa: BLE001 - a run that cannot be read is answered as not read
        logger.debug("run-workflow: the step's run %s could not be read", run_id, exc_info=True)
        return None


def _node_of_step(ctx: Any) -> str | None:
    """The step *ctx* dispatches, as its dispatch names it (``payload["node_id"]``)."""
    return str((getattr(ctx, "payload", None) or {}).get("node_id", "") or "") or None


def _ahead(active: list[Any]) -> str:
    """The run a start waits behind or displaces, for its history row's sentence."""
    return f"run {active[0].id}" if len(active) == 1 else "the runs in progress"


async def config_problem(action_config: dict[str, Any] | None) -> str:
    """Why this action config could not start its workflow, or "" when it could.

    Asked when a trigger is saved (`dashboard/handlers/triggers`). The form's own comment said the
    run-workflow provider was gone while the registry still registered it, so the action saved
    with an empty form and failed at fire time with "run-workflow requires a `workflow` name".
    """
    config = action_config or {}
    name = str(config.get("workflow", "") or "").strip()
    if not name:
        return "choose the workflow this trigger runs"
    provided = config.get("inputs") or {}
    if not isinstance(provided, dict):
        return "the workflow's inputs must be an object of input names to values"
    from personalclaw.workflows import automation_version
    from personalclaw.workflows.contracts import start_problem

    now = await automation_version.current(name)
    if now is None:
        return f"there is no workflow named {name!r}"
    if not now.spec.get("root"):
        return f"workflow {name!r} has no usable spec"
    _coerced, problem = start_problem(now.spec, provided, name=name)
    return f"workflow {name!r}: {problem}" if problem else ""


def _overlap_of(spec: dict[str, Any], policy_cls: Any) -> Any:
    """The overlap policy *spec* declares, ``skip`` when it declares none it knows."""
    try:
        return policy_cls(str(spec.get("on_overlap", "skip") or "skip"))
    except ValueError:
        return policy_cls.SKIP


async def _launch(run: Any, spec: dict[str, Any]) -> bool:
    """Hand the run to the watchdog, which owns controller registration.

    Going through the supervisor rather than constructing a controller here is what keeps
    ONE writer per run: a provider-owned controller would be invisible to adoption and
    cancel, and a restart would start a second one alongside it.
    """
    try:
        from personalclaw.action_providers.services import get_action_services

        services = get_action_services()
    except Exception:
        logger.debug("action services unavailable for run-workflow", exc_info=True)
        return False
    supervisor = getattr(services, "workflows", None) if services else None
    if supervisor is None:
        return False
    try:
        await supervisor.launch(run, spec)
    except Exception:
        logger.exception("run-workflow: supervisor refused to launch run %s", run.id)
        return False
    return True


def create_provider(config: dict[str, Any] | None = None) -> "RunWorkflowActionProvider":
    """The factory the bundled `run-workflow-action` manifest names, like every native action's."""
    return RunWorkflowActionProvider()
