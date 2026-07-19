"""``invoke-agent`` hook provider — spawn a child agent on a lifecycle event.

The marquee E3 action: a coder agent's ``Stop`` hook spawns a ``code-reviewer``
agent. Guarded three ways and always fire-and-forget so the lifecycle never
blocks on the child:

* **Recursion depth cap** (``_HOOK_INVOKE_MAX_DEPTH``): a spawned agent can have
  its own hooks that spawn agents. ``fire_for_ids`` injects ``__hook_depth``
  into the payload from the originating agent's depth; at the cap we refuse.
* **Concurrency cap** (``_HOOK_INVOKE_MAX_CONCURRENT`` semaphore): bounds total
  in-flight hook-spawned agents so a wide fan-out can't fork-bomb.
* **Approval**: spawn is requested with ``approval_mode="auto"`` only when the
  hook opts in (``approval_mode: "auto"``) or the global
  ``auto_approve_subagent_spawn`` is set; otherwise SubagentManager.spawn
  applies its normal approval gate (rejected if no interactive approver).

``action_config`` shape::

    {
        "task_template": "Review the changes in $CONTEXT",  # required
        "agent": "code-reviewer",   # optional child agent name
        "model": "...", "max_turns": 20,  # optional
        "approval_mode": "auto"     # optional opt-in to auto-approve
    }
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionResult,
    ActionProvider,
)
from personalclaw.action_providers.services import get_action_services
from personalclaw.action_providers.template import render_template

logger = logging.getLogger(__name__)

# Depth 0 = the user's top-level agent. A child spawned by a hook is depth 1, its
# child depth 2, … We refuse at the cap so coder→reviewer→… can't recurse forever.
_HOOK_INVOKE_MAX_DEPTH = 3
# Total in-flight hook-spawned agents (matches the webhook path's _HOOK_MAX_CONCURRENT).
_HOOK_INVOKE_MAX_CONCURRENT = 6
_invoke_agent_sem = asyncio.Semaphore(_HOOK_INVOKE_MAX_CONCURRENT)


class InvokeAgentActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "invoke-agent"

    @property
    def display_name(self) -> str:
        return "Invoke Agent"

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
            return ActionResult(
                success=False, error="invoke-agent hook is missing 'task_template'"
            )

        services = get_action_services()
        if services is None or services.subagents is None:
            return ActionResult(
                success=False, error="invoke-agent: subagent manager unavailable"
            )

        if _invoke_agent_sem.locked():
            return ActionResult(
                success=False,
                error=f"invoke-agent capacity reached ({_HOOK_INVOKE_MAX_CONCURRENT} in flight)",
            )

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

                if AppConfig.load().hooks.auto_approve_subagent_spawn:
                    approval_mode = "auto"
            except Exception:
                logger.debug("invoke-agent: auto-approve config lookup failed", exc_info=True)

        parent_key = str((ctx.payload or {}).get("session_key", "") or "")

        async def _spawn() -> None:
            try:
                services.subagents.spawn(  # type: ignore[union-attr]
                    task=task,
                    parent_session_key=parent_key,
                    agent=agent,
                    max_turns=max_turns,
                    model=model,
                    approval_mode=approval_mode,
                    silent=False,
                )
            except Exception:
                logger.warning("invoke-agent: spawn failed", exc_info=True)
            finally:
                _invoke_agent_sem.release()

        await _invoke_agent_sem.acquire()
        # Fire-and-forget: the lifecycle event returns immediately; the child
        # runs in the background (and the semaphore is released in _spawn).
        services.spawn_background(_spawn())
        # "launched", not "succeeded": the spawned agent's real outcome is recorded
        # by its own run, not known here (T7 honest "started ≠ succeeded" status).
        return ActionResult(
            success=True, exit_code=0, stdout=f"spawned agent for: {task[:80]}",
            outcome="launched",
        )


def create_provider(config: dict[str, Any] | None = None) -> "InvokeAgentActionProvider":
    return InvokeAgentActionProvider()
