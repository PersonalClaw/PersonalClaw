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
        return ActionResult(
            success=True,
            exit_code=0,
            stdout=f"created task {getattr(task, 'id', '?')}: {title[:80]}",
        )


def create_provider(config: dict[str, Any] | None = None) -> "CreateTaskActionProvider":
    return CreateTaskActionProvider()
