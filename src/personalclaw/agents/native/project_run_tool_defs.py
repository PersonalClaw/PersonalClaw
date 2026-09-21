"""The autonomous project-RUN tool schemas, kept out of :mod:`builtin_tools`.

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


def project_run_tool_definitions(provider: str, s: dict[str, Any]) -> list[ToolDefinition]:
    """Create + launch + watch an autonomous project run (any kind).

    A Project is the uber work-unit; a loop is one KIND of project-scoped run. ONE cohesive
    ``project_run_*`` set operates them all (``kind`` selects code SDLC vs
    goal/general/design/research), matching the unified Project+Loop architecture. In
    ``builtin_tools``' order.
    """
    return [
        ToolDefinition(
            name="project_run_create",
            provider=provider,
            requires_approval=True,
            risk_level=RiskLevel.CAUTION,
            description=(
                "Create a project RUN — an autonomous, multi-cycle execution (a 'loop') — from a "  # noqa: E501
                "plan you shaped with the user. USE WHEN the user wants substantial over-many-cycles "  # noqa: E501
                "work rather than a one-shot chat answer. The `kind` selects the engine: 'code' "  # noqa: E501
                "(SDLC plan→execute in a codebase — feature/refactor/bugfix, gated stages, its own "  # noqa: E501
                "workspace + tasks), 'goal' (open-ended research-or-action toward an outcome — "
                "investigate/monitor/drive to done), 'research' (deep web research → a synthesized "  # noqa: E501
                "report), 'design' (a design system — tokens/components/exports), or 'general' (a "  # noqa: E501
                "generic iterative task). Offer it, then create on the user's go. Does NOT start it "  # noqa: E501
                "— call project_run_start on their go. (To create a plain task CONTAINER instead, "  # noqa: E501
                "use project_create.) Args: kind (required), task (str, required, 12+ chars — the "  # noqa: E501
                "goal/work), name?, project_id? (bind under an existing Project container), attended?, "  # noqa: E501
                "max_cycles?, success_criteria?. kind 'code': project_kind? (greenfield|brownfield), "  # noqa: E501
                "entry_stage?, workspace_dir? (brownfield needs one to start), stage_plan? "
                "([{stage,title,objective,exit_criteria?,tasks?}]), verify_command?, test_command?. "  # noqa: E501
                "kind goal/research/design/general: sub_goals? ([str]), deliverables? ([str]), "
                "scope? ([str]), goal_type? (goal only), rubric? ([str])."
            ),
            parameters={
                **s,
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["code", "goal", "general", "design", "research"],
                    },
                    "task": {"type": "string"},
                    "name": {"type": "string"},
                    "project_id": {"type": "string"},
                    "attended": {"type": "boolean"},
                    "max_cycles": {"type": "integer"},
                    "success_criteria": {"type": "string"},
                    # code-kind
                    "project_kind": {"type": "string"},
                    "entry_stage": {"type": "string"},
                    "workspace_dir": {"type": "string"},
                    "stage_plan": {"type": "array"},
                    "verify_command": {"type": "string"},
                    "test_command": {"type": "string"},
                    # goal/research/design/general
                    "sub_goals": {"type": "array"},
                    "deliverables": {"type": "array"},
                    "scope": {"type": "array"},
                    "goal_type": {"type": "string"},
                    "rubric": {"type": "array"},
                },
                "required": ["kind", "task"],
            },
        ),
        ToolDefinition(
            name="project_run_start",
            provider=provider,
            requires_approval=True,
            risk_level=RiskLevel.CAUTION,
            description="Launch a created project run (any kind), or resume a paused/failed one. Args: project_id (str, required — the run id).",  # noqa: E501
            parameters={
                **s,
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
            },
        ),
        ToolDefinition(
            name="project_run_status",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description=(
                "Read live progress of any project run — status, stage/phase progress, cycles, latest "  # noqa: E501
                "finding, and any blocker / needs-input — to report to the user. Args: project_id (str, required — the run id)."  # noqa: E501
            ),
            parameters={
                **s,
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
            },
        ),
        ToolDefinition(
            name="project_run_list",
            provider=provider,
            requires_approval=False,
            risk_level=RiskLevel.SAFE,
            description=(
                "List the user's project runs (autonomous executions) with kind + live status, to find "  # noqa: E501
                "one to report on or resume. Args: optional kind (filter: code|goal|general|design|research), limit (int)."  # noqa: E501
            ),
            parameters={
                **s,
                "properties": {
                    "kind": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        ),
    ]
