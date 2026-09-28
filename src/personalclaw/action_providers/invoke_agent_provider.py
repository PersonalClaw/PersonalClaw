"""``invoke-agent`` hook provider — spawn a child agent on a lifecycle event.

The marquee E3 action: a coder agent's ``Stop`` hook spawns a ``code-reviewer``
agent. Guarded two ways and always fire-and-forget so the lifecycle never
blocks on the child:

* **Recursion depth cap** (``_HOOK_INVOKE_MAX_DEPTH``): a spawned agent can have
  its own hooks that spawn agents. ``fire_for_ids`` injects ``__hook_depth``
  into the payload from the originating agent's depth; at the cap we refuse.
* **Approval**: spawn is requested with ``approval_mode="auto"`` only when the
  hook opts in (``approval_mode: "auto"``) or the global
  ``auto_approve_subagent_spawn`` is set; otherwise SubagentManager.spawn
  applies its normal approval gate (rejected if no interactive approver).

How many hook-spawned agents run at once is the subagent manager's to bound: past its
concurrency cap a spawn waits in its queue. A spawn it refuses outright (low memory, an
incident, the day's budget) is this action's failure.

``action_config`` shape::

    {
        "task_template": "Review the changes in $CONTEXT",  # required
        "agent": "code-reviewer",   # optional child agent name
        "model": "...", "max_turns": 20,  # optional
        "approval_mode": "auto"     # optional opt-in to auto-approve
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
from personalclaw.action_providers.services import get_action_services, spawn_refusal
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

        services = get_action_services()
        if services is None or services.subagents is None:
            return ActionResult(success=False, error="invoke-agent: subagent manager unavailable")

        agent = (action_config.get("agent") or "").strip()
        model = (action_config.get("model") or "").strip() or None
        try:
            max_turns = int(action_config.get("max_turns", 0) or 0)
        except (ValueError, TypeError):
            max_turns = 0
        # Approval: opt-in per hook, else fall back to the global auto-approve.
        approval_mode = (action_config.get("approval_mode") or "").strip() or None
        if approval_mode is None:
            try:
                from personalclaw.config.loader import AppConfig
                from personalclaw.hooks import HooksConfig

                if HooksConfig.from_dict(AppConfig.load().hooks).auto_approve_subagent_spawn:
                    approval_mode = "auto"
            except Exception:
                logger.debug("invoke-agent: auto-approve config lookup failed", exc_info=True)

        parent_key = str((ctx.payload or {}).get("session_key", "") or "")
        # §4.1 creation-time write grant. An auto-fired fire (``approval_mode="auto"``) defaults the
        # spawn to the read-only RESEARCH class unless the hook declares an explicit
        # ``capability: "mutating"`` grant — write on an unattended agent is created deliberately,
        # never acquired by default.
        capability_class = str(action_config.get("capability") or "").strip().lower() or None
        from personalclaw.triggers.store import run_title

        title = run_title(ctx.trigger_id, task)

        # Fire-and-forget: spawn() schedules the child and returns at once, so the lifecycle never
        # waits on it. A spawn it refuses there is this fire's failure: a launch says nothing until
        # the agent ends, so a refusal nobody reported would leave the trigger silent.
        try:
            info = services.subagents.spawn(
                task=task,
                parent_session_key=parent_key,
                agent=agent,
                max_turns=max_turns,
                model=model,
                approval_mode=approval_mode,
                capability_class=capability_class,
                silent=False,
                # The trigger whose fire this is (`ActionContext.trigger_id`): an approval the
                # agent asks for names it and can be run again from the Inbox, and the agent says
                # how it went on the trigger's route when it ends.
                trigger_id=ctx.trigger_id,
                # What the run is called: its trigger's name, else its task's first line.
                title=title,
            )
        except Exception as exc:  # noqa: BLE001 - a spawn that raises is this fire's failure
            logger.warning("invoke-agent: spawn failed", exc_info=True)
            return ActionResult(
                success=False, error=f"invoke-agent: the agent did not start: {exc}"
            )
        refused = spawn_refusal(info)
        if refused:
            return ActionResult(success=False, error=f"invoke-agent: {refused}")
        # "launched", not "succeeded": the spawned agent's real outcome is recorded
        # by its own run, not known here (T7 honest "started ≠ succeeded" status).
        return ActionResult(
            success=True,
            exit_code=0,
            stdout=f"spawned agent for: {task[:80]}",
            outcome="launched",
        )


def create_provider(config: dict[str, Any] | None = None) -> "InvokeAgentActionProvider":
    return InvokeAgentActionProvider()
