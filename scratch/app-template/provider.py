"""The app-template tool provider.

Implements ToolProvider from personalclaw.sdk.tool — the contract core
resolves this app through. Every method below is a stub: fill them in, and keep
imports on the SDK surface (personalclaw.sdk.*), never a core internal.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.sdk.tool import ToolProvider

logger = logging.getLogger("app_template")


class AppTemplateProvider(ToolProvider):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = dict(config or {})
        self._timeout = int(self._config.get("timeout_secs", 20))

    @property
    def display_name(self):
        return "App Template"

    async def invoke(self, tool_name, arguments):
        """Execute a tool with the given arguments."""
        raise NotImplementedError("invoke: implement this against ToolProvider")

    async def list_tools(self):
        """List all tools available from this provider."""
        return []

    @property
    def name(self):
        return "app-template"


def create_provider(config: dict[str, Any] | None = None) -> AppTemplateProvider:
    """Manifest factory — core calls this with this app's saved settings."""
    return AppTemplateProvider(config)
