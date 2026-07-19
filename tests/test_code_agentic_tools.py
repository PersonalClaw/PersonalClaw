"""Tests for the agentic-coder builtin tool ``repo_map`` (structural overview).

git / run_tests / diagnostics are NO LONGER tools — the agent runs git, the test
runner, and the linter via ``bash`` (shell-first). ``repo_map`` survives because a
tree+top-level-defs overview would take many round-trips to build with raw shell.
Workspace-confined like the other native file tools.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "import os\n\ndef hello():\n    return 1\n\nclass Greeter:\n    def greet(self):\n        pass\n"
    )
    (tmp_path / "src" / "util.js").write_text(
        "export function add(a, b) { return a + b }\nexport const mul = (a, b) => a * b\n"
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("export const x = 1\n")
    return tmp_path


def _prov(ws: Path) -> NativeBuiltinToolProvider:
    return NativeBuiltinToolProvider(cwd=ws)


class TestRepoMap:
    def test_lists_tool(self, ws):
        names = {t.name for t in _run(_prov(ws).list_tools())}
        assert "repo_map" in names
        # the shell-wrapper tools are gone — the agent uses bash for these.
        assert "run_tests" not in names and "diagnostics" not in names and "git" not in names

    def test_repo_map_shows_python_defs_and_classes(self, ws):
        r = _run(_prov(ws).invoke("repo_map", {}))
        assert r.success
        assert "src/app.py" in r.output
        assert "def hello()" in r.output
        assert "class Greeter" in r.output and "greet" in r.output

    def test_repo_map_shows_js_exports(self, ws):
        r = _run(_prov(ws).invoke("repo_map", {}))
        assert "src/util.js" in r.output
        assert "add" in r.output and "mul" in r.output

    def test_repo_map_skips_node_modules(self, ws):
        r = _run(_prov(ws).invoke("repo_map", {}))
        assert "node_modules" not in r.output

    def test_repo_map_empty_dir(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "x.md").write_text("# hi")  # not a source ext
        r = _run(_prov(tmp_path).invoke("repo_map", {}))
        assert "no source files" in r.output.lower()

    def test_repo_map_path_escape_blocked(self, ws):
        r = _run(_prov(ws).invoke("repo_map", {"path": "../../etc"}))
        assert r.success is False

    def test_repo_map_prunes_skip_dirs_at_any_depth(self, tmp_path):
        # A source file nested inside a skip-dir (node_modules) must never appear —
        # the walk prunes those dirs rather than descending + filtering after.
        (tmp_path / "real.py").write_text("def keep(): pass\n")
        nm = tmp_path / "node_modules" / "pkg" / "lib"
        nm.mkdir(parents=True)
        (nm / "vendored.py").write_text("def drop(): pass\n")
        r = _run(_prov(tmp_path).invoke("repo_map", {}))
        assert r.success
        assert "real.py" in r.output and "keep" in r.output
        assert "vendored.py" not in r.output and "node_modules" not in r.output

    def test_repo_map_signals_truncation(self, tmp_path):
        # More source files than max_files → the map must SAY it's partial (not
        # report the capped count as the whole repo).
        for i in range(5):
            (tmp_path / f"m{i}.py").write_text("def f(): pass\n")
        r = _run(_prov(tmp_path).invoke("repo_map", {"max_files": 2}))
        assert r.success
        assert "PARTIAL" in r.output and "max_files=2" in r.output

    def test_repo_map_no_truncation_note_when_complete(self, tmp_path):
        (tmp_path / "only.py").write_text("def f(): pass\n")
        r = _run(_prov(tmp_path).invoke("repo_map", {"max_files": 50}))
        assert r.success and "PARTIAL" not in r.output
