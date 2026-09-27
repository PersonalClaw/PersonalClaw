"""Tool risk taxonomy — the effective-risk resolver and what a tool's declaration means.

`risk_level` is what a tool DECLARES a call does (safe/caution/destructive), and ``safe`` is the
one read-only declaration. Two pure functions carry the contract the approval gates and the UI
depend on:

- ``resolve_effective_risk`` — the PER-INVOCATION risk of one call: the declaration, except that
  a shell call's command decides (a DESTRUCTIVE ``bash`` running ``cat`` is effectively safe), and
  a call that declares nothing floors at caution so Trust reads can never auto-approve it.
- ``risk_from_annotations`` — what an MCP-shaped tool's ``annotations`` declare, with the
  read-only label counting only from a trusted source.

These are security-relevant (Trust reads and ``--approval reads`` auto-approve EFFECTIVE-SAFE), so
the classification is pinned here against regression — including that a tool's NAME never
counts.
"""

from __future__ import annotations

import pytest

from personalclaw.task_modes import resolve_effective_risk
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, risk_from_annotations

# ── resolve_effective_risk: per-invocation risk ──
# (declared, title, tool_kind, tool_input, expected)
_RESOLVE_CASES = [
    # 1. the platform shell: a read-only command downgrades to safe, whatever bash declares
    ("destructive", "bash", "", {"command": "cat foo"}, "safe"),
    ("destructive", "bash", "", {"command": "grep x f | wc -l"}, "safe"),
    ("destructive", "bash", "", {"command": "ls -la"}, "safe"),
    # ...but a writing/side-effecting command keeps the declaration
    ("destructive", "bash", "", {"command": "rm -rf x"}, "destructive"),
    ("destructive", "bash", "", {"command": "printf x > f"}, "destructive"),
    # a shell call with no readable command falls back to the declaration
    ("destructive", "bash", "", {}, "destructive"),
    # 2. anything else: its declaration
    ("safe", "read_file", "", {"path": "x"}, "safe"),
    ("caution", "write_file", "", {"path": "x"}, "caution"),
    ("destructive", "artifact_delete", "", {"slug": "x"}, "destructive"),
    ("caution", "memory_remember", "", {"rule": "x"}, "caution"),
    # 3. a call that declares nothing is CAUTION — never safe, whatever its name or ACP kind says
    ("", "mcp/x/search_things", "search", {"q": "x"}, "caution"),
    ("", "mcp/x/get_thing", "read", {"id": "1"}, "caution"),
    ("", "Read File", "read", {}, "caution"),
    ("", "Task", "think", {}, "caution"),
    ("", "mcp/x/delete_thing", "", {"id": "1"}, "caution"),
    ("", "weird_external_tool", "", {}, "caution"),
    (None, "some_writer", "", {"x": 1}, "caution"),
    # an ACP CLI's shell call: the command text decides
    ("", "Terminal", "execute", {"command": "ls"}, "safe"),
    ("", "Terminal", "execute", {"command": "rm x"}, "destructive"),
    # a declared tool that is not the platform shell is not a shell call, whatever it is named
    ("caution", "run_script", "", {"command": "ls"}, "caution"),
]


@pytest.mark.parametrize("declared,title,kind,tool_input,expected", _RESOLVE_CASES)
def test_resolve_effective_risk(declared, title, kind, tool_input, expected):
    assert resolve_effective_risk(declared, title, kind, tool_input) == expected


def test_resolve_accepts_risklevel_enum():
    """``declared`` may be a RiskLevel enum (from the runtime's _tool_risk map) or
    its bare string value (from an event field) — both resolve identically."""
    assert resolve_effective_risk(RiskLevel.DESTRUCTIVE, "artifact_delete", "", {}) == "destructive"
    assert resolve_effective_risk(RiskLevel.SAFE, "read_file", "", {}) == "safe"
    # enum + read-only bash still downgrades
    assert resolve_effective_risk(RiskLevel.DESTRUCTIVE, "bash", "", {"command": "cat x"}) == "safe"


def test_an_unknown_level_is_no_declaration():
    """A level no build defines is not a declaration — least of all a read."""
    assert resolve_effective_risk("harmless", "x", "", {}) == "caution"


def test_a_tool_that_declares_nothing_is_a_change():
    """The allowlist's default: a ToolDefinition built without a risk level is CAUTION."""
    assert ToolDefinition(name="t", description="").risk_level is RiskLevel.CAUTION


# ── risk_from_annotations: what an MCP-shaped declaration means ──
# (annotations, trusted, expected)
_ANNOTATION_CASES = [
    ({"readOnlyHint": True}, True, RiskLevel.SAFE),
    # an untrusted server's read-only label is a claim, not a declaration
    ({"readOnlyHint": True}, False, RiskLevel.CAUTION),
    # a destructive label is believed from anyone: it only adds a question
    ({"readOnlyHint": False, "destructiveHint": True}, False, RiskLevel.DESTRUCTIVE),
    ({"destructiveHint": True}, True, RiskLevel.DESTRUCTIVE),
    # the spec: destructiveHint means nothing when the tool says it only reads
    ({"readOnlyHint": True, "destructiveHint": True}, False, RiskLevel.CAUTION),
    ({"readOnlyHint": False}, True, RiskLevel.CAUTION),
    ({}, True, RiskLevel.CAUTION),
    (None, True, RiskLevel.CAUTION),
    ("readOnly", True, RiskLevel.CAUTION),
    # only a literal true counts
    ({"readOnlyHint": "true"}, True, RiskLevel.CAUTION),
]


@pytest.mark.parametrize("annotations,trusted,expected", _ANNOTATION_CASES)
def test_risk_from_annotations(annotations, trusted, expected):
    assert risk_from_annotations(annotations, trusted=trusted) is expected
