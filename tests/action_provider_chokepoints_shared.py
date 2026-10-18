"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_action_provider_chokepoints.py``

Its checks:
``tests/rails/test_action_provider_chokepoints_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import inspect

#: Every module that resolves an action provider and RUNS it. Discovered by grepping
#: `get_action_provider(` across `src/`, then reading each hit to see whether it executes.
#: `dashboard/handlers/hooks.py` is excluded on purpose: it resolves providers to build the
#: `/api/action-providers` catalog and never calls `execute`, verified by
#: `test_the_catalog_site_does_not_execute` below.
EXECUTION_SITES: tuple[tuple[str, str], ...] = (
    ("personalclaw.hooks", "the lifecycle-hook fire path"),
    ("personalclaw.gateway", "the clock/file/event/webhook/view trigger fire path"),
    # Your Run now, the restart review's Run now and your answer to a run's question: attended,
    # carrying `manual_refusal`. Every other run this module starts (a webhook's, a view's, a Run
    # now an agent, an app or a program asks for) is a fire, dispatched through the gateway seam
    # (`test_only_your_run_reaches_the_hand_dispatch`).
    ("personalclaw.dashboard.handlers.trigger_runs", "the run-by-hand path"),
    # Approving an inbox proposal whose apply case is `action` dispatches a provider
    # directly (not through `triggers.tools.run`), so it is a real execution site. User-clicked,
    # so it carries `manual_refusal` — the manual Run path's gate — rather than the unattended
    # denylist seam; `test_the_denylist_seam_list_covers_every_unattended_execution_site`
    # therefore lists it beside that documented exemption.
    ("personalclaw.proposals_contract", "the inbox proposal apply path"),
    # A TTL dashboard tile re-runs its bound data nodes with nobody watching, so it is a
    # real UNATTENDED execution site and joins the denylist seams below rather than claiming an
    # exemption. Its providers are additionally narrowed to a read-only allowlist
    # (`tile_refresh.DATA_PROVIDERS`) — a second fence, not a substitute for these gates.
    ("personalclaw.dashboard.tile_refresh", "the chatless tile-refresh path"),
    # "Run now" on a scheduled research report dispatches the report provider
    # directly, so it is a real execution site. User-clicked, so it carries `manual_refusal`
    # — the same gate and the same documented exemption as the trigger Run path above.
    ("personalclaw.dashboard.handlers.research_reports", "the manual report Run path"),
    # The trivial-tier auto-execution dispatches a provider per approved proposal with
    # nobody watching, so it is a real UNATTENDED execution site and joins the denylist seams
    # below rather than claiming an exemption. Its providers are additionally narrowed to a
    # frozen capability set (`autoexec.AUTO_CAPABLE_PROVIDERS`) and its actions bounded by a
    # per-run cap and the budget floor — more fences, not a substitute for these gates.
    ("personalclaw.proactive.autoexec", "the triage auto-execution path"),
    # Every action step of a workflow run, whatever started the run and whatever lookup the
    # engine was handed: a run is unattended work, so it is a denylist seam below. It resolves
    # through a name it is handed (`getter = get_action_provider`), which the census finds by the
    # name, not by a literal call (`test_the_census_sees_a_lookup_under_another_name`).
    ("personalclaw.workflows.engine", "a workflow run's action step"),
)


REVERSAL_SITE = "personalclaw.guardrails.ladder"


#: The unattended dispatch seams, each of which must call `enforce_action`
#: BEFORE it reaches a provider. This is narrower than `EXECUTION_SITES` by exactly one entry —
#: the manual Run path, exempted below — and the two lists are cross-checked by
#: `test_the_denylist_seam_list_covers_every_unattended_execution_site` so a FOURTH unattended
#: seam cannot appear without joining this one.
#:
#: 🔴 Why a rail and not trust: the third seam was written as `gateway.py:701`
#: (`_run_action_job`), which retired with `ScheduleService`. The successor
#: (`_fire_store_trigger`) kept the kill switch and gained the rung ladder but silently lost the
#: denylist — measured at 1 / 1 / 0 `enforce_action` calls across hooks / event_triggers / gateway
#: while gateway is the busiest of the three (every clock, file, webhook and chained trigger).
#: It is restored; this rail is what stops the next retirement dropping it again.
DENYLIST_SEAMS: tuple[tuple[str, str], ...] = (
    ("personalclaw.hooks", "script hooks"),
    ("personalclaw.gateway", "clock / file / webhook / chained / data-event triggers"),
    ("personalclaw.dashboard.tile_refresh", "TTL dashboard tiles"),
    ("personalclaw.proactive.autoexec", "trivial-tier triage auto-execution"),
    ("personalclaw.workflows.engine", "workflow action steps"),
)


#: The run-by-hand dispatch, yours alone. A webhook's outside caller, a view's refresh and an
#: agent's `automation_run` used to reach it with nobody answering: a webhook whose action said
#: `personalclaw stop` stopped the gateway, and an agent's runs were recorded as yours, which the
#: hourly cap and the failure streak pass over. Each of them is a fire now, admitted and dispatched
#: through the gateway seam above, which holds it to the denylist under the automation's own
#: unattended identity; only a run you start yourself reaches this dispatch, attended and gated by
#: `manual_refusal`. Asserted in `test_only_your_run_reaches_the_hand_dispatch`.
MANUAL_SEAM = "personalclaw.dashboard.handlers.trigger_runs"


#: The workflow modules that look a step's provider up only to read it, each with the one thing it
#: reads: whether the provider is registered at all, and whether its action is a model turn.
WORKFLOW_LOOKUPS: dict[str, str] = {
    "personalclaw.workflows.preflight": "is None",
    "personalclaw.workflows.node_bindings": "hands_config_to_a_model",
}


def _source(module_name: str) -> str:
    import importlib

    return inspect.getsource(importlib.import_module(module_name))


def _enforce_action_calls(module_name: str) -> list:
    """Every `enforce_action(...)` CALL node in a module, found via AST.

    AST rather than a substring search because the property under test is a property of the
    CALL — that it passes `session_key=` — and the three seams spell the call across one, four
    and five lines. A regex that happened to match today's formatting would stop seeing the call
    the moment someone reflowed it, and a rail that matches nothing reads exactly like a pass.
    """
    import ast

    tree = ast.parse(_source(module_name))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "enforce_action"
    ]


def _reaches_the_registry(source: str | object) -> bool:
    """Whether *source* reaches the provider registry's lookup at all: calls it, imports it under
    any name, or hands the name on (`getter = get_action_provider`). Read from the syntax tree, so
    an alias is a site and a mention in a comment or a docstring is not.

    A literal-call search misses a module that looks its provider up through a name it is handed,
    as the workflow engine does (`getter = get_action_provider`): `get_action_provider(` never
    appears in it, so such a site would run providers with nothing here asking it for a check.
    """
    import ast

    tree = source if isinstance(source, ast.Module) else ast.parse(str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "get_action_provider":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "get_action_provider":
            return True
        if isinstance(node, ast.ImportFrom) and any(
            alias.name == "get_action_provider" for alias in node.names
        ):
            return True
    return False
