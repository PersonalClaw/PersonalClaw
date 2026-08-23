"""The `automation_*` MCP surface is wired, not just written (§4 — S92).

The logic is tested in `test_triggers_tools.py`; these tests pin the ADAPTER — that the eight
tools list, validate, dispatch to the store, and are reachable through the same aggregation and
native-app registration every other category uses. A module that exposed `_list_tools` but was
never registered would be the present-and-inert defect this program keeps finding, so the
registration itself is asserted, not assumed.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import mcp_automation as A


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home so the store the tools build never touches the real one."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    # `config_dir()` may be cached; point it at the temp home directly too.
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _data(text: str) -> dict:
    """Pull the trailing <automation-data> JSON a dispatch appends, if any."""
    marker = "<automation-data>"
    if marker not in text:
        return {}
    blob = text.split(marker, 1)[1].split("</automation-data>", 1)[0]
    return json.loads(blob)


# ── the surface lists and every tool validates ──


#: WF2LOO-9 added two self-scheduling tools to this surface. They are NOT in
#: `triggers.tools.TOOL_NAMES` on purpose: that tuple is §4's table of `automation_*` handlers
#: living in that module, and these two live here and delegate into `tools.create`. Naming them
#: separately keeps both meanings honest — "handlers there" and "names an agent may call here".
SELF_SCHEDULE_TOOLS = frozenset({"set_onetime_task", "set_recurring_task"})


def test_the_surface_is_section_4s_table_plus_the_self_schedule_pair():
    from personalclaw.triggers.tools import TOOL_NAMES

    listed = {t["name"] for t in A._list_tools()}
    assert listed == set(TOOL_NAMES) | SELF_SCHEDULE_TOOLS


def test_every_listed_tool_has_a_schema():
    """🔴 A tool with no schema silently skips validation — an agent-supplied `id` or `patch`
    would reach the store unchecked."""
    from personalclaw.validation import MCP_AUTOMATION_SCHEMAS

    for tool in A._list_tools():
        assert tool["name"] in MCP_AUTOMATION_SCHEMAS, tool["name"]


def test_every_tool_declares_an_input_schema():
    for tool in A._list_tools():
        assert tool["inputSchema"]["type"] == "object"


def test_create_advertises_the_criterion_2_example():
    """The description is the only thing steering a model toward this tool; it must name the
    file-watch shape, or an agent reaches for `schedule_add` and gets a cron."""
    create = next(t for t in A._list_tools() if t["name"] == "automation_create")
    assert "~/notes" in create["description"]


# ── 🔴 criterion 2 through the real dispatch ──


def test_criterion_2_through_the_full_dispatch(home):
    """🔴 validate → route → store → text, no shortcut. The one-message bar, end to end through the
    surface an agent actually calls."""
    out = A._call_tool(
        "automation_create",
        {
            "name": "Summarize notes",
            "when": "when a file in ~/notes changes",
            "message": "Summarize into my knowledge base",
        },
    )
    assert "Created automation" in out
    payload = _data(out)
    assert payload["trigger"]["kind"] == "file"
    assert payload["trigger"]["spec"]["paths"] == ["~/notes/**"]
    assert payload["trigger"]["created_by"] == "agent"


def test_the_created_trigger_persists_to_the_shared_store(home):
    A._call_tool(
        "automation_create",
        {"name": "Notes", "when": "when a file in ~/notes changes", "message": "go"},
    )
    from personalclaw.triggers.store import TriggerStore

    assert TriggerStore(base_dir=home).get("file:notes") is not None


def test_list_reflects_a_created_trigger(home):
    A._call_tool(
        "automation_create",
        {"name": "Notes", "when": "when a file in ~/notes changes", "message": "go"},
    )
    out = A._call_tool("automation_list", {})
    assert "file:notes" in out


def test_a_dry_run_needs_no_gateway_and_executes_nothing(home):
    A._call_tool(
        "automation_create",
        {"name": "Notes", "when": "when a file in ~/notes changes", "message": "go"},
    )
    out = A._call_tool("automation_run", {"id": "file:notes", "dry_run": True})
    assert "nothing was executed" in out
    assert _data(out)["plan"]["executes"] is False


def test_pause_resume_delete_through_dispatch(home):
    A._call_tool(
        "automation_create",
        {"name": "Notes", "when": "when a file in ~/notes changes", "message": "go"},
    )
    assert "Paused" in A._call_tool("automation_pause", {"id": "file:notes"})
    assert "Resumed" in A._call_tool("automation_resume", {"id": "file:notes"})
    assert "confirm" in A._call_tool("automation_delete", {"id": "file:notes"})
    assert "Deleted" in A._call_tool("automation_delete", {"id": "file:notes", "confirm": True})


# ── validation actually runs ──


def test_a_missing_required_id_is_rejected_by_validation(home):
    """The schema layer, not the handler, catches this — proving `_validate_args` is in the path."""
    out = A._call_tool("automation_delete", {"confirm": True})
    assert "error" in out.lower()


def test_an_unknown_tool_name_is_a_clean_error(home):
    assert "unknown automation tool" in A._call_tool("automation_bogus", {}).lower()


# ── 🔴 the wiring: aggregated AND registered as a native app ──


def test_the_module_is_in_the_core_aggregation():
    """🔴 The ACP MCP server an external CLI spawns exposes the aggregated set; if this module is
    not in it, `automation_*` is invisible to claude-code/codex."""
    from personalclaw.mcp_core import _AGGREGATED_CATEGORY_MODULES

    assert "personalclaw.mcp_automation" in _AGGREGATED_CATEGORY_MODULES


def test_the_aggregated_surface_includes_the_automation_tools():
    from personalclaw.mcp_core import _aggregated_list_tools

    names = {t["name"] for t in _aggregated_list_tools()}
    assert "automation_create" in names
    assert "automation_run" in names


def test_a_native_app_bundle_registers_the_provider():
    """🔴 The native surface loads tools from `apps/native/*/app.json`. Without the bundle the
    provider factory exists but nothing constructs it — the tools never appear in chat."""
    import json as _json
    from pathlib import Path

    import personalclaw

    root = Path(personalclaw.__file__).parent / "apps" / "native" / "personalclaw-automation-tools"
    manifest = _json.loads((root / "app.json").read_text())
    assert manifest["native"] is True
    assert (
        manifest["provider"]["implementation"]
        == "personalclaw.tool_providers.registry:create_automation_provider"
    )


def test_the_provider_factory_builds_and_names_itself():
    from personalclaw.tool_providers.registry import create_automation_provider

    provider = create_automation_provider()
    assert provider.name == "personalclaw-automation"
