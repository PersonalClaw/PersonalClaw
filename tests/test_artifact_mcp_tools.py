"""E13-P2: artifact_* MCP tools.

The tools dispatch directly against the native provider entity (no HTTP),
attributed as the agent so artifact_update snapshots + emits 'iterated'. They
go through the full _call_tool validation path. content_file is sensitive-path
gated.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.mcp_artifacts import _call_tool


@pytest.fixture
def provider(tmp_path):
    return NativeArtifactProvider(root=tmp_path / "artifacts")


@pytest.fixture
def wired(provider):
    """Resolve the native provider as our tmp-rooted instance + fixed session."""
    with patch("personalclaw.artifacts.registry.get_provider", return_value=provider), patch(
        "personalclaw.mcp_artifacts._resolve_session_key", return_value="dashboard:chat-1"
    ):
        yield provider


class TestArtifactMcpTools:
    def test_save_returns_slug(self, wired) -> None:
        out = _call_tool("artifact_save", {"name": "My Chart", "content": "<div>x</div>", "kind": "widget"})
        assert "my-chart" in out
        assert wired.get("my-chart") is not None

    def test_get_returns_content(self, wired) -> None:
        _call_tool("artifact_save", {"name": "Doc", "content": "hello", "kind": "markdown"})
        out = _call_tool("artifact_get", {"slug": "doc"})
        assert out == "hello"

    def test_save_stamps_bound_project_id(self, wired) -> None:
        """S5: artifact_save ties the artifact to the Project bound for this turn, so it
        surfaces in the Project detail page (/api/projects/{id}/linked filters by it)."""
        from personalclaw.agents.native import builtin_tools as _bt
        toks = _bt.bind_tool_context(cwd="/tmp", agent="a", project_id="proj-xyz")
        try:
            _call_tool("artifact_save", {"name": "Scoped", "content": "x", "kind": "text"})
        finally:
            _bt.reset_tool_context(toks)
        art = wired.get("scoped")
        assert art is not None and art.project_id == "proj-xyz"
        # and it's retrievable by the project filter the linked-work handler uses
        assert any(a.slug == "scoped" for a in wired.list(project_id="proj-xyz"))

    def test_save_unscoped_has_no_project(self, wired) -> None:
        """No project bound (unscoped chat) → artifact carries project_id='' (not tied)."""
        _call_tool("artifact_save", {"name": "Loose", "content": "y", "kind": "text"})
        assert wired.get("loose").project_id == ""

    def test_get_missing_slug(self, wired) -> None:
        out = _call_tool("artifact_get", {"slug": "nope"})
        assert "not found" in out.lower()

    def test_update_snapshots_and_iterates(self, wired) -> None:
        _call_tool("artifact_save", {"name": "C", "content": "v1", "kind": "text"})
        out = _call_tool("artifact_update", {"slug": "c", "content": "v2"})
        assert "version 2" in out
        art = wired.get("c")
        assert art.version == 2
        assert art.content == "v2"
        assert art.events[-1].type == "iterated"  # agent attribution

    def test_update_missing_slug(self, wired) -> None:
        out = _call_tool("artifact_update", {"slug": "ghost", "content": "x"})
        assert "not found" in out.lower()

    def test_list_filters(self, wired) -> None:
        _call_tool("artifact_save", {"name": "Widget One", "content": "1", "kind": "widget"})
        _call_tool("artifact_save", {"name": "Doc Two", "content": "2", "kind": "markdown"})
        out = _call_tool("artifact_list", {"kind": "markdown"})
        rows = json.loads(out)
        assert len(rows) == 1
        assert rows[0]["slug"] == "doc-two"

    def test_list_empty(self, wired) -> None:
        assert "No artifacts" in _call_tool("artifact_list", {})

    def test_versions(self, wired) -> None:
        _call_tool("artifact_save", {"name": "C", "content": "v1", "kind": "text"})
        _call_tool("artifact_update", {"slug": "c", "content": "v2"})
        out = json.loads(_call_tool("artifact_versions", {"slug": "c"}))
        assert out["versions"] == [1, 2]

    def test_delete(self, wired) -> None:
        _call_tool("artifact_save", {"name": "C", "content": "x", "kind": "text"})
        out = _call_tool("artifact_delete", {"slug": "c"})
        assert "Deleted" in out
        assert wired.get("c") is None

    def test_content_file_read(self, wired, tmp_path) -> None:
        f = tmp_path / "body.md"
        f.write_text("# from file")
        out = _call_tool("artifact_save", {"name": "FromFile", "content_file": str(f), "kind": "markdown"})
        assert "fromfile" in out.lower()
        assert wired.get("fromfile").content == "# from file"

    def test_content_file_sensitive_refused(self, wired) -> None:
        sensitive = str(Path.home() / ".aws" / "credentials")
        out = _call_tool("artifact_save", {"name": "Steal", "content_file": sensitive, "kind": "text"})
        assert "sensitive" in out.lower()
        # Nothing was created.
        assert wired.list() == []

    def test_save_redacts_listing_name(self, wired) -> None:
        secret = "AKIAIOSFODNN7EXAMPLE"
        _call_tool("artifact_save", {"name": f"key {secret}", "content": "x", "kind": "text"})
        out = _call_tool("artifact_list", {})
        assert secret not in out

    def test_invalid_kind_rejected_by_schema(self, wired) -> None:
        # Schema enum rejects unknown kinds at the validation layer.
        out = _call_tool("artifact_save", {"name": "X", "content": "y", "kind": "executable"})
        assert "error" in out.lower() or "invalid" in out.lower()

    def test_react_kind_accepted(self, wired) -> None:
        # The Design loop saves generated components as kind='react' (rendered live on
        # the design Canvas); the tool enum must accept it (regression: it was omitted,
        # so a design worker could never produce a canvas-renderable component).
        out = _call_tool("artifact_save", {
            "name": "Button", "kind": "react",
            "content": "function App(){return React.createElement('button',null,'Hi')}",
        })
        assert "saved artifact" in out.lower()
        saved = wired.list()
        assert any(a.kind == "react" for a in saved)
