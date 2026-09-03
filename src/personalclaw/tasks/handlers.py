"""HTTP handlers for /api/tasks — unified task entity endpoints."""

from aiohttp import web

from personalclaw.tasks import reconcile, registry
from personalclaw.tasks.models import Task


def _with_block_reason(task: Task, task_map: dict[str, Task]) -> dict:
    """Serialize a task + its derived ``block_reason`` (needs the sibling set) and
    ``comment_count`` (stamped by the provider on read, for the comment badge)."""
    d = task.to_dict()
    d["block_reason"] = reconcile.block_reason(task, task_map)
    d["comment_count"] = getattr(task, "_comment_count", 0)
    return d


async def api_tasks_list(request: web.Request) -> web.Response:
    """GET /api/tasks"""
    status = request.query.get("status")
    assignee = request.query.get("assignee")
    project = request.query.get("project")
    task_list = request.query.get("task_list") or request.query.get("task_list_id")
    provider = request.query.get("provider")
    limit = int(request.query.get("limit", "50"))
    offset = int(request.query.get("offset", "0"))

    # `mine=1` — the "mine vs everyone" view (TEAM-SHARED-ENTITIES §2.1). Resolved
    # server-side from the configured username rather than taking a name from the
    # client, and it means "assigned to me, or authored by me and unassigned" —
    # which the `assignee` filter alone cannot express.
    mine = str(request.query.get("mine", "")).strip().lower() in ("1", "true", "yes")

    tasks, total = await registry.list_all_tasks(
        status=status,
        assignee=assignee,
        project=project,
        task_list_id=task_list,
        provider_filter=provider,
        limit=limit,
        offset=offset,
    )
    if mine:
        from personalclaw.identity import current_username

        owner = current_username()
        if owner:
            tasks = [t for t in tasks if t.belongs_to(owner)]
            # `total` describes the filtered set now; reporting the provider's count
            # would make the UI show "12 of 40" for a list holding 12.
            total = len(tasks)
    task_map = {t.id: t for t in tasks}
    return web.json_response(
        {
            "tasks": [_with_block_reason(t, task_map) for t in tasks],
            "total": total,
            "limit": limit,
            "offset": offset,
            "owner": _owner_username(),
        }
    )


def _owner_username() -> str:
    """The configured username, so the frontend can label rows as mine vs theirs."""
    try:
        from personalclaw.identity import current_username

        return current_username()
    except Exception:  # noqa: BLE001
        return ""


async def api_tasks_graph(request: web.Request) -> web.Response:
    """GET /api/tasks/graph — adjacency + DependencyAnalysis (seam S3)."""
    provider = request.query.get("provider")
    return web.json_response(await registry.task_graph(provider_filter=provider))


async def api_tasks_ready(request: web.Request) -> web.Response:
    """GET /api/tasks/ready — tasks startable now (no unfinished prerequisites).

    Owner-scoped by default (TEAM-SHARED-ENTITIES §2.1): a shared provider's tasks
    assigned to other people are never the owner's ready work. `everyone=1` opts into
    the unfiltered view for a shared board.
    """
    project = request.query.get("project")
    task_list = request.query.get("task_list") or request.query.get("task_list_id")
    everyone = str(request.query.get("everyone", "")).strip().lower() in ("1", "true", "yes")
    tasks = await registry.ready_tasks(
        project=project, task_list_id=task_list, mine_only=not everyone
    )
    task_map = {t.id: t for t in tasks}
    return web.json_response({"tasks": [_with_block_reason(t, task_map) for t in tasks]})


async def api_tasks_search(request: web.Request) -> web.Response:
    """POST /api/tasks/search — query + status/priority/tag/scope filters + sort."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    tasks, total = await registry.search_tasks(
        query=body.get("query", ""),
        statuses=body.get("status") or body.get("statuses"),
        priorities=body.get("priority") or body.get("priorities"),
        tags=body.get("tags"),
        project=body.get("project") or body.get("project_id"),
        task_list_id=body.get("task_list_id"),
        sort_by=body.get("sort_by", "relevance"),
        limit=int(body.get("limit", 50)),
        offset=int(body.get("offset", 0)),
    )
    task_map = {t.id: t for t in tasks}
    return web.json_response(
        {"tasks": [_with_block_reason(t, task_map) for t in tasks], "total": total}
    )


async def api_tasks_bulk(request: web.Request) -> web.Response:
    """POST /api/tasks/bulk — validate-all-then-apply bulk create/update/delete.

    Body: ``{op: create|update|delete, items: [...]}``. Phase 1 validates every
    item (a single failure aborts the whole batch); phase 2 applies and returns a
    per-item result set."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    op = body.get("op", "")
    items = body.get("items")
    if op not in ("create", "update", "delete") or not isinstance(items, list):
        return web.json_response(
            {"error": "body must be {op: create|update|delete, items: [...]}"}, status=400
        )

    # Phase 1 — validate all.
    errors = []
    for i, item in enumerate(items):
        if op == "create":
            if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                errors.append({"index": i, "error": "title required"})
        else:  # update/delete need an id
            tid = item.get("id") if isinstance(item, dict) else item
            if not tid:
                errors.append({"index": i, "error": "id required"})
    if errors:
        return web.json_response(
            {
                "total": len(items),
                "succeeded": 0,
                "failed": len(items),
                "results": [],
                "errors": errors,
            },
            status=400,
        )

    # Phase 2 — apply.
    results, errs = [], []
    for i, item in enumerate(items):
        try:
            if op == "create":
                t = await registry.create_task(**{k: v for k, v in item.items() if k != "provider"})
                results.append({"index": i, "task_id": t.id, "status": "created"})
            elif op == "update":
                updated = await registry.update_task(
                    item["id"], **{k: v for k, v in item.items() if k not in ("id", "provider")}
                )
                results.append(
                    {
                        "index": i,
                        "task_id": item["id"],
                        "status": "updated" if updated else "not_found",
                    }
                )
            else:  # delete
                tid = item.get("id") if isinstance(item, dict) else item
                ok = await registry.delete_task(str(tid)) if tid else False
                results.append(
                    {"index": i, "task_id": tid, "status": "deleted" if ok else "not_found"}
                )
        except Exception as e:  # noqa: BLE001 — surface per-item failure
            errs.append({"index": i, "error": str(e)})
    return web.json_response(
        {
            "total": len(items),
            "succeeded": len(results),
            "failed": len(errs),
            "results": results,
            "errors": errs,
        }
    )


async def api_tasks_get(request: web.Request) -> web.Response:
    """GET /api/tasks/{task_id}"""
    task_id = request.match_info["task_id"]
    provider = request.query.get("provider")
    task = await registry.get_task(task_id, provider_name=provider)
    if not task:
        return web.json_response({"error": "not found"}, status=404)
    d = task.to_dict()
    d["comment_count"] = getattr(task, "_comment_count", 0)
    return web.json_response(d)


def _attach_project_general_list(body: dict) -> None:
    """Honor an explicit ``project_id`` when no task list was chosen: a task's
    ``project`` label derives solely from its list, so an empty ``task_list_id``
    would silently discard the user's project choice. Attach to the project's
    find-or-create "General" list instead."""
    project_id = body.pop("project_id", "")
    if not project_id:
        return
    # An explicitly-sent EMPTY `task_list_id` means "no list chosen", so the project still has to
    # be resolved. The form always sends the key, so this branch is the live one — it worked before
    # only because `""` is falsy, which is luck rather than intent. Stated as a rule so a later
    # `if "task_list_id" in body` refactor cannot invert it silently.
    if str(body.get("task_list_id") or "").strip():
        return
    from personalclaw.tasks.hierarchy import HierarchyStore

    store = HierarchyStore()
    # Attach to the OLDEST "General" — the original default — not whichever id sorts first.
    # `create_task_list` now rejects a duplicate name per project (#777), so new projects have
    # exactly one; this only disambiguates a project that carried duplicates from before that
    # check, keeping a `project_id`-only task's landing list deterministic rather than arbitrary.
    general = min(
        (tl for tl in store.list_task_lists(project_id) if tl.name == "General"),
        key=lambda tl: tl.created_at or "",
        default=None,
    )
    if general is None:
        try:
            general = store.create_task_list(name="General", project_id=project_id)
        except ValueError:
            return
    body["task_list_id"] = general.id


async def api_tasks_create(request: web.Request) -> web.Response:
    """POST /api/tasks"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    raw_title = body.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title:
        return web.json_response({"error": "title required"}, status=400)
    provider_name = body.pop("provider", "native")
    if provider_name == "native":
        _attach_project_general_list(body)
    try:
        task = await registry.create_task(provider_name=provider_name, **body)
    except reconcile.DependencyCycleError as e:
        return web.json_response({"error": str(e), "cycle": e.cycle}, status=400)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(task.to_dict(), status=201)


async def api_tasks_update(request: web.Request) -> web.Response:
    """PUT /api/tasks/{task_id}"""
    task_id = request.match_info["task_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    provider_name = body.pop("provider", None)
    # The SAME resolution the create path does. `project_id` is not a `Task` field, so without this
    # it reached `update_task`, was ignored, and the edit answered 200 having changed nothing — the
    # project dropdown reverted on the next load (issue 2142). `TaskForm.draftToPayload` is shared
    # by the create page and the detail page, so the identical payload worked on one and was a no-op
    # on the other. Idempotent and already a no-op when a list was chosen, so the create path's
    # behaviour is the whole specification.
    _attach_project_general_list(body)
    try:
        task = await registry.update_task(task_id, provider_name=provider_name, **body)
    except reconcile.DependencyCycleError as e:
        return web.json_response({"error": str(e), "cycle": e.cycle}, status=400)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not task:
        return web.json_response({"error": "not found"}, status=404)
    # Return the full set of tasks whose status cascaded (the edited task plus any
    # auto-block/unblock'd dependents) so the client can patch all of them, not
    # just the one edited. block_reason is derived against the post-write set.
    reconciled = getattr(task, "_reconciled", [task])
    task_map = {t.id: t for t in reconciled}
    payload = _with_block_reason(task, task_map)
    payload["reconciled"] = [_with_block_reason(t, task_map) for t in reconciled]
    return web.json_response(payload)


async def api_tasks_delete(request: web.Request) -> web.Response:
    """DELETE /api/tasks/{task_id}"""
    task_id = request.match_info["task_id"]
    provider = request.query.get("provider")
    try:
        deleted = await registry.delete_task(task_id, provider_name=provider)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True})


async def api_tasks_comments_get(request: web.Request) -> web.Response:
    """GET /api/tasks/{task_id}/comments"""
    task_id = request.match_info["task_id"]
    provider = request.query.get("provider")
    comments = await registry.get_comments(task_id, provider_name=provider)
    return web.json_response({"comments": [c.to_dict() for c in comments]})


async def api_tasks_comments_post(request: web.Request) -> web.Response:
    """POST /api/tasks/{task_id}/comments"""
    task_id = request.match_info["task_id"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    # An explicit `author` is REFUSED, not ignored. This endpoint used to pass the
    # caller's string straight through, so any authenticated client could sign a
    # comment as anyone. Silently dropping the field would answer 201 while storing a
    # different author than the caller asked for — a forging client would believe it
    # succeeded and an honest one would never learn its attribution was discarded.
    if "author" in body:
        return web.json_response(
            {"error": "author is server-derived and must not be supplied"}, status=400
        )
    raw = body.get("body")
    # A non-string body is a client bug, not a comment — say so instead of crashing on
    # .strip(). Absent/null stays valid here and falls through to "body required".
    if raw is not None and not isinstance(raw, str):
        return web.json_response({"error": "body must be a string"}, status=400)
    message = (raw or "").strip()
    if not message:
        return web.json_response({"error": "body required"}, status=400)
    provider = body.get("provider")
    # Attribution comes from the server's own view of who is acting — the same handle
    # `Task.author` is stamped with on create, so a task and its comments agree.
    comment = await registry.add_comment(
        task_id, body=message, author=_owner_username(), provider_name=provider
    )
    if not comment:
        return web.json_response({"error": "task not found"}, status=404)
    return web.json_response(comment.to_dict(), status=201)


async def api_tasks_comments_delete(request: web.Request) -> web.Response:
    """DELETE /api/tasks/{task_id}/comments/{comment_id}

    A comment is the one task field with no edit path, so without this a wrong one
    was permanent through the API and only removable by hand-editing the sidecar.
    """
    task_id = request.match_info["task_id"]
    comment_id = request.match_info["comment_id"]
    provider = request.query.get("provider")
    try:
        deleted = await registry.delete_comment(task_id, comment_id, provider_name=provider)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True, "id": comment_id})


async def api_tasks_providers(request: web.Request) -> web.Response:
    """GET /api/tasks/providers"""
    return web.json_response({"providers": registry.list_providers()})


def register_task_routes(app: web.Application) -> None:
    """Register /api/tasks/* + /api/projects/* + /api/task-lists/* routes."""
    from personalclaw.tasks.hierarchy_handlers import register_hierarchy_routes

    # Static sub-paths MUST be registered before the dynamic /{task_id} routes
    # so they aren't captured by the id matcher.
    app.router.add_get("/api/tasks/providers", api_tasks_providers)
    app.router.add_get("/api/tasks/graph", api_tasks_graph)
    app.router.add_get("/api/tasks/ready", api_tasks_ready)
    app.router.add_post("/api/tasks/search", api_tasks_search)
    app.router.add_post("/api/tasks/bulk", api_tasks_bulk)
    app.router.add_get("/api/tasks", api_tasks_list)
    app.router.add_post("/api/tasks", api_tasks_create)
    app.router.add_get("/api/tasks/{task_id}", api_tasks_get)
    app.router.add_put("/api/tasks/{task_id}", api_tasks_update)
    app.router.add_delete("/api/tasks/{task_id}", api_tasks_delete)
    app.router.add_get("/api/tasks/{task_id}/comments", api_tasks_comments_get)
    app.router.add_post("/api/tasks/{task_id}/comments", api_tasks_comments_post)
    app.router.add_delete("/api/tasks/{task_id}/comments/{comment_id}", api_tasks_comments_delete)

    register_hierarchy_routes(app)
