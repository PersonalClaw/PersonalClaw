"""A read-only batch leaf is refused a write in the words its tools are shown by.

The tool handler every in-process tool call funnels through (`mcp_shared.leaf_tool_denial`)
refused a read-only leaf's write in the grant algebra's own vocabulary: "artifact_update is
write-class and the 'leaf_research' profile grants 'read' tools only". A subagent repeats the
reason it was given, and those words, a profile's internal name among them, told the owner nothing
she could act on. It now refuses in the sentence a read-only subagent's own tier uses
(`guardrails.policy.granted_call_refusal`): its tools are read-only, and that tool is not one of
them.
"""

from __future__ import annotations

import pytest

from personalclaw import mcp_shared
from personalclaw.guardrails.policy import READ_ONLY_REASON


@pytest.fixture
def leaf():
    """The lineage of a read-only leaf of a batch, as its spawn binds it."""

    def _bind(*, read_only: bool) -> None:
        lineage = {"__wf_depth": "1", "__wf_run_id": "run-1", "__wf_node_id": "find_0"}
        if read_only:
            lineage[mcp_shared.LEAF_READ_ONLY_KEY] = "1"
        tokens.append(mcp_shared.bind_leaf_lineage(lineage))

    tokens: list = []
    yield _bind
    for token in reversed(tokens):
        mcp_shared.reset_leaf_lineage(token)


def test_a_read_only_leafs_write_is_refused_as_outside_its_read_only_tools(leaf):
    leaf(read_only=True)

    why = mcp_shared.leaf_tool_denial("artifact_update")

    assert why.startswith(READ_ONLY_REASON.format(tool="artifact_update")), why
    assert "write-class" not in why and "leaf_research" not in why, why


def test_its_reads_still_run(leaf):
    leaf(read_only=True)

    assert mcp_shared.leaf_tool_denial("memory_list") == ""


def test_a_leaf_that_may_write_is_not_refused(leaf):
    leaf(read_only=False)

    assert mcp_shared.leaf_tool_denial("artifact_update") == ""
