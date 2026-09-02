"""HTTP handlers for /api/projects and /api/task-lists (the Project → TaskList
levels of the task hierarchy)."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.http_errors import json_error
from personalclaw.security import is_sensitive_path, is_system_path
from personalclaw.tasks.hierarchy import HierarchyStore
from personalclaw.workflows import containers, leases
from personalclaw.workflows import store as run_store

logger = logging.getLogger(__name__)

#: TTL for a Work-board claim taken through the claim route. A claim is advisory-but-
#: recorded and short by design: long enough for a single co-tenant to pick up a leaf,
#: short enough that a crashed holder frees it without an admin step.
_CLAIM_TTL_SECS = 300

# The body keys a caller may write through the PUT routes. An ALLOWLIST, not a denylist:
# these handlers used to splat the raw body into the store as ``**body``, so every field
# ever added to Project/TaskList became writable the moment it existed, and a denylist
# would have to be edited in lockstep with the model to stay correct. An allowlist fails
# closed instead. Both sets are the fields the matching ``HierarchyStore.update_*`` method
# actually reads, minus the ones no client should name: ``id`` (identity — the URL path
# carries it), ``is_builtin`` (a delete-protection flag recomputed from the project name),
# and ``created_at``/``updated_at`` (store-owned; ``update_*`` stamps them itself).
_PROJECT_UPDATABLE = frozenset(
    {
        "name",
        "name_locked",
        "status",
        "brief",
        "workspace_dir",
        "agent_instructions_template",
    }
)
_TASK_LIST_UPDATABLE = frozenset({"name", "project_id", "agent_instructions_template"})


def _store() -> HierarchyStore:
    return HierarchyStore()


def _unwritable_field(body: dict, allowed: frozenset[str]) -> str | None:
    """The first key in *body* outside *allowed*, or None when every key is writable.

    A rejected key is REPORTED, never dropped: silently ignoring it answers 200 for a
    write that did not happen, so the caller reasonably believes its change landed.
    Screening here is also what keeps a key that collides with the store method's own
    parameters (``self``, ``project_id``, ``list_id``) from reaching the ``**fields``
    splat, where it surfaced as a TypeError and a bare 500 with nothing for the caller.
    """
    for key in body:
        if key not in allowed:
            return key
    return None


def _workspace_refusal(workspace_dir: str) -> web.Response | None:
    """A 403 when *workspace_dir* names a credential dir or an OS system tree, else None.

    A bound workspace becomes the cwd for an UNSANDBOXED worker that reads, writes and
    runs commands there, and a chat session opened under the project inherits the path —
    so it needs the gate the terminal endpoint already applies before spawning a PTY
    (``dashboard/handlers/terminal.py``), on the same two security helpers. Without it
    this route stored verbatim (200) exactly what that route refuses (403) for the
    identical path. Callers pass the already-stripped value the store would persist, so
    surrounding whitespace cannot carry a sensitive path past the check.
    """
    if not workspace_dir:
        return None  # clearing the binding — the project's context dir becomes the workspace
    if is_sensitive_path(workspace_dir) or is_system_path(workspace_dir):
        return web.json_response(
            {"error": "Workspace directory points to a system or sensitive location."},
            status=403,
        )
    return None


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
        return json_error("invalid_json", status=400)
    store = _store()
    refusal = _workspace_refusal(str(body.get("workspace_dir") or "").strip())
    if refusal is not None:
        return refusal
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
    payload = _project_payload(store, project)
    payload["pack_proposals"] = _fingerprint_proposals(project)
    return web.json_response(payload, status=201)


def _fingerprint_proposals(project: Any) -> list[dict[str, Any]]:
    """The AGENT-PACKS §7 propose-only pack cards for a just-created project.

    ONE of the two places a fingerprint scan may run (the other is the on-demand
    ``GET /api/packs/proposals``); §7 forbids a background loop, and
    :func:`packs.fingerprint.scan_project` enforces that by refusing any other ``reason``.

    The scan writes nothing and is fail-soft: a project must be created even if pack discovery
    breaks, so any failure logs and returns no proposals. ``with_inspect=False`` keeps creation
    latency independent of how many packs matched — the card in the pack store fetches the full
    §3.1 report from the on-demand route when the user actually looks at it.
    """
    from personalclaw.packs.fingerprint import SCAN_REASON_CREATE, scan_project

    try:
        return [
            p.to_dict()
            for p in scan_project(project, reason=SCAN_REASON_CREATE, with_inspect=False)
        ]
    except Exception:  # noqa: BLE001 - pack discovery must never fail project creation
        logger.warning("fingerprint scan failed for a new project", exc_info=True)
        return []


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
    integration — everything the user does on one effort, in one place.

    Also carries the project's ARTIFACTS and its run-written KNOWLEDGE (WORK-CONTAINERS
    §1.6) — the two "what did work on this project leave behind" surfaces."""
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
            row = {
                "id": lp.id,
                "name": lp.name or lp.task[:60],
                "status": lp.status,
                "kind": lp.kind,
                "error_message": lp.error_message or None,
            }
            (code if lp.kind == "code" else loops).append(row)
    except Exception:
        pass

    artifacts: list[dict] = []
    try:
        from personalclaw.artifacts.registry import get_provider

        prov = get_provider()
        if prov is not None:
            artifacts = [
                {"slug": a.slug, "name": a.name, "kind": a.kind} for a in prov.list(project_id=pid)
            ]
    except Exception:
        pass

    # Run-written KNOWLEDGE scoped to this project (WORK-CONTAINERS §1.6): items a run in
    # this project persisted, plus other projects' items whose `sharing_policy` is `shared`
    # (carrying `source_project` so the view can say where they came from). Another project's
    # PRIVATE items never appear — that filter is the whole point of the policy field.
    # Best-effort, like every other section here: no knowledge store, no section.
    knowledge: list[dict] = []
    try:
        from personalclaw.knowledge import project_scope
        from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

        knowledge = project_scope.project_items(
            KnowledgeStore(db_path=str(knowledge_db_path())), project_id=pid, limit=25
        )
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
            chats.append(
                {
                    "key": s.key,
                    "title": getattr(s, "title", "") or s.key,
                    "running": bool(getattr(s, "running", False)),
                }
            )
    except Exception:
        pass

    return web.json_response(
        {
            "loops": loops,
            "code": code,
            "artifacts": artifacts,
            "chats": chats,
            "knowledge": knowledge,
        }
    )


# ── Work board (WORK-CONTAINERS §1/§5.2/§6.1) ──


#: loop.status → the board's own vocabulary. A run and a legacy loop answer the same
#: question on one board, so both project onto `BoardState` here rather than each surface
#: inventing a mapping. An unmapped status degrades to WORKING (visible, not hidden) —
#: never DONE, which would drop live work off the board.
_LOOP_STATE = {
    "running": containers.BoardState.WORKING,
    "intake": containers.BoardState.WORKING,
    "planning": containers.BoardState.WORKING,
    "review": containers.BoardState.REVIEW,
    "ready": containers.BoardState.QUEUED,
    "paused": containers.BoardState.SUSPENDED,
    "blocked": containers.BoardState.NEEDS_INPUT,
    "stagnant": containers.BoardState.NEEDS_INPUT,
    "needs_input": containers.BoardState.NEEDS_INPUT,
    "complete": containers.BoardState.DONE,
    "stopped": containers.BoardState.DONE,
    "failed": containers.BoardState.DONE,
}

#: task.status → the board's vocabulary. Same contract as `_LOOP_STATE`.
_TASK_STATE = {
    "in_progress": containers.BoardState.WORKING,
    "blocked": containers.BoardState.NEEDS_INPUT,
    "done": containers.BoardState.DONE,
    "cancelled": containers.BoardState.DONE,
    "skipped": containers.BoardState.DONE,
    "open": containers.BoardState.QUEUED,
}


def _as_board_row(d: dict) -> containers.BoardRow:
    """Rebuild a `BoardRow` from its dict form, for the flatten→group pass.

    `collect_sections` hands back plain dicts (one source, one section, isolated), so the
    board grouping re-lifts the OK rows into `BoardRow` to reuse `group_board`'s ordering
    and attention arithmetic rather than duplicating it per surface.
    """
    claim_raw = d.get("claim")
    claim = None
    if isinstance(claim_raw, dict):
        claim = containers.Claim(
            holder=str(claim_raw.get("holder", "") or ""),
            expires_at=float(claim_raw.get("expires_at", 0.0) or 0.0),
            taken_at=float(claim_raw.get("taken_at", 0.0) or 0.0),
            renewals=int(claim_raw.get("renewals", 0) or 0),
        )
    try:
        state = containers.BoardState(str(d.get("state", "") or ""))
    except ValueError:
        state = containers.BoardState.WORKING
    return containers.BoardRow(
        run_id=str(d.get("run_id", "") or ""),
        title=str(d.get("title", "") or ""),
        state=state,
        origin=str(d.get("origin", "") or ""),
        project_id=str(d.get("project_id", "") or ""),
        claim=claim,
        collapsed=bool(d.get("collapsed", False)),
        attention=bool(d.get("attention", False)),
        resumable=bool(d.get("resumable", False)),
    )


def _run_rows(pid: str, now: float) -> list[dict]:
    """WF2 runs bound to this project, as board-row dicts with their live claim."""
    runs, _ = run_store.list_runs(project_id=pid, limit=500)
    return [
        containers.board_row(r, claim_record=leases.read_claim(r.id), now=now).to_dict()
        for r in runs
    ]


def _loop_rows(pid: str) -> list[dict]:
    """Legacy Goal/Code loops under this project, adapted to the board-row shape.

    Loops predate the run engine and have their own store; they are still work the user
    is running, so they share the board. Origin `manual` (a user launched them) so they
    are never collapsed as machine noise.
    """
    from personalclaw.loop import store as loop_store

    rows: list[dict] = []
    for lp in loop_store.list_for_project(pid):
        state = _LOOP_STATE.get(str(lp.status), containers.BoardState.WORKING)
        rows.append(
            containers.BoardRow(
                run_id=lp.id,
                title=lp.name or (lp.task or "")[:60] or "(unnamed loop)",
                state=state,
                origin="manual",
                project_id=pid,
                resumable=state is containers.BoardState.SUSPENDED,
                attention=state is containers.BoardState.NEEDS_INPUT,
            ).to_dict()
        )
    return rows


def _task_rows(tasks: list, pid: str) -> list[dict]:
    """Standalone tasks under this project, adapted to the board-row shape.

    A task bound to a run (`workflow_binding`) is already surfaced by the run source, so it
    is skipped here — two rows for one unit of work is a board that double-counts.
    """
    rows: list[dict] = []
    for t in tasks:
        if getattr(t, "workflow_binding", None) is not None:
            continue
        status = getattr(getattr(t, "status", None), "value", "") or ""
        state = _TASK_STATE.get(status, containers.BoardState.QUEUED)
        rows.append(
            containers.BoardRow(
                run_id=getattr(t, "id", "") or "",
                title=getattr(t, "title", "") or "(untitled task)",
                state=state,
                origin="task",
                project_id=pid,
                attention=state is containers.BoardState.NEEDS_INPUT,
            ).to_dict()
        )
    return rows


async def api_projects_work(request: web.Request) -> web.Response:
    """GET /api/projects/{project_id}/work — the state-grouped Work board.

    One board over three heterogeneous sources — WF2 runs, legacy loops, standalone
    tasks — each collected under its own try/except so a slow or broken source degrades
    ONE section rather than the whole first paint (`containers.collect_sections`). OK
    sections' rows are flattened and grouped by `containers.group_board`, which pins
    needs-input first and drops expired claims, so the board is truthful across a gateway
    kill. `completeness` tells the client a partially-failed board apart from a complete
    one; `sections` carries the per-source status the FE renders as a skeleton/degraded
    note.
    """
    store = _store()
    pid = request.match_info["project_id"]
    project = store.get_project(pid)
    if project is None:
        return web.json_response({"error": "not found"}, status=404)
    now = time.time()

    # Tasks are keyed by project NAME and read through an ASYNC provider, so they are
    # fetched HERE (in its own guard) and handed to `collect_sections` as a pre-fetched
    # list — a prefetch failure re-raises inside the source callable so that section still
    # records `status: "error"` rather than silently emptying.
    task_error: Exception | None = None
    tasks: list = []
    try:
        from personalclaw.tasks import registry as task_registry

        tasks, _ = await task_registry.list_all_tasks(project=project.name, limit=10_000)
    except Exception as exc:  # noqa: BLE001 — recorded as the tasks section's failure
        task_error = exc

    def _tasks_source() -> list[dict]:
        if task_error is not None:
            raise task_error
        return _task_rows(tasks, pid)

    sources = {
        "runs": lambda: _run_rows(pid, now),
        "loops": lambda: _loop_rows(pid),
        "tasks": _tasks_source,
    }
    sections, completeness = containers.collect_sections(sources, now=now)

    ok_rows: list[containers.BoardRow] = []
    for section in sections:
        if section.get("status") == "ok":
            ok_rows.extend(_as_board_row(d) for d in section.get("items") or [])
    board = containers.group_board(ok_rows)

    return web.json_response(
        {
            "board": board,
            "sections": sections,
            "completeness": completeness.value,
            "attention": containers.attention_count(ok_rows),
            "loadedAt": now,
        }
    )


async def _claim_body(request: web.Request) -> tuple[str, str] | web.Response:
    """Parse `{target_id, holder}` from a claim/release POST, or return a 400 response."""
    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)
    target_id = str(body.get("target_id", "") or "").strip()
    holder = str(body.get("holder", "") or "").strip()
    if not target_id or not holder:
        return web.json_response({"error": "target_id and holder are required"}, status=400)
    return target_id, holder


async def api_projects_work_claim(request: web.Request) -> web.Response:
    """POST /api/projects/{project_id}/work/claim — take a TTL'd claim on one board row.

    Delegates to the flock-guarded `leases.acquire_claim`. A refusal (someone else holds
    it, or the flock is contended) is a normal 200 outcome the board renders, not an error
    — `granted:false` with the reason, so the caller can show why.
    """
    if _store().get_project(request.match_info["project_id"]) is None:
        return web.json_response({"error": "not found"}, status=404)
    parsed = await _claim_body(request)
    if isinstance(parsed, web.Response):
        return parsed
    target_id, holder = parsed
    granted, reason = leases.acquire_claim(target_id, holder, ttl=_CLAIM_TTL_SECS)
    return web.json_response(
        {
            "granted": granted is not None,
            "claim": granted.to_dict() if granted else None,
            "reason": reason,
        }
    )


async def api_projects_work_release(request: web.Request) -> web.Response:
    """POST /api/projects/{project_id}/work/release — release a claim you hold.

    Only the holder may release; a foreign claim is returned unchanged with the reason.
    """
    if _store().get_project(request.match_info["project_id"]) is None:
        return web.json_response({"error": "not found"}, status=404)
    parsed = await _claim_body(request)
    if isinstance(parsed, web.Response):
        return parsed
    target_id, holder = parsed
    remaining, reason = leases.release_claim(target_id, holder)
    return web.json_response(
        {
            "released": remaining is None and not reason,
            "claim": remaining.to_dict() if remaining else None,
            "reason": reason,
        }
    )


async def api_projects_update(request: web.Request) -> web.Response:
    """PUT /api/projects/{project_id}"""
    try:
        body = await request.json()
    except Exception:
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)
    rejected = _unwritable_field(body, _PROJECT_UPDATABLE)
    if rejected is not None:
        return web.json_response({"error": f"'{rejected}' is not an updatable field"}, status=400)
    if "workspace_dir" in body:
        refusal = _workspace_refusal(str(body["workspace_dir"] or "").strip())
        if refusal is not None:
            return refusal
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
        from personalclaw.autonudge import get_instance
        from personalclaw.loop import manager as loop_manager
        from personalclaw.loop import store as loop_store

        svc = get_instance()
        bound = [
            lp.id for lp in loop_store.list_all() if pid in (lp.project_id, lp.tasks_project_id)
        ]
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
            return web.json_response(
                {
                    "error": "project has bound work",
                    "loops": loops,
                    "code": code,
                    "chats": chats,
                },
                status=409,
            )
    else:
        # Force-delete: tear down the bound loops FIRST (stop workers + clean worktrees +
        # delete rows) so they aren't orphaned, then UNBIND the project-bound chats (clear
        # their project_id) — chats are the user's conversations, detached not destroyed.
        await _teardown_bound_loops(pid)
        _unbind_bound_chats(state, pid)
    # Cascade the project's TASKS before the project + its lists are dropped. `delete_project`
    # removes the task LISTS (and tombstones them) but the task rows live in the native task
    # provider, keyed by task_list_id — so without this they'd survive pointing at dead list ids
    # with a blanked project label, unreachable from every scoped view (#457). Resolve by project
    # NAME (the provider's stable key) BEFORE delete_project, because the derive-label lookup the
    # provider uses to answer `project=` reads the very lists we're about to unlink. Tasks are
    # project content, not live work needing a teardown handshake (that's loops); a per-task failure
    # is logged but never blocks the delete, matching the loop-teardown sweep above.
    _proj = _store().get_project(pid)
    if _proj is not None:
        try:
            from personalclaw.tasks import registry as task_registry

            doomed, _ = await task_registry.list_all_tasks(project=_proj.name, limit=10_000)
            for t in doomed:
                try:
                    await task_registry.delete_task(t.id)
                except Exception:
                    logger.debug(
                        "delete-project: task %s cascade delete failed", t.id, exc_info=True
                    )
        except Exception:
            logger.debug("delete-project: task cascade sweep failed for %s", pid, exc_info=True)
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
        return json_error("invalid_json", status=400)
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
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)
    rejected = _unwritable_field(body, _TASK_LIST_UPDATABLE)
    if rejected is not None:
        return web.json_response({"error": f"'{rejected}' is not an updatable field"}, status=400)
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
        await registry.update_task(t.id, status="open", exit_criteria=criteria, execution_notes=[])
        reset_ids.append(t.id)
    return web.json_response({"ok": True, "reset_task_ids": reset_ids})


async def api_projects_export(request: web.Request) -> web.Response:
    """GET /api/projects/{project_id}/export — download one project as a manifest ZIP.

    Serves the ARCHIVE, not a JSON summary: the point of the format is that the bytes travel to
    another machine. The plan's skip list and expected-credential names ride back in response
    HEADERS, because a user who is handed a file has no other way to learn that three credentials
    must be re-entered on the far side — and the values themselves are, by design, not in the file.

    `?passphrase=` encrypts client-side (AES-GCM). Optional and off by default: an encrypted archive
    is unreadable without the passphrase the user chose, which is a real way to lose a project.
    """
    from personalclaw.artifacts import registry as artifact_registry
    from personalclaw.config.loader import config_dir
    from personalclaw.workflows import project_archive as pa
    from personalclaw.workflows import store as wf_store

    pid = request.match_info["project_id"]
    store = _store()
    project = store.get_project(pid)
    if project is None:
        return web.json_response({"error": "not found"}, status=404)

    passphrase = request.query.get("passphrase", "")
    if passphrase and not pa.encryption_available():
        return web.json_response(
            {"error": "encryption needs the optional `cryptography` extra"}, status=400
        )

    artifacts: list[dict] = []
    try:
        provider = artifact_registry.get_provider()
        if provider is not None:
            artifacts = [a.to_dict() for a in provider.list(project_id=pid)]
    except Exception:  # noqa: BLE001 — an export must not fail because one store is unreadable
        logger.warning("project export: artifact metadata unavailable for %s", pid)

    runs: list[dict] = []
    try:
        rows, _total = wf_store.list_runs(project_id=pid, limit=1000)
        runs = [r.to_dict() for r in rows]
    except Exception:  # noqa: BLE001
        logger.warning("project export: run digests unavailable for %s", pid)

    project_root = config_dir() / "projects" / pid
    try:
        raw, plan = await asyncio.to_thread(
            pa.export_project_archive,
            pid,
            project_root=project_root,
            project_name=project.name,
            artifacts=artifacts,
            runs=runs,
            passphrase=passphrase,
        )
    except pa.ArchiveRefused as exc:
        return web.json_response({"error": str(exc), "reason": exc.reason}, status=400)

    filename = pa.archive_filename(project.name, pid, encrypted=bool(passphrase))
    return web.Response(
        body=raw,
        content_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(raw)),
            # The two decisions a user must act on, in headers a download can carry.
            "X-PersonalClaw-Entities": str(len(plan.entries)),
            "X-PersonalClaw-Skipped": str(len(plan.skipped)),
            "X-PersonalClaw-Secrets-Expected": ",".join(sorted(plan.secrets_present)),
        },
    )


async def api_projects_import(request: web.Request) -> web.Response:
    """POST /api/projects/import — import a project archive (multipart `file`).

    `?preview=1` plans without writing, which is the honest default for an archive that came from
    somewhere else: the user sees what will be accepted, what is refused and under which name it
    will land BEFORE anything touches the home.

    A name collision takes an `imported-N` slot; the existing project is the one thing an import
    must not damage.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.workflows import project_archive as pa

    upload, err = await _read_project_upload(request)
    if err is not None:
        return err
    assert upload is not None

    preview = request.query.get("preview", "") in ("1", "true", "yes")
    passphrase = request.query.get("passphrase", "")
    store = _store()
    existing = [p.name for p in store.list_projects()]

    try:
        plan, archive = await asyncio.to_thread(
            pa.read_archive_plan, upload, existing_names=existing, passphrase=passphrase
        )
    except pa.ArchiveRefused as exc:
        return web.json_response({"error": str(exc), "reason": exc.reason}, status=400)
    except pa.EncryptionUnavailable as exc:
        return web.json_response(
            {"error": str(exc), "reason": "encryption_unavailable"}, status=400
        )
    finally:
        upload.unlink(missing_ok=True)

    payload = plan.to_dict()
    payload["summary"] = _import_summary(plan)
    if preview:
        payload["preview"] = True
        return web.json_response(payload)

    if not plan.ok:
        return web.json_response(
            {**payload, "error": "the archive contributed nothing importable"}, status=400
        )

    created = store.create_project(plan.project_name)
    project_root = config_dir() / "projects" / created.id
    written = await asyncio.to_thread(pa.commit_import, plan, archive, project_root=project_root)
    payload.update({"preview": False, "project_id": created.id, "written": written})
    return web.json_response(payload, status=201)


def _import_summary(plan) -> str:
    from personalclaw.workflows.project_export import import_summary

    return import_summary(plan)


async def _read_project_upload(request: web.Request):
    """Read a multipart `file` field into a unique temp file.

    Mirrors `dashboard.handlers.portability._read_upload_file`'s shape rather than sharing it: that
    one lives in the dashboard package and importing it here would put a handler module's private
    helper on the tasks package's import path.
    """
    import tempfile

    from aiohttp.multipart import BodyPartReader

    ctype = request.headers.get("Content-Type", "")
    if not ctype.lower().startswith("multipart/"):
        return None, web.json_response(
            {"error": "multipart/form-data with a 'file' field is required"}, status=400
        )
    try:
        reader = await request.multipart()
    except (ValueError, AssertionError, RuntimeError) as exc:
        return None, web.json_response(
            {"error": f"failed to parse multipart body: {exc}"}, status=400
        )
    part = await reader.next()
    if part is None or not isinstance(part, BodyPartReader) or part.name != "file":
        return None, web.json_response({"error": "file field required"}, status=400)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        while True:
            chunk = await part.read_chunk(65536)
            if not chunk:
                break
            tmp.write(chunk)
        tmp.close()
        return Path(tmp.name), None
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise


def register_hierarchy_routes(app: web.Application) -> None:
    """Register /api/projects/* and /api/task-lists/* routes."""
    # Static sub-paths BEFORE the dynamic /{project_id} matcher, else `import` reads as an id.
    app.router.add_post("/api/projects/import", api_projects_import)
    app.router.add_get("/api/projects", api_projects_list)
    app.router.add_post("/api/projects", api_projects_create)
    app.router.add_get("/api/projects/{project_id}", api_projects_get)
    app.router.add_get("/api/projects/{project_id}/export", api_projects_export)
    app.router.add_get("/api/projects/{project_id}/linked", api_projects_linked)
    app.router.add_get("/api/projects/{project_id}/work", api_projects_work)
    app.router.add_post("/api/projects/{project_id}/work/claim", api_projects_work_claim)
    app.router.add_post("/api/projects/{project_id}/work/release", api_projects_work_release)
    app.router.add_put("/api/projects/{project_id}", api_projects_update)
    app.router.add_delete("/api/projects/{project_id}", api_projects_delete)

    app.router.add_post("/api/task-lists/{list_id}/reset", api_task_lists_reset)
    app.router.add_get("/api/task-lists", api_task_lists_list)
    app.router.add_post("/api/task-lists", api_task_lists_create)
    app.router.add_get("/api/task-lists/{list_id}", api_task_lists_get)
    app.router.add_put("/api/task-lists/{list_id}", api_task_lists_update)
    app.router.add_delete("/api/task-lists/{list_id}", api_task_lists_delete)
