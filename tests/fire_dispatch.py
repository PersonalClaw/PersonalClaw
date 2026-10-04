"""The gateway's dispatch for an automation's fire, handed to a dashboard state.

The gateway hands its dashboard this at boot (`GatewayOrchestrator._start_dashboard`), and a
webhook's fire and a view's refresh run through it, as every fire does. A test that serves those
routes without a gateway hands its state the same dispatch, on an orchestrator with nothing else
started, so what it drives is what a running gateway runs.
"""

from __future__ import annotations

from typing import Any


def attach(state: Any) -> Any:
    """Give *state* the gateway's fire dispatch (`DashboardState.fire_trigger`); *state*."""
    from personalclaw.gateway import GatewayOrchestrator

    orchestrator = object.__new__(GatewayOrchestrator)
    orchestrator.dashboard_state = state
    state.fire_trigger = orchestrator._fire_store_trigger
    return state
