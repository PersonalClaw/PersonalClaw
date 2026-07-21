"""HTTP handlers for /api/workflows — workflow SOP CRUD endpoints.

API responses omit the match_embedding vector (large, internal) via
``to_dict(include_embedding=False)``. The native provider recomputes the
embedding on write.
"""

from typing import Any

from aiohttp import web

from personalclaw.workflows import registry
from personalclaw.workflows.models import WorkflowScope


def _scope_arg(value: str | None) -> WorkflowScope | None:
    if not value:
        return None
    try:
        return WorkflowScope(value)
    except ValueError:
        return None


def _err_payload(e: Exception) -> dict:
    """Error body for a create/update failure; surfaces a ref cycle path when
    present so the UI can point at the offending edge."""
    from personalclaw.workflows.composition import WorkflowCycleError

    payload: dict[str, Any] = {"error": str(e)}
    if isinstance(e, WorkflowCycleError):
        payload["cycle"] = e.cycle
    return payload


async def api_workflows_list(request: web.Request) -> web.Response:
    """GET /api/workflows"""
    scope = _scope_arg(request.query.get("scope"))
    scope_ref = request.query.get("scope_ref")
    tag = request.query.get("tag")
    provider = request.query.get("provider")
    limit = int(request.query.get("limit", "200"))
    offset = int(request.query.get("offset", "0"))

    workflows, total = await registry.list_all_workflows(
        scope=scope,
        scope_ref=scope_ref,
        tag=tag,
        provider_filter=provider,
        limit=limit,
        offset=offset,
    )
    return web.json_response(
        {
            "workflows": [w.to_dict(include_embedding=False) for w in workflows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    )


async def api_workflows_get(request: web.Request) -> web.Response:
    """GET /api/workflows/{workflow_id}"""
    workflow_id = request.match_info["workflow_id"]
    provider = request.query.get("provider")
    wf = await registry.get_workflow(workflow_id, provider_name=provider)
    if not wf:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(wf.to_dict(include_embedding=False))


async def api_workflows_create(request: web.Request) -> web.Response:
    """POST /api/workflows"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    name = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    provider_name = body.pop("provider", "native")
    try:
        wf = await registry.create_workflow(provider_name=provider_name, **body)
    except ValueError as e:
        return web.json_response(_err_payload(e), status=400)
    return web.json_response(wf.to_dict(include_embedding=False), status=201)


async def api_workflows_update(request: web.Request) -> web.Response:
    """PUT /api/workflows/{workflow_id}"""
    workflow_id = request.match_info["workflow_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    provider_name = body.pop("provider", None)
    try:
        wf = await registry.update_workflow(workflow_id, provider_name=provider_name, **body)
    except ValueError as e:
        return web.json_response(_err_payload(e), status=400)
    if not wf:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(wf.to_dict(include_embedding=False))


async def api_workflows_promote(request: web.Request) -> web.Response:
    """POST /api/workflows/{workflow_id}/promote — widen scope up the ladder."""
    workflow_id = request.match_info["workflow_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    target = (body.get("scope") or "").strip()
    if not target:
        return web.json_response({"error": "scope required"}, status=400)
    try:
        wf = await registry.promote_workflow(
            workflow_id,
            target,
            scope_ref=body.get("scope_ref"),
            provider_name=body.get("provider"),
        )
    except ValueError as e:
        return web.json_response(_err_payload(e), status=400)
    if not wf:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(wf.to_dict(include_embedding=False))


async def api_workflows_delete(request: web.Request) -> web.Response:
    """DELETE /api/workflows/{workflow_id}"""
    workflow_id = request.match_info["workflow_id"]
    provider = request.query.get("provider")
    try:
        deleted = await registry.delete_workflow(workflow_id, provider_name=provider)
    except registry.WorkflowReferencedError as e:
        # Referenced by other workflows — refuse + list referrers (409 conflict).
        return web.json_response({"error": str(e), "referrers": e.referrers}, status=409)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True})


async def api_workflows_graph(request: web.Request) -> web.Response:
    """GET /api/workflows/{workflow_id}/graph — composition tree + cycles +
    the inline-flattened expansion (S3 reuse)."""
    from personalclaw.workflows.composition import build_graph

    workflow_id = request.match_info["workflow_id"]
    all_wf, _ = await registry.list_all_workflows(limit=1000, offset=0)
    if not any(w.id == workflow_id for w in all_wf):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(build_graph(workflow_id, all_wf))


async def api_workflows_providers(request: web.Request) -> web.Response:
    """GET /api/workflows/providers"""
    return web.json_response({"providers": registry.list_providers()})


async def api_workflows_preview_match(request: web.Request) -> web.Response:
    """POST /api/workflows/preview-match — debug: rank eligible workflows for a
    synthetic turn against a query intent, returning scores. Powers the
    "test this SOP against an intent" UI affordance (E4-P4)."""
    from personalclaw.workflows.surfacing import (
        TurnScope,
        best_match,
        eligible_workflows,
    )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    query = (body.get("query") or "").strip()
    if not query:
        return web.json_response({"error": "query required"}, status=400)

    turn = TurnScope(
        session_key=body.get("session_key"),
        agent=body.get("agent"),
        cwd=body.get("cwd") or body.get("scope_ref"),
        # The resolved agent binding id agent-scoped SOPs match on; default to the
        # bare agent name so the preview mirrors a native turn.
        agent_id=body.get("agent_id") or body.get("agent") or "",
    )
    candidates = await eligible_workflows(turn)
    match = best_match(query, candidates)
    return web.json_response(
        {
            "eligible": [
                {"id": w.id, "name": w.name, "scope": w.scope.value, "scope_ref": w.scope_ref}
                for w in candidates
            ],
            "match": (
                {
                    "id": match.workflow.id,
                    "name": match.workflow.name,
                    "scope": match.scope.value,
                    "score": round(match.score, 4),
                    "method": match.method,
                }
                if match
                else None
            ),
        }
    )


async def api_workflows_used_by(request: web.Request) -> web.Response:
    """GET /api/workflows/used-by/{agent} — agent-scoped SOPs offered to this
    agent: workflows with ``scope='agent'`` whose ``scope_ref`` matches the agent
    binding id (the inverse index of the scope_ref model — computed, not stored)."""
    agent_name = request.match_info["agent"]
    workflows, _ = await registry.list_all_workflows(
        scope=WorkflowScope.AGENT, scope_ref=agent_name, limit=1000, offset=0
    )
    return web.json_response(
        {
            "agent": agent_name,
            "workflows": [w.to_dict(include_embedding=False) for w in workflows],
        }
    )


def register_workflow_routes(app: web.Application) -> None:
    """Register /api/workflows/* routes. Literal sub-paths before {workflow_id}."""
    app.router.add_get("/api/workflows/providers", api_workflows_providers)
    app.router.add_post("/api/workflows/preview-match", api_workflows_preview_match)
    app.router.add_get("/api/workflows/used-by/{agent}", api_workflows_used_by)
    app.router.add_get("/api/workflows", api_workflows_list)
    app.router.add_post("/api/workflows", api_workflows_create)
    app.router.add_get("/api/workflows/{workflow_id}/graph", api_workflows_graph)
    app.router.add_post("/api/workflows/{workflow_id}/promote", api_workflows_promote)
    app.router.add_get("/api/workflows/{workflow_id}", api_workflows_get)
    app.router.add_put("/api/workflows/{workflow_id}", api_workflows_update)
    app.router.add_delete("/api/workflows/{workflow_id}", api_workflows_delete)
