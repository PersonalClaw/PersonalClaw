"""Per-subagent hook attribution.

The three fields (subagent_id / parent_session_key / agent_role) are optional
kwargs on the scoped-firing path. They ride the event payload ONLY when
populated, so a top-level chat fire's payload omits them while a subagent-fired
PreToolUse carries them. Matcher and blocking semantics are unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.hooks import (
    HOOK_EVENT_PRE_TOOL_USE,
    ScriptHook,
    ScriptHookResult,
    ScriptHookStore,
    fire_tool_hooks,
)


@pytest.fixture
def store_with_hook(tmp_path: Path) -> tuple[ScriptHookStore, str]:
    """A store with one enabled PreToolUse hook (no matcher → fires for any tool)."""
    store = ScriptHookStore(tmp_path)
    hook = store.create({
        "name": "audit", "event": HOOK_EVENT_PRE_TOOL_USE,
        "provider": "script", "provider_config": {"command": "true"},
        "enabled": True,
    })
    return store, hook.id


def _capture_payloads():
    """Patch run_script_hook to record the hook_event each fire builds."""
    seen: list[dict] = []

    async def _fake(hook, context="", hook_event=None):
        seen.append(hook_event or {})
        return ScriptHookResult(hook_id=hook.id, hook_name=hook.name, event=hook.event, exit_code=0)

    return seen, _fake


class TestFireForIdsAttribution:
    @pytest.mark.asyncio
    async def test_subagent_fields_present_when_populated(self, store_with_hook):
        store, hook_id = store_with_hook
        seen, fake = _capture_payloads()
        with patch("personalclaw.hooks.run_script_hook", side_effect=fake):
            await store.fire_for_ids(
                HOOK_EVENT_PRE_TOOL_USE, [hook_id],
                tool_name="execute_bash",
                subagent_id="sa-42",
                parent_session_key="dashboard:chat-1-abc",
                agent_role="researcher",
            )
        assert len(seen) == 1
        payload = seen[0]
        assert payload["subagent_id"] == "sa-42"
        assert payload["parent_session_key"] == "dashboard:chat-1-abc"
        assert payload["agent_role"] == "researcher"

    @pytest.mark.asyncio
    async def test_top_level_fire_omits_fields(self, store_with_hook):
        store, hook_id = store_with_hook
        seen, fake = _capture_payloads()
        with patch("personalclaw.hooks.run_script_hook", side_effect=fake):
            await store.fire_for_ids(
                HOOK_EVENT_PRE_TOOL_USE, [hook_id], tool_name="execute_bash",
            )
        assert len(seen) == 1
        payload = seen[0]
        assert "subagent_id" not in payload
        assert "parent_session_key" not in payload
        assert "agent_role" not in payload

    @pytest.mark.asyncio
    async def test_partial_fields_only_present_ones(self, store_with_hook):
        """An empty field is omitted; a populated sibling still rides."""
        store, hook_id = store_with_hook
        seen, fake = _capture_payloads()
        with patch("personalclaw.hooks.run_script_hook", side_effect=fake):
            await store.fire_for_ids(
                HOOK_EVENT_PRE_TOOL_USE, [hook_id], tool_name="x",
                subagent_id="sa-9",  # only this one
            )
        payload = seen[0]
        assert payload["subagent_id"] == "sa-9"
        assert "parent_session_key" not in payload
        assert "agent_role" not in payload


class TestFireToolHooksAttribution:
    @pytest.mark.asyncio
    async def test_fire_tool_hooks_threads_fields(self, store_with_hook):
        """fire_tool_hooks (the ACP-subagent path) forwards the 3 fields to fire()."""
        store, _ = store_with_hook
        with patch.object(store, "fire", new_callable=AsyncMock) as mock_fire:
            await fire_tool_hooks(
                store, "Running: execute_bash", None,
                subagent_id="sa-7", parent_session_key="dashboard:p", agent_role="coder",
            )
        mock_fire.assert_awaited_once_with(
            HOOK_EVENT_PRE_TOOL_USE, tool_name="execute_bash", tool_input=None,
            subagent_id="sa-7", parent_session_key="dashboard:p", agent_role="coder",
        )

    @pytest.mark.asyncio
    async def test_fire_tool_hooks_defaults_empty(self, store_with_hook):
        """Top-level tool call → empty attribution kwargs (payload omits them)."""
        store, _ = store_with_hook
        with patch.object(store, "fire", new_callable=AsyncMock) as mock_fire:
            await fire_tool_hooks(store, "Running: execute_bash", None)
        mock_fire.assert_awaited_once_with(
            HOOK_EVENT_PRE_TOOL_USE, tool_name="execute_bash", tool_input=None,
            subagent_id="", parent_session_key="", agent_role="",
        )
