"""HTTP handlers for /api/projects and /api/task-lists (the Project → TaskList
levels of the task hierarchy)."""

import logging

from aiohttp import web

from personalclaw.tasks.hierarchy import HierarchyStore

logger = logging.getLogger(__name__)


def _store() -> HierarchyStore:
    return HierarchyStore()


def _project_payload(store: HierarchyStore, project, *, list_counts: dict | None = None) -> dict:
    """Serialize a project for the API, enriched with its context dir path + a
    task-list count. ``list_counts`` lets the list endpoint pass a precomputed
    {project_id: count} map so it isn't recomputed per project."""
    d = project.to_dict()
    d["context_dir"] = str(store.context_dir(project.id))
    if list_counts is None:
        d["task_list_count"] = len(store.list_task_lists(project_id=project.id))
    else:
        d["task_list_count"] = list_counts.get(project.id, 0)
    return d


# ── Projects ──

async def api_projects_list(request: web.Request) -> web.Response:
    """GET /api/projects"""
    store = _store()
    projects = store.list_projects()
    # Precompute task-list counts once (one pass over all lists) for the UI.
    counts: dict[str, int] = {}
    for tl in store.list_task_lists():
        counts[tl.project_id] = counts.get(tl.project_id, 0) + 1
    out = [_project_payload(store, p, list_counts=counts) for p in projects]
    return web.json_response({"projects": out})


async def api_projects_create(request: web.Request) -> web.Response:
    """POST /api/projects"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    store = _store()
    try:
        project = store.create_project(
            name=body.get("name", ""),
            agent_instructions_template=body.get("agent_instructions_template", ""),
            brief=body.get("brief", ""),
            workspace_dir=body.get("workspace_dir", ""),
            # A name the user typed at creation is explicit → lock it (same as a rename),
            # so it isn't mislabeled "Auto-named" and isn't auto-renamed by the LLM. The
            # loop's auto-backing-project path (tasks_link.ensure_project) omits this, so
            # those stay correctly auto-named.
            name_locked=bool(body.get("name_locked", False)),
        )
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(_project_payload(store, project), status=201)


async def api_projects_get(request: web.Request) -> web.Response:
    """GET /api/projects/{project_id}"""
    store = _store()
    project = store.get_project(request.match_info["project_id"])
    if not project:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_project_payload(store, project))


async def api_projects_linked(request: web.Request) -> web.Response:
    """GET /api/projects/{project_id}/linked — the work units scoped under this
    project: Goal Loops (loop.project_id) + Code projects (code.tasks_project_id).
    Read-only summaries (id/name/status) so the Projects detail page can show the
    integration — everything the user does on one effort, in one place."""
    pid = request.match_info["project_id"]
    if _store().get_project(pid) is None:
        return web.json_response({"error": "not found"}, status=404)

    loops: list[dict] = []
    code: list[dict] = []
    try:
        from personalclaw.loop import store as loop_store
        for lp in loop_store.list_all():
            if pid not in (lp.project_id, lp.tasks_project_id):
                continue
            # error_message lets the FE distinguish a genuine 'complete' from a
            # budget-exhausted finish (→ "Ended early"), matching the list + cockpit.
            row = {"id": lp.id, "name": lp.name or lp.task[:60], "status": lp.status,
                   "kind": lp.kind, "error_message": lp.error_message or None}
            (code if lp.kind == "code" else loops).append(row)
    except Exception:
        pass

    artifacts: list[dict] = []
    try:
        from personalclaw.artifacts.registry import get_provider
        prov = get_provider()
        if prov is not None:
            artifacts = [
                {"slug": a.slug, "name": a.name, "kind": a.kind}
                for a in prov.list(project_id=pid)
            ]
    except Exception:
        pass

    # Project-bound CHATS (manual sessions scoped to this project) — the vision frames
    # chats as first-class project work ("launch a new loop OR chat about it"), so the
    # detail page can list + resume them, not just loops. Worker sessions (loop-*) are
    # excluded — they already surface as loops above. Best-effort.
    chats: list[dict] = []
    try:
        state = request.app["state"]
        for s in state._sessions.values():
            if getattr(s, "project_id", "") != pid:
                continue
            if str(getattr(s, "_app", "") or ""):
                continue  # worker session (loop/code/campaign) — listed as a loop, not a chat
            chats.append({"key": s.key, "title": getattr(s, "title", "") or s.key,
                          "running": bool(getattr(s, "running", False))})
    except Exception:
        pass

    return web.json_response({"loops": loops, "code": code, "artifacts": artifacts, "chats": chats})


async def api_projects_update(request: web.Request) -> web.Response:
    """PUT /api/projects/{project_id}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    store = _store()
    try:
        project = store.update_project(request.match_info["project_id"], **body)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not project:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(_project_payload(store, project))


def _bound_work_counts(pid: str) -> tuple[int, int]:
    """(loops, code) count still bound to this project — so deleting it doesn't
    silently orphan live work (and yank its worktrees out from under git)."""
    loops = code = 0
    try:
        from personalclaw.loop import store as loop_store
        for lp in loop_store.list_all():
            if pid not in (lp.project_id, lp.tasks_project_id):
                continue
            if lp.kind == "code":
                code += 1
            else:
                loops += 1
    except Exception:
        pass
    return loops, code


def _bound_chat_sessions(state, pid: str) -> list:
    """The live project-bound CHAT sessions for a project (manual sessions with
    project_id==pid and no _app — worker/loop sessions are excluded; they're counted
    as loops). Returns the session objects so the caller can count them for the
    delete-guard AND unbind them on force-delete. Best-effort; [] on any failure."""
    out: list = []
    try:
        for s in (getattr(state, "_sessions", {}) or {}).values():
            if getattr(s, "project_id", "") != pid:
                continue
            if str(getattr(s, "_app", "") or ""):
                continue  # worker/loop/campaign session — surfaced as a loop, not a chat
            out.append(s)
    except Exception:
        logger.debug("bound-chat scan failed for %s", pid, exc_info=True)
    return out


def _unbind_bound_chats(state, pid: str) -> int:
    """Detach project-bound chats from a project being force-deleted: clear their
    project_id so they don't dangle (preamble/context-dir grant would resolve a gone
    project). Chats are the USER'S conversations — we unbind, never delete them."""
    n = 0
    for s in _bound_chat_sessions(state, pid):
        try:
            s.project_id = ""
            n += 1
        except Exception:
            logger.debug("unbind chat %s failed", getattr(s, "key", "?"), exc_info=True)
    return n


async def _teardown_bound_loops(pid: str) -> None:
    """Tear down every loop scoped under a project being force-deleted: stop the worker
    + clean its git worktrees/branches + delete the loop row. Without this, force-delete
    rmtree'd the project dir but left bound loops orphaned — workers still running, their
    tasks_project_id pointing at a deleted project, and `.worktrees/`/`pclaw/task-*`
    branches littering the user's repo (the exact harm the 409 guard warns about, done
    anyway on force). Best-effort per loop; never raises."""
    try:
        from personalclaw.loop import store as loop_store, manager as loop_manager
        from personalclaw.autonudge import get_instance
        svc = get_instance()
        bound = [lp.id for lp in loop_store.list_all() if pid in (lp.project_id, lp.tasks_project_id)]
        for lid in bound:
            try:
                if svc is not None:
                    await loop_manager.teardown_for_delete(svc, lid)
                loop_store.delete(lid)
            except Exception:
                logger.debug("force-delete: teardown of bound loop %s failed", lid, exc_info=True)
    except Exception:
        logger.debug("force-delete: bound-loop teardown sweep failed for %s", pid, exc_info=True)


async def api_projects_delete(request: web.Request) -> web.Response:
    """DELETE /api/projects/{project_id}[?force=true]

    Refuses (409) to delete a project that still has bound Goal Loops / Code
    projects — deleting would orphan that live work and rmtree its worktrees out
    from under git. The caller confirms + retries with ?force=true to delete anyway."""
    pid = request.match_info["project_id"]
    state = request.app.get("state")  # may be absent in task-only test apps → no chats
    force = request.query.get("force") in ("1", "true", "yes")
    if not force:
        loops, code = _bound_work_counts(pid)
        chats = len(_bound_chat_sessions(state, pid))
        if loops or code or chats:
            # Chats are first-class project work (surfaced in /linked), so deleting a
            # project with active project-bound chats must warn too — else they silently
            # dangle (project_id → a gone project; preamble/context-dir grant break).
            return web.json_response({
                "error": "project has bound work",
                "loops": loops, "code": code, "chats": chats,
            }, status=409)
    else:
        # Force-delete: tear down the bound loops FIRST (stop workers + clean worktrees +
        # delete rows) so they aren't orphaned, then UNBIND the project-bound chats (clear
        # their project_id) — chats are the user's conversations, detached not destroyed.
        await _teardown_bound_loops(pid)
        _unbind_bound_chats(state, pid)
    try:
        deleted = _store().delete_project(pid)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not deleted:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True})


# ── Task lists ──

async def api_task_lists_list(request: web.Request) -> web.Response:
    """GET /api/task-lists?project_id=…"""
    project_id = request.query.get("project_id")
    lists = _store().list_task_lists(project_id=project_id)
    return web.json_response({"task_lists": [tl.to_dict() for tl in lists]})


async def api_task_lists_create(request: web.Request) -> web.Response:
    """POST /api/task-lists"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    try:
        tl = _store().create_task_list(
            name=body.get("name", ""),
            project_id=body.get("project_id", ""),
            project_name=body.get("project_name", ""),
            repeatable=bool(body.get("repeatable", False)),
            agent_instructions_template=body.get("agent_instructions_template", ""),
        )
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response(tl.to_dict(), status=201)


async def api_task_lists_get(request: web.Request) -> web.Response:
    """GET /api/task-lists/{list_id}"""
    tl = _store().get_task_list(request.match_info["list_id"])
    if not tl:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(tl.to_dict())


async def api_task_lists_update(request: web.Request) -> web.Response:
    """PUT /api/task-lists/{list_id}"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    try:
        tl = _store().update_task_list(request.match_info["list_id"], **body)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if not tl:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(tl.to_dict())


async def api_task_lists_delete(request: web.Request) -> web.Response:
    """DELETE /api/task-lists/{list_id}"""
    if not _store().delete_task_list(request.match_info["list_id"]):
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"ok": True})


async def api_task_lists_reset(request: web.Request) -> web.Response:
    """POST /api/task-lists/{list_id}/reset — reset a Repeatable-project list: all
    its tasks → open, exit criteria → incomplete, execution notes cleared. Only
    allowed for lists under the Repeatable project and only when all tasks done."""
    from personalclaw.tasks import registry
    from personalclaw.tasks.models import TaskStatus

    store = _store()
    list_id = request.match_info["list_id"]
    tl = store.get_task_list(list_id)
    if not tl:
        return web.json_response({"error": "not found"}, status=404)
    project = store.get_project(tl.project_id)
    if not project or project.name != "Repeatable":
        return web.json_response(
            {"error": "only task lists under the Repeatable project can be reset"}, status=400
        )
    tasks, _ = await registry.list_all_tasks(task_list_id=list_id, limit=10_000)
    non_terminal = [t for t in tasks if t.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)]
    if non_terminal:
        return web.json_response(
            {"error": "all tasks must be complete before the list can be reset"}, status=400
        )
    reset_ids = []
    for t in tasks:
        criteria = [{**c, "status": "incomplete", "met": False} for c in t.exit_criteria]
        await registry.update_task(
            t.id, status="open", exit_criteria=criteria, execution_notes=[]
        )
        reset_ids.append(t.id)
    return web.json_response({"ok": True, "reset_task_ids": reset_ids})


def register_hierarchy_routes(app: web.Application) -> None:
    """Register /api/projects/* and /api/task-lists/* routes."""
    app.router.add_get("/api/projects", api_projects_list)
    app.router.add_post("/api/projects", api_projects_create)
    app.router.add_get("/api/projects/{project_id}", api_projects_get)
    app.router.add_get("/api/projects/{project_id}/linked", api_projects_linked)
    app.router.add_put("/api/projects/{project_id}", api_projects_update)
    app.router.add_delete("/api/projects/{project_id}", api_projects_delete)

    app.router.add_post("/api/task-lists/{list_id}/reset", api_task_lists_reset)
    app.router.add_get("/api/task-lists", api_task_lists_list)
    app.router.add_post("/api/task-lists", api_task_lists_create)
    app.router.add_get("/api/task-lists/{list_id}", api_task_lists_get)
    app.router.add_put("/api/task-lists/{list_id}", api_task_lists_update)
    app.router.add_delete("/api/task-lists/{list_id}", api_task_lists_delete)
