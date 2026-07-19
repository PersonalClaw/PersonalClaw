"""Semantic surfacing engine for workflows (E4-P2) — the net-new core.

Given the current turn's identities (a :class:`TurnScope`) and the user's
message, find the single best-matching workflow SOP to inject as guidance:

1. **Scope gate** (:func:`eligible_workflows`) — collect every workflow eligible
   for this turn (global always; workspace by cwd; agent by reference; session
   by key), enabled-only.
2. **Semantic rank** (:func:`best_match`) — embed the query, score eligible
   candidates by cosine vs their cached ``match_embedding``, return the single
   best above threshold. Degrades to keyword word-overlap when no embedding
   model is active or a candidate's embedding is missing/stale.
3. **Inject** (:func:`render_injection`) — render the winner as a guidance block.

Embedding is reused via ``embedding_providers.registry.get_active_embed_fn``
(a sync ``(text) -> list[float] | None``), so this whole module stays sync
except the registry fan-out (async). No ``build_message`` signature change is
needed (OD#6). The match is never allowed to break a turn — callers wrap it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from personalclaw.workflows.models import Workflow, WorkflowScope

# Default cosine threshold for a semantic match. Calibrated against
# vector_memory's short-text 0.55 / dedup 0.7; SOP match_text is short → 0.62.
# Tunable via config.workflows.match_threshold.
DEFAULT_MATCH_THRESHOLD = 0.62

# Keyword fallback gate (word-overlap), mirroring SkillsLoader._MIN_TRIGGER_OVERLAP.
_KEYWORD_OVERLAP_GATE = 0.7

# Within this cosine epsilon, the more-specific scope wins (session>agent>
# workspace>global). Relevance dominates outside the epsilon.
_TIE_EPSILON = 0.05

_SCOPE_SPECIFICITY = {
    WorkflowScope.SESSION: 3,
    WorkflowScope.AGENT: 2,
    WorkflowScope.WORKSPACE: 1,
    WorkflowScope.GLOBAL: 0,
}


@dataclass(frozen=True)
class TurnScope:
    """The identities of the current turn used to gate eligibility."""

    session_key: str | None = None
    agent: str | None = None  # AgentDefinition name (None/"personalclaw")
    cwd: str | None = None  # the working directory == workspace scope
    # The turn's resolved agent BINDING id (the form workflow scope_ref uses):
    # a native profile name (e.g. "default") or "acp:<cli>/<modeId>". An
    # agent-scoped SOP is eligible iff its scope_ref == this. Resolved by the
    # caller via composition.resolve_agent_id.
    agent_id: str = ""


@dataclass(frozen=True)
class WorkflowMatch:
    workflow: Workflow
    score: float
    scope: WorkflowScope
    method: str = "embedding"  # "embedding" | "keyword"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _norm_path(p: str | None) -> str:
    """Normalize a cwd for comparison (trailing slash, expanduser-agnostic)."""
    if not p:
        return ""
    return p.rstrip("/") or "/"


def _is_eligible(wf: Workflow, turn: TurnScope) -> bool:
    if not wf.enabled:
        return False
    if wf.scope == WorkflowScope.GLOBAL:
        return True
    if wf.scope == WorkflowScope.WORKSPACE:
        return bool(turn.cwd) and _norm_path(wf.scope_ref) == _norm_path(turn.cwd)
    if wf.scope == WorkflowScope.AGENT:
        # scope_ref carries the agent binding id; eligible only when the running
        # turn's resolved agent id matches (e.g. "default" or "acp:claude-code/<m>").
        return bool(turn.agent_id) and wf.scope_ref == turn.agent_id
    if wf.scope == WorkflowScope.SESSION:
        return bool(turn.session_key) and wf.scope_ref == turn.session_key
    return False


async def eligible_workflows(turn: TurnScope) -> list[Workflow]:
    """All enabled workflows eligible for this turn, across providers (union of
    scopes). Ranking happens in :func:`best_match`."""
    from personalclaw.workflows.registry import list_all_workflows

    workflows, _ = await list_all_workflows(limit=1000, offset=0)
    return [wf for wf in workflows if _is_eligible(wf, turn)]


def _keyword_score(query: str, match_text: str) -> float:
    """Best per-phrase word-overlap of the query against the SOP's match_text.

    match_text is authored as comma-separated intent phrases (e.g. "committing
    changes, making a git commit, saving work"). This mirrors
    SkillsLoader.get_triggered_skills exactly: each phrase scores
    ``len(phrase_words & query_words) / len(phrase_words)`` and the best phrase
    wins. Per-phrase (vs whole-text) keeps the ratio meaningful — one matching
    phrase shouldn't be diluted by other phrases in the SOP's intent list.
    """
    if not match_text.strip():
        return 0.0
    query_words = set(re.findall(r"\w+", query.lower()))
    best = 0.0
    for phrase in match_text.split(","):
        phrase_words = set(re.findall(r"\w+", phrase.lower()))
        if not phrase_words:
            continue
        best = max(best, len(phrase_words & query_words) / len(phrase_words))
    return best


def _embed_query(query: str):
    """Return the active sync embed fn's vector for the query, or None."""
    try:
        from personalclaw.embedding_providers.registry import (
            _active_embedding_spec,
            get_active_embed_fn,
        )
    except Exception:
        return None, ""
    try:
        fn = get_active_embed_fn()
    except Exception:
        fn = None
    if fn is None:
        return None, ""
    model = ""
    try:
        spec = _active_embedding_spec()
        if spec:
            model = f"{spec[0]}:{spec[1]}"
    except Exception:
        model = ""
    try:
        return fn(query), model
    except Exception:
        return None, model


def best_match(
    query: str,
    candidates: list[Workflow],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> WorkflowMatch | None:
    """Return the single best candidate above ``threshold``, or None.

    Embedding-preferred: cosine vs each candidate's cached ``match_embedding``
    (when an embedding fn is active and the candidate's embedding matches the
    active model). Falls back to keyword word-overlap for candidates lacking a
    usable embedding (or when no embedding model is active at all). Ties within
    ``_TIE_EPSILON`` break toward the more-specific scope.
    """
    if not query.strip() or not candidates:
        return None

    query_vec, active_model = _embed_query(query)
    scored: list[WorkflowMatch] = []

    for wf in candidates:
        score = 0.0
        method = "keyword"
        usable_embedding = (
            query_vec is not None
            and wf.match_embedding
            and (not active_model or not wf.embedding_model or wf.embedding_model == active_model)
        )
        if usable_embedding:
            score = _cosine(query_vec, wf.match_embedding)
            method = "embedding"
            gate = threshold
        else:
            # Keyword fallback over match_text (or name+description if empty).
            target = wf.match_text or f"{wf.name} {wf.description}"
            score = _keyword_score(query, target)
            gate = _KEYWORD_OVERLAP_GATE
        if score >= gate:
            scored.append(WorkflowMatch(workflow=wf, score=score, scope=wf.scope, method=method))

    if not scored:
        return None

    # Sort by (score desc, specificity desc). The specificity tiebreak only
    # matters within _TIE_EPSILON; outside it, score dominates (the sort already
    # respects that since score is the primary key).
    def _key(m: WorkflowMatch) -> tuple[float, int]:
        return (m.score, _SCOPE_SPECIFICITY.get(m.scope, 0))

    scored.sort(key=_key, reverse=True)
    top = scored[0]
    # If a slightly-lower-scored but more-specific candidate is within epsilon,
    # prefer it.
    for m in scored[1:]:
        if top.score - m.score > _TIE_EPSILON:
            break
        if _SCOPE_SPECIFICITY.get(m.scope, 0) > _SCOPE_SPECIFICITY.get(top.scope, 0):
            top = m
    return top


async def surface_for_turn(
    query: str, turn: TurnScope, threshold: float | None = None
) -> WorkflowMatch | None:
    """eligible_workflows(turn) → best_match(query, …). Async entry point
    (used by preview-match). Returns None when nothing matches."""
    if threshold is None:
        threshold = _configured_threshold()
    candidates = await eligible_workflows(turn)
    return best_match(query, candidates, threshold=threshold)


def _eligible_workflows_blocking(turn: TurnScope) -> list[Workflow]:
    """Synchronous eligibility read for the per-turn injection path.

    ``build_message`` is sync (OD#6), so this drives the async registry fan-out
    to completion — directly when no loop is running, else on a worker thread
    (mirrors embedding_providers.registry._sync_embed's loop-bridge). Keeps the
    surfacing call out of ``build_message``'s signature/async-ness.
    """
    import asyncio

    async def _run() -> list[Workflow]:
        return await eligible_workflows(turn)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _run()).result(timeout=30)


def surface_for_turn_sync(
    query: str, turn: TurnScope, threshold: float | None = None
) -> WorkflowMatch | None:
    """Synchronous entry point the context builder calls per turn (P3).

    Mirrors the triggered-skills injection: runs on every turn against the
    current message, so a matching SOP surfaces whenever the turn matches it
    (turn 1 or turn N), not just turn-0. Never raises — returns None on any
    failure so a surfacing error can't break a turn.
    """
    try:
        if threshold is None:
            threshold = _configured_threshold()
        candidates = _eligible_workflows_blocking(turn)
        return best_match(query, candidates, threshold=threshold)
    except Exception:
        return None


def _configured_threshold() -> float:
    try:
        from personalclaw.config.loader import AppConfig

        cfg = AppConfig.load()
        return float(getattr(cfg.workflows, "match_threshold", DEFAULT_MATCH_THRESHOLD))
    except Exception:
        return DEFAULT_MATCH_THRESHOLD


def render_injection(match: WorkflowMatch, all_workflows: list[Workflow] | None = None) -> str:
    """Render the matched workflow as an injectable guidance block (P3).

    Ref-steps are inline-flattened (recursively, depth-bounded + cycle-guarded)
    into the referenced workflow's steps, with a provenance marker noting which
    sub-workflow a step came from. Embedded refs are pulled in here via expansion;
    they are NOT independently match-fired (only top-level eligible SOPs are).
    """
    from personalclaw.workflows.composition import expand_steps

    wf = match.workflow
    if all_workflows is None:
        all_workflows = _all_workflows_blocking()
    by_id = {w.id: w for w in all_workflows}
    by_id.setdefault(wf.id, wf)

    lines = [
        "[PREFERRED WORKFLOW — the user has a defined SOP for this kind of task.",
        "Follow these steps in order unless the user directs otherwise.]",
        f"## {wf.name}: {wf.description}".rstrip(": ").rstrip(),
    ]
    for i, step in enumerate(expand_steps(wf, by_id), 1):
        # Provenance: tag a step with its sub-workflow when it came from a ref
        # (depth>0), so the agent can trace a flattened line back to its SOP.
        suffix = f"  _(from: {step.source_workflow})_" if step.depth > 0 else ""
        lines.append(f"{i}. {step.title}{suffix}")
        if step.instruction:
            lines.append(f"   {step.instruction}")
    lines.append("[End of preferred workflow]")
    return "\n".join(lines)


def _all_workflows_blocking() -> list[Workflow]:
    """Synchronous full-workflow read for ref expansion at inject time (mirrors
    ``_eligible_workflows_blocking``'s loop-bridge)."""
    import asyncio

    from personalclaw.workflows.registry import list_all_workflows

    async def _run() -> list[Workflow]:
        wfs, _ = await list_all_workflows(limit=1000, offset=0)
        return wfs

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _run()).result(timeout=30)
