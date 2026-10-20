"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_tools_handler.py``

Its checks:
``tests/rails/test_tools_handler_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations


class _RecordingProvider:
    """A provider that records whether it was reached. Reaching it IS the bug."""

    def __init__(
        self,
        tool_name: str,
        provider_tag: str = "personalclaw-artifacts",
        risk: str = "destructive",
    ) -> None:
        from personalclaw.tool_providers.base import RiskLevel, ToolDefinition

        self.name = provider_tag
        self.invoked: list[tuple[str, dict]] = []
        self._defs = [
            ToolDefinition(
                name=tool_name,
                description="d",
                provider=provider_tag,
                risk_level=RiskLevel(risk),
            )
        ]

    async def list_tools(self):
        return self._defs

    async def invoke(self, name, arguments):
        from personalclaw.tool_providers.base import ToolResult

        self.invoked.append((name, dict(arguments or {})))
        return ToolResult(success=True, output="[]")


def _install_provider(monkeypatch, provider):
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.get_provider",
        lambda name: provider if name == provider.name else None,
    )
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [provider])


def _disable(monkeypatch, *keys: str):
    """Point the preference store at an explicit disabled set (no real home touched)."""
    monkeypatch.setattr("personalclaw.tool_providers.tool_prefs.load_disabled", lambda: set(keys))
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.load_disabled_providers", lambda: set()
    )
