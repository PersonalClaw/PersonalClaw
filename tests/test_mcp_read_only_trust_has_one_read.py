"""The owner's trust in an MCP server's read-only labels has one read, and it checks the digest.

The trust covers the tools the owner saw when she gave it, sealed to a digest of each one's
definition (`personalclaw.mcp_read_only_trust`). That holds only while every read that can turn a
label into a read compares the digest: a read of the record that skipped it, or a risk taken of a
label with a trust that was not that answer, would let a tool she never saw run unasked. So:

* the record is read by its own module and nothing else: no other module names its files or reaches
  into what the module keeps;
* a label becomes a risk only through ``risk_from_annotations``, which every caller hands the
  literal ``True`` (PersonalClaw's own tools, which declare themselves) except
  ``mcp_client.declared_risk``, which hands it ``believes(server, tool)``, the digest check, and
  takes no answer from its caller;
* ``holds_trust`` (whether the owner trusts a server at all) is read only for the words that say
  why a tool is not believed;
* the server-wide read this replaced is gone.

Read from the source, so a new read is red the day it is written. Each check is proved to catch the
thing it exists for on a snippet that does it.
"""

from __future__ import annotations

import ast
import inspect

from mcp_read_only_trust_has_one_read_shared import (
    GATE,
    SRC,
    _label_reads,
    _record_reads,
    _standing_reads,
)

# ── the tree ────────────────────────────────────────────────────────────────────────────────────


def test_the_gate_takes_no_answer_from_its_caller():
    from personalclaw.mcp_client import declared_risk

    assert list(inspect.signature(declared_risk).parameters) == ["server", "tool"]


# ── each check catches what it exists for ───────────────────────────────────────────────────────


def _snippet(text: str) -> ast.Module:
    return ast.parse(text)


def test_the_record_check_catches_a_read_beside_the_module():
    tree = _snippet(
        "import json\n"
        "from personalclaw.owner_grants import grants_dir\n"
        "from personalclaw.mcp_read_only_trust import _sealed_tools\n"
        "from personalclaw import mcp_read_only_trust\n"
        "data = json.loads((grants_dir() / 'mcp_read_only.json').read_text())\n"
        "seen = mcp_read_only_trust._servers(mcp_read_only_trust.SEEN_RECORD)\n"
    )
    found = _record_reads(SRC / "elsewhere.py", tree)
    assert len(found) == 4, found


def test_the_label_check_catches_a_trust_that_skips_the_digest():
    tree = _snippet(
        "from personalclaw.mcp_read_only_trust import holds_trust\n"
        "def declared_risk(server, tool):\n"
        "    return risk_from_annotations(tool.annotations, trusted=holds_trust(server))\n"
        "def own(tool):\n"
        "    return risk_from_annotations(tool.annotations, trusted=True)\n"
        "def lenient(tool, trusted):\n"
        "    return risk_from_annotations(tool.annotations, trusted=trusted)\n"
    )
    # Named as the gate's own file, so only the digest check itself is let through there.
    found, seen = _label_reads(GATE, tree)
    assert seen == 3
    assert len(found) == 2, found
    assert _standing_reads(SRC / "elsewhere.py", tree)
