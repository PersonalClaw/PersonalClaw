"""Workflow provider registry — aggregates workflows across all backends."""

import logging
from typing import Any

from personalclaw.workflows.models import Workflow, WorkflowScope
from personalclaw.workflows.provider import WorkflowProvider

logger = logging.getLogger(__name__)

_providers: dict[str, WorkflowProvider] = {}


def register_provider(provider: WorkflowProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> WorkflowProvider | None:
    return _providers.get(name)


def list_providers() -> list[str]:
    return list(_providers.keys())


def _ensure_native() -> None:
    if "native" not in _providers:
        from personalclaw.workflows.native import NativeWorkflowProvider

        register_provider(NativeWorkflowProvider())


async def list_all_workflows(
    scope: WorkflowScope | None = None,
    scope_ref: str | None = None,
    tag: str | None = None,
    provider_filter: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[Workflow], int]:
    """Aggregate workflows from all providers (or a specific one)."""
    _ensure_native()
    all_wf: list[Workflow] = []
    sources = (
        {provider_filter: _providers[provider_filter]}
        if provider_filter and provider_filter in _providers
        else _providers
    )
    for prov in sources.values():
        try:
            wfs, _ = await prov.list_workflows(
                scope=scope, scope_ref=scope_ref, tag=tag, limit=1000, offset=0
            )
            all_wf.extend(wfs)
        except Exception:
            logger.warning("Workflow provider %s failed to list", prov.name, exc_info=True)

    all_wf.sort(key=lambda w: w.updated_at or w.created_at, reverse=True)
    total = len(all_wf)
    return all_wf[offset : offset + limit], total


async def create_workflow(provider_name: str = "native", **fields: Any) -> Workflow:
    _ensure_native()
    prov = _providers.get(provider_name)
    if not prov:
        raise ValueError(f"Unknown workflow provider: {provider_name}")
    if prov.readonly:
        raise ValueError(f"Provider '{provider_name}' is read-only")
    wf = await prov.create_workflow(**fields)
    # Server-authoritative referential integrity + acyclicity + scope wiring. On
    # a violation, roll back the just-created workflow so a bad def never persists.
    try:
        await _validate_composition(wf)
    except ValueError:
        await prov.delete_workflow(wf.id)
        raise
    return wf


async def _validate_composition(wf: Workflow) -> None:
    """Validate a (just-written) workflow's ref-steps + scope against the full set."""
    from personalclaw.workflows.composition import validate_refs, validate_scope

    validate_scope(wf)
    all_wf, _ = await list_all_workflows(limit=1000, offset=0)
    # Ensure the target's latest state is in the set (it was just written).
    others = [w for w in all_wf if w.id != wf.id]
    validate_refs(wf, [*others, wf])


async def update_workflow(
    workflow_id: str, provider_name: str | None = None, **fields: Any
) -> Workflow | None:
    _ensure_native()
    if provider_name and provider_name in _providers:
        prov = _providers[provider_name]
    else:
        for p in _providers.values():
            w = await p.get_workflow(workflow_id)
            if w:
                prov = p
                break
        else:
            return None
    if prov.readonly:
        raise ValueError(f"Provider '{prov.name}' is read-only")
    # A steps edit can change refs; a scope/scope_ref edit can break agent-scope
    # wiring. Validate (and roll back) when either is touched.
    validates = any(k in fields for k in ("steps", "scope", "scope_ref"))
    prior = await prov.get_workflow(workflow_id) if validates else None
    wf = await prov.update_workflow(workflow_id, **fields)
    if wf is not None and validates:
        try:
            await _validate_composition(wf)
        except ValueError:
            if prior is not None:
                await prov.update_workflow(
                    workflow_id,
                    steps=[s.to_dict() for s in prior.steps],
                    scope=prior.scope.value,
                    scope_ref=prior.scope_ref,
                )
            raise
    return wf


async def delete_workflow(workflow_id: str, provider_name: str | None = None) -> bool:
    _ensure_native()
    if provider_name and provider_name in _providers:
        prov = _providers[provider_name]
    else:
        for p in _providers.values():
            w = await p.get_workflow(workflow_id)
            if w:
                prov = p
                break
        else:
            return False
    if prov.readonly:
        raise ValueError(f"Provider '{prov.name}' is read-only")
    # Refuse the delete if other workflows reference this one — list the referrers
    # so the user can detach them first (no silent cascade-mutation of siblings).
    from personalclaw.workflows.composition import referrers

    all_wf, _ = await list_all_workflows(limit=1000, offset=0)
    refs = referrers(workflow_id, all_wf)
    if refs:
        raise WorkflowReferencedError([{"id": w.id, "name": w.name} for w in refs])
    return await prov.delete_workflow(workflow_id)


class WorkflowReferencedError(ValueError):
    """Delete refused — other workflows reference this one (lists referrers)."""

    def __init__(self, referrers: list[dict]):
        self.referrers = referrers
        names = ", ".join(r["name"] for r in referrers)
        super().__init__(f"workflow is referenced by: {names}")


# The promotion ladder — a workflow can only widen its visibility, one or more
# rungs UP this order (session is narrowest, global widest). EVOLVE-WORKFLOWS #28.
_SCOPE_LADDER = [
    WorkflowScope.SESSION,
    WorkflowScope.AGENT,
    WorkflowScope.WORKSPACE,
    WorkflowScope.GLOBAL,
]


async def promote_workflow(
    workflow_id: str,
    target_scope: WorkflowScope | str,
    *,
    scope_ref: str | None = None,
    provider_name: str | None = None,
) -> Workflow | None:
    """Widen a workflow's visibility up the ladder (session→agent→workspace→global).

    A SOP proves itself in a narrow scope, then graduates. Promotion may only move
    UP the ladder (never demote — that's a manual edit). The new ``scope_ref`` is:
    cleared for ``global``; required for ``agent``/``workspace`` (the caller passes
    the agent id / cwd; for ``agent`` the current value is reused if already set).
    Returns the updated workflow, or raises ValueError on an invalid transition.
    """
    if isinstance(target_scope, str):
        try:
            target_scope = WorkflowScope(target_scope)
        except ValueError:
            raise ValueError(f"unknown scope: {target_scope!r}")

    wf = await get_workflow(workflow_id, provider_name=provider_name)
    if wf is None:
        return None
    cur_rung = _SCOPE_LADDER.index(wf.scope)
    new_rung = _SCOPE_LADDER.index(target_scope)
    if new_rung <= cur_rung:
        raise ValueError(
            f"cannot promote from {wf.scope.value} to {target_scope.value} "
            "(promotion only widens scope: session→agent→workspace→global)"
        )

    # Resolve the new scope_ref for the target rung.
    if target_scope == WorkflowScope.GLOBAL:
        new_ref = ""
    elif target_scope == WorkflowScope.WORKSPACE:
        new_ref = (scope_ref or "").strip()
        if not new_ref:
            raise ValueError("promotion to workspace requires a scope_ref (the cwd)")
    elif target_scope == WorkflowScope.AGENT:
        new_ref = (scope_ref or wf.scope_ref or "").strip()
        if not new_ref:
            raise ValueError("promotion to agent requires a scope_ref (the agent id)")
    else:  # SESSION is the floor — unreachable as a promotion target
        new_ref = (scope_ref or "").strip()

    return await update_workflow(
        workflow_id,
        provider_name=provider_name,
        scope=target_scope.value,
        scope_ref=new_ref,
    )


async def get_workflow(workflow_id: str, provider_name: str | None = None) -> Workflow | None:
    """Fetch one workflow by id across providers (or a named provider)."""
    _ensure_native()
    provs = (
        [_providers[provider_name]]
        if provider_name and provider_name in _providers
        else list(_providers.values())
    )
    for p in provs:
        w = await p.get_workflow(workflow_id)
        if w:
            return w
    return None


async def delete_session_workflows(session_key: str, provider_name: str = "native") -> list[str]:
    """Delete every SESSION-scoped workflow bound to *session_key*. Returns the
    deleted ids. End-of-session cleanup (EVOLVE-WORKFLOWS #28): an ephemeral SOP an
    agent authored for one chat is swept when that chat ends, unless it was promoted
    to a wider scope (which changes its scope away from session). Skips any workflow
    still referenced by another (let it survive rather than break a ref)."""
    _ensure_native()
    deleted: list[str] = []
    sess, _ = await list_all_workflows(
        scope=WorkflowScope.SESSION, scope_ref=session_key, limit=1000, offset=0
    )
    if not sess:
        return deleted
    from personalclaw.workflows.composition import referrers

    all_wf, _ = await list_all_workflows(limit=1000, offset=0)
    for wf in sess:
        if referrers(wf.id, all_wf):
            logger.info("Keeping referenced session workflow %s on cleanup", wf.id)
            continue
        try:
            if await delete_workflow(wf.id, provider_name=provider_name):
                deleted.append(wf.id)
        except ValueError:
            logger.debug("session workflow %s not deletable on cleanup", wf.id, exc_info=True)
    if deleted:
        logger.info("Swept %d session-scoped workflow(s) for %s", len(deleted), session_key)
    return deleted
