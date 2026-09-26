"""The task/project-container tool schemas, kept out of :mod:`builtin_tools`.

Extracted from :mod:`builtin_tools` for a STRUCTURAL reason, not a stylistic one, and in
the shape :mod:`personalclaw.agents.native.decision_tool_defs` already established (#3253).
``tests/test_structural_baseline.py::test_the_watch_band_is_not_sitting_on_a_cliff`` demands
>=100 lines of headroom below the 2800-line watch band, and ``builtin_tools.py`` at 2,667
lines held the controlling position with only 33 usable lines left — so an unrelated +34
reddened the rail. The band is NOT the thing to move; the file is.

Nothing about the tools changed. This module owns their SCHEMAS only: the category mapping
(``_CATEGORY_OF``), the ``_t_*`` dispatch methods and the call-site rails all stay in
``builtin_tools``, and ``_all_tool_defs`` splices these definitions back in at the same
position, so the advertised order is unchanged.
"""

from __future__ import annotations

from typing import Any

from personalclaw.tool_providers.base import RiskLevel, ToolDefinition


def _max_task_page() -> int:
    """The page ceiling, read from the module that OWNS it.

    Same reasoning as :func:`knowledge_tool_defs._structural_verbs`: writing 500 into the
    schema would let the advertised bound drift from the one ``_t_task_list`` actually clamps
    to, which is how a model comes to be told a limit is legal that the handler silently
    narrows.
    """
    from personalclaw.tasks.registry import MAX_TASK_PAGE

    return MAX_TASK_PAGE


def _exit_criteria() -> dict[str, Any]:
    """The criterion shape ``tasks.models.normalize_exit_criterion`` reads. Declared, not left
    as a bare ``object``: a nested object with no properties has no portable schema, and a
    strict provider rejects the whole request over it (``tool_providers.portable_schema``)."""
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"description": {"type": "string"}, "met": {"type": "boolean"}},
            "required": ["description"],
        },
    }


def _action_plan() -> dict[str, Any]:
    """The step shape ``tasks.models.normalize_action_plan_item`` reads, in order."""
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"content": {"type": "string"}, "completed": {"type": "boolean"}},
            "required": ["content"],
        },
    }


def task_tool_definitions(provider: str, s: dict[str, Any]) -> list[ToolDefinition]:
    """The nine Project -> TaskList -> Task container tools, in ``builtin_tools``' order."""
    return [
        ToolDefinition(
            name="task_create",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Create a task in the user's task system. Args: title (str, required), "
                "optional description (str), priority ('critical'|'high'|'medium'|'low'|"
                "'trivial', default medium), task_list_id (str — place it in a task list; "
                "the task's project label is derived from the list), labels (list of str), "
                "due (str ISO date), exit_criteria (list of {description, met?}), "
                "action_plan (list of {content, completed?} in order), depends_on (list of "
                "task ids that must finish first). Cycles are rejected."
            ),
            parameters={
                **s,
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "trivial"],
                    },
                    "task_list_id": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "due": {"type": "string"},
                    "exit_criteria": _exit_criteria(),
                    "action_plan": _action_plan(),
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title"],
            },
        ),
        ToolDefinition(
            name="task_list",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description=(
                "List tasks, most-recent first. Args: optional status "
                "('open'|'in_progress'|'blocked'|'done'|'cancelled'), project (str label), "
                "task_list_id (str), limit (int, 1 to "
                f"{_max_task_page()}, default 25 — there is no 'everything' value; "
                "omit it or raise it)."
            ),
            parameters={
                **s,
                "properties": {
                    "status": {"type": "string"},
                    "project": {"type": "string"},
                    "task_list_id": {"type": "string"},
                    # A model reads the schema, not the handler — an unconstrained
                    # `integer` is what invited `limit: -1` (#2984).
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": _max_task_page(),
                    },
                },
            },
        ),
        ToolDefinition(
            name="task_get",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description="Fetch one task by id (full detail incl. exit criteria, plan, deps). Args: id (str).",  # noqa: E501
            parameters={**s, "properties": {"id": {"type": "string"}}, "required": ["id"]},
        ),
        ToolDefinition(
            name="task_update",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Update a task. Args: id (str, required), and any of title, description, "
                "status ('open'|'in_progress'|'blocked'|'done'|'cancelled' — 'done' is "
                "rejected while exit criteria are incomplete), priority, task_list_id, "
                "labels, due, exit_criteria, action_plan, depends_on. The 'project' label "
                "is derived from the task list and cannot be set directly."
            ),
            parameters={
                **s,
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string"},
                    "priority": {"type": "string"},
                    "task_list_id": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "due": {"type": "string"},
                    "exit_criteria": _exit_criteria(),
                    "action_plan": _action_plan(),
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id"],
            },
        ),
        ToolDefinition(
            name="task_ready",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description=(
                "List tasks that can be started now (no unfinished prerequisites), "
                "optionally scoped. Args: optional project (str), task_list_id (str)."
            ),
            parameters={
                **s,
                "properties": {
                    "project": {"type": "string"},
                    "task_list_id": {"type": "string"},
                },
            },
        ),
        ToolDefinition(
            name="task_search",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description=(
                "Search tasks by text + filters. Args: optional query (str over title+"
                "description), status (list), priority (list), tags (list), project (str), "
                "sort_by ('relevance'|'created_at'|'updated_at'|'priority'), limit (int)."
            ),
            parameters={
                **s,
                "properties": {
                    "query": {"type": "string"},
                    "status": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "project": {"type": "string"},
                    "sort_by": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        ),
        ToolDefinition(
            name="project_create",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Create a project (a scoping container for task lists). Args: name (str, "
                "required, unique), optional agent_instructions_template (str)."
            ),
            parameters={
                **s,
                "properties": {
                    "name": {"type": "string"},
                    "agent_instructions_template": {"type": "string"},
                },
                "required": ["name"],
            },
        ),
        ToolDefinition(
            name="project_list",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description="List projects (with their task lists). No args.",
            parameters={**s, "properties": {}},
        ),
        ToolDefinition(
            name="task_list_create",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Create a task list inside a project. Args: name (str, required), optional "
                "project_id (str) or project_name (str, find-or-create); repeatable (bool — "
                "place under the Repeatable project). With no project it lands in 'Chore'."
            ),
            parameters={
                **s,
                "properties": {
                    "name": {"type": "string"},
                    "project_id": {"type": "string"},
                    "project_name": {"type": "string"},
                    "repeatable": {"type": "boolean"},
                },
                "required": ["name"],
            },
        ),
    ]
