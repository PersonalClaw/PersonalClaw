"""`action_providers` reaches the dashboard's state through a Protocol, never an import.

`ActionServices.state` used to be typed with a `TYPE_CHECKING` import of
`personalclaw.dashboard.state`, the one upward edge from the provider layer into the HTTP
surface (`structural-baseline.json`, `core-must-not-import-the-http-surface`). The
`DashboardStateProtocol` in `action_providers/services.py` names what providers actually use,
`DashboardState` satisfies it structurally, and mypy checks that where the dashboard wires
`ActionServices`.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ACTION_PROVIDERS = (
    Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "action_providers"
)


def _dashboard_imports(path: Path) -> list[str]:
    """Every `personalclaw.dashboard` import in *path*, at any depth, `TYPE_CHECKING` included."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "personalclaw.dashboard"
        ):
            found.append(f"{path.name}:{node.lineno} from {node.module}")
        elif isinstance(node, ast.Import):
            found += [
                f"{path.name}:{node.lineno} import {alias.name}"
                for alias in node.names
                if alias.name.startswith("personalclaw.dashboard")
            ]
    return found


def test_no_action_provider_imports_the_dashboard():
    """Not at module level, not inside a function, and not under `TYPE_CHECKING`."""
    files = sorted(_ACTION_PROVIDERS.glob("*.py"))
    assert len(files) > 10, "the scan found almost nothing — the path is wrong"
    offenders = [hit for path in files for hit in _dashboard_imports(path)]
    assert offenders == [], f"action_providers imports the dashboard: {offenders}"


def test_dashboard_state_satisfies_every_protocol_member():
    from personalclaw.action_providers.services import DashboardStateProtocol
    from personalclaw.dashboard.state import DashboardState

    members = DashboardStateProtocol.__protocol_attrs__
    assert members, "the protocol declares nothing"
    for attr in members:
        if attr == "owner_id":
            # An instance attribute, set from the constructor.
            assert "owner_id" in inspect.signature(DashboardState.__init__).parameters
        else:
            assert hasattr(DashboardState, attr), f"DashboardState is missing {attr!r}"


def test_the_protocol_declares_what_its_consumers_use():
    """`notify` (the notify, send-message and proactive paths), `channel_delivery` and
    `owner_id` (send-message's owner routing), `broadcast_ws` and `push_refresh` (the inbox
    items those paths raise)."""
    from personalclaw.action_providers.services import DashboardStateProtocol

    for attr in ("notify", "channel_delivery", "owner_id", "broadcast_ws", "push_refresh"):
        assert attr in DashboardStateProtocol.__protocol_attrs__, attr


@pytest.mark.asyncio
async def test_the_notify_provider_dispatches_through_the_protocol(monkeypatch):
    """The real `NotifyActionProvider`, fed a state that is ONLY the protocol: a call to anything
    the protocol does not declare would raise on the spec'd mock."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.notify_provider import NotifyActionProvider
    from personalclaw.action_providers.services import ActionServices, DashboardStateProtocol

    state = MagicMock(spec=DashboardStateProtocol)
    state.owner_id = "owner"
    monkeypatch.setattr(
        "personalclaw.action_providers.services._services",
        ActionServices(state=state, spawn_background=MagicMock()),
    )

    result = await NotifyActionProvider().execute(
        {"title_template": "Test alert", "body_template": "body", "kind": "info"},
        ActionContext(event="after_turn", context="test context"),
    )

    assert result.success, result.error
    state.notify.assert_called_once_with("info", "Test alert", "body")
