"""The reader that holds a reply to its turn's record imports nothing of the gateway (#6057).

`turn_claims` reads a repository's index through the git its caller hands in
(`dashboard.chat_claims.GIT`, `net.git`'s own). An import of `net.git` in it, though only inside a
function, made it the gateway's biggest import cycle's 277th unit, one more than 0.4.1 shipped,
and a release's cycle may only shrink (`scripts/generate_structural_baseline.py`).
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
from rails_repo import REPO

from scripts import generate_structural_baseline as gen

READER = Path("src") / "personalclaw" / "turn_claims.py"


def core_imports(tree: ast.Module) -> list[str]:
    """Each import of ``personalclaw`` or a module under it in *tree*, in a function too."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            continue
        found += [f"{node.lineno} imports {n}" for n in names if n.split(".")[0] == "personalclaw"]
    return found


def test_the_claims_reader_imports_nothing_of_the_gateway():
    """🔴 Red before: `_git_names` imported `net.git` for its read of the index."""
    assert core_imports(source_index.tree(REPO / READER)) == []


def test_the_claims_reader_is_no_member_of_the_biggest_import_cycle():
    members = gen.scan_import_cycle().rows["members"]
    assert "dashboard" in members, "the cycle this rail reads is gone: it would pass vacuously"
    assert "turn_claims" not in members


def test_an_import_inside_a_function_is_named():
    planted = "import re\n\ndef read():\n    from personalclaw.net.git import git_argv\n"
    assert core_imports(ast.parse(planted)) == ["4 imports personalclaw.net.git"]
