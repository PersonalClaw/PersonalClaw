"""The read-only ``workflow_list`` MCP tool and bundled starter SOPs.

``workflow_list`` lists the workflow SOPs available to the agent on demand
(distinct from the per-turn auto-surfacing path). It goes through the full
``_call_tool`` validation path and reads ``/api/workflows`` over the in-process
``_get`` helper. The bundled starter SOPs sync into the user's workflows dir on
first use via the skills-loader mtime-copy pattern.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from personalclaw.mcp_workflows import _call_tool, _list_tools
from personalclaw.validation import MCP_CORE_SCHEMAS


class TestWorkflowListRegistration:
    def test_tool_is_listed(self) -> None:
        names = {t["name"] for t in _list_tools()}
        assert "workflow_list" in names

    def test_tool_has_schema(self) -> None:
        assert "workflow_list" in MCP_CORE_SCHEMAS

    def test_discoverable_in_native_loop(self) -> None:
        from personalclaw.agents.native.tools import InProcessMcpToolProvider

        prov = InProcessMcpToolProvider(module="personalclaw.mcp_workflows")
        names = {t.name for t in asyncio.run(prov.list_tools())}
        assert "workflow_list" in names


class TestWorkflowListDispatch:
    def test_lists_enabled_workflows_with_steps(self) -> None:
        payload = {
            "workflows": [
                {
                    "name": "commit-changes",
                    "scope": "global",
                    "scope_ref": "",
                    "description": "Review, stage, commit",
                    "tags": ["git"],
                    "enabled": True,
                    "steps": [{"title": "Review diff"}, {"title": "Stage"}],
                },
            ]
        }
        with patch("personalclaw.mcp_workflows._get", return_value=payload):
            out = _call_tool("workflow_list", {})
        assert "commit-changes" in out
        assert "(global)" in out
        assert "[git]" in out
        assert "1. Review diff" in out
        assert "2. Stage" in out

    def test_disabled_workflows_are_hidden(self) -> None:
        payload = {
            "workflows": [
                {"name": "on", "scope": "global", "enabled": True, "steps": []},
                {"name": "off", "scope": "global", "enabled": False, "steps": []},
            ]
        }
        with patch("personalclaw.mcp_workflows._get", return_value=payload):
            out = _call_tool("workflow_list", {})
        assert "on" in out
        assert "off" not in out

    def test_empty_catalog_message(self) -> None:
        with patch("personalclaw.mcp_workflows._get", return_value={"workflows": []}):
            out = _call_tool("workflow_list", {})
        assert "No workflows defined" in out

    def test_scope_filter_forwarded_to_query(self) -> None:
        captured: dict[str, str] = {}

        def fake_get(path: str) -> dict:
            captured["path"] = path
            return {"workflows": []}

        with patch("personalclaw.mcp_workflows._get", side_effect=fake_get):
            _call_tool("workflow_list", {"scope": "agent", "tag": "git"})
        assert "scope=agent" in captured["path"]
        assert "tag=git" in captured["path"]

    def test_invalid_scope_rejected_by_validation(self) -> None:
        # scope is enum-gated; an out-of-allowlist value is a validation error,
        # surfaced as a string (the tool never reaches _get).
        with patch("personalclaw.mcp_workflows._get", return_value={"workflows": []}) as mget:
            out = _call_tool("workflow_list", {"scope": "bogus"})
        mget.assert_not_called()
        assert "error" in out.lower() or "invalid" in out.lower()

    def test_get_error_surfaced(self) -> None:
        with patch("personalclaw.mcp_workflows._get", return_value={"error": "boom"}):
            out = _call_tool("workflow_list", {})
        assert "boom" in out


class TestWorkflowRunResolveByName:
    """workflow_run / workflow_get accept a NAME as well as the opaque wf-<id>.

    The LLM only ever SEES workflow names (workflow_list surfaces names, not ids),
    so a by-name reference must resolve — else `workflow_run <name>` 404s and the
    run silently fails. Regression for that bug."""

    def _payloads(self):
        # /api/workflows/<name> 404s (route keys off id); /api/workflows (list) has it;
        # /api/workflows/<id> resolves.
        listing = {"workflows": [
            {"id": "wf-abc123", "name": "my-sop", "enabled": True,
             "scope": "global", "steps": [{"title": "Do the thing"}]},
        ]}
        detail = {"id": "wf-abc123", "name": "my-sop", "description": "",
                  "steps": [{"title": "Do the thing", "instruction": "carefully"}]}
        return listing, detail

    def test_run_resolves_name_to_id(self) -> None:
        listing, detail = self._payloads()

        def fake_get(path: str) -> dict:
            if path == "/api/workflows":
                return listing
            if path.endswith("/wf-abc123"):
                return detail
            return {"error": "not found"}  # by-name GET 404s

        with patch("personalclaw.mcp_workflows._get", side_effect=fake_get):
            out = _call_tool("workflow_run", {"workflow_id": "my-sop"})
        assert "Following workflow 'my-sop'" in out
        assert "Do the thing" in out
        assert "carefully" in out

    def test_get_resolves_name_to_id(self) -> None:
        listing, detail = self._payloads()

        def fake_get(path: str) -> dict:
            if path == "/api/workflows":
                return listing
            if path.endswith("/wf-abc123"):
                return detail
            return {"error": "not found"}

        with patch("personalclaw.mcp_workflows._get", side_effect=fake_get):
            out = _call_tool("workflow_get", {"workflow_id": "my-sop"})
        assert "my-sop" in out and "Do the thing" in out

    def test_direct_id_still_works_without_extra_list(self) -> None:
        _, detail = self._payloads()
        calls: list[str] = []

        def fake_get(path: str) -> dict:
            calls.append(path)
            if path.endswith("/wf-abc123"):
                return detail
            return {"error": "not found"}

        with patch("personalclaw.mcp_workflows._get", side_effect=fake_get):
            out = _call_tool("workflow_run", {"workflow_id": "wf-abc123"})
        assert "Following workflow 'my-sop'" in out
        # A direct-id hit resolves on the first GET — no fallback list lookup.
        assert calls == ["/api/workflows/wf-abc123"]

    def test_unknown_name_still_errors(self) -> None:
        def fake_get(path: str) -> dict:
            if path == "/api/workflows":
                return {"workflows": []}
            return {"error": "not found"}

        with patch("personalclaw.mcp_workflows._get", side_effect=fake_get):
            out = _call_tool("workflow_run", {"workflow_id": "does-not-exist"})
        assert "Error" in out


class TestBundledWorkflowSync:
    def test_starter_sops_sync_into_empty_dir(self, tmp_path) -> None:
        from personalclaw.workflows.native import _ensure_bundled_workflows

        _ensure_bundled_workflows(tmp_path)
        synced = {p.name for p in tmp_path.iterdir() if p.is_dir()}
        assert {"commit-changes", "debug-failing-test"} <= synced
        assert (tmp_path / "commit-changes" / "WORKFLOW.md").exists()

    def test_bundled_sops_parse_through_provider(self, tmp_path) -> None:
        from personalclaw.workflows.native import (
            NativeWorkflowProvider,
            _ensure_bundled_workflows,
        )

        _ensure_bundled_workflows(tmp_path)
        prov = NativeWorkflowProvider(storage_dir=str(tmp_path))
        wfs, total = asyncio.run(prov.list_workflows())
        assert total == 2
        by_name = {w.name: w for w in wfs}
        assert by_name["commit-changes"].steps
        assert by_name["commit-changes"].match_text
        assert all(w.enabled for w in wfs)

    def test_user_authored_workflow_left_untouched(self, tmp_path) -> None:
        from personalclaw.workflows.native import _ensure_bundled_workflows

        mine = tmp_path / "my-sop"
        mine.mkdir()
        (mine / "WORKFLOW.md").write_text("---\nid: mine\nname: my-sop\n---\n# my-sop\n")
        _ensure_bundled_workflows(tmp_path)
        # The user dir is preserved alongside the synced starters.
        assert (mine / "WORKFLOW.md").read_text().startswith("---\nid: mine")
