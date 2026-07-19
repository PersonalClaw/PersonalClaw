"""Semantic surfacing engine — scope gating + ranking + fallback."""

from __future__ import annotations

import tempfile

import pytest

from personalclaw.workflows import registry
from personalclaw.workflows.models import Workflow, WorkflowScope, WorkflowStep
from personalclaw.workflows.native import NativeWorkflowProvider
from personalclaw.workflows.surfacing import (
    TurnScope,
    best_match,
    eligible_workflows,
    render_injection,
    _is_eligible,
    _keyword_score,
)


@pytest.fixture
def temp_native():
    saved = dict(registry._providers)
    registry._providers.clear()
    registry.register_provider(NativeWorkflowProvider(storage_dir=tempfile.mkdtemp()))
    yield
    registry._providers.clear()
    registry._providers.update(saved)


def _wf(**kw) -> Workflow:
    kw.setdefault("id", "wf-x")
    kw.setdefault("name", "x")
    return Workflow(**kw)


# ── scope gating ─────────────────────────────────────────────────────────────


def test_global_always_eligible():
    wf = _wf(scope=WorkflowScope.GLOBAL)
    assert _is_eligible(wf, TurnScope())
    assert _is_eligible(wf, TurnScope(cwd="/anywhere", session_key="s1"))


def test_disabled_never_eligible():
    wf = _wf(scope=WorkflowScope.GLOBAL, enabled=False)
    assert not _is_eligible(wf, TurnScope())


def test_workspace_matches_cwd_only():
    wf = _wf(scope=WorkflowScope.WORKSPACE, scope_ref="/repo/a")
    assert _is_eligible(wf, TurnScope(cwd="/repo/a"))
    assert _is_eligible(wf, TurnScope(cwd="/repo/a/"))  # trailing slash normalized
    assert not _is_eligible(wf, TurnScope(cwd="/repo/b"))
    assert not _is_eligible(wf, TurnScope())  # no cwd


def test_session_matches_key_only():
    wf = _wf(scope=WorkflowScope.SESSION, scope_ref="sess-123")
    assert _is_eligible(wf, TurnScope(session_key="sess-123"))
    assert not _is_eligible(wf, TurnScope(session_key="sess-999"))
    assert not _is_eligible(wf, TurnScope())


def test_agent_scope_matches_resolved_agent_id():
    # Agent-scope eligibility: scope_ref (the agent binding id) must equal the
    # turn's resolved agent_id. (P5b switched from the old reference model.)
    wf = _wf(id="wf-7", scope=WorkflowScope.AGENT, scope_ref="acp:test-cli/researcher")
    assert _is_eligible(wf, TurnScope(agent_id="acp:test-cli/researcher"))
    assert not _is_eligible(wf, TurnScope(agent_id="default"))
    assert not _is_eligible(wf, TurnScope())


@pytest.mark.asyncio
async def test_eligible_workflows_unions_scopes(temp_native):
    await registry.create_workflow(name="g", scope="global", match_text="a")
    await registry.create_workflow(name="ws", scope="workspace", scope_ref="/repo/a", match_text="b")
    await registry.create_workflow(name="other", scope="workspace", scope_ref="/repo/b", match_text="c")
    elig = await eligible_workflows(TurnScope(cwd="/repo/a"))
    names = sorted(w.name for w in elig)
    assert names == ["g", "ws"]


# ── keyword fallback (no embedding model active in test env) ─────────────────


def test_keyword_score_per_phrase():
    mt = "committing changes, making a git commit, saving work"
    # "make a git commit" overlaps the 2nd phrase strongly
    assert _keyword_score("how do I make a git commit", mt) >= 0.7
    # unrelated query scores ~0
    assert _keyword_score("what is the weather", mt) < 0.3


def test_best_match_keyword_path_returns_match():
    wf = _wf(name="git-commit", scope=WorkflowScope.GLOBAL,
             match_text="committing changes, making a git commit, saving work",
             steps=[WorkflowStep(id="s1", title="Run tests")])
    m = best_match("I want to make a git commit", [wf])
    assert m is not None
    assert m.workflow.name == "git-commit"
    assert m.method == "keyword"


def test_best_match_no_match_returns_none():
    wf = _wf(name="git-commit", scope=WorkflowScope.GLOBAL,
             match_text="committing changes, making a git commit")
    assert best_match("the weather in paris today", [wf]) is None


def test_best_match_empty_query_or_candidates():
    wf = _wf(scope=WorkflowScope.GLOBAL, match_text="x y z")
    assert best_match("", [wf]) is None
    assert best_match("anything", []) is None


# ── embedding path + threshold + tiebreak (mock the embed fn) ────────────────


def _patch_embed(monkeypatch, query_vec, model="native:test"):
    """Make get_active_embed_fn return a fn yielding query_vec, and the spec
    return `model`."""
    import personalclaw.workflows.surfacing as s

    def fake_embed_query(query):
        return query_vec, model

    monkeypatch.setattr(s, "_embed_query", fake_embed_query)


def test_embedding_ranking_picks_highest_cosine(monkeypatch):
    _patch_embed(monkeypatch, [1.0, 0.0])
    near = _wf(id="a", name="near", scope=WorkflowScope.GLOBAL,
               match_text="x", match_embedding=[0.99, 0.14], embedding_model="native:test")
    far = _wf(id="b", name="far", scope=WorkflowScope.GLOBAL,
              match_text="y", match_embedding=[0.0, 1.0], embedding_model="native:test")
    m = best_match("q", [far, near], threshold=0.62)
    assert m is not None and m.workflow.name == "near" and m.method == "embedding"


def test_embedding_threshold_boundary(monkeypatch):
    _patch_embed(monkeypatch, [1.0, 0.0])
    # cosine([1,0],[0.5,0.5]) = 0.707 > 0.62 → matches
    hi = _wf(id="a", name="hi", scope=WorkflowScope.GLOBAL,
             match_text="x", match_embedding=[0.5, 0.5], embedding_model="native:test")
    assert best_match("q", [hi], threshold=0.62) is not None
    # raise threshold above 0.707 → no match
    assert best_match("q", [hi], threshold=0.8) is None


def test_scope_specificity_tiebreak_within_epsilon(monkeypatch):
    _patch_embed(monkeypatch, [1.0, 0.0])
    # Two near-equal cosines (within epsilon); session scope should win.
    glob = _wf(id="g", name="glob", scope=WorkflowScope.GLOBAL,
               match_text="x", match_embedding=[1.0, 0.02], embedding_model="native:test")
    sess = _wf(id="s", name="sess", scope=WorkflowScope.SESSION, scope_ref="k",
               match_text="y", match_embedding=[1.0, 0.0], embedding_model="native:test")
    m = best_match("q", [glob, sess], threshold=0.5)
    assert m.workflow.name == "sess"


def test_relevance_dominates_outside_epsilon(monkeypatch):
    _patch_embed(monkeypatch, [1.0, 0.0])
    # Global is much more relevant; specificity must NOT override a big gap.
    glob = _wf(id="g", name="glob", scope=WorkflowScope.GLOBAL,
               match_text="x", match_embedding=[1.0, 0.0], embedding_model="native:test")
    sess = _wf(id="s", name="sess", scope=WorkflowScope.SESSION, scope_ref="k",
               match_text="y", match_embedding=[0.4, 0.92], embedding_model="native:test")
    m = best_match("q", [sess, glob], threshold=0.5)
    assert m.workflow.name == "glob"


def test_stale_embedding_falls_back_to_keyword(monkeypatch):
    # active model differs from the candidate's embedding_model → keyword path.
    _patch_embed(monkeypatch, [1.0, 0.0], model="native:NEW-model")
    wf = _wf(name="git-commit", scope=WorkflowScope.GLOBAL,
             match_text="making a git commit",
             match_embedding=[1.0, 0.0], embedding_model="native:OLD-model")
    m = best_match("make a git commit", [wf])
    assert m is not None and m.method == "keyword"


# ── render ───────────────────────────────────────────────────────────────────


def test_render_injection_format():
    wf = _wf(name="git-commit", description="my flow", scope=WorkflowScope.GLOBAL,
             match_text="x",
             steps=[WorkflowStep(id="s1", title="Run tests", instruction="green first"),
                    WorkflowStep(id="s2", title="Commit")])
    from personalclaw.workflows.surfacing import WorkflowMatch

    block = render_injection(WorkflowMatch(workflow=wf, score=0.9, scope=WorkflowScope.GLOBAL))
    assert "PREFERRED WORKFLOW" in block
    assert "git-commit: my flow" in block
    assert "1. Run tests" in block
    assert "green first" in block
    assert "2. Commit" in block
    assert "End of preferred workflow" in block
