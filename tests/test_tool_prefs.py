"""PT3 — user tool disable: persistence + enforcement + core-lock guard."""

from __future__ import annotations

import json

import pytest

from personalclaw.tool_providers import tool_prefs


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_prefs, "config_dir", lambda: tmp_path)
    return tmp_path


def test_disable_then_enable_roundtrips(home):
    assert tool_prefs.set_enabled("Builder", "SomeTool", False)["ok"] is True
    assert tool_prefs.is_disabled("Builder", "SomeTool") is True
    # persisted
    data = json.loads((home / "tool_prefs.json").read_text())
    assert "Builder:SomeTool" in data["disabled"]
    # re-enable
    tool_prefs.set_enabled("Builder", "SomeTool", True)
    assert tool_prefs.is_disabled("Builder", "SomeTool") is False


def test_core_locked_cannot_be_disabled(home):
    for locked in ("bash", "read_file", "tool_search", "tool_schema", "finish"):
        res = tool_prefs.set_enabled("builtin", locked, False)
        assert res["ok"] is False and res.get("locked") is True
        assert tool_prefs.is_disabled("builtin", locked) is False  # never disabled


def test_locked_entry_in_file_is_ignored(home):
    # Even if a stale locked key lands in the file, is_disabled ignores it.
    (home / "tool_prefs.json").write_text(json.dumps({"disabled": ["builtin:bash"]}))
    assert tool_prefs.is_disabled("builtin", "bash") is False


def test_missing_file_means_nothing_disabled(home):
    assert tool_prefs.load_disabled() == set()
    assert tool_prefs.is_disabled("Builder", "Anything") is False


def test_corrupt_file_fails_open(home):
    (home / "tool_prefs.json").write_text("{ not json")
    assert tool_prefs.load_disabled() == set()


def test_key_for_uses_other_when_provider_blank(home):
    assert tool_prefs.key_for("", "t") == "other:t"


@pytest.mark.asyncio
async def test_runtime_excludes_disabled_tool(home, monkeypatch):
    """A disabled native tool is absent from the runtime's defs AND index."""
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

    class _P(ToolProvider):
        @property
        def name(self):
            return "myprov"

        @property
        def display_name(self):
            return "My"

        async def list_tools(self):
            return [
                ToolDefinition(
                    name="keep_me",
                    description="d",
                    parameters={"type": "object"},
                    provider="myprov",
                ),
                ToolDefinition(
                    name="drop_me",
                    description="d",
                    parameters={"type": "object"},
                    provider="myprov",
                ),
            ]

        async def invoke(self, n, a):
            return ToolResult(success=True, output="x")

    class _M:
        supports_tools = True
        _model = "scripted"

        async def complete(self, messages, *, tools=None, model=None):
            from personalclaw.llm.events import EVENT_COMPLETE, AgentEvent

            yield AgentEvent(kind=EVENT_COMPLETE)

    tool_prefs.set_enabled("myprov", "drop_me", False)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_M(),
        tool_providers=[_P()],
    )
    await rt.start()
    names = {d.name for d in rt._tool_defs}
    assert "keep_me" in names
    assert "drop_me" not in names  # dropped from schema/catalog
    assert "drop_me" not in rt._tool_index  # AND uncallable


# ── UT4: provider-level disable ──


def test_disable_whole_provider_roundtrips(home):
    assert tool_prefs.set_provider_enabled("myprov", False)["ok"] is True
    assert tool_prefs.is_provider_disabled("myprov") is True
    # every tool of a disabled provider reads as disabled
    assert tool_prefs.is_disabled("myprov", "anything") is True
    tool_prefs.set_provider_enabled("myprov", True)
    assert tool_prefs.is_provider_disabled("myprov") is False
    assert tool_prefs.is_disabled("myprov", "anything") is False


def test_platform_provider_cannot_be_disabled(home):
    res = tool_prefs.set_provider_enabled("personalclaw-filesystem", False)
    assert res["ok"] is False and res.get("locked") is True
    assert tool_prefs.is_provider_disabled("personalclaw-filesystem") is False


def test_provider_disable_independent_of_tool_disable(home):
    # tool + provider disable lists are independent keys in the doc
    tool_prefs.set_enabled("myprov", "t1", False)
    tool_prefs.set_provider_enabled("other", False)
    assert tool_prefs.load_disabled() == {"myprov:t1"}
    assert tool_prefs.load_disabled_providers() == {"other"}


@pytest.mark.asyncio
async def test_runtime_skips_disabled_provider_toolset(home):
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

    class _P(ToolProvider):
        @property
        def name(self):
            return "killme"

        @property
        def display_name(self):
            return "Kill"

        async def list_tools(self):
            return [
                ToolDefinition(
                    name=f"k_{i}", description="d", parameters={"type": "object"}, provider="killme"
                )
                for i in range(3)
            ]

        async def invoke(self, n, a):
            return ToolResult(success=True, output="x")

    class _M:
        supports_tools = True
        _model = "scripted"

        async def complete(self, messages, *, tools=None, model=None):
            from personalclaw.llm.events import EVENT_COMPLETE, AgentEvent

            yield AgentEvent(kind=EVENT_COMPLETE)

    tool_prefs.set_provider_enabled("killme", False)
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_M(),
        tool_providers=[_P()],
    )
    await rt.start()
    # the whole provider's toolset is gone from schema AND dispatch index
    assert not any(d.name.startswith("k_") for d in rt._tool_defs)
    assert not any(n.startswith("k_") for n in rt._tool_index)
