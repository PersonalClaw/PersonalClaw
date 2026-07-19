"""Tool risk taxonomy — the effective-risk resolver + name-based inference
(docs/plans/ports/tool-risk-taxonomy.md).

`risk_level` is a gradient (safe/caution/destructive) over the old binary approval
flag. Two pure functions carry the contract the approval gate + UI depend on:

- ``resolve_effective_risk`` — the PER-INVOCATION risk of one call (a DESTRUCTIVE
  ``bash`` tool running ``cat`` is effectively safe; an unclassified external tool
  floors at caution so trust-reads can't silently auto-approve it).
- ``infer_risk_from_name`` — the DECLARED risk for a tool that ships none (dict-defined
  MCP tools + external MCP), classified by name verb.

These are security-relevant (trust-reads auto-approves EFFECTIVE-SAFE), so the
classification is pinned here against regression.
"""

from __future__ import annotations

import pytest

from personalclaw.task_modes import infer_risk_from_name, resolve_effective_risk


# ── infer_risk_from_name: declared risk from a bare/prefixed tool name ──
# (name, expected) — destructive verb wins; read verb short-circuits to safe;
# other mutating verb → caution; nothing recognizable → safe (construction default).
_INFER_CASES = [
    # destructive verbs
    ("artifact_delete", "destructive"),
    ("memory_forget", "destructive"),
    ("schedule_remove", "destructive"),
    ("schedule_remove_all", "destructive"),
    ("purge_cache", "destructive"),
    ("drop_table", "destructive"),
    # read verbs short-circuit to safe EVEN when a broad mutating hint also matches
    # (schedule_list contains "schedule" but "list" wins → safe, not caution)
    ("schedule_list", "safe"),
    ("task_get", "safe"),
    ("project_run_status", "safe"),
    ("knowledge_search", "safe"),
    ("read_config", "safe"),
    ("find_thing", "safe"),
    ("inspect_run", "safe"),
    # other mutating verbs → caution
    ("write_file", "caution"),
    ("artifact_save", "caution"),
    ("artifact_update", "caution"),
    ("schedule_add", "caution"),
    ("notify", "caution"),
    ("subagent_run", "caution"),
    ("workflow_run", "caution"),
    ("post_to_inbox", "caution"),
    ("deploy_app", "caution"),
    # mcp/<server>/<tool> prefix is stripped before matching the verb
    ("mcp/GitHub/CreateIssue", "caution"),
    ("mcp/GitHub/GetWorkflowDetails", "safe"),
    ("mcp/foo/delete_widget", "destructive"),
    # no recognizable verb → safe (static construction default)
    ("frobnicate", "safe"),
    ("", "safe"),
]


@pytest.mark.parametrize("name,expected", _INFER_CASES)
def test_infer_risk_from_name(name, expected):
    assert infer_risk_from_name(name) == expected


# ── resolve_effective_risk: per-invocation risk ──
# (declared, title, tool_kind, tool_input, expected)
_RESOLVE_CASES = [
    # 1. read-only bash downgrades to safe regardless of the DESTRUCTIVE declaration
    ("destructive", "bash", "execute", {"command": "cat foo"}, "safe"),
    ("destructive", "bash", "execute", {"command": "grep x f | wc -l"}, "safe"),
    ("destructive", "bash", "execute", {"command": "ls -la"}, "safe"),
    # ...but a writing/side-effecting bash stays destructive
    ("destructive", "bash", "execute", {"command": "rm -rf x"}, "destructive"),
    ("destructive", "bash", "execute", {"command": "printf x > f"}, "destructive"),
    # a bash-like call with no parseable command falls back to the declaration
    ("destructive", "bash", "execute", {}, "destructive"),
    # 2. a declared risk is honored for non-bash tools
    ("safe", "read_file", "read", {"path": "x"}, "safe"),
    ("caution", "write_file", "edit", {"path": "x"}, "caution"),
    ("destructive", "artifact_delete", "", {"slug": "x"}, "destructive"),
    # RiskLevel enum value (not just str) is accepted via .value
    # (covered separately below)
    # 3. no declared risk: positive read-only KIND → safe
    ("", "mcp/x/search_things", "search", {"q": "x"}, "safe"),
    ("", "mcp/x/get_thing", "read", {"id": "1"}, "safe"),
    # 3. no declared risk + no read signal: name inference (destructive/caution),
    #    else FLOOR at caution (never silently safe → trust-reads can't auto-approve)
    ("", "mcp/x/delete_thing", "", {"id": "1"}, "destructive"),
    ("", "mcp/x/create_thing", "", {"id": "1"}, "caution"),
    ("", "mcp/x/frobnicate", "", {"a": 1}, "caution"),   # unknown external → caution floor
    ("", "weird_external_tool", "", {}, "caution"),
    (None, "some_writer", "", {"x": 1}, "caution"),
]


@pytest.mark.parametrize("declared,title,kind,tool_input,expected", _RESOLVE_CASES)
def test_resolve_effective_risk(declared, title, kind, tool_input, expected):
    assert resolve_effective_risk(declared, title, kind, tool_input) == expected


def test_resolve_accepts_risklevel_enum():
    """``declared`` may be a RiskLevel enum (from the runtime's _tool_risk map) or
    its bare string value (from an event field) — both resolve identically."""
    from personalclaw.tool_providers.base import RiskLevel

    assert resolve_effective_risk(RiskLevel.DESTRUCTIVE, "artifact_delete", "", {}) == "destructive"
    assert resolve_effective_risk(RiskLevel.SAFE, "read_file", "read", {}) == "safe"
    # enum + read-only bash still downgrades
    assert resolve_effective_risk(RiskLevel.DESTRUCTIVE, "bash", "execute", {"command": "cat x"}) == "safe"


def test_unknown_external_never_resolves_safe_by_name_absence():
    """The security property: a non-read external tool with no declared risk and a
    neutral name must be CAUTION, not safe — else trust-reads (auto-approves SAFE)
    would silently run an unknown external tool without a card."""
    assert resolve_effective_risk("", "mcp/vendor/do_the_thing", "", {"x": 1}) == "caution"


def test_resolver_agrees_with_inference_for_undeclared_tools():
    """When a tool ships no declared risk, the resolver must not DISAGREE with the
    name-based classifier (the invoked-log vs rejected-log consistency fix): a
    destructive-named tool resolves destructive, not a flat caution."""
    for name in ("mcp/x/delete_all", "purge_everything", "drop_db"):
        assert resolve_effective_risk("", name, "", {}) == "destructive"
        assert infer_risk_from_name(name) == "destructive"
