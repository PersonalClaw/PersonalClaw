"""HTTP handlers for /api/tasks — unified task entity endpoints."""

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.tasks import reconcile, registry
from personalclaw.tasks.models import Task

# Every refusal these routes add answers with the ONE wire envelope (`json_error`), not the
# flat `{"error": "<prose>"}` several of the handlers below still carry: a client that has
# to branch on a parameter it got wrong needs a code, and the flat population is
# shrink-only (`tests/test_wire_error_envelope_census.py`).


def _page_window(request: web.Request) -> tuple[int, int]:
    """The (limit, offset) this route will actually apply.

    Clamped with the idiom every sibling paginated list route uses — ``triggers``,
    ``workflows``, ``sessions``, ``files`` — because this one passed both straight into a
    Python slice and answered 200 with a silently short page (#2984). The ceiling lives
    here rather than in the registry: a page cap is the serving surface's business, and the
    internal projections legitimately read the whole corpus.

    Raises ``ValueError`` for a non-numeric value, which the caller turns into the wire
    envelope. That refusal is the route's OWN — relying on the global request-shape
    middleware to convert an unhandled ``ValueError`` leaves the route a bare 500 in any
    app (a test app, an embedded mount) that has not installed it.
    """
    limit = max(1, min(int(request.query.get("limit", "50")), registry.MAX_TASK_PAGE))
    offset = max(0, int(request.query.get("offset", "0")))
    return limit, offset


def _bad_page(name: str = "limit/offset") -> web.Response:
    return json_error("bad_request", message=f"{name} must be integers", status=400)


def _unknown_provider(exc: registry.UnknownTaskProvider) -> web.Response:
    """The refusal for a ``provider`` this registry does not recognize (#2983).

    ``bad_request`` + a message naming the provider, which is exactly how the artifacts
    routes already answer the identical input on the identical registry design.
    """
    return json_error("bad_request", message=str(exc), status=400)


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
    try:
        limit, offset = _page_window(request)
    except ValueError:
        return _bad_page()

    # `mine=1` — the "mine vs everyone" view (TEAM-SHARED-ENTITIES §2.1). Resolved
    # server-side from the configured username rather than taking a name from the
    # client, and it means "assigned to me, or authored by me and unassigned" —
    # which the `assignee` filter alone cannot express.
    mine = str(request.query.get("mine", "")).strip().lower() in ("1", "true", "yes")

    owner = ""
    if mine:
        from personalclaw.identity import current_username

        owner = current_username()

    # The WHOLE matching set, then one window of it — deliberately in that order.
    # `block_reason` resolves each prerequisite id against the set it is handed, and a
    # prerequisite that is merely absent counts as satisfied, so deriving it from the page
    # would answer `is_blocked: false` for a task whose blocker sits on another page. The
    # window is a presentation concern; blocked-ness is not.
    try:
        matched, truncated = await registry.collect_tasks(
            status=status,
            assignee=assignee,
            project=project,
            task_list_id=task_list,
            provider_filter=provider,
            owner=owner,
        )
    except registry.UnknownTaskProvider as e:
        # An unrecognized `?provider=` is a client error, not "every provider" (#2983).
        return _unknown_provider(e)
    task_map = {t.id: t for t in matched}
    page = matched[offset : offset + limit]
    return web.json_response(
        {
            "tasks": [_with_block_reason(t, task_map) for t in page],
            "total": len(matched),
            # Whether `total` is the whole truth. A client that needs every task (the DAG,
            # the prerequisite picker) pages until it has `total` rows; without this flag a
            # provider-side bound would end that loop looking like completion.
            "complete": not truncated,
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


# Attribution is server-derived on EVERY task write path, and a supplied `author` is
# REFUSED rather than ignored — the same call the comment path already makes, for the
# same reason: a 201/200 that stored a different author than the caller asked for tells
# a forging client it worked and never tells an honest one its attribution was dropped.
#
# `Task.author` is not decoration. `Task.belongs_to` reads it, so it decides `?mine=1`
# on the list route and `mine_only` on `/api/tasks/ready` — measured, a task created
# with `author` set to anything else vanishes from the owner's own views. And the update
# path was the worse half: it rewrote the author of an already-honestly-attributed row,
# which `identity.py` forbids in as many words ("existing records keep the string they
# were written with — rewriting history to match a new name would silently falsify the
# record it exists to preserve"). The store enforces that half too, by listing `author`
# alongside `id`/`provider`/`created_at` in `native._IMMUTABLE_FIELDS`.
_SUPPLIED_AUTHOR_ERROR = "author is server-derived and must not be supplied"

# Ceiling on one comment body. Mirrors `dashboard/handlers/hooks.py:_HOOK_MESSAGE_MAX_LEN`
# (~50K chars, one char of headroom) — the app's settled size-refusal for a free-text field
# that lands in a JSON file read back whole on every access.
_COMMENT_MAX_LEN = 49_999


def _supplies_author(payload: object) -> bool:
    """Whether a request payload tries to set `author` itself.

    Keyed on PRESENCE, not truthiness: `author: ""` would otherwise slip through and
    blank the attribution the server was about to stamp. `isinstance` first so a
    non-dict body keeps whatever behavior it already had rather than gaining a new
    failure mode from this check.
    """
    return isinstance(payload, dict) and "author" in payload


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
    try:
        limit = min(int(body.get("limit", 50)), registry.MAX_TASK_PAGE)
        offset = int(body.get("offset", 0))
    except (TypeError, ValueError):
        return _bad_page()
    tasks, total = await registry.search_tasks(
        query=body.get("query", ""),
        statuses=body.get("status") or body.get("statuses"),
        priorities=body.get("priority") or body.get("priorities"),
        tags=body.get("tags"),
        project=body.get("project") or body.get("project_id"),
        task_list_id=body.get("task_list_id"),
        sort_by=body.get("sort_by", "relevance"),
        limit=limit,
        offset=offset,
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
        # Bulk is the same two write verbs, so it inherits the same attribution rule —
        # in PHASE 1, so one forging item aborts the batch instead of landing alongside
        # honest ones. Bulk is the path where this matters most: it is the cheapest way
        # to mint many rows, and it reaches `create_task`/`update_task` with `**item`
        # exactly as the single-item handlers reach them with `**body`.
        if op in ("create", "update") and _supplies_author(item):
            errors.append({"index": i, "error": _SUPPLIED_AUTHOR_ERROR})
        if op in ("create", "update"):
            # And the same parent rule (#2977), for the same reason the attribution rule is
            # here: bulk is the cheapest way to mint many rows, and it reaches
            # `create_task`/`update_task` with `**item` exactly as the single-item handlers
            # reach them with `**body`. In PHASE 1, so one dangling parent aborts the batch
            # instead of landing an orphan alongside sound rows.
            dangling = _dangling_parent(item)
            if dangling:
                errors.append({"index": i, "error": dangling})
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
    try:
        task = await registry.get_task(task_id, provider_name=provider)
    except registry.UnknownTaskProvider as e:
        return _unknown_provider(e)
    if not task:
        return web.json_response({"error": "not found"}, status=404)
    d = task.to_dict()
    d["comment_count"] = getattr(task, "_comment_count", 0)
    return web.json_response(d)


def _dangling_parent(body: object) -> str | None:
    """The reason a task write names a parent that does not exist, or None (#2977).

    Neither parent id was validated, so ``POST /api/tasks`` answered 201 for a nonexistent
    ``task_list_id`` and STORED it — a task born straight into the orphan state the project
    and task-list cascades exist to prevent (#457/#2976) — while ``project_id`` was worse
    still: ``_attach_project_general_list`` caught the store's own rejection and dropped it,
    so the user's project choice was silently discarded at 201. The sibling
    ``POST /api/task-lists`` refuses the identical ``project_id``, and that is the reference
    behavior here; this is not a new convention.

    Returns the message rather than a response so the two single-item verbs and bulk's
    validate-all phase share one implementation of the rule instead of three.

    An explicitly EMPTY id is "no parent chosen" (the payload ``TaskForm.draftToPayload``
    always sends), never a lookup that fails.
    """
    if not isinstance(body, dict):
        return None
    from personalclaw.tasks.hierarchy import HierarchyStore

    store = HierarchyStore()
    project_id = str(body.get("project_id") or "").strip()
    if project_id and store.get_project(project_id) is None:
        return f"no project with id '{project_id}'"
    task_list_id = str(body.get("task_list_id") or "").strip()
    if task_list_id and store.get_task_list(task_list_id) is None:
        return f"no task list with id '{task_list_id}'"
    return None


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
        # No `except ValueError` here any more. It used to swallow `create_task_list`'s
        # "no project with id '…'" and leave `task_list_id` unset, so a bad `project_id`
        # answered 201 having discarded the choice this function exists to honor (#2977).
        # `_dangling_parent` has already refused that id upstream; anything this call raises
        # now is a real fault, and both callers run it inside their `except ValueError` →
        # 400 arm rather than dropping it.
        general = store.create_task_list(name="General", project_id=project_id)
    body["task_list_id"] = general.id


async def api_tasks_create(request: web.Request) -> web.Response:
    """POST /api/tasks"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if _supplies_author(body):
        return web.json_response({"error": _SUPPLIED_AUTHOR_ERROR}, status=400)
    raw_title = body.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title:
        return web.json_response({"error": "title required"}, status=400)
    dangling = _dangling_parent(body)
    if dangling:
        return json_error("invalid_request", message=dangling, status=400)
    # An empty `provider` is an unset parameter, not a name to look up — the same reading
    # the by-id routes give it, where it means "search them all".
    provider_name = body.pop("provider", "") or "native"
    try:
        if provider_name == "native":
            _attach_project_general_list(body)
        task = await registry.create_task(provider_name=provider_name, **body)
    except registry.UnknownTaskProvider as e:
        return _unknown_provider(e)
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
    if _supplies_author(body):
        return web.json_response({"error": _SUPPLIED_AUTHOR_ERROR}, status=400)
    dangling = _dangling_parent(body)
    if dangling:
        return json_error("invalid_request", message=dangling, status=400)
    provider_name = body.pop("provider", None)
    # The SAME resolution the create path does. `project_id` is not a `Task` field, so without this
    # it reached `update_task`, was ignored, and the edit answered 200 having changed nothing — the
    # project dropdown reverted on the next load (issue 2142). `TaskForm.draftToPayload` is shared
    # by the create page and the detail page, so the identical payload worked on one and was a no-op
    # on the other. Idempotent and already a no-op when a list was chosen, so the create path's
    # behaviour is the whole specification.
    try:
        _attach_project_general_list(body)
        task = await registry.update_task(task_id, provider_name=provider_name, **body)
    except registry.UnknownTaskProvider as e:
        return _unknown_provider(e)
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
    except registry.UnknownTaskProvider as e:
        # The sharpest form of #2983: this answered `{"ok": true}` for `?provider=jira` and
        # deleted the NATIVE task — a call scoped to a provider that does not exist still
        # destroyed a record it had not addressed.
        return _unknown_provider(e)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True})


async def api_tasks_comments_get(request: web.Request) -> web.Response:
    """GET /api/tasks/{task_id}/comments"""
    task_id = request.match_info["task_id"]
    provider = request.query.get("provider")
    try:
        comments = await registry.get_comments(task_id, provider_name=provider)
    except registry.UnknownTaskProvider as e:
        return _unknown_provider(e)
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
    # Shares the predicate and the message with the task write paths so the two cannot
    # drift into refusing on different grounds or saying different things.
    if _supplies_author(body):
        return web.json_response({"error": _SUPPLIED_AUTHOR_ERROR}, status=400)
    raw = body.get("body")
    # A non-string body is a client bug, not a comment — say so instead of crashing on
    # .strip(). Absent/null stays valid here and falls through to "body required".
    if raw is not None and not isinstance(raw, str):
        return web.json_response({"error": "body must be a string"}, status=400)
    message = (raw or "").strip()
    if not message:
        return web.json_response({"error": "body required"}, status=400)
    # Bounded, because the whole thread is read back into memory and re-serialised on every
    # read and every write: a single 100K comment made the sidecar 102KB and the GET response
    # 101KB, and nothing refused it. Same shape as the hook-message ceiling
    # (`handlers/hooks.py:_HOOK_MESSAGE_MAX_LEN`) — a 400 naming the limit, not a truncation,
    # so a client never believes it stored text the server dropped.
    if len(message) > _COMMENT_MAX_LEN:
        return web.json_response({"error": f"body exceeds {_COMMENT_MAX_LEN} chars"}, status=400)
    provider = body.get("provider")
    # Attribution comes from the server's own view of who is acting — the same handle
    # `Task.author` is stamped with on create, so a task and its comments agree.
    try:
        comment = await registry.add_comment(
            task_id, body=message, author=_owner_username(), provider_name=provider
        )
    except registry.UnknownTaskProvider as e:
        return _unknown_provider(e)
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
    except registry.UnknownTaskProvider as e:
        return _unknown_provider(e)
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
