"""The source-tree check split from
``tests/test_a_lesson_has_no_category.py``.

It runs once per CI run, in the rails job. The module named above says what it holds to.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index

import personalclaw
from personalclaw import mcp_memory

BUNDLED = Path(personalclaw.__file__).parent / "skills" / "bundled"


def test_a_bundled_skill_teaches_a_memory_remember_call_the_tool_takes():
    [tool] = [t for t in mcp_memory._list_tools() if t["name"] == "memory_remember"]
    published = set(tool["inputSchema"]["properties"])
    calls = [
        (path.parent.name, call)
        for path in source_index.under(source_index.package_any_files(), BUNDLED, (".md",))
        for call in re.findall(r"memory_remember\(([^)]*)\)", source_index.text(path))
    ]
    assert calls, "no bundled skill teaches a memory_remember call"
    for skill, call in calls:
        named = set(re.findall(r"(\w+)=", call))
        assert named <= published, f"{skill} teaches {sorted(named - published)}"
        if 'scope="workspace"' in call:
            assert "workspace" in named, f"{skill} teaches a workspace lesson with no workspace"
