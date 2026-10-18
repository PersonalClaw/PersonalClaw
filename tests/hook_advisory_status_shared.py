"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_hook_advisory_status.py``

Its checks:
``tests/rails/test_hook_advisory_status_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

import personalclaw.action_providers as action_providers_mod
from personalclaw.action_providers.base import ActionResult


class _ExitTwoProvider:
    """A bash-shaped provider that asks to block: exit 2 → `ActionResult.blocked`."""

    async def execute(self, config, ctx, timeout=30):
        return ActionResult(success=False, blocked=True, exit_code=2, stderr="denied")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Never the real home: `run_script_hook` consults the incident switch, the denylist and the
    rung ladder, each of which reads config."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        action_providers_mod, "get_action_provider", lambda name: _ExitTwoProvider()
    )
