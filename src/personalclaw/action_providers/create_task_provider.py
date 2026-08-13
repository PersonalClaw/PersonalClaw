"""``create-task`` hook provider — file a task on a lifecycle event.

Non-blocking native action. ``action_config`` shape::

    {
        "provider": "native",                 # task provider (default native)
        "title_template": "Review $CONTEXT",  # required; $EVENT/$CONTEXT/$<key>
        "body_template": "...",               # optional → task description
        "priority": "medium",                 # optional
        "project": "...", "labels": [...]     # optional passthrough
    }

Calls :func:`personalclaw.tasks.registry.create_task`, which honors read-only
providers (raises → surfaced as an error result). "File a follow-up task when
the agent hits an Error / finishes a Stop", without shelling out.
"""

from __future__ import annotations

from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from personalclaw.action_providers.template import render_template


class CreateTaskActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "create-task"

    @property
    def display_name(self) -> str:
        return "Create Task"

    @property
    def reversal_kinds(self) -> tuple[str, ...]:
        """``task:<provider>:<id>`` — the handle :meth:`execute` returns."""
        return ("task",)

    async def reverse(self, handle: str) -> ActionResult:
        """Delete the task this action filed (AUTONOMY-GUARDRAILS §6.1).

        Undo for "create a task" is "the task is not there any more", so the reversal is a
        real delete through the same registry the creation went through — which is also
        what makes a read-only task provider refuse instead of pretending.

        The handle is re-parsed and RESOLVED here rather than trusted: a task that has
        already been deleted (or renumbered by a provider that reuses ids) must come back
        as a refusal, because the caller turns a successful reversal into a demotion and a
        false success would spend that on nothing.
        """
        kind, _, rest = handle.partition(":")
        provider_name, _, task_id = rest.partition(":")
        if kind != "task" or not provider_name or not task_id:
            return ActionResult(success=False, error=f"not a create-task handle: {handle!r}")
        try:
            from personalclaw.tasks.registry import delete_task, get_task

            existing = await get_task(task_id, provider_name)
            if existing is None:
                return ActionResult(
                    success=False,
                    error=f"task {task_id} is already gone — nothing left to undo",
                )
            deleted = await delete_task(task_id, provider_name)
        except Exception as exc:  # noqa: BLE001 - refusal, never raise
            return ActionResult(success=False, error=f"could not delete task {task_id}: {exc}")
        if not deleted:
            return ActionResult(success=False, error=f"task {task_id} could not be deleted")
        return ActionResult(success=True, stdout=f"deleted task {task_id}")

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        title = render_template(action_config.get("title_template", ""), ctx).strip()
        if not title:
            return ActionResult(success=False, error="create-task hook is missing 'title_template'")
        provider_name = (action_config.get("provider") or "native").strip() or "native"
        fields: dict[str, Any] = {"title": title}
        body = render_template(action_config.get("body_template", ""), ctx)
        if body:
            fields["description"] = body
        # Optional structured passthroughs (no templating — they're plain values).
        for key in ("priority", "project", "assignee", "due"):
            val = action_config.get(key)
            if val:
                fields[key] = val
        labels = action_config.get("labels")
        if isinstance(labels, list) and labels:
            fields["labels"] = labels

        try:
            from personalclaw.tasks.registry import create_task

            task = await create_task(provider_name, **fields)
        except Exception as exc:  # noqa: BLE001 - error result, never raise
            return ActionResult(success=False, error=f"create-task failed: {exc}")
        task_id = str(getattr(task, "id", "") or "")
        return ActionResult(
            success=True,
            exit_code=0,
            stdout=f"created task {task_id or '?'}: {title[:80]}",
            # The reversal handle for the `auto_with_undo` rung (AUTONOMY-GUARDRAILS
            # §5.2): the row this action filed is exactly what "undo" has to delete, and
            # the id is the only thing that identifies it. Empty when the provider gave
            # back no id — then the seam records the run and offers no undo, rather than
            # offering one that would have nothing to act on.
            reversal=f"task:{provider_name}:{task_id}" if task_id else "",
        )


def create_provider(config: dict[str, Any] | None = None) -> "CreateTaskActionProvider":
    return CreateTaskActionProvider()
