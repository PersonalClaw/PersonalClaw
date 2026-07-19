"""``run-workflow`` action provider — run a *saved Workflow* on a trigger's cadence.

The Workflow sibling of ``run-prompt`` (T1). Where a Workflow normally *surfaces*
passively (the per-turn injection picks it when the message matches its SOP), a
``run-workflow`` action **invokes** it — the workflow's steps become the turn's
active instruction, driven end-to-end as an unattended run. This is the point of
binding a Workflow to a cadence: "every weekday at 9am, *run* the standup SOP",
not "seed a turn that might surface it."

Reuses ``run-prompt``'s spawn path (auto-approve + T5 unattended), so it inherits
the recursion/concurrency caps for free. The only new work is resolving the saved
Workflow (by id or name) and rendering its expanded steps as an instruction.

``action_config`` shape::

    {
        "workflow_id": "release-checklist",  # required: workflow id OR name
        "cwd": "/path/to/repo",                # optional working dir for the run
        "agent": "PersonalClaw",               # optional child agent name
        "model": "...",                         # optional model override
        "max_turns": 40,                        # optional
        "session": "cron:release"              # optional pinned session (continuity)
    }
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.action_providers.services import get_action_services, validate_spawn_cwd
from personalclaw.autonomous_framing import with_autonomous_framing

logger = logging.getLogger(__name__)


async def resolve_workflow(workflow_id: str):
    """Resolve a saved Workflow by id, falling back to an exact name match.

    Returns the ``Workflow`` or ``None``. Name fallback so a trigger config can
    reference the human-facing workflow name (what the author sees) and not only
    the opaque id.
    """
    from personalclaw.workflows.registry import get_workflow, list_all_workflows

    wf = await get_workflow(workflow_id)
    if wf is not None:
        return wf
    wfs, _ = await list_all_workflows(limit=1000, offset=0)
    for candidate in wfs:
        if candidate.name == workflow_id:
            return candidate
    return None


async def render_workflow_instruction(workflow) -> str:
    """Render ``workflow``'s expanded steps as an active turn instruction.

    Distinct from ``surfacing.render_injection`` (which frames the SOP as a
    *preferred* guideline to follow "unless the user directs otherwise"): here the
    workflow IS the task, so the framing directs the agent to execute it. Ref
    steps are flattened via the same composition expansion.
    """
    from personalclaw.workflows.composition import expand_steps
    from personalclaw.workflows.registry import list_all_workflows

    wfs, _ = await list_all_workflows(limit=1000, offset=0)
    by_id = {w.id: w for w in wfs}
    by_id.setdefault(workflow.id, workflow)

    header = workflow.description or workflow.name
    lines = [
        f"Run the workflow **{workflow.name}**: {header}".rstrip(": ").rstrip(),
        "",
        "Execute these steps in order, completing each before moving to the next:",
    ]
    for i, step in enumerate(expand_steps(workflow, by_id), 1):
        suffix = f"  _(from: {step.source_workflow})_" if step.depth > 0 else ""
        lines.append(f"{i}. {step.title}{suffix}")
        if step.instruction:
            lines.append(f"   {step.instruction}")
    return "\n".join(lines)


class RunWorkflowActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "run-workflow"

    @property
    def display_name(self) -> str:
        return "Run Workflow"

    @property
    def supports_dry_run(self) -> bool:
        # The spawned turn runs with observe-mode tools (subagent dry_run=True):
        # write-capable tools preview instead of executing.
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        workflow_id = str(action_config.get("workflow_id") or "").strip()
        if not workflow_id:
            return ActionResult(
                success=False, error="run-workflow is missing 'workflow_id'"
            )

        try:
            workflow = await resolve_workflow(workflow_id)
        except Exception as exc:  # noqa: BLE001 - registry failure → error result
            logger.warning("run-workflow: resolution failed", exc_info=True)
            return ActionResult(success=False, error=f"run-workflow: {exc}")
        if workflow is None:
            return ActionResult(
                success=False, error=f"run-workflow: no saved workflow {workflow_id!r}"
            )
        if not workflow.steps:
            return ActionResult(
                success=False,
                error=f"run-workflow: workflow {workflow_id!r} has no steps",
            )

        instruction = await render_workflow_instruction(workflow)
        task = with_autonomous_framing(instruction)

        services = get_action_services()
        if services is None or services.subagents is None:
            return ActionResult(
                success=False, error="run-workflow: subagent manager unavailable"
            )

        agent = (action_config.get("agent") or "").strip()
        model = (action_config.get("model") or "").strip() or None
        cwd = (action_config.get("cwd") or "").strip()
        # Honest cwd pre-check (see run-prompt): a refused cwd would otherwise show
        # as a false "launched" because the spawn validates it asynchronously.
        cwd_err = validate_spawn_cwd(cwd)
        if cwd_err:
            return ActionResult(success=False, error=f"run-workflow: {cwd_err}")
        try:
            max_turns = int(action_config.get("max_turns", 0) or 0)
        except (ValueError, TypeError):
            max_turns = 0
        parent_key = str(action_config.get("session") or "").strip() or str(
            (ctx.payload or {}).get("session_key", "") or ""
        )

        dry_run = bool(action_config.get("dry_run", False))

        async def _spawn() -> None:
            try:
                services.subagents.spawn(  # type: ignore[union-attr]
                    task=task,
                    parent_session_key=parent_key,
                    agent=agent,
                    max_turns=max_turns,
                    model=model,
                    cwd=cwd,
                    approval_mode="auto",
                    silent=False,
                    dry_run=dry_run,
                )
            except Exception:
                logger.warning("run-workflow: spawn failed", exc_info=True)

        services.spawn_background(_spawn())
        # "launched", not "succeeded": the background workflow turn's real outcome
        # is recorded by the spawned run itself (T7 honesty).
        return ActionResult(
            success=True, exit_code=0, stdout=f"launched workflow {workflow.name!r}",
            outcome="launched",
        )


def create_provider(config: dict[str, Any] | None = None) -> "RunWorkflowActionProvider":
    return RunWorkflowActionProvider()
