"""Tests for fire_tool_hooks helper and global hook store accessor."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.hooks import (
    HOOK_EVENT_PRE_TOOL_USE,
    ScriptHookStore,
    fire_tool_hooks,
    get_global_hook_store,
    set_global_hook_store,
)


@pytest.fixture(autouse=True)
def _reset_global_store():
    """Reset global hook store between tests."""
    set_global_hook_store(None)  # type: ignore[arg-type]
    yield
    set_global_hook_store(None)  # type: ignore[arg-type]


@pytest.fixture
def hook_store(tmp_path: Path) -> ScriptHookStore:
    return ScriptHookStore(tmp_path)


class TestGlobalHookStore:
    """Test get/set global hook store accessor."""

    def test_default_is_none(self):
        assert get_global_hook_store() is None

    def test_set_and_get(self, hook_store: ScriptHookStore):
        set_global_hook_store(hook_store)
        assert get_global_hook_store() is hook_store

    def test_overwrite(self, tmp_path: Path):
        store1 = ScriptHookStore(tmp_path / "a")
        store2 = ScriptHookStore(tmp_path / "b")
        set_global_hook_store(store1)
        set_global_hook_store(store2)
        assert get_global_hook_store() is store2


class TestFireToolHooks:
    """Test fire_tool_hooks helper."""

    @pytest.mark.asyncio
    async def test_none_store_is_noop(self):
        # Should not raise
        await fire_tool_hooks(None, "Running: echo hello")

    @pytest.mark.asyncio
    async def test_strips_running_prefix(self, hook_store: ScriptHookStore):
        with patch.object(hook_store, "fire", new_callable=AsyncMock) as mock_fire:
            await fire_tool_hooks(hook_store, "Running: echo hello")
            mock_fire.assert_called_once_with(
                HOOK_EVENT_PRE_TOOL_USE,
                tool_name="echo hello",
                tool_input=None,
                subagent_id="",
                parent_session_key="",
                agent_role="",
            )

    @pytest.mark.asyncio
    async def test_no_prefix(self, hook_store: ScriptHookStore):
        with patch.object(hook_store, "fire", new_callable=AsyncMock) as mock_fire:
            await fire_tool_hooks(hook_store, "@my-mcp-server/ReadFile")
            mock_fire.assert_called_once_with(
                HOOK_EVENT_PRE_TOOL_USE,
                tool_name="@my-mcp-server/ReadFile",
                tool_input=None,
                subagent_id="",
                parent_session_key="",
                agent_role="",
            )

    @pytest.mark.asyncio
    async def test_parses_tool_input_json(self, hook_store: ScriptHookStore):
        ti = json.dumps({"path": "/tmp/test.txt"})
        with patch.object(hook_store, "fire", new_callable=AsyncMock) as mock_fire:
            await fire_tool_hooks(hook_store, "ReadFile", ti)
            mock_fire.assert_called_once_with(
                HOOK_EVENT_PRE_TOOL_USE,
                tool_name="ReadFile",
                tool_input={"path": "/tmp/test.txt"},
                subagent_id="",
                parent_session_key="",
                agent_role="",
            )

    @pytest.mark.asyncio
    async def test_invalid_json_passes_none(self, hook_store: ScriptHookStore):
        with patch.object(hook_store, "fire", new_callable=AsyncMock) as mock_fire:
            await fire_tool_hooks(hook_store, "ReadFile", "not-json")
            mock_fire.assert_called_once_with(
                HOOK_EVENT_PRE_TOOL_USE,
                tool_name="ReadFile",
                tool_input=None,
                subagent_id="",
                parent_session_key="",
                agent_role="",
            )

    @pytest.mark.asyncio
    async def test_empty_title(self, hook_store: ScriptHookStore):
        with patch.object(hook_store, "fire", new_callable=AsyncMock) as mock_fire:
            await fire_tool_hooks(hook_store, "")
            mock_fire.assert_called_once_with(
                HOOK_EVENT_PRE_TOOL_USE,
                tool_name="",
                tool_input=None,
                subagent_id="",
                parent_session_key="",
                agent_role="",
            )

    @pytest.mark.asyncio
    async def test_fire_exception_swallowed(self, hook_store: ScriptHookStore):
        with patch.object(
            hook_store,
            "fire",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise
            await fire_tool_hooks(hook_store, "ReadFile")

    @pytest.mark.asyncio
    async def test_none_tool_input_skipped(self, hook_store: ScriptHookStore):
        with patch.object(hook_store, "fire", new_callable=AsyncMock) as mock_fire:
            await fire_tool_hooks(hook_store, "ReadFile", None)
            mock_fire.assert_called_once_with(
                HOOK_EVENT_PRE_TOOL_USE,
                tool_name="ReadFile",
                tool_input=None,
                subagent_id="",
                parent_session_key="",
                agent_role="",
            )
