"""``call-app-route`` action provider — drive a declared app backend route from a
hook / cron / event-trigger (PLATFORM-LEGIBILITY §4.2).

Exactly ONE new action provider ships for app routes (per-app generated providers
can't be enumerated in the static ``ALLOWED_HOOK_PROVIDERS`` frozenset). Its
``action_config`` selects the route to drive::

    {
        "app": "growth",              # installed + enabled app name
        "op":  "list_artifacts",      # a route the app declared agentCallable
        "args": {"limit": 20}         # path params + query/body, per the route
    }

It refuses an op the app didn't declare ``agentCallable`` — the SAME gate the
:class:`~personalclaw.tool_providers.app_routes.AppRoutesToolProvider` enforces,
shared via :func:`~personalclaw.tool_providers.app_routes.resolve_route` so the
tool path and the action path can't diverge. Non-blocking; invokes through the
existing reverse proxy on loopback (``LOOPBACK_INTERNAL``) — no new egress surface.
"""

from __future__ import annotations

from typing import Any

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult


class CallAppRouteActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "call-app-route"

    @property
    def display_name(self) -> str:
        return "Call App Route"

    async def execute(
        self,
        action_config: dict[str, Any],
        ctx: ActionContext,
        timeout: int = 30,
    ) -> ActionResult:
        from personalclaw.tool_providers.app_routes import (
            RouteError,
            call_app_route,
            resolve_route,
        )

        app = str(action_config.get("app", "") or "").strip()
        op = str(action_config.get("op", "") or "").strip()
        if not app or not op:
            return ActionResult(
                success=False,
                error="call-app-route action is missing 'app' and/or 'op'",
            )
        args = action_config.get("args") or {}
        if not isinstance(args, dict):
            return ActionResult(success=False, error="call-app-route 'args' must be an object")

        try:
            resolution = resolve_route(app, op, args)
        except RouteError as exc:
            # The op isn't declared agentCallable (or doesn't exist) — refuse with
            # the coded WHAT/WHY/FIX envelope, never a raw string.
            return ActionResult(
                success=False, error=exc.agent_error.what, agent_error=exc.agent_error
            )

        result = await call_app_route(resolution)
        if not result.success:
            return ActionResult(
                success=False,
                error=result.error or (result.agent_error.what if result.agent_error else ""),
                stdout=result.output,
                agent_error=result.agent_error,
            )
        return ActionResult(
            success=True,
            exit_code=0,
            stdout=result.output,
        )


def create_provider(config: dict[str, Any] | None = None) -> "CallAppRouteActionProvider":
    return CallAppRouteActionProvider()
