"""Service accessor for native action providers.

bash/webhook/run-script actions are self-contained, but the native action
providers (notify / send-message / create-task / invoke-agent) must reach
in-process services — DashboardState (notifications / channel send), the tasks
registry, the SubagentManager — without importing the dashboard package
(layering) and without each provider re-discovering globals.

Mirrors ``personalclaw.hooks.set_global_hook_store`` / ``get_global_hook_store``:
the dashboard wires a :class:`ActionServices` at startup; providers fetch it
lazily and return an error result (never raise) if it is unset, so a misordered
startup fails loudly in tests rather than silently no-opping.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.subagent import SubagentManager


@dataclass
class ActionServices:
    """Handles native action providers need. Wired once at dashboard startup."""

    state: "DashboardState"
    # Schedule a coroutine as a tracked background task (fire-and-forget spawn),
    # mirroring the dashboard's ``_background_tasks`` bookkeeping. Used by
    # invoke-agent (E3-P3) so the lifecycle never blocks on a child agent.
    spawn_background: Callable[[Awaitable[Any]], Any]
    # The subagent manager invoke-agent (E3-P3) spawns child agents through.
    subagents: "SubagentManager | None" = None


_services: "ActionServices | None" = None


def set_action_services(svc: "ActionServices") -> None:
    global _services
    _services = svc


def get_action_services() -> "ActionServices | None":
    """The wired services, or ``None`` if startup hasn't wired them yet.

    Providers MUST handle ``None`` (return an error result) rather than assume.
    """
    return _services


def validate_spawn_cwd(cwd: str) -> str:
    """Pre-validate a spawn ``cwd`` against the configured allowed roots.

    Returns an error string if ``cwd`` is non-empty and would be REFUSED by the
    subagent manager (out of allowed roots / nonexistent / relative), else "".
    The fire-and-forget spawn validates cwd asynchronously inside the background
    task, so without this a misconfigured cwd would surface as a false "launched"
    (the spawn silently refused). Checking up front lets run-prompt/run-workflow
    return an honest error result instead. Empty cwd always passes (the subagent
    uses its default sandbox).
    """
    if not cwd:
        return ""
    try:
        from personalclaw.config.loader import AppConfig
        from personalclaw.subagent import validate_cwd

        allowed = AppConfig.load().agent.subagent_cwd_allowed_roots
        _resolved, err = validate_cwd(cwd, allowed)
        return err or ""
    except Exception:
        return ""  # never block a run on a validation lookup failure
