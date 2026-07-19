"""Workflow composition — referential integrity, cycle prevention, and ref
expansion for composable SOPs (P5b, reuses the DAG discipline of seam S3).

A workflow step may REFERENCE another workflow (``step.ref`` = its id) so SOPs
compose. This module is the server-authoritative graph layer:

* :func:`validate_refs` — on write, every ``ref`` must point to an existing
  workflow and the whole composition graph must stay acyclic. Raises
  :class:`WorkflowIntegrityError` (dangling) / :class:`WorkflowCycleError` (cycle).
* :func:`referrers` — workflows that reference a given id (delete-policy: refuse +
  list these).
* :func:`expand_steps` — recursively inline-flatten ref-steps into the referenced
  workflow's steps at surface/inject time, depth-bounded (``MAX_DEPTH``) and
  cycle-guarded, tagging each expanded step with its source workflow for
  provenance.
* :func:`build_graph` — adjacency + cycle list for ``GET /api/workflows/{id}/graph``.

Refs are resolved live (by id, not snapshot), so editing a referenced workflow
flows through to every parent — single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass

from personalclaw.workflows.models import Workflow, WorkflowStep

# Max ref-expansion depth at surface time. Beyond this we stop recursing and
# leave a marker step (cycles are already rejected on write, but expansion
# defends anyway).
MAX_DEPTH = 5


class WorkflowIntegrityError(ValueError):
    """A step references a workflow id that doesn't exist."""


class WorkflowCycleError(ValueError):
    """A ref edge would make the composition graph cyclic."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        super().__init__("workflow reference cycle: " + " → ".join(cycle))


def _ref_ids(wf: Workflow) -> list[str]:
    return [s.ref for s in wf.steps if s.ref]


def _ref_map(workflows: list[Workflow]) -> dict[str, list[str]]:
    """id → referenced workflow ids (filtered to known workflows)."""
    known = {w.id for w in workflows}
    return {w.id: [r for r in _ref_ids(w) if r in known] for w in workflows}


def _find_cycle_from(start: str, ref_map: dict[str, list[str]]) -> list[str]:
    """DFS from ``start``; return a cycle path if one is reachable, else []."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        color[node] = GRAY
        stack.append(node)
        for nxt in ref_map.get(node, []):
            if color.get(nxt, WHITE) == GRAY:
                i = stack.index(nxt)
                return stack[i:] + [nxt]
            if color.get(nxt, WHITE) == WHITE:
                found = visit(nxt)
                if found:
                    return found
        color[node] = BLACK
        stack.pop()
        return []

    return visit(start)


def validate_refs(target: Workflow, all_workflows: list[Workflow]) -> None:
    """Validate ``target``'s ref-steps against the full workflow set.

    Raises :class:`WorkflowIntegrityError` if any ref is dangling, or
    :class:`WorkflowCycleError` if including ``target`` makes the graph cyclic.
    ``all_workflows`` should already include (the updated) ``target``.
    """
    known = {w.id for w in all_workflows}
    for r in _ref_ids(target):
        if r == target.id:
            raise WorkflowCycleError([target.id, target.id])
        if r not in known:
            raise WorkflowIntegrityError(f"step references unknown workflow id: {r!r}")
    cycle = _find_cycle_from(target.id, _ref_map(all_workflows))
    if cycle:
        raise WorkflowCycleError(cycle)


def validate_scope(wf: Workflow) -> None:
    """Soft-validate scope wiring: an ``agent``-scoped SOP needs a ``scope_ref``
    (the agent binding id), else it can never match a turn. Resolvability of the
    id against live runtimes is NOT checked (bindings come/go as runtimes connect
    — that stays a UI warning), only that it's present."""
    from personalclaw.workflows.models import WorkflowScope

    if wf.scope == WorkflowScope.AGENT and not (wf.scope_ref or "").strip():
        raise WorkflowIntegrityError("agent-scoped workflow requires a scope_ref (the agent id)")


def referrers(workflow_id: str, all_workflows: list[Workflow]) -> list[Workflow]:
    """Workflows that reference ``workflow_id`` via a ref-step."""
    return [w for w in all_workflows if workflow_id in _ref_ids(w) and w.id != workflow_id]


@dataclass
class ExpandedStep:
    """A flattened step in an expanded workflow, with provenance."""

    title: str
    instruction: str = ""
    source_workflow: str = ""  # name of the workflow this step came from (provenance)
    depth: int = 0             # nesting depth (0 = the top workflow's own steps)


def expand_steps(
    wf: Workflow,
    by_id: dict[str, Workflow],
    *,
    max_depth: int = MAX_DEPTH,
) -> list[ExpandedStep]:
    """Inline-flatten ``wf``'s steps, recursively expanding ref-steps into the
    referenced workflow's steps. Depth-bounded + cycle-guarded; each emitted step
    carries the name of the workflow it came from for traceability."""
    out: list[ExpandedStep] = []

    def walk(w: Workflow, depth: int, on_stack: set[str]) -> None:
        for step in w.steps:
            if not step.is_ref():
                out.append(ExpandedStep(
                    title=step.title, instruction=step.instruction,
                    source_workflow=w.name, depth=depth,
                ))
                continue
            sub = by_id.get(step.ref)
            if sub is None:
                out.append(ExpandedStep(
                    title=f"[missing workflow: {step.ref}]", source_workflow=w.name, depth=depth,
                ))
                continue
            if depth + 1 > max_depth or sub.id in on_stack:
                reason = "max workflow depth reached" if depth + 1 > max_depth else "recursive reference"
                out.append(ExpandedStep(
                    title=f"[{reason}: {sub.name}]", source_workflow=w.name, depth=depth,
                ))
                continue
            walk(sub, depth + 1, on_stack | {sub.id})

    walk(wf, 0, {wf.id})
    return out


def build_graph(workflow_id: str, all_workflows: list[Workflow]) -> dict:
    """Composition tree + cycle list for ``GET /api/workflows/{id}/graph``."""
    by_id = {w.id: w for w in all_workflows}
    target = by_id.get(workflow_id)
    if target is None:
        return {"nodes": [], "edges": [], "cycles": [], "expanded": []}
    # Nodes/edges reachable from the target via refs.
    nodes: dict[str, Workflow] = {}
    edges: list[dict] = []

    def collect(w: Workflow, on_stack: set[str]) -> None:
        nodes[w.id] = w
        for r in _ref_ids(w):
            edges.append({"from": w.id, "to": r})
            sub = by_id.get(r)
            if sub and sub.id not in on_stack:
                collect(sub, on_stack | {sub.id})

    collect(target, {target.id})
    cycle = _find_cycle_from(workflow_id, _ref_map(all_workflows))
    return {
        "nodes": [{"id": w.id, "name": w.name} for w in nodes.values()],
        "edges": edges,
        "cycles": [cycle] if cycle else [],
        "expanded": [
            {"title": s.title, "instruction": s.instruction,
             "source_workflow": s.source_workflow, "depth": s.depth}
            for s in expand_steps(target, by_id)
        ],
    }


# ── agent identity resolution (agent-scope eligibility) ──

def resolve_agent_id(agent: str | None, provider_kind: str | None, provider_agent: str | None) -> str:
    """Normalize the turn's agent identity to the binding-id form the workflow
    ``scope_ref`` uses (matching the FE agent catalog values):

    - native turn → the bare profile name (e.g. ``default``, ``personalclaw-loop``)
    - ACP turn   → ``acp:<cli>/<modeId>`` (e.g. ``acp:claude-code/<agent>``)

    ``provider_kind`` is the resolved provider (``native`` or ``acp:<cli>``);
    ``provider_agent`` is the ACP-internal modeId/agent. Falls back to ``agent``.
    """
    kind = (provider_kind or "").strip()
    if kind.startswith("acp:"):
        cli = kind.split(":", 1)[1]
        mode = (provider_agent or "").strip()
        return f"acp:{cli}/{mode}" if mode else f"acp:{cli}"
    # native (or unknown) → bare profile name
    return (agent or provider_agent or "").strip()
