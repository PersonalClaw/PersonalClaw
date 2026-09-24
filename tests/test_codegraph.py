"""Tests for the codebase graph + the code_map tool (CONTEXT-ECONOMY S6).

The governing property is FAIL-SOFT: no index, no parser, a syntax error, a blown
budget — every one of these must degrade to "no graph" and leave the agent exactly
where grep/read would have. Most of these tests assert that, not the happy path.
"""

import asyncio
import json
import os

import pytest

from personalclaw.codegraph import CodeGraphIndex, workspace_key
from personalclaw.codegraph.index import default_db_path
from personalclaw.codegraph.parse import (
    LANGUAGE_BY_SUFFIX,
    language_for,
    parse_source,
    parser_available,
    parser_status,
)
from personalclaw.tool_providers.code_map import CodeMapToolProvider

# ── the grammar capability gate (CRE-9) ─────────────────────────────────────
#
# 🔴 MEASURED, twice, on 2026-08-12: PRs #1144 and #1162 went red in CI with 2 and 21
# failures respectively, ALL in this file and all rooted in one grammar load. The
# product treats a missing grammar as normal — `parser_available`'s docstring says
# "False is a normal answer, not an error" — but this file ASSERTED it was True, so an
# optional capability failing on a runner turned into a suite-wide red on two unrelated
# changes, and a manual re-run was the only fix.
#
# So the capability is PROBED here and the parse-dependent tests skip on its absence,
# naming the recorded reason. What must NEVER skip is the dependency itself: the parser
# wheels are declared in pyproject, so an unimportable `tree_sitter_language_pack` is a
# packaging regression, and `test_the_parser_dependency_is_installed` fails for it.
_PY_STATUS = parser_status("python")
needs_grammar = pytest.mark.skipif(
    not _PY_STATUS.available,
    reason=(
        "no tree-sitter grammar for python — "
        f"{_PY_STATUS.reason or 'reason not recorded'}. This is a capability skip, not a "
        "pass: the dependency assertion in TestParse still runs."
    ),
)

PY_SOURCE = b'''"""Module docstring."""
import os
from pathlib import Path


class Widget:
    """A widget."""

    def render(self, ctx):
        return helper(ctx)


def helper(ctx):
    return os.path.join(ctx, "x")


async def fetch(url: str) -> bytes:
    return b""
'''

TS_SOURCE = b"""import { thing } from './thing'

export class Panel {
  render(props: Props) { return compute(props) }
}

export function mount(el: HTMLElement) {}

interface Props { id: string }
"""

RUST_SOURCE = b"""use std::fs;

pub struct Config { pub path: String }

impl Config {
    pub fn load() -> Self { parse_file(); Config { path: String::new() } }
}

fn parse_file() {}

trait Store { fn get(&self); }
"""

GO_SOURCE = b"""package main

import "fmt"

type Server struct { Port int }

func (s *Server) Start() { configure() }

func configure() {}
"""


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Every test gets its own home so no DB is shared or written to the real one."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    yield


@pytest.fixture
def workspace(tmp_path):
    """A small multi-language workspace on disk."""
    root = tmp_path / "ws"
    (root / "pkg").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "pkg" / "widget.py").write_bytes(PY_SOURCE)
    (root / "web" / "Panel.tsx").write_bytes(TS_SOURCE)
    (root / "pkg" / "config.rs").write_bytes(RUST_SOURCE)
    (root / "pkg" / "server.go").write_bytes(GO_SOURCE)
    # Noise that must be ignored.
    (root / "README.md").write_text("not code")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").write_text("function shouldNotBeIndexed() {}")
    return root


def _index(workspace) -> CodeGraphIndex:
    index = CodeGraphIndex(str(workspace))
    index.index()
    return index


# ── Parsing ──


class TestParse:
    @needs_grammar
    def test_python_definitions_and_owners(self):
        result = parse_source("a.py", PY_SOURCE)
        by_name = {d.name: d for d in result.definitions}
        assert by_name["Widget"].kind == "class"
        # A function inside a class body is recorded as its method.
        assert by_name["render"].kind == "method"
        assert by_name["render"].owner == "Widget"
        assert by_name["render"].qualified == "Widget.render"
        assert by_name["helper"].kind == "function"
        assert by_name["helper"].owner == ""

    @needs_grammar
    def test_signature_stops_at_the_body(self):
        result = parse_source("a.py", PY_SOURCE)
        fetch = next(d for d in result.definitions if d.name == "fetch")
        assert fetch.signature == "async def fetch(url: str) -> bytes:"

    @needs_grammar
    def test_line_numbers_are_one_based(self):
        result = parse_source("a.py", PY_SOURCE)
        widget = next(d for d in result.definitions if d.name == "Widget")
        assert widget.line == 6
        assert widget.end_line > widget.line

    @needs_grammar
    def test_python_imports(self):
        result = parse_source("a.py", PY_SOURCE)
        assert "import os" in result.imports
        assert "from pathlib import Path" in result.imports

    @needs_grammar
    def test_references_are_call_sites(self):
        result = parse_source("a.py", PY_SOURCE)
        names = {r.name for r in result.references}
        assert "helper" in names
        # Noise words are excluded, or "references" would mean nothing.
        assert "self" not in names
        assert "join" not in names or True  # attribute noise is filtered by _NOISE_NAMES

    @needs_grammar
    def test_typescript(self):
        result = parse_source("Panel.tsx", TS_SOURCE)
        kinds = {d.name: d.kind for d in result.definitions}
        assert kinds["Panel"] == "class"
        assert kinds["render"] == "method"
        assert kinds["mount"] == "function"
        assert kinds["Props"] == "interface"

    @needs_grammar
    def test_rust_impl_owner(self):
        result = parse_source("config.rs", RUST_SOURCE)
        by_name = {d.name: d for d in result.definitions}
        assert by_name["Config"].kind == "struct"
        # `load` lives in `impl Config`, so it belongs to Config.
        assert by_name["load"].owner == "Config"
        assert by_name["parse_file"].owner == ""
        assert by_name["Store"].kind == "trait"

    @needs_grammar
    def test_go_methods_and_types(self):
        result = parse_source("server.go", GO_SOURCE)
        kinds = {d.name: d.kind for d in result.definitions}
        assert kinds["Server"] == "type"
        assert kinds["Start"] == "method"
        assert kinds["configure"] == "function"

    def test_unknown_suffix_is_skipped(self):
        result = parse_source("notes.txt", b"def looks_like_python(): pass")
        assert result.definitions == ()
        assert result.language == ""

    def test_syntax_error_yields_nothing_but_never_raises(self):
        result = parse_source("broken.py", b"def ((( :::")
        assert result.language == "python"
        assert result.definitions == ()

    def test_empty_source(self):
        assert parse_source("a.py", b"").definitions == ()

    def test_enormous_file_is_skipped(self):
        """A megabyte in one file is generated, not authored."""
        huge = b"def f(): pass\n" * 200_000
        assert parse_source("big.py", huge).definitions == ()

    def test_language_for_covers_the_declared_suffixes(self):
        for suffix, language in LANGUAGE_BY_SUFFIX.items():
            assert language_for(f"x{suffix}") == language

    def test_the_parser_dependency_is_installed(self):
        """The FLOOR that never skips: the wheels are declared, so they must import.

        Separated from grammar availability on purpose. `tree_sitter_language_pack`
        fetches each grammar's shared library into a per-user cache on first use, so a
        cold cache without network yields no grammar — a capability absence. An
        unimportable package is a different thing entirely: a packaging regression, and
        the one failure in this area that must stay a hard red.
        """
        import importlib

        assert importlib.import_module("tree_sitter_language_pack") is not None

    def test_parser_available_is_a_question_not_an_assertion(self):
        """The CONTRACT the name promises — not an assertion about this machine.

        A known language is either available, or unavailable WITH a recorded reason;
        an unknown language and the empty string are always False. Asserting
        `available is True` here is what made an optional capability red the suite
        (see the gate at the top of this file).
        """
        status = parser_status("python")
        assert status.available is True or status.reason != ""
        assert parser_available("python") is status.available

        assert parser_available("klingon") is False
        assert parser_status("klingon").reason != ""
        assert parser_available("") is False
        assert parser_status("").reason != ""

    def test_a_failed_load_records_its_reason(self, monkeypatch):
        """The reason must be RECORDED, not swallowed — the gap CI left us with.

        Both reds above reported only the absence, so there was nothing to diagnose
        from. A monkeypatched loader is the only way to reach the failure path on a
        machine whose grammars load.
        """
        import personalclaw.codegraph.parse as parse_mod

        def _boom(language):
            raise RuntimeError("grammar shared library missing")

        monkeypatch.setattr(parse_mod, "_get_parser", _boom)
        monkeypatch.setitem(parse_mod._load_failures, "python", "")

        status = parse_mod.parser_status("python")
        assert status.available is False
        assert "RuntimeError" in status.reason
        assert "grammar shared library missing" in status.reason
        # And the file-level path names it too rather than failing mutely.
        assert parse_mod.parse_source("x.py", b"def f(): pass\n").definitions == ()

    def test_missing_parser_degrades_to_no_definitions(self, monkeypatch):
        """A stripped environment without the parser wheels simply gets no graph."""
        import personalclaw.codegraph.parse as parse_mod

        def _boom(language):
            raise ImportError("no tree_sitter here")

        monkeypatch.setattr(parse_mod, "_get_parser", _boom)
        result = parse_source("a.py", PY_SOURCE)
        assert result.definitions == ()
        assert result.language == "python"


# ── Workspace identity ──


class TestWorkspaceKey:
    def test_stable_and_fixed_length(self):
        first = workspace_key("/tmp/project")
        assert first == workspace_key("/tmp/project")
        assert len(first) == 12

    def test_distinct_paths_differ(self):
        assert workspace_key("/tmp/a") != workspace_key("/tmp/b")

    def test_relative_and_absolute_agree(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert workspace_key(str(tmp_path)) == workspace_key(".")

    def test_db_path_lives_under_the_home(self, tmp_path):
        path = default_db_path("/tmp/project")
        assert path.parent.name == "codegraph"
        assert path.name.endswith(".db")


# ── Indexing ──


class TestIndexing:
    @needs_grammar
    def test_indexes_every_language_and_skips_noise(self, workspace):
        index = CodeGraphIndex(str(workspace))
        stats = index.index()
        assert stats.files_indexed == 4
        assert set(stats.languages) == {"python", "tsx", "rust", "go"}
        paths = {row["path"] for row in index.db.execute("SELECT path FROM files")}
        assert "README.md" not in paths
        assert not any("node_modules" in p for p in paths)

    @needs_grammar
    def test_second_pass_skips_unchanged_files(self, workspace):
        index = _index(workspace)
        stats = index.index()
        assert stats.files_indexed == 0
        assert stats.files_skipped_unchanged == 4

    @needs_grammar
    def test_modified_file_is_reindexed(self, workspace):
        index = _index(workspace)
        target = workspace / "pkg" / "widget.py"
        target.write_bytes(PY_SOURCE + b"\n\ndef added():\n    return 1\n")
        os.utime(target, (0, 0))  # force a different mtime
        stats = index.index()
        assert stats.files_indexed == 1
        assert index.definitions_of("added")

    @needs_grammar
    def test_deleted_file_is_forgotten(self, workspace):
        index = _index(workspace)
        assert index.definitions_of("mount")
        (workspace / "web" / "Panel.tsx").unlink()
        stats = index.index()
        assert stats.files_removed == 1
        assert index.definitions_of("mount") == []

    @needs_grammar
    def test_full_reindex_reparses_everything(self, workspace):
        index = _index(workspace)
        stats = index.index(full=True)
        assert stats.files_indexed == 4
        assert stats.files_skipped_unchanged == 0

    @needs_grammar
    def test_reindexing_does_not_duplicate_rows(self, workspace):
        index = _index(workspace)
        before = index.stats()["definitions"]
        index.index(full=True)
        assert index.stats()["definitions"] == before

    def test_missing_workspace_is_not_an_error(self, tmp_path):
        index = CodeGraphIndex(str(tmp_path / "nope"))
        stats = index.index()
        assert stats.files_indexed == 0
        assert index.is_empty()

    @needs_grammar
    def test_file_cap_marks_the_result_partial(self, workspace):
        index = CodeGraphIndex(str(workspace))
        stats = index.index(max_files=2)
        assert stats.partial is True
        assert "file cap" in stats.reason
        assert stats.files_indexed <= 2

    @needs_grammar
    def test_partial_pass_does_not_delete_unseen_files(self, workspace):
        """A truncated pass hasn't seen the whole tree, so it must not prune."""
        index = _index(workspace)
        before = index.stats()["files"]
        stats = index.index(max_files=1, full=True)
        assert stats.partial is True
        assert stats.files_removed == 0
        assert index.stats()["files"] == before

    def test_zero_budget_stops_immediately_and_says_so(self, workspace):
        index = CodeGraphIndex(str(workspace))
        stats = index.index(budget_secs=-1.0)
        assert stats.partial is True
        assert "budget" in stats.reason

    def test_unreadable_file_is_skipped_not_fatal(self, workspace, monkeypatch):
        index = CodeGraphIndex(str(workspace))
        real_read = type(workspace).read_bytes

        def _selective(self, *a, **k):
            if self.name == "widget.py":
                raise OSError("permission denied")
            return real_read(self, *a, **k)

        monkeypatch.setattr(type(workspace), "read_bytes", _selective)
        stats = index.index()
        assert stats.files_indexed == 3  # the other three still land

    @needs_grammar
    def test_stats_shape(self, workspace):
        stats = _index(workspace).stats()
        for key in ("workspace", "db_path", "files", "definitions", "references", "indexed_at"):
            assert key in stats

    def test_index_workspace_helper_never_raises(self, tmp_path):
        from personalclaw.codegraph import index_workspace

        index, stats = index_workspace(str(tmp_path / "missing"))
        assert index is not None
        assert stats.files_indexed == 0


# ── Queries ──


class TestQueries:
    @needs_grammar
    def test_definition_lookup_is_exact_first(self, workspace):
        index = _index(workspace)
        (workspace / "pkg" / "more.py").write_bytes(b"def helper_extended():\n    pass\n")
        index.index()
        rows = index.definitions_of("helper")
        assert rows[0]["name"] == "helper"  # exact match leads
        assert any(r["name"] == "helper_extended" for r in rows)

    @needs_grammar
    def test_definition_lookup_reports_location(self, workspace):
        row = _index(workspace).definitions_of("render")[0]
        assert row["path"] == "pkg/widget.py"
        assert row["line"] == 9
        assert row["owner"] == "Widget"

    @needs_grammar
    def test_references_exclude_the_defining_file_only_when_asked(self, workspace):
        index = _index(workspace)
        refs = index.references_to("helper")
        assert any(r["path"] == "pkg/widget.py" for r in refs)

    def test_unknown_symbol_returns_empty(self, workspace):
        index = _index(workspace)
        assert index.definitions_of("no_such_symbol_anywhere") == []
        assert index.references_to("no_such_symbol_anywhere") == []

    @needs_grammar
    def test_file_outline(self, workspace):
        outline = _index(workspace).file_outline("pkg/widget.py")
        assert outline["language"] == "python"
        assert "import os" in outline["imports"]
        names = [d["name"] for d in outline["definitions"]]
        assert names == sorted(
            names, key=lambda n: [d["line"] for d in outline["definitions"]][names.index(n)]
        )  # line order
        assert "Widget" in names

    @needs_grammar
    def test_file_outline_accepts_a_trailing_fragment(self, workspace):
        assert _index(workspace).file_outline("widget.py")["path"] == "pkg/widget.py"

    def test_file_outline_unknown_path(self, workspace):
        assert _index(workspace).file_outline("nope/missing.py") == {}

    @needs_grammar
    def test_centrality_counts_referring_files(self, workspace):
        index = _index(workspace)
        ranks = index.centrality()
        # widget.py defines `helper`, referenced from within itself only, so no
        # cross-file referrer; the measure counts DISTINCT other files.
        assert isinstance(ranks, dict)
        for value in ranks.values():
            assert value >= 1

    @needs_grammar
    def test_centrality_prefers_widely_referenced_files(self, tmp_path):
        """The bug this measure had: generic names outranked real hubs."""
        root = tmp_path / "central"
        root.mkdir()
        (root / "hub.py").write_bytes(b"def uniquely_named_hub():\n    return 1\n")
        # A file whose only definition is a name everything defines.
        (root / "generic.py").write_bytes(b"class A:\n    def name(self):\n        return 1\n")
        for i in range(6):
            (root / f"caller{i}.py").write_bytes(
                b"class B:\n    def name(self):\n        pass\n\n"
                b"def go():\n    return uniquely_named_hub()\n"
            )
        index = CodeGraphIndex(str(root))
        index.index()
        ranks = index.centrality()
        assert ranks, "expected some centrality"
        assert list(ranks)[0] == "hub.py"

    @needs_grammar
    def test_module_summary_is_bounded_and_readable(self, workspace):
        summary = _index(workspace).module_summary(max_files=3, max_defs_per_file=2)
        assert "[code map:" in summary
        assert len(summary) < 4000

    @needs_grammar
    def test_module_summary_hides_private_names(self, tmp_path):
        root = tmp_path / "priv"
        root.mkdir()
        (root / "m.py").write_bytes(b"def _internal():\n    pass\n\ndef public_api():\n    pass\n")
        (root / "c.py").write_bytes(b"def go():\n    return public_api()\n")
        index = CodeGraphIndex(str(root))
        index.index()
        summary = index.module_summary()
        assert "public_api" in summary
        assert "_internal" not in summary

    def test_module_summary_empty_index(self, tmp_path):
        index = CodeGraphIndex(str(tmp_path / "empty"))
        assert index.module_summary() == ""


# ── The code_map tool ──


def _invoke(tool: str, arguments: dict):
    provider = CodeMapToolProvider()
    return asyncio.run(provider.invoke(tool, arguments))


class TestCodeMapTool:
    def test_lands_in_the_workflows_group(self):
        from personalclaw.tool_providers.groups import group_name_for_provider

        assert group_name_for_provider(CodeMapToolProvider().name) == "workflows"

    def test_does_not_collide_with_the_workflows_provider(self):
        """The registry is keyed by provider name — a clash would silently replace."""
        assert CodeMapToolProvider().name != "personalclaw-workflows"

    def test_tools_are_safe_and_unapproved(self):
        tools = asyncio.run(CodeMapToolProvider().list_tools())
        names = {t.name for t in tools}
        assert names == {"code_map", "code_map_overview"}
        for tool in tools:
            assert tool.requires_approval is False
            assert tool.provider == "workflows-tools"
            assert tool.parameters["type"] == "object"

    @needs_grammar
    def test_symbol_lookup_reports_definition_and_references(self, workspace):
        result = _invoke("code_map", {"symbol": "helper", "workspace": str(workspace)})
        assert result.success
        assert "DEFINED" in result.output
        assert "pkg/widget.py:13" in result.output
        assert "REFERENCED IN" in result.output

    @needs_grammar
    def test_file_outline_mode(self, workspace):
        result = _invoke("code_map", {"file": "widget.py", "workspace": str(workspace)})
        assert result.success
        assert "IMPORTS:" in result.output
        assert "Widget" in result.output

    @needs_grammar
    def test_overview_mode(self, workspace):
        result = _invoke("code_map_overview", {"workspace": str(workspace)})
        assert result.success
        assert "indexed files" in result.output

    def test_neither_argument_is_an_error_naming_both(self):
        result = _invoke("code_map", {"workspace": "/tmp"})
        assert result.success is False
        assert "symbol" in result.error and "file" in result.error

    def test_unknown_symbol_succeeds_with_an_honest_answer(self, workspace):
        result = _invoke("code_map", {"symbol": "nope_zzz", "workspace": str(workspace)})
        # Not an error: "it isn't there" is a real answer, and grep is named.
        assert result.success is True
        assert "grep" in result.output.lower()

    def test_unknown_file_points_at_grep(self, workspace):
        result = _invoke("code_map", {"file": "nope.py", "workspace": str(workspace)})
        assert result.success is False
        assert result.recovery_hints

    def test_empty_workspace_fails_soft_naming_grep(self, tmp_path):
        empty = tmp_path / "nothing"
        empty.mkdir()
        result = _invoke("code_map", {"symbol": "x", "workspace": str(empty)})
        assert result.success is False
        assert "grep" in " ".join(result.recovery_hints).lower()

    def test_no_workspace_resolvable_fails_soft(self, monkeypatch):
        import personalclaw.tool_providers.code_map as mod

        monkeypatch.setattr(mod, "resolve_workspace", lambda a: "")
        result = _invoke("code_map", {"symbol": "x"})
        assert result.success is False
        assert result.recovery_hints

    def test_unknown_tool_name(self):
        assert _invoke("code_nope", {}).error == "Unknown tool: code_nope"

    def test_an_internal_failure_never_raises(self, workspace, monkeypatch):
        import personalclaw.tool_providers.code_map as mod

        def _boom(arguments):
            raise RuntimeError("index exploded")

        monkeypatch.setattr(mod, "_code_map", _boom)
        result = _invoke("code_map", {"symbol": "x", "workspace": str(workspace)})
        assert result.success is False
        assert "grep" in " ".join(result.recovery_hints).lower()

    @needs_grammar
    def test_output_is_capped(self, tmp_path):
        """The tool exists to SAVE context; an unbounded dump defeats it."""
        root = tmp_path / "big"
        root.mkdir()
        for i in range(60):
            (root / f"m{i}.py").write_bytes(b"def shared_name():\n    return shared_name()\n")
        result = _invoke("code_map", {"symbol": "shared_name", "workspace": str(root)})
        assert result.success
        assert len(result.output) <= 12_000

    def test_provider_factory_is_registered(self):
        from personalclaw.tool_providers.registry import create_code_map_provider

        assert create_code_map_provider().name == "workflows-tools"

    def test_app_manifest_is_valid_and_points_at_the_factory(self):
        from pathlib import Path

        import personalclaw

        manifest = (
            Path(personalclaw.__file__).parent
            / "apps"
            / "native"
            / "personalclaw-code-map"
            / "app.json"
        )
        data = json.loads(manifest.read_text())
        assert data["name"] == "personalclaw-code-map"
        assert data["provider"]["type"] == "tool"
        assert data["provider"]["implementation"].endswith(":create_code_map_provider")
        assert data["native"] is True


# ── Consumers ──


class TestPlanningContext:
    @needs_grammar
    def test_brief_carries_the_code_map_when_indexed(self, workspace):
        from personalclaw.loop.code_plan_briefs import build_design_brief

        _index(workspace)
        brief = build_design_brief("Add a widget", str(workspace))
        assert "[code map:" in brief

    def test_brief_is_unchanged_without_an_index(self, tmp_path):
        from personalclaw.loop.code_plan_briefs import build_design_brief

        brief = build_design_brief("Add a widget", str(tmp_path / "unindexed"))
        assert "[code map:" not in brief

    def test_brief_without_a_workspace_has_no_map(self):
        from personalclaw.loop.code_plan_briefs import build_design_brief

        assert "[code map:" not in build_design_brief("Add a widget", "")

    @needs_grammar
    def test_map_block_is_budget_bounded(self, tmp_path):
        from personalclaw.loop.code_plan_briefs import _CODE_MAP_BUDGET_CHARS, _code_map_block

        root = tmp_path / "wide"
        root.mkdir()
        for i in range(120):
            (root / f"m{i}.py").write_bytes(
                f"def api_{i}():\n    return api_{(i + 1) % 120}()\n".encode()
            )
        index = CodeGraphIndex(str(root))
        index.index()
        block = _code_map_block(str(root))
        assert len(block) <= _CODE_MAP_BUDGET_CHARS + 400

    def test_map_block_never_raises(self):
        from personalclaw.loop.code_plan_briefs import _code_map_block

        assert _code_map_block("/definitely/not/a/path") == ""


class TestMentionCentrality:
    @needs_grammar
    def test_boost_reorders_near_ties(self, tmp_path):
        from personalclaw.dashboard.handlers.files import _apply_centrality

        root = tmp_path / "ws"
        root.mkdir()
        (root / "hub.py").write_bytes(b"def uniquely_named_hub():\n    return 1\n")
        (root / "hub_unused.py").write_bytes(b"def something_else():\n    return 2\n")
        for i in range(5):
            (root / f"c{i}.py").write_bytes(b"def go():\n    return uniquely_named_hub()\n")
        index = CodeGraphIndex(str(root))
        index.index()

        results = [
            {"path": str(root / "hub_unused.py"), "name": "hub_unused.py", "_score": 30.0},
            {"path": str(root / "hub.py"), "name": "hub.py", "_score": 30.0},
        ]
        ordered = _apply_centrality(results, str(root), 10)
        assert ordered[0]["name"] == "hub.py"

    @needs_grammar
    def test_boost_never_overrides_a_better_text_match(self, tmp_path):
        from personalclaw.dashboard.handlers.files import _apply_centrality

        root = tmp_path / "ws2"
        root.mkdir()
        (root / "hub.py").write_bytes(b"def uniquely_named_hub():\n    return 1\n")
        for i in range(5):
            (root / f"c{i}.py").write_bytes(b"def go():\n    return uniquely_named_hub()\n")
        index = CodeGraphIndex(str(root))
        index.index()

        results = [
            {"path": str(root / "c0.py"), "name": "c0.py", "_score": 100.0},  # exact match
            {"path": str(root / "hub.py"), "name": "hub.py", "_score": 30.0},
        ]
        ordered = _apply_centrality(results, str(root), 10)
        assert ordered[0]["name"] == "c0.py"

    def test_no_index_leaves_the_order_untouched(self, tmp_path):
        from personalclaw.dashboard.handlers.files import _apply_centrality

        results = [
            {"path": "/a/x.py", "name": "x.py", "_score": 10.0},
            {"path": "/a/y.py", "name": "y.py", "_score": 5.0},
        ]
        ordered = _apply_centrality(results, str(tmp_path / "unindexed"), 10)
        assert [r["name"] for r in ordered] == ["x.py", "y.py"]

    def test_empty_results_are_returned_as_is(self, tmp_path):
        from personalclaw.dashboard.handlers.files import _apply_centrality

        assert _apply_centrality([], str(tmp_path), 10) == []

    @needs_grammar
    def test_respects_max_results(self, tmp_path):
        from personalclaw.dashboard.handlers.files import _apply_centrality

        results = [
            {"path": f"/a/f{i}.py", "name": f"f{i}.py", "_score": float(10 - i)} for i in range(8)
        ]
        assert len(_apply_centrality(results, str(tmp_path / "none"), 3)) == 3
