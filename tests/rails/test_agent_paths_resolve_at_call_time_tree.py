"""The source-tree checks split from
``tests/test_agent_paths_resolve_at_call_time.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index


def test_the_dead_mcp_json_constant_is_gone() -> None:
    """``_USER_MCP_JSON`` had the same defect and **zero readers**.

    The issue asks for it to be resolved lazily too, on the ground that fixing only
    ``AGENTS_DIR`` leaves ``mcp.json`` exposed. Nothing in ``src/``, ``tests/``,
    ``harness/``, ``web/`` or ``scripts/`` ever read it, and the path it named is separately
    read as ``<home>/mcp.json`` inside ``rebuild_agent_config``. So it is deleted rather
    than made lazy — a constant that does not exist cannot be exposed, and a lazy resolver with
    no callers would be the inert-surface shape this repo has a ratchet for.
    """
    import personalclaw.agent as agent

    assert not hasattr(agent, "_USER_MCP_JSON")
    src = Path(agent.__file__).resolve().parents[1]
    offenders = [
        f"{p.relative_to(src)}"
        for p in source_index.package_files()
        if "_USER_MCP_JSON" in source_index.text(p)
    ]
    assert not offenders, f"_USER_MCP_JSON came back: {offenders}"
