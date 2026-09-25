"""A session CREATED with an approval policy hands it to a runtime that gates tools itself.

Measured on a live General loop run unattended (driven 2026-09-25): the loop's worker step was
spawned with the explicit ``auto`` grant (SEL ``subagent.approval_mode_auto_policy ok``), and then
its ``write_file`` and ``bash`` calls were answered, inside the native runtime, with

    this tool needs approval but the run is unattended (no human to approve) — it was auto-declined

so the step reported it "could not create the file". The grant lived on the SESSION RECORD only:
``SessionManager.get_or_create(approval_policy=...)`` stored it on ``_Session`` and never told the
provider, while ``set_approval_policy`` (the path a dashboard trust toggle takes) did. The native
runtime's comment on that branch — "when the run carries an auto/yolo policy, _requires_approval
already returned False and we never reach here" — was true for the toggle and false for creation.

Driven through the REAL ``NativeAgentRuntime`` built by the session factory, so the assertion is
about the runtime's own gate rather than about a recorded call.
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import AppConfig
from personalclaw.session import SessionManager
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult


class _Model:
    supports_tools = True
    _model = "scripted"

    async def complete(self, messages: Any, **_: Any) -> Any:  # pragma: no cover — never prompted
        raise AssertionError("no turn runs in this test")


class _Files(ToolProvider):
    """One write tool that, like the real filesystem provider's, requires approval."""

    @property
    def name(self) -> str:
        return "personalclaw-filesystem"

    @property
    def display_name(self) -> str:
        return "Files"

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="write_file",
                description="write a file",
                provider="personalclaw-filesystem",
                parameters={"type": "object", "properties": {}},
                requires_approval=True,
            )
        ]

    async def invoke(self, tool_name: str, arguments: dict) -> ToolResult:  # pragma: no cover
        return ToolResult(success=True, output="written")


def _manager() -> SessionManager:
    def factory(session_key: Any = None, **_: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(
                name="a", provider="native", model="m", tools=[], skills=[]
            ),
            model_provider=_Model(),
            tool_providers=[_Files()],
            unattended=True,
        )

    return SessionManager(AppConfig(), provider_factory=factory)


@pytest.mark.asyncio
async def test_an_auto_grant_at_creation_reaches_the_runtime() -> None:
    """At `origin/main` this is True: the unattended runtime would auto-decline the write."""
    mgr = _manager()
    runtime, is_new, _ = await mgr.get_or_create("subagent:abc123", approval_policy="auto")
    try:
        assert is_new
        assert runtime._requires_approval("write_file") is False
    finally:
        mgr.release("subagent:abc123")
        await mgr.close_all()


@pytest.mark.asyncio
async def test_no_grant_leaves_the_runtime_asking() -> None:
    """The control: without a grant the write still needs approval — and an unattended runtime
    still declines it, which is the fail-closed default this change must not widen."""
    mgr = _manager()
    runtime, _, _ = await mgr.get_or_create("subagent:def456")
    try:
        assert runtime._requires_approval("write_file") is True
    finally:
        mgr.release("subagent:def456")
        await mgr.close_all()


@pytest.mark.asyncio
async def test_a_provider_without_a_setter_is_untouched() -> None:
    """ACP enforces approval through its own protocol path and has no setter."""

    class _Acp:
        async def start(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

        def context_usage_pct(self) -> float:
            return 0.0

    mgr = SessionManager(AppConfig(), provider_factory=lambda *a, **k: _Acp())
    provider, _, _ = await mgr.get_or_create("subagent:acp1", approval_policy="auto")
    try:
        assert mgr.get_approval_policy("subagent:acp1") == "auto"
        assert not hasattr(provider, "set_approval_policy")
    finally:
        mgr.release("subagent:acp1")
        await mgr.close_all()
