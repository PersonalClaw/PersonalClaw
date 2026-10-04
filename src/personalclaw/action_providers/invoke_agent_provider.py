"""``invoke-agent`` hook provider — spawn a child agent on a lifecycle event.

The marquee E3 action: a coder agent's ``Stop`` hook spawns a ``code-reviewer``
agent. Guarded two ways and always fire-and-forget so the lifecycle never
blocks on the child:

* **Recursion depth cap** (``_HOOK_INVOKE_MAX_DEPTH``): a spawned agent can have
  its own hooks that spawn agents. ``fire_for_ids`` injects ``__hook_depth``
  into the payload from the originating agent's depth; at the cap we refuse.
* **Approval**: the agent starts on the Allow its trigger was given — the owner allowed the
  trigger "to use the “Invoke Agent” action when it runs" — so its start does not ask again
  (`triggers.grants.allows_its_agent`). A spawn with no trigger's Allow behind it takes
  SubagentManager.spawn's normal approval gate: the hook setting that starts subagents without
  asking (``auto_approve_subagent_spawn``) starts it, else it asks (rejected if no interactive
  approver). Starting it decides nothing about its own tool calls. They approve themselves only
  when the step opts in (``approval_mode: "auto"``, saved with the owner's yes), and are otherwise
  decided as any subagent's are (`SubagentManager._standing_grant`): asked, unless the owner's
  Approval mode "Auto" or the hook setting for subagents' tool calls approves them.
* **An app's scheduled job** (a fire of an ``app:`` trigger, ``app_crons.app_of``) is none of the
  above: its agent runs at the agent tier the app holds, as the app's work, and approves none of
  its calls (``app_crons.start_job``). The step's approval, write access, files to change and
  working folder are not read for it.

How many hook-spawned agents run at once is the subagent manager's to bound: past its
concurrency cap a spawn waits in its queue. A spawn it refuses outright (low memory, an
incident, the day's budget) is this action's failure.

``action_config`` shape::

    {
        "task_template": "Review the changes in $CONTEXT",  # required
        "agent": "code-reviewer",   # optional child agent name
        "cwd": "~/Documents",       # optional working folder; the workspace when empty
        "model": "...", "max_turns": 20,  # optional
        "approval_mode": "auto"     # optional opt-in to auto-approve
    }

The working folder is where the agent works, so its file tools reach the files there. It is held to
the rule every subagent's folder is (`subagent.validate_cwd`): the workspace, or a folder the owner
listed under Settings → Agent defaults → Allowed working directories. Checked when the trigger is
saved (`dashboard.handlers.triggers._action_problem`), here before the spawn, and by the spawn. A
folder its owner has not trusted is in Preview, and its agent only reads there, whatever write
access the step asks for (`guardrails.project_trust`, through `automation_posture.fire_policy`, the
check a run-prompt fire makes too).
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
    run_identity,
)
from personalclaw.action_providers.services import (
    get_action_services,
    spawn_refusal,
    validate_spawn_cwd,
)
from personalclaw.action_providers.template import render_template

logger = logging.getLogger(__name__)

# Depth 0 = the user's top-level agent. A child spawned by a hook is depth 1, its
# child depth 2, … We refuse at the cap so coder→reviewer→… can't recurse forever.
_HOOK_INVOKE_MAX_DEPTH = 3


class InvokeAgentActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "invoke-agent"

    @property
    def display_name(self) -> str:
        return "Invoke Agent"

    @property
    def hands_config_to_a_model(self) -> bool:
        """The task template is the agent's task, so a ``{{secret:KEY}}`` in it stays a name."""
        return True

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        depth = int((ctx.payload or {}).get("__hook_depth", 0) or 0)
        if depth >= _HOOK_INVOKE_MAX_DEPTH:
            return ActionResult(
                success=False,
                error=f"invoke-agent depth cap ({_HOOK_INVOKE_MAX_DEPTH}) reached — not spawning",
            )

        task = render_template(action_config.get("task_template", ""), ctx).strip()
        if not task:
            return ActionResult(success=False, error="invoke-agent hook is missing 'task_template'")
        # What started this run (`ActionContext.fire_facts`), after the task: the file that
        # arrived, the message that came. A template that names none of the event's keys gave the
        # agent no way to know.
        if ctx.fire_facts:
            task = f"{task}\n\n{ctx.fire_facts}"
        from personalclaw import write_scope

        # The files it may change, checked again at the fire.
        scope_refused = write_scope.problem(write_scope.entries(action_config))
        if scope_refused:
            return ActionResult(success=False, error=f"invoke-agent: {scope_refused}")

        services = get_action_services()
        if services is None or services.subagents is None:
            return ActionResult(success=False, error="invoke-agent: subagent manager unavailable")

        agent = (action_config.get("agent") or "").strip()
        model = (action_config.get("model") or "").strip() or None
        try:
            max_turns = int(action_config.get("max_turns", 0) or 0)
        except (ValueError, TypeError):
            max_turns = 0
        from personalclaw.apps.app_crons import app_of, start_job
        from personalclaw.triggers.store import run_title

        # What the run is called: its trigger's name, else its task's first line.
        title = run_title(ctx.trigger_id, task)
        app = app_of(ctx.trigger_id)

        # Fire-and-forget: spawn() schedules the child and returns at once, so the lifecycle never
        # waits on it. A spawn it refuses there is this fire's failure: a launch says nothing until
        # the agent ends, so a refusal nobody reported would leave the trigger silent.
        try:
            if app:
                # An app's scheduled job: its agent at the tier the app holds now, as the app's
                # work, approving none of its calls (`app_crons.start_job`).
                info, refused = start_job(
                    app,
                    task=task,
                    agent=agent,
                    model=model,
                    max_turns=max_turns,
                    trigger_id=ctx.trigger_id,
                    title=title,
                    subagents=services.subagents,
                )
                if refused:
                    return ActionResult(success=False, error=f"invoke-agent: {refused}")
            else:
                info = self._spawn(
                    action_config,
                    ctx,
                    services.subagents,
                    task,
                    agent=agent,
                    model=model,
                    max_turns=max_turns,
                    title=title,
                )
                if isinstance(info, ActionResult):
                    return info
        except Exception as exc:  # noqa: BLE001 - a spawn that raises is this fire's failure
            logger.warning("invoke-agent: spawn failed", exc_info=True)
            return ActionResult(
                success=False, error=f"invoke-agent: the agent did not start: {exc}"
            )
        refused = spawn_refusal(info)
        if refused or info is None:  # `spawn_refusal` names why a None did not start
            return ActionResult(success=False, error=f"invoke-agent: {refused}")
        from personalclaw.subagent import agent_work_id

        # "launched", not "succeeded": the agent's outcome is not known here (T7 honest "started ≠
        # succeeded" status). The run's row names the agent, and says how it went when it ends.
        return ActionResult(
            success=True,
            exit_code=0,
            stdout=f"spawned agent for: {task[:80]}",
            outcome="launched",
            work_id=agent_work_id(info.id),
        )

    def _spawn(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        subagents: Any,
        task: str,
        *,
        agent: str,
        model: str | None,
        max_turns: int,
        title: str,
    ) -> Any:
        """Start the agent of a trigger's step (or a hook's) as its Allow says it may run, or the
        ActionResult refusing its working folder."""
        # Its working folder, checked now: the spawn refuses a folder outside the allowed ones in
        # its background task, which would read as launched.
        cwd = str(action_config.get("cwd") or "").strip()
        cwd_refused = validate_spawn_cwd(cwd)
        if cwd_refused:
            return ActionResult(success=False, error=f"invoke-agent: {cwd_refused}")
        from personalclaw.automation_posture import fire_policy

        # What its agent may do, as its Allow said it (`automation_posture.fire_policy`, the check
        # run-prompt builds its run from too): the run is built from the same mapping the Allow's
        # sentence is, and a working folder the owner has not trusted holds it to reading.
        policy = fire_policy(self.name, action_config)
        # No parent session: the agent's work is its trigger's (or its step's run's), never a
        # session a payload names, since a payload is event data or a template's own, and a chat
        # named there would lend the agent that chat's Trust and post its results into it.
        return subagents.spawn(
            task=task,
            agent=agent,
            cwd=cwd,
            max_turns=max_turns,
            model=model,
            # §4.1 creation-time write grant, read from the policy: an agent that approves its
            # own calls is read-only unless the step carries ``capability: "mutating"``.
            approval_mode=policy.approval_mode or None,
            capability_class=policy.capability_class,
            silent=False,
            # The trigger whose fire this is (`ActionContext.trigger_id`): an approval the
            # agent asks for names it and can be run again from the Inbox, and the agent says
            # how it went on the trigger's route when it ends.
            trigger_id=ctx.trigger_id,
            title=title,
            may_read=ctx.fire_files,
            may_change=policy.may_change,
            held_back=policy.held_back,
            # The project the step's run belongs to (from the run's record): the agent's work is
            # the project's. "" for a trigger's fire, which runs in no project.
            project_id=ctx.project_id,
            # The run whose step this is, from its dispatch ("" for a trigger's fire): a workflow
            # the agent starts is held to what that run may start (`automation_version.bound_for`).
            workflow_run=run_identity(ctx, "run_id"),
        )


def create_provider(config: dict[str, Any] | None = None) -> "InvokeAgentActionProvider":
    return InvokeAgentActionProvider()
