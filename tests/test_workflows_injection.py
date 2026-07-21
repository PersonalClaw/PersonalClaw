"""Workflow surfacing wired into ContextBuilder.build_message.

The [PREFERRED WORKFLOW] block injects on any matching turn (not just turn-0),
respects scope isolation, is skipped for custom ACP agents, and honors the
workflows.enabled kill-switch. Uses the keyword fallback (no embedding model
active in the test env) so it stays deterministic.
"""

from __future__ import annotations

import tempfile

import pytest

from personalclaw.context import ContextBuilder
from personalclaw.workflows import registry
from personalclaw.workflows.native import NativeWorkflowProvider


@pytest.fixture
def temp_native():
    saved = dict(registry._providers)
    registry._providers.clear()
    registry.register_provider(NativeWorkflowProvider(storage_dir=tempfile.mkdtemp()))
    yield
    registry._providers.clear()
    registry._providers.update(saved)


def _build(text: str, *, cwd=None, agent=None, resolved_agent_id=None, is_new=False) -> str:
    cb = ContextBuilder()
    full, _ = cb.build_message(
        text,
        is_new,
        session_key="sess-1",
        agent=agent,
        cwd=cwd,
        resolved_agent_id=resolved_agent_id if resolved_agent_id is not None else (agent or ""),
    )
    return full


@pytest.mark.asyncio
async def test_workspace_sop_injects_on_matching_followup_turn(temp_native):
    await registry.create_workflow(
        name="git-commit",
        scope="workspace",
        scope_ref="/repo/a",
        match_text="committing changes, making a git commit, saving work",
        steps=[{"title": "Run tests"}, {"title": "Write conventional message"}],
    )
    # A FOLLOW-UP turn (is_new=False) whose text matches → SOP injects. This is
    # the crux: surfacing is not gated on the first turn.
    out = _build("help me make a git commit", cwd="/repo/a")
    assert "[PREFERRED WORKFLOW" in out
    assert "git-commit" in out
    assert "Run tests" in out


@pytest.mark.asyncio
async def test_scope_isolation_other_cwd_no_injection(temp_native):
    await registry.create_workflow(
        name="git-commit",
        scope="workspace",
        scope_ref="/repo/a",
        match_text="committing changes, making a git commit",
    )
    out = _build("help me make a git commit", cwd="/repo/b")
    assert "[PREFERRED WORKFLOW" not in out


@pytest.mark.asyncio
async def test_global_sop_injects_regardless_of_cwd(temp_native):
    await registry.create_workflow(
        name="git-commit",
        scope="global",
        match_text="committing changes, making a git commit",
    )
    out = _build("how do I make a git commit", cwd="/anywhere")
    assert "[PREFERRED WORKFLOW" in out


@pytest.mark.asyncio
async def test_no_match_no_injection(temp_native):
    await registry.create_workflow(
        name="git-commit",
        scope="global",
        match_text="committing changes, making a git commit",
    )
    out = _build("what is the weather in paris", cwd="/repo/a")
    assert "[PREFERRED WORKFLOW" not in out


@pytest.mark.asyncio
async def test_global_sop_injects_for_custom_agent(temp_native):
    # P5b: workflow surfacing runs for ALL agents (scope gating controls
    # relevance), so a global SOP now surfaces even on a custom/ACP agent's turn.
    # (Previously gated out by is_custom — that gate was removed so agent-scoped
    # SOPs can reach their specific custom agent.)
    await registry.create_workflow(
        name="git-commit",
        scope="global",
        match_text="committing changes, making a git commit",
    )
    out = _build("make a git commit", cwd="/repo/a", agent="my-custom-agent")
    assert "[PREFERRED WORKFLOW" in out


@pytest.mark.asyncio
async def test_disabled_kill_switch(temp_native, monkeypatch):
    await registry.create_workflow(
        name="git-commit",
        scope="global",
        match_text="committing changes, making a git commit",
    )
    # Force workflows.enabled = False via the loaded config.
    from personalclaw.config.loader import AppConfig

    real_load = AppConfig.load

    def _load_disabled(*a, **k):
        cfg = real_load(*a, **k)
        object.__setattr__(cfg.workflows, "enabled", False)
        return cfg

    monkeypatch.setattr(AppConfig, "load", staticmethod(_load_disabled))
    out = _build("make a git commit", cwd="/repo/a")
    assert "[PREFERRED WORKFLOW" not in out


@pytest.mark.asyncio
async def test_agent_scoped_sop_injects_for_matching_agent(temp_native):
    # P5b scope_ref model: an agent-scoped SOP carries scope_ref = the agent id
    # and surfaces only on that agent's turns.
    await registry.create_workflow(
        name="review-flow",
        scope="agent",
        scope_ref="reviewer",
        match_text="reviewing code, doing a code review",
    )
    # Different agent → not eligible.
    out_other = _build(
        "do a code review", cwd="/x", agent="personalclaw", resolved_agent_id="personalclaw"
    )
    assert "[PREFERRED WORKFLOW" not in out_other
    # Matching resolved agent id → eligible and injected.
    out_match = _build("do a code review", cwd="/x", agent="reviewer", resolved_agent_id="reviewer")
    assert "[PREFERRED WORKFLOW" in out_match
