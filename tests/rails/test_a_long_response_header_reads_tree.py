"""The source-tree check split from ``tests/test_a_long_response_header_reads.py``.

It runs once per CI run, in the rails job. The module named above says what it holds to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import personalclaw

PACKAGE = Path(personalclaw.__file__).parent


def test_every_aiohttp_session_takes_the_header_limits():
    """A session made without both limits at ``RESPONSE_HEADER_LINE_MAX`` refuses GitHub's header
    again."""
    sessions: list[str] = []
    missing: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "ClientSession(" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            func = node.func if isinstance(node, ast.Call) else None
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "ClientSession"
                and isinstance(func.value, ast.Name)
                and func.value.id == "aiohttp"
            ):
                continue
            where = f"{path.relative_to(PACKAGE)}:{node.lineno}"
            sessions.append(where)
            named = {k.arg: ast.get_source_segment(source, k.value) or "" for k in node.keywords}
            if any(
                "RESPONSE_HEADER_LINE_MAX" not in named.get(limit, "")
                for limit in ("max_line_size", "max_field_size")
            ):
                missing.append(where)
    assert len(sessions) >= 6, f"found only {sessions}: this check would pass vacuously"
    assert (
        missing == []
    ), f"aiohttp sessions without both limits at RESPONSE_HEADER_LINE_MAX: {missing}"
