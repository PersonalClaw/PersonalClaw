"""Every `mcp.json` resolver honours `PERSONALCLAW_HOME` — all three of them.

The dashboard's `_canonical_mcp_json()` was fixed to use `config_dir()`, and its comment says
the old `Path.home()` hardcode "ignored it". Three siblings kept the bug:

* `mcp_client._personalclaw_mcp_specs` — **the store the native agent loop spawns from**
* `mcp_discovery._MCP_SOURCES` — a module-level tuple, so frozen at IMPORT time
* `dashboard/handlers/mcp._PERSONALCLAW_MCP_JSON` — likewise a module-level constant

What that cost, concretely: under `PERSONALCLAW_HOME=./.dev-home` the dashboard wrote
`.dev-home/mcp.json` while the reader that actually spawns servers read the operator's REAL
`~/.personalclaw/mcp.json`. So a dev session loaded the operator's live MCP servers **and their
credentials**, and every dev-side edit looked like it did nothing.

These tests assert the CALL SITES agree, not that a helper exists — and the import-time half
matters most: a constant computed before the env var is set cannot be fixed by setting it later,
which is why the two former constants are now functions.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """A PERSONALCLAW_HOME that is emphatically not the real one."""
    home = tmp_path / "dev-home"
    home.mkdir()
    # `config_dir()` reads the env var on every call and caches nothing (loader.py:222-232),
    # so setting it is the whole fixture — no cache to invalidate.
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _resolvers():
    """The three paths, each read the way its own module reads it."""
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.handlers.mcp import _canonical_mcp_json
    from personalclaw.mcp_discovery import _mcp_json_paths

    return {
        "config_dir": config_dir() / "mcp.json",
        "handlers.mcp": _canonical_mcp_json(),
        "mcp_discovery": _mcp_json_paths()[0],
    }


def test_all_three_resolvers_agree_on_the_isolated_home(isolated_home):
    """One store, one path. A divergence here is a dev session reading real credentials."""
    paths = _resolvers()
    expected = isolated_home / "mcp.json"
    for name, path in paths.items():
        assert path == expected, f"{name} resolved {path}, not the isolated home's {expected}"


def test_the_native_client_reads_the_isolated_store_not_the_real_one(isolated_home):
    """The one that mattered most: `mcp_client` is what spawns the servers.

    Asserted by CONTENT rather than by path, because the failure mode was silent: the client
    happily returned the operator's real servers.
    """
    (isolated_home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"dev-only": {"command": "/bin/true", "args": []}}}),
        encoding="utf-8",
    )
    from personalclaw.mcp_client import _personalclaw_mcp_specs

    specs = _personalclaw_mcp_specs()
    assert list(specs) == ["dev-only"], f"the native client read {list(specs)}"


def test_the_two_former_constants_are_resolved_per_call(monkeypatch, tmp_path):
    """The import-time half of the bug, asserted directly.

    A module-level `Path.home()` value is frozen before `PERSONALCLAW_HOME` can matter, so
    "set the env var earlier" was never a fix. These must answer differently for two different
    homes within one process — which a constant cannot do.
    """
    from personalclaw.dashboard.handlers.mcp import _canonical_mcp_json
    from personalclaw.mcp_discovery import _mcp_json_paths

    seen = []
    for name in ("home-a", "home-b"):
        home = tmp_path / name
        home.mkdir()
        monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
        seen.append((_mcp_json_paths()[0], _canonical_mcp_json()))

    (disc_a, hand_a), (disc_b, hand_b) = seen
    assert disc_a != disc_b, "mcp_discovery still answers one frozen path"
    assert hand_a != hand_b, "handlers/mcp still answers one frozen path"
    assert disc_a == hand_a and disc_b == hand_b, "the two resolvers disagree within one home"


def test_no_module_hardcodes_the_real_home_for_mcp_json():
    """The vacuity guard: the closed set stays closed.

    Without this, a fourth site could reintroduce the bug and every test above would still
    pass — they only prove the three resolvers we know about. Scoped to `mcp.json` because the
    other `Path.home() / ".personalclaw"` sites (session_map, subagent_persistence,
    learning/staging, vector_memory) legitimately try `config_dir()` first and fall back inside
    an exception handler.
    """
    import subprocess
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src"
    out = subprocess.run(
        ["grep", "-rn", 'Path.home() / ".personalclaw" / "mcp.json"', str(src)],
        capture_output=True,
        text=True,
    )
    hits = [line for line in out.stdout.splitlines() if line.strip()]
    assert not hits, "an mcp.json path bypasses config_dir() again:\n  " + "\n  ".join(hits)
