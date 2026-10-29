"""Which family a tool call is, for the words that describe it (shared contracts §3.3.1).

One closed vocabulary, ``turns.Family``, sorts a call for every surface that says it in words: a
folded turn's sentence ("edited 3 files, ran 8 commands"), and the approval's. :func:`family_of`
reads only the call's tool, its kind and its declaration, never a guess that widens anything:

* PersonalClaw's own tools, by :data:`CORE_FAMILIES`, an exact lookup by name. A call an agent CLI
  makes to one through the tool server it is handed is that tool's
  (``acp.mcp_servers.core_tool_titled``). Each of core's own tools has a row, so a tool added
  without one fails ``tests/test_tool_families.py``.
* A shell call (``task_modes.is_shell_invocation``) is a command.
* An agent CLI's own tool, by the kind it declares (:data:`KIND_FAMILIES`).
* Everything else, every MCP and app tool included, is ``other``.

A family is words, never a gate: nothing decides whether a call runs, or asks, by it.
:data:`PHRASES` gives each family's past tense, and :func:`phrase` says it for a count.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from personalclaw.turns import Family

_READ = (
    "read_file",
    "list_dir",
    "glob",
    "grep",
    "repo_map",
    "tool_result_get",
)
_EDIT = ("write_file", "edit_file", "sheet_create")
_COMMAND = ("bash",)
_SEARCH = (
    "knowledge_search",
    "knowledge_structural",
    "memory_recall",
    "memory_list",
    "chat_search",
    "skill_search",
    "task_search",
)
_DELEGATE = ("subagent_run", "best_of_n")
_SEND = ("notify", "notify_attachment", "post_to_inbox")
_OTHER = (
    # The native runtime's own discovery tools.
    "tool_search",
    "tool_schema",
    "reset_tools",
    # Skills, context and templates.
    "skill_invoke",
    "skill_resource",
    "skill_remember",
    "skill_promote",
    "get_context",
    "project_context_review",
    "template_save_from_session",
    "suggest_template",
    "propose_template_diff",
    "refiner_evidence",
    "dashboard_tile_propose",
    "prompt_render",
    # The turn's own pacing and the owner's hooks.
    "wait",
    "hook_register",
    "loop_nudge_stop",
    # Artifacts and what makes them.
    "artifact_save",
    "artifact_get",
    "artifact_update",
    "artifact_list",
    "artifact_versions",
    "artifact_delete",
    "image_generate",
    "video_generate",
    "document_create",
    "deck_create",
    "document_formats",
    "visualize",
    # Memory that changes, and triage.
    "memory_remember",
    "memory_forget",
    "triage_rules_list",
    "triage_rules",
    # Helpers already started.
    "subagent_list",
    "subagent_status",
    # Knowledge items, decisions, tasks, projects and the inbox.
    "knowledge_create",
    "knowledge_get",
    "knowledge_update",
    "knowledge_stats",
    "log_decision",
    "decision_list",
    "decision_resolve",
    "task_create",
    "task_list",
    "task_get",
    "task_update",
    "task_ready",
    "project_create",
    "project_list",
    "task_list_create",
    "project_run_create",
    "project_run_start",
    "project_run_status",
    "project_run_list",
    "inbox_list",
    # Workflows.
    "workflow_author",
    "workflow_check",
    "workflow_plan",
    "workflow_list_defs",
    "workflow_get_def",
    "workflow_start",
    "workflow_status",
    "workflow_observe",
    "workflow_edit",
    "workflow_edit_preview",
    "workflow_skip",
    "workflow_rewind",
    "workflow_run_from",
    "workflow_fork",
    "workflow_start_draft",
    "workflow_pause",
    "workflow_resume",
    "workflow_cancel",
    "workflow_output",
    "workflow_audit",
    "workflow_repair",
    "workflow_manifest",
    "workflow_delete_def",
    # Automations.
    "automation_create",
    "automation_list",
    "automation_update",
    "automation_pause",
    "automation_resume",
    "automation_run",
    "automation_dry_run",
    "automation_history",
    "automation_delete",
    "automation_delete_all",
    "set_onetime_task",
    "set_recurring_task",
    # Desktop computer use: it drives apps, not a browser.
    "computer_list_apps",
    "computer_snapshot",
    "computer_click",
    "computer_type",
    "computer_set_value",
    "computer_scroll",
    "computer_perform_action",
)


def _all(names: tuple[str, ...], family: Family) -> dict[str, Family]:
    return dict.fromkeys(names, family)


#: PersonalClaw's own tools, by name: the native built-ins, the native runtime's discovery tools
#: and the tool server an agent CLI is handed.
CORE_FAMILIES: Mapping[str, Family] = MappingProxyType(
    {
        **_all(_READ, "read"),
        **_all(_EDIT, "edit"),
        **_all(_COMMAND, "command"),
        **_all(_SEARCH, "search"),
        **_all(_DELEGATE, "delegate"),
        **_all(_SEND, "send"),
        **_all(_OTHER, "other"),
    }
)

#: An agent CLI's own tool, by the kind its runtime declares for the call (ACP's ``ToolKind``).
KIND_FAMILIES: Mapping[str, Family] = MappingProxyType(
    {
        "read": "read",
        "edit": "edit",
        "delete": "edit",
        "move": "edit",
        "execute": "command",
        "search": "search",
        "fetch": "fetch",
    }
)

#: Each family's past tense: the verb, and what it counts, one and many.
PHRASES: Mapping[Family, tuple[str, str, str]] = MappingProxyType(
    {
        "read": ("read", "file", "files"),
        "edit": ("edited", "file", "files"),
        "command": ("ran", "command", "commands"),
        "search": ("searched", "time", "times"),
        "fetch": ("fetched", "page", "pages"),
        "browse": ("browsed", "page", "pages"),
        "delegate": ("started", "helper", "helpers"),
        "send": ("sent", "message", "messages"),
        "other": ("used", "tool", "tools"),
    }
)


def family_of(tool: str, tool_kind: str, declared: object, tool_input: object) -> Family:
    """The family of a call to *tool*, of kind *tool_kind*, whose tool declares *declared*.

    *tool_input* is not read: a call's arguments can name any tool, and its family is only ever
    the words that describe it."""
    from personalclaw.acp.mcp_servers import core_tool_titled
    from personalclaw.task_modes import is_shell_invocation

    own = core_tool_titled(tool) or tool
    if own in CORE_FAMILIES:
        return CORE_FAMILIES[own]
    if is_shell_invocation(tool, tool_kind, declared):
        return "command"
    return KIND_FAMILIES.get((tool_kind or "").lower(), "other")


def phrase(family: Family, count: int) -> str:
    """What *count* calls of *family* did, in the past tense: ``ran 8 commands``."""
    verb, one, many = PHRASES[family]
    return f"{verb} {count} {one if count == 1 else many}"


__all__ = ["CORE_FAMILIES", "KIND_FAMILIES", "PHRASES", "family_of", "phrase"]
