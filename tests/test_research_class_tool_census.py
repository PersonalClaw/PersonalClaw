"""Every tool PersonalClaw ships is classified as a write or a read, and the read list is pinned.

`is_write_tool` decides whether a research-class leaf or an unattended research SUBAGENT may
call a tool. It was a substring match over CRUD verb fragments, and the comment above it
claimed "a newly-added write tool is denied by default" — which is what an allowlist of READS
would give you. A denylist of verb fragments gives the opposite: a tool whose name contains
none of them is allowed.

Measured across the seven shipped registries before the fix: **59 of 70 tools classified as
non-writes**. The learning store #1775 reports is the visible part; the rest included
`hook_register` (creates an external webhook ingress), `set_recurring_task` (creates a cron
that fires unattended), `loop_nudge_stop`, `notify`, `notify_attachment`, `artifact_save`,
`automation_run` and every `workflow_*` run-control verb. `subagent_run` and `best_of_n` were
in neither list, so a read-only spawn could fan out.

**Why this file is a census and not a list of examples.** The defect was not a wrong verdict on
a tool someone thought about; it was a whole surface nobody enumerated. So the rail enumerates
the live registries and pins the READ-ONLY set. Adding a tool to any registry without
classifying it fails here, and the failure names the tool — which is the only mechanism that
makes "denied by default" true for the tools we ship.

The classification of each write was taken from the tool's own registry DESCRIPTION, not from
its name, and that mattered in both directions: `refiner_evidence` says "Read-only: this is the
ONLY evidence…" and `suggest_template` says "it never saves anything", so two names that read
like writers are not; while `notify`, `hook_register` and `loop_nudge_stop` carry no CRUD verb
at all.
"""

from __future__ import annotations

import importlib

import pytest

from personalclaw.workflows.batch_compile import MUTATING_TOOLS, is_write_tool

#: The in-process MCP registries whose tools a leaf/subagent can reach.
_REGISTRIES = (
    "mcp_core",
    "mcp_workflows",
    "mcp_artifacts",
    "mcp_prompts",
    "mcp_subagents",
    "mcp_memory",
    "mcp_automation",
)

#: Every shipped tool that is genuinely READ-ONLY, so a research leaf keeps it.
#:
#: 🔴 This is the ratchet. A new tool must be added here or to `MUTATING_TOOLS`, and choosing
#: is the point — the old mechanism let a writer land with no decision made about it.
#:
#: The five PROPOSE tools are here deliberately, and #1775 asks for three of them the other
#: way. A proposal is a row the owner accepts or dismisses, not an unconsented change, and
#: propose-only is an established posture rather than an oversight: the shipped
#: `refine-template` workflow's stage is a RESEARCH leaf whose agent holds
#: `propose_template_diff`, and `leaf_tool_posture` is called there with no `declared` list —
#: so classifying a proposal as a write takes the refiner's only writing tool away and breaks a
#: shipped workflow. What remains is that a research run can fill a triage queue, which the
#: owner sees and dismisses.
_EXPECTED_READ_ONLY: frozenset[str] = frozenset(
    {
        # Reads
        "artifact_get",
        "artifact_list",
        "artifact_versions",
        "automation_history",
        "automation_list",
        "document_formats",
        "get_context",
        "memory_list",
        "memory_recall",
        "prompt_render",
        "refiner_evidence",  # "Read-only: this is the ONLY evidence…"
        "skill_invoke",
        "skill_resource",
        "skill_search",
        "subagent_list",
        "subagent_status",
        "wait",
        "workflow_audit",
        "workflow_get_def",
        "workflow_list_defs",
        "workflow_manifest",
        "workflow_observe",  # "Read-only."
        "workflow_output",
        "workflow_status",
        # Renders inline, persists nothing ("the agency-free two-step")
        "visualize",
        # Proposals the owner triages — see the note above
        "dashboard_tile_propose",
        "project_context_review",
        "propose_template_diff",
        "skill_promote",
        "suggest_template",  # "it never saves anything"
        "template_save_from_session",  # "never writes a definition"
    }
)


def _shipped_tools() -> dict[str, str]:
    """Every tool name the in-process registries expose, mapped to its module."""
    out: dict[str, str] = {}
    for mod_name in _REGISTRIES:
        mod = importlib.import_module(f"personalclaw.{mod_name}")
        for tool in mod._list_tools():
            name = tool.name if hasattr(tool, "name") else tool.get("name")
            if name:
                out[str(name)] = mod_name
    return out


def test_the_census_is_not_vacuous():
    """The floor. An empty registry walk would make every assertion below pass on nothing."""
    tools = _shipped_tools()
    assert len(tools) >= 60, f"only {len(tools)} tools found — the registry walk is broken"
    assert len(_REGISTRIES) == len({*_REGISTRIES})


def test_every_shipped_tool_is_classified():
    """The ratchet. A tool that is neither a known read nor classified as a write is a tool
    nobody decided about, which is exactly how 59 writers ended up allowed."""
    tools = _shipped_tools()
    unclassified = sorted(
        name for name in tools if name not in _EXPECTED_READ_ONLY and not is_write_tool(name)
    )
    assert not unclassified, (
        "these shipped tools are classified as neither a write nor a known read — add each to "
        "`MUTATING_TOOLS` (it mutates, reaches outward or spawns) or to `_EXPECTED_READ_ONLY` "
        f"(it does not): {unclassified}"
    )


def test_no_expected_read_is_actually_classified_as_a_write():
    """The other side of the ratchet: the read list must not silently disagree with the
    classifier, or a rename would leave a stale entry claiming coverage it no longer has."""
    contradictions = sorted(n for n in _EXPECTED_READ_ONLY if is_write_tool(n))
    assert not contradictions, f"listed as read-only but classified as writes: {contradictions}"


def test_the_read_only_list_has_no_stale_entries():
    """A name that no registry exposes any more is a row that pins nothing."""
    shipped = set(_shipped_tools())
    stale = sorted(_EXPECTED_READ_ONLY - shipped)
    assert not stale, f"no registry exposes these any more — drop them: {stale}"


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
    """#1775's headline, asserted at the enforcement seam rather than on the set. Every one of
    these was allowed: none contains a CRUD verb fragment, so the marker list never fired."""
    assert read_only_leaf.leaf_tool_denial(tool), f"{tool} reached a read-only leaf"


@pytest.mark.parametrize(
    "tool",
    [
        "hook_register",
        "set_recurring_task",
        "set_onetime_task",
        "automation_run",
        "loop_nudge_stop",
        "notify",
        "notify_attachment",
        "artifact_save",
        "workflow_cancel",
    ],
)
def test_the_seam_denies_the_rest_of_the_surface_too(tool, read_only_leaf):
    """The part #1775 does not list. `hook_register` opens an external ingress and
    `set_recurring_task` creates a cron — both from a class documented as read-only."""
    assert read_only_leaf.leaf_tool_denial(tool), f"{tool} reached a read-only leaf"


@pytest.mark.parametrize("tool", ["subagent_run", "best_of_n"])
def test_the_seam_denies_fan_out_to_a_read_only_leaf(tool, read_only_leaf):
    """`best_of_n` samples N candidates in parallel and was in neither list, so a read-only
    leaf could fan out — the exact thing `ORCHESTRATION_TOOLS` exists to prevent."""
    assert read_only_leaf.leaf_tool_denial(tool), f"{tool} reached a read-only leaf"


@pytest.mark.parametrize(
    "tool", ["memory_recall", "skill_search", "workflow_status", "artifact_get", "wait"]
)
def test_a_read_only_leaf_can_still_read(tool, read_only_leaf):
    """The vacuity floor, and the actual cost of over-blocking: a research leaf that cannot
    read cannot research. Without this the fix could 'pass' by denying everything."""
    assert read_only_leaf.leaf_tool_denial(tool) == ""


def test_the_refiner_keeps_the_only_tool_it_writes_with(read_only_leaf):
    """The shipped `refine-template` stage is a research leaf and `leaf_tool_posture` is called
    there with no `declared` list, so this is what stands between the classification and a
    broken workflow."""
    from personalclaw.learning import refiner_tools

    for tool in refiner_tools.REFINER_TOOL_NAMES:
        assert read_only_leaf.leaf_tool_denial(tool) == "", f"the refiner lost {tool}"


def test_a_decorated_permission_title_still_matches():
    """`subagent.py` passes `event.title` from a permission request, not a bare tool name, and
    `task_modes` has to strip a `"running: "` prefix for the same reason. An exact-match set
    would have been silently useless at that call site."""
    assert is_write_tool("running: memory_remember")
    assert is_write_tool("Tool: hook_register (register a webhook)")
    assert not is_write_tool("running: memory_recall")


def test_the_open_tool_universe_still_falls_back_to_the_verb_markers():
    """An app's MCP server contributes names nobody here has seen, so the marker guess has to
    stay — naming our own tools is an addition to it, not a replacement. This is also why the
    fix is not an inversion to a read-only allowlist: that would deny every external read
    tool, which is a product decision rather than a bug fix."""
    assert is_write_tool("acme_write_ledger")
    assert is_write_tool("some_future_delete_thing")
    assert not is_write_tool("acme_fetch_ledger")
    assert MUTATING_TOOLS, "a vacuous set would make the census tests pass on nothing"
