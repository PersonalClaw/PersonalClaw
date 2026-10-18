"""The source-tree checks split from
``tests/test_mcp_read_only_trust_has_one_read.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from mcp_read_only_trust_has_one_read_shared import (
    _STANDING_READERS,
    OWNER,
    SRC,
    _label_reads,
    _record_reads,
    _standing_reads,
)


def _tree() -> tuple[tuple[Path, str, ast.Module], ...]:
    """Every module of the package with its text and tree, from the suite's one source index."""
    return tuple(
        (path, source_index.text(path), tree) for path, tree in source_index.package_trees()
    )


def test_only_its_own_module_reads_the_record():
    found = [
        hit for path, _text, tree in _tree() if path != OWNER for hit in _record_reads(path, tree)
    ]
    assert not found, (
        "the owner's trust is read only through `mcp_read_only_trust`, whose `believes` checks "
        "the digest:\n  " + "\n  ".join(found)
    )


def test_a_label_becomes_a_read_only_through_the_digest_check():
    found: list[str] = []
    calls = 0
    for path, _text, tree in _tree():
        hits, seen = _label_reads(path, tree)
        found += hits
        calls += seen
    # Vacuity: the gate's own call and PersonalClaw's own tools' calls are in the tree.
    assert calls >= 4, f"only {calls} calls to risk_from_annotations were read — parser drift?"
    assert not found, (
        "a tool's read-only label is taken only from `mcp_client.declared_risk`, which asks "
        "`mcp_read_only_trust.believes`; every other caller is PersonalClaw's own tool and passes "
        "trusted=True:\n  " + "\n  ".join(found)
    )


def test_whether_a_server_is_trusted_at_all_is_read_only_for_words():
    found = [hit for path, _text, tree in _tree() for hit in _standing_reads(path, tree)]
    assert not found, (
        "`holds_trust` says whether the owner trusts a server at all, which is never a tool's "
        "answer; read it only where a refusal or a note is worded:\n  " + "\n  ".join(found)
    )
    used = [path for path, text, _tree_ in _tree() if "holds_trust" in text]
    assert set(used) >= _STANDING_READERS, "the allowed reader no longer reads it — prune the list"


def test_the_server_wide_read_it_replaced_is_gone():
    # The retired key is named where it is dropped and where the retirement is explained.
    retired_at = {SRC / "config" / "validation.py", OWNER}
    stale = [
        f"{path.relative_to(SRC)}"
        for path, text, _tree_ in _tree()
        if "read_only_labels_trusted" in text
        or ("mcp_read_only_servers" in text and path not in retired_at)
    ]
    assert not stale, stale
