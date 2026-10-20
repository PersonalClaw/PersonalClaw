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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from personalclaw.subagent import SubagentManager


class DashboardStateProtocol(Protocol):
    """What native action providers use of the dashboard's state object.

    ``notify`` for push notifications, ``broadcast_ws`` and ``push_refresh`` for live updates,
    ``channel_delivery`` and ``owner_id`` for routing a message to the owner,
    ``knowledge_ingest_queue`` for the enrichment of what ``knowledge-persist`` writes (its
    ``enqueue(item_id)``). Declared here so ``action_providers`` never imports
    ``dashboard.state``, not even under ``TYPE_CHECKING``: ``DashboardState`` satisfies it
    structurally, and mypy checks that where the dashboard wires :class:`ActionServices`.
    """

    owner_id: str

    @property
    def channel_delivery(self) -> Any: ...
    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None: ...
    def push_refresh(self, *kinds: str) -> None: ...
    def broadcast_ws(self, msg_type: str, data: object) -> None: ...
    def knowledge_ingest_queue(self) -> Any: ...


@dataclass
class ActionServices:
    """Handles native action providers need. Wired once at dashboard startup."""

    state: DashboardStateProtocol
    # The subagent manager invoke-agent and run-prompt spawn agent tasks through. Its
    # spawn schedules the task and returns, so neither waits on the agent it starts.
    subagents: "SubagentManager | None" = None
    # The workflow supervisor (`WorkflowWatchdog`) run-workflow starts runs through.
    # Deliberately the SUPERVISOR, not a controller factory: it owns controller
    # registration, so a provider-started run is visible to adoption and cancel. A
    # provider-owned controller would be invisible to both, and a restart would start a
    # second one alongside it — two writers on one run.
    workflows: Any = None


_services: "ActionServices | None" = None


def set_action_services(svc: "ActionServices") -> None:
    global _services
    _services = svc


def get_action_services() -> "ActionServices | None":
    """The wired services, or ``None`` if startup hasn't wired them yet.

    Providers MUST handle ``None`` (return an error result) rather than assume.
    """
    return _services


def spawn_refusal(info: Any) -> str:
    """Why an agent task ``SubagentManager.spawn`` just returned did not start, or "" when it
    started or waits for a slot.

    The manager refuses some spawns on the spot (too little memory, an incident, the day's budget,
    a folder outside the allowed roots, an unknown agent, a stopped fan-out) and returns them
    already ended, with the reason. An action that only reports "launched" would hide that, and
    its trigger would hear nothing, since a launch says nothing until the agent ends.
    """
    if info is None:
        return "the agent did not start: there was no room for another one"
    if getattr(info, "done", False):
        return str(getattr(info, "error", "") or "") or "the agent ended before it started"
    return ""


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
