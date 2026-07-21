"""EVOLVE-WORKFLOWS (#28) — scope promotion + end-of-session cleanup +
agent-authored scope_ref auto-binding."""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.workflows import native, registry
from personalclaw.workflows.models import WorkflowScope


@pytest.fixture
def store(tmp_path):
    prov = native.NativeWorkflowProvider(storage_dir=str(tmp_path / "wf"))
    registry._providers.clear()
    registry.register_provider(prov)
    yield prov
    registry._providers.clear()


def _run(coro):
    return asyncio.run(coro)


async def _mk(scope, scope_ref="", name="wf"):
    return await registry.create_workflow(
        name=name,
        description="d",
        steps=[{"title": "do it"}],
        scope=scope,
        scope_ref=scope_ref,
    )


# ── scope promotion ladder ──


def test_promote_session_to_agent(store):
    async def go():
        wf = await _mk("session", "sess-1")
        out = await registry.promote_workflow(wf.id, "agent", scope_ref="default")
        return out

    out = _run(go())
    assert out.scope == WorkflowScope.AGENT
    assert out.scope_ref == "default"


def test_promote_to_global_clears_ref(store):
    async def go():
        wf = await _mk("agent", "default")
        return await registry.promote_workflow(wf.id, "global")

    out = _run(go())
    assert out.scope == WorkflowScope.GLOBAL
    assert out.scope_ref == ""


def test_promote_skips_rungs(store):
    async def go():
        wf = await _mk("session", "sess-1")
        return await registry.promote_workflow(wf.id, "global")

    out = _run(go())
    assert out.scope == WorkflowScope.GLOBAL


def test_cannot_demote(store):
    async def go():
        wf = await _mk("workspace", "/proj")
        await registry.promote_workflow(wf.id, "session", scope_ref="s")

    with pytest.raises(ValueError, match="only widens"):
        _run(go())


def test_cannot_promote_to_same_scope(store):
    async def go():
        wf = await _mk("agent", "default")
        await registry.promote_workflow(wf.id, "agent")

    with pytest.raises(ValueError, match="only widens"):
        _run(go())


def test_promote_workspace_requires_ref(store):
    async def go():
        wf = await _mk("session", "sess-1")
        await registry.promote_workflow(wf.id, "workspace")  # no scope_ref

    with pytest.raises(ValueError, match="workspace requires"):
        _run(go())


def test_promote_unknown_scope(store):
    async def go():
        wf = await _mk("session", "sess-1")
        await registry.promote_workflow(wf.id, "everywhere")

    with pytest.raises(ValueError, match="unknown scope"):
        _run(go())


def test_promote_missing_workflow(store):
    assert _run(registry.promote_workflow("wf-nope", "global")) is None


# ── end-of-session cleanup ──


def test_session_cleanup_deletes_only_matching_session(store):
    async def go():
        await _mk("session", "sess-A", name="a")
        await _mk("session", "sess-B", name="b")
        await _mk("global", "", name="g")
        deleted = await registry.delete_session_workflows("sess-A")
        remaining, _ = await registry.list_all_workflows(limit=100, offset=0)
        return deleted, {w.name for w in remaining}

    deleted, names = _run(go())
    assert len(deleted) == 1
    assert names == {"b", "g"}  # only sess-A's workflow swept


def test_session_cleanup_promoted_workflow_survives(store):
    """A session workflow promoted to global is no longer session-scoped → kept."""

    async def go():
        wf = await _mk("session", "sess-A", name="keeper")
        await registry.promote_workflow(wf.id, "global")
        deleted = await registry.delete_session_workflows("sess-A")
        remaining, _ = await registry.list_all_workflows(limit=100, offset=0)
        return deleted, {w.name for w in remaining}

    deleted, names = _run(go())
    assert deleted == []  # nothing session-scoped left for sess-A
    assert names == {"keeper"}


def test_session_cleanup_no_matches_is_noop(store):
    async def go():
        await _mk("global", "", name="g")
        return await registry.delete_session_workflows("sess-X")

    assert _run(go()) == []


# ── lifecycle composition (cleanup runs alongside prior callback) ──


def test_with_session_workflow_cleanup_runs_both(store):
    from personalclaw.workflows.lifecycle import with_session_workflow_cleanup

    calls: list[str] = []

    async def prior(key):
        calls.append(f"prior:{key}")

    async def go():
        await _mk("session", "sess-Z", name="z")
        cb = with_session_workflow_cleanup(prior)
        await cb("sess-Z")
        remaining, _ = await registry.list_all_workflows(limit=100, offset=0)
        return [w.name for w in remaining]

    remaining = _run(go())
    assert calls == ["prior:sess-Z"]  # prior ran
    assert remaining == []  # and the session workflow was swept


def test_cleanup_survives_prior_failure(store):
    from personalclaw.workflows.lifecycle import with_session_workflow_cleanup

    async def boom(key):
        raise RuntimeError("consolidation blew up")

    async def go():
        await _mk("session", "sess-Q", name="q")
        cb = with_session_workflow_cleanup(boom)
        await cb("sess-Q")  # must not raise
        remaining, _ = await registry.list_all_workflows(limit=100, offset=0)
        return [w.name for w in remaining]

    # prior raised, but cleanup still swept the session workflow
    assert _run(go()) == []


# ── agent-facing tool dispatch: scope_ref auto-binding + promote ──


class TestWorkflowCreateAutoBind:
    def test_session_scope_binds_current_session(self):
        from unittest.mock import patch

        from personalclaw import mcp_workflows

        with (
            patch.object(mcp_workflows, "_resolve_session_key", return_value="sess-XYZ"),
            patch.object(
                mcp_workflows, "_post", return_value={"name": "wf", "scope": "session"}
            ) as mp,
        ):
            mcp_workflows._call_tool_inner(
                "workflow_create", {"name": "wf", "steps": [{"title": "do"}], "scope": "session"}
            )
            assert mp.call_args[0][1]["scope_ref"] == "sess-XYZ"

    def test_agent_scope_binds_current_agent(self):
        from unittest.mock import patch

        from personalclaw import mcp_core, mcp_workflows

        # The agent-id contextvar lives in mcp_core; mcp_workflows reads the same object.
        tok = mcp_core.set_current_agent_id("default")
        try:
            with patch.object(
                mcp_workflows, "_post", return_value={"name": "wf", "scope": "agent"}
            ) as mp:
                mcp_workflows._call_tool_inner(
                    "workflow_create", {"name": "wf", "steps": [{"title": "do"}], "scope": "agent"}
                )
                assert mp.call_args[0][1]["scope_ref"] == "default"
        finally:
            mcp_core.reset_current_agent_id(tok)

    def test_agent_scope_without_binding_errors(self):
        from unittest.mock import patch

        from personalclaw import mcp_core, mcp_workflows

        tok = mcp_core.set_current_agent_id("")
        try:
            with patch.object(mcp_workflows, "_resolve_session_key", return_value=""):
                out = mcp_workflows._call_tool_inner(
                    "workflow_create", {"name": "w", "steps": [{"title": "d"}], "scope": "agent"}
                )
                assert out.startswith("Error")
        finally:
            mcp_core.reset_current_agent_id(tok)

    def test_explicit_scope_ref_wins(self):
        from unittest.mock import patch

        from personalclaw import mcp_workflows

        with (
            patch.object(mcp_workflows, "_resolve_session_key", return_value="auto-sess"),
            patch.object(
                mcp_workflows, "_post", return_value={"name": "wf", "scope": "session"}
            ) as mp,
        ):
            mcp_workflows._call_tool_inner(
                "workflow_create",
                {
                    "name": "wf",
                    "steps": [{"title": "do"}],
                    "scope": "session",
                    "scope_ref": "explicit",
                },
            )
            assert mp.call_args[0][1]["scope_ref"] == "explicit"


class TestWorkflowPromoteDispatch:
    def test_promote_posts_to_endpoint(self):
        from unittest.mock import patch

        from personalclaw import mcp_workflows

        with patch.object(
            mcp_workflows, "_post", return_value={"name": "wf", "scope": "global"}
        ) as mp:
            out = mcp_workflows._call_tool_inner(
                "workflow_promote", {"workflow_id": "wf-1", "scope": "global"}
            )
            assert mp.call_args[0][0] == "/api/workflows/wf-1/promote"
            assert "global" in out

    def test_promote_requires_args(self):
        from personalclaw import mcp_workflows

        assert mcp_workflows._call_tool_inner("workflow_promote", {"scope": "global"}).startswith(
            "Error"
        )
        assert mcp_workflows._call_tool_inner("workflow_promote", {"workflow_id": "w"}).startswith(
            "Error"
        )

    def test_promote_in_schema_and_tool_list(self):
        from personalclaw.mcp_workflows import _list_tools
        from personalclaw.validation import MCP_CORE_SCHEMAS

        assert "workflow_promote" in {t["name"] for t in _list_tools()}
        assert "workflow_promote" in MCP_CORE_SCHEMAS
