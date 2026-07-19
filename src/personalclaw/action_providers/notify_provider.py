"""``notify`` hook provider — raise a dashboard notification on a lifecycle event.

Non-blocking native action. ``action_config`` shape::

    {
        "kind": "info",                      # optional: info|success|warning|error
        "title_template": "Agent finished",  # $EVENT/$CONTEXT/$<payload-key>
        "body_template": "$CONTEXT"          # optional
    }

Reaches :meth:`DashboardState.notify` via the hook service accessor. Requires no
external process — the agent-scoped equivalent of "ping me when this happens".
"""

from __future__ import annotations

from typing import Any

from personalclaw.action_providers.base import (
    ActionContext,
    ActionResult,
    ActionProvider,
)
from personalclaw.action_providers.services import get_action_services
from personalclaw.action_providers.template import render_template

_ALLOWED_KINDS = {"info", "success", "warning", "error"}


class NotifyActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "notify"

    @property
    def display_name(self) -> str:
        return "Dashboard Notification"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        title = render_template(action_config.get("title_template", ""), ctx).strip()
        if not title:
            return ActionResult(
                success=False, error="notify hook is missing 'title_template'"
            )
        body = render_template(action_config.get("body_template", ""), ctx)
        kind = (action_config.get("kind") or "info").strip().lower()
        if kind not in _ALLOWED_KINDS:
            kind = "info"

        services = get_action_services()
        if services is None:
            return ActionResult(
                success=False, error="notify hook: services unavailable (startup not wired)"
            )
        try:
            services.state.notify(kind, title, body)
        except Exception as exc:  # noqa: BLE001 - surface as error result, never raise
            return ActionResult(success=False, error=f"notify failed: {exc}")
        return ActionResult(success=True, exit_code=0, stdout=f"notified: {title[:80]}")


def create_provider(config: dict[str, Any] | None = None) -> "NotifyActionProvider":
    return NotifyActionProvider()
