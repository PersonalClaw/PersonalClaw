"""Every tool PersonalClaw ships DECLARES whether it only reads, and the declared reads are pinned.

A research-class leaf, an unattended research SUBAGENT and a read-only room member hold a
``read`` tool grant, which admits a tool that declares it only reads, or that its only effect is
a proposal the owner reviews (``task_modes.read_grant_admits``). It used to be a guess from the
tool's NAME — a substring match over CRUD verb fragments, and then an exact list of our own
writers on top — and a denylist of words passes whatever carries none of them: 59 of 70 shipped
tools before #1775, and still the desktop tools (``computer_click``, ``computer_type``) after it.

**Why this file is a census and not a list of examples.** The defect was never a wrong verdict
on a tool someone thought about; it was a surface nobody enumerated. So the rail walks the live
registries and requires every tool to state its effect in its own definition (the MCP spec's
``annotations.readOnlyHint``, true or false), and pins the READ and PROPOSE sets. A new tool that
says nothing fails here, and the failure names it.

Each declaration was taken from the tool's own description and code, not its name, and that
mattered in both directions: ``refiner_evidence`` says "Read-only: this is the ONLY evidence…",
while ``notify``, ``hook_register``, ``loop_nudge_stop`` and ``computer_click`` carry no
write-shaped word at all.
"""

from __future__ import annotations

import importlib

import pytest

from personalclaw.tool_providers.base import PROPOSES_META_KEY

#: The in-process MCP registries whose tools a leaf/subagent can reach — the modules the
#: ``mcp-core`` server aggregates (``mcp_core._AGGREGATED_CATEGORY_MODULES``) and core itself.
_REGISTRIES = (
    "mcp_core",
    "mcp_workflows",
    "mcp_artifacts",
    "mcp_prompts",
    "mcp_subagents",
    "mcp_memory",
    "mcp_automation",
    "computer_use.tools",
)

#: Every shipped tool that DECLARES it only reads. 🔴 The ratchet: adding a tool here is a claim
#: that a call to it changes nothing, and every "reads run without asking" posture acts on it.
_DECLARED_READS: frozenset[str] = frozenset(
    {
        "artifact_get",
        "artifact_list",
        "artifact_versions",
        "automation_dry_run",
        "automation_history",
        "automation_list",
        "computer_list_apps",
        "computer_snapshot",
        "document_formats",
        "get_context",
        "memory_list",
        "memory_recall",
        "prompt_render",
        "refiner_evidence",
        "skill_invoke",
        "skill_resource",
        "skill_search",
        "subagent_list",
        "subagent_status",
        "triage_rules_list",
        "visualize",
        "wait",
        "workflow_audit",
        "workflow_check",
        "workflow_edit_preview",
        "workflow_get_def",
        "workflow_list_defs",
        "workflow_manifest",
        "workflow_observe",
        "workflow_output",
        "workflow_plan",
        "workflow_status",
    }
)

#: Every shipped tool whose only effect is a proposal the owner accepts or dismisses. Not reads
#: — each writes its proposal, so Ask mode and Trust reads treat it as the change it is — but a
#: research run's ``read`` grant admits them: the shipped ``refine-template`` stage is a research
#: leaf whose agent files through ``propose_template_diff``.
_DECLARED_PROPOSALS: frozenset[str] = frozenset(
    {
        "dashboard_tile_propose",
        "project_context_review",
        "propose_template_diff",
        "skill_promote",
        "template_save_from_session",
    }
)


def _shipped_tools() -> dict[str, dict]:
    """Every tool the in-process registries expose, by name."""
    out: dict[str, dict] = {}
    for mod_name in _REGISTRIES:
        mod = importlib.import_module(f"personalclaw.{mod_name}")
        for tool in mod._list_tools():
            out[str(tool["name"])] = tool
    return out


def test_the_census_is_not_vacuous():
    """The floor. An empty registry walk would make every assertion below pass on nothing."""
    tools = _shipped_tools()
    assert len(tools) >= 70, f"only {len(tools)} tools found — the registry walk is broken"
    assert len(_REGISTRIES) == len({*_REGISTRIES})


def test_every_shipped_tool_declares_whether_it_only_reads():
    """The ratchet. A tool that states nothing is a tool nobody decided about — it would be
    treated as a change, which is safe, but silently; this makes the decision explicit."""
    undeclared = sorted(
        name
        for name, tool in _shipped_tools().items()
        if not isinstance(tool.get("annotations"), dict)
        or not isinstance(tool["annotations"].get("readOnlyHint"), bool)
    )
    assert not undeclared, (
        "these shipped tools do not declare `annotations.readOnlyHint` (true when a call "
        f"changes nothing, false otherwise): {undeclared}"
    )


def test_the_declared_reads_are_exactly_the_pinned_set():
    declared = {
        name for name, tool in _shipped_tools().items() if tool["annotations"]["readOnlyHint"]
    }
    assert declared == _DECLARED_READS, (
        f"declared reads changed — added {sorted(declared - _DECLARED_READS)}, "
        f"removed {sorted(_DECLARED_READS - declared)}"
    )


def test_the_declared_proposals_are_exactly_the_pinned_set():
    declared = {
        name
        for name, tool in _shipped_tools().items()
        if (tool.get("_meta") or {}).get(PROPOSES_META_KEY) is True
    }
    assert declared == _DECLARED_PROPOSALS
    # A proposal is not a read, and a read does not need to be admitted as a proposal.
    assert not declared & _DECLARED_READS


# ── the CALL SITE, not the set (#1775's explicit ask) ─────────────────────────


@pytest.fixture
def read_only_leaf(monkeypatch):
    """A read-only leaf at depth 1 — the posture an unattended research spawn resolves to."""
    from personalclaw import mcp_shared
    from personalclaw.workflows.engine import WF_DEPTH_KEY

    monkeypatch.setenv(WF_DEPTH_KEY, "1")
    monkeypatch.setenv(mcp_shared.LEAF_READ_ONLY_KEY, "1")
    return mcp_shared


@pytest.mark.parametrize(
    "tool",
    ["memory_remember", "memory_forget", "skill_remember", "triage_rules"],
)
def test_the_seam_denies_a_learning_write_to_a_read_only_leaf(tool, read_only_leaf):
    """#1775's headline, asserted at the enforcement seam rather than on the set."""
    assert read_only_leaf.leaf_tool_denial(tool), f"{tool} reached a read-only leaf"


@pytest.mark.parametrize(
    "tool",
    [
        "hook_register",
        "set_recurring_task",
        "set_onetime_task",
        "automation_run",
        "automation_pause",
        "loop_nudge_stop",
        "notify",
        "notify_attachment",
        "artifact_save",
        "workflow_cancel",
        "workflow_resume",
        "workflow_repair",
        "suggest_template",
    ],
)
def test_the_seam_denies_the_rest_of_the_surface_too(tool, read_only_leaf):
    """`hook_register` opens an external ingress and `set_recurring_task` creates a cron — both
    from a class documented as read-only. `workflow_resume` answers another run's gate."""
    assert read_only_leaf.leaf_tool_denial(tool), f"{tool} reached a read-only leaf"


@pytest.mark.parametrize(
    "tool",
    ["computer_click", "computer_type", "computer_scroll", "computer_perform_action"],
)
def test_the_seam_denies_acting_on_the_desktop(tool, read_only_leaf):
    """No write-shaped word in any of these names, so both earlier classifiers passed them to a
    read-only leaf. Each changes the desktop, and says so (`ToolSpec.acts`)."""
    assert read_only_leaf.leaf_tool_denial(tool), f"{tool} reached a read-only leaf"


@pytest.mark.parametrize("tool", ["subagent_run", "best_of_n"])
def test_the_seam_denies_fan_out_to_a_read_only_leaf(tool, read_only_leaf):
    """`best_of_n` samples N candidates in parallel, so a read-only leaf could fan out — the
    exact thing `ORCHESTRATION_TOOLS` exists to prevent."""
    assert read_only_leaf.leaf_tool_denial(tool), f"{tool} reached a read-only leaf"


def test_a_name_the_surface_does_not_serve_is_denied(read_only_leaf):
    """An allowlist: a tool that declares nothing — here, one no registry defines — is a change
    as far as a `read` grant is concerned, whatever its name suggests."""
    assert read_only_leaf.leaf_tool_denial("acme_fetch_ledger")


@pytest.mark.parametrize(
    "tool", ["memory_recall", "skill_search", "workflow_status", "artifact_get", "wait"]
)
def test_a_read_only_leaf_can_still_read(tool, read_only_leaf):
    """The vacuity floor, and the actual cost of over-blocking: a research leaf that cannot
    read cannot research. Without this the fix could 'pass' by denying everything."""
    assert read_only_leaf.leaf_tool_denial(tool) == ""


def test_the_refiner_keeps_the_only_tool_it_writes_with(read_only_leaf):
    """The shipped `refine-template` stage is a research leaf; its agent files through a
    proposal tool, which the `read` grant admits because the tool declares it only proposes."""
    from personalclaw.learning import refiner_tools

    for tool in refiner_tools.REFINER_TOOL_NAMES:
        assert read_only_leaf.leaf_tool_denial(tool) == "", f"the refiner lost {tool}"
