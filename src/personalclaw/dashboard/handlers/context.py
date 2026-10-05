"""§7 context-provider endpoints — the live-store side of ``context_router``.

Two surfaces:

- ``GET /api/context`` — the routed-context manifest for a project, backing the
  in-process ``get_context`` MCP tool. Resolves the project from an explicit
  ``?project_id=`` or, failing that, the bound project of the chat the call's work is for
  (``approval_answer.work_of_request``), or the Personal default. Read-only.
- ``POST /api/projects/{project_id}/context-adapters/regenerate`` — renders the
  marker-fenced PClaw block into the project's ``workspace_dir`` adapter files
  (``CLAUDE.md`` / ``AGENTS.md`` / ``.cursorrules``), replace-in-place. Gated on
  ``legibility.context_adapters`` (default off — writing into a user's project dir
  is consent-gated) AND a bound ``workspace_dir``. Every write is SEL-audited.

The wiring here is the ONLY place the router touches live stores; the assembly
itself lives in :mod:`personalclaw.legibility.context_router` (pure, testable).
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw import session_keys
from personalclaw.approval_answer import work_of_request
from personalclaw.atomic_write import atomic_write
from personalclaw.http_errors import json_error
from personalclaw.legibility import context_router as cr
from personalclaw.request_validation import json_object_body, string_field
from personalclaw.tasks.hierarchy import HierarchyStore

logger = logging.getLogger(__name__)

# The canonical per-tool adapter files rendered into a project's workspace_dir.
# Each is replace-in-place inside the PCLAW fence; content outside is never touched.
ADAPTER_FILES = ("CLAUDE.md", "AGENTS.md", ".cursorrules")


def _sel():
    from personalclaw.sel import sel

    return sel()


def _skills_index(query: str) -> list[dict]:
    """The query-ranked skills index (key + description), best-effort."""
    try:
        from personalclaw.skills.loader import SkillsLoader

        rows = SkillsLoader().list_skills(with_usage=True)
        # Retired/inactive skills don't belong in a context handoff.
        active = [r for r in rows if (r.get("status") or "active") == "active"]
        return cr._rank_skills(query, active)
    except Exception:
        logger.debug("context: skills index unavailable", exc_info=True)
        return []


def _knowledge_retriever():
    """A HybridRetriever over the shared knowledge store, or None if unavailable."""
    try:
        from personalclaw.knowledge import get_knowledge_embedder, get_knowledge_store
        from personalclaw.knowledge.retrieval import HybridRetriever

        return HybridRetriever(get_knowledge_store(), embedder=get_knowledge_embedder())
    except Exception:
        logger.debug("context: knowledge retriever unavailable", exc_info=True)
        return None


class _ProjectFirst:
    """A project's memory, then the global memory: what its routed context recalls, as the work
    done for the project reads them (``memory_locality.compose_recall``). The global memory's
    episodes come after the project's, each said to come from outside it; ordering only, so one
    the project's memory crowds out is still recalled."""

    def __init__(self, own: Any, shared: Any) -> None:
        self._own, self._shared = own, shared

    def recall_with_provenance(self, *, query_text: str, limit: int = 8) -> list[dict]:
        from personalclaw.memory_locality import CROSS_PARTITION_SOURCE

        own = self._own.recall_with_provenance(query_text=query_text, limit=limit) or []
        shared = self._shared.recall_with_provenance(query_text=query_text, limit=limit) or []
        outside = [
            {**m, "source": ", ".join(filter(None, (m.get("source"), CROSS_PARTITION_SOURCE)))}
            for m in shared
        ]
        return list(own) + outside


def _memory_service(state, project) -> Any:
    """The memory project *project*'s routed context recalls from, or None: the project's own,
    the folder it binds (``memory_locality.project_folder``), first, then the global memory; the
    global memory alone for a project that binds none. Reuses the memory handler's resolver so
    the context read can never drift from the agent's own memory view."""
    try:
        from personalclaw import memory_locality
        from personalclaw.dashboard.handlers.memory import _folder_memory, _global_service

        shared = _global_service(state)
        own = _folder_memory(memory_locality.project_folder(str(getattr(project, "id", ""))))
        return shared if own is None else _ProjectFirst(own, shared)
    except Exception:
        logger.debug("context: memory service unavailable", exc_info=True)
        return None


def _route_for_project(state, project, query: str, withheld: str = "") -> cr.RoutedContext:
    """Assemble the routed context for one project against the live stores. *withheld* is why
    the work it is for reads no memory (``memory_reads``): its memory tier is then not recalled."""
    return cr.route_context(
        project,
        query=query,
        memory_svc=None if withheld else _memory_service(state, project),
        knowledge_retriever=_knowledge_retriever(),
        skills=_skills_index(query),
        memory_withheld=withheld,
    )


def _session_project_id(state, request: web.Request) -> str:
    """The bound project of the chat the request's work is for (``work_of_request``), or "": your
    pages and an app's own work are no chat's."""
    if state is None:
        return ""
    sk = work_of_request(request)
    if not sk or sk == session_keys.DASHBOARD_UI or session_keys.APP.names(sk):
        return ""
    session_name = sk.split(":", 1)[-1] if ":" in sk else sk
    session = (getattr(state, "_sessions", {}) or {}).get(session_name)
    return str(getattr(session, "project_id", "") or "") if session else ""


async def api_context_get(request: web.Request) -> web.Response:
    """GET /api/context?query=…&project_id=… — the routed-context manifest.

    Project resolution precedence: explicit ``project_id`` → the calling session's
    bound project → the Personal default. Rules top, scored memory/knowledge/skills
    middle (distinct headings), L0 unloaded-catalog bottom. Never writes. The memory tier is
    recalled only for work that may read your memory (``memory_reads``): a Temporary chat's
    work and an app's without the memory permission get it empty, saying why.
    """
    from personalclaw.dashboard.handlers._shared import _memory_refusal

    state = request.app.get("state")
    sk = work_of_request(request)
    withheld = _memory_refusal(state, request)
    if withheld:
        _sel().log_api_access(
            caller=sk,
            operation="context.memory",
            outcome="denied",
            source="dashboard",
            resources=sk,
        )
    store = HierarchyStore()
    query = request.query.get("query", "")[:500]

    pid = request.query.get("project_id", "").strip()
    if not pid:
        pid = _session_project_id(state, request)
    project = store.get_project(pid) if pid else None
    if project is None:
        # Fall back to the Personal default so a context read always has a home.
        store.ensure_defaults()
        project = store.get_project_by_name("Personal")
    if project is None:
        return web.json_response({"error": "no project available"}, status=404)

    # Off the event loop: the routing recalls memory and searches knowledge, each embedding the
    # query, a round trip to the model.
    routed = await asyncio.to_thread(_route_for_project, state, project, query, withheld)
    return web.json_response(routed.to_dict())


async def api_project_context_regenerate(request: web.Request) -> web.Response:
    """POST /api/projects/{project_id}/context-adapters/regenerate.

    Renders the marker-fenced PClaw block into the project's workspace_dir adapter
    files, replace-in-place. Refuses (403) when ``legibility.context_adapters`` is
    off, and (400) when the project binds no workspace_dir. Every write is SEL-audited.

    No app reaches this, whatever its manifest declares (``apps/permissions.ROUTE_AUTHZ``): an
    agent CLI working in the folder follows these files as its instructions.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.loop.validation import workspace_write_target_errors

    pid = request.match_info["project_id"]
    state = request.app.get("state")
    store = HierarchyStore()
    project = store.get_project(pid)
    if project is None:
        return web.json_response({"error": "not found"}, status=404)

    if not bool(getattr(AppConfig.load().legibility, "context_adapters", False)):
        return web.json_response(
            {
                "error": "context adapters are disabled",
                "hint": "Enable Settings › Legibility › Context files first.",
            },
            status=403,
        )

    workspace = str(getattr(project, "workspace_dir", "") or "").strip()
    if not workspace:
        return web.json_response(
            {"error": "project has no bound workspace directory to write into"},
            status=400,
        )
    # Path-safety guard (#358): the bound workspace is a WRITE target for the adapter files,
    # so refuse a relative path, the home directory itself, a credential dir or an OS/system
    # root BEFORE touching disk — realpath'd, so a `..`/symlink form cannot slip past. Belt to
    # the bind-time guard's braces: a workspace_dir persisted before that guard existed, or set
    # through a path that bypassed it, is still caught here rather than planting agent files at
    # / or in $HOME.
    unsafe = workspace_write_target_errors(workspace)
    if unsafe:
        return json_error("workspace_dir_unsafe", message=unsafe[0], status=400)
    ws = Path(workspace).expanduser()
    if not ws.is_dir():
        return web.json_response(
            {"error": f"workspace directory does not exist: {workspace}"}, status=400
        )

    body = await json_object_body(request)
    query = string_field(body, "query")[:500]

    from personalclaw.dashboard.handlers._shared import _memory_refusal

    # The files carry the memory tier, so they carry it only for work that may read it.
    withheld = _memory_refusal(state, request)
    routed = await asyncio.to_thread(_route_for_project, state, project, query, withheld)
    block = cr.render_block(routed)

    written: list[str] = []
    errors: list[dict] = []
    for name in ADAPTER_FILES:
        target = ws / name
        try:
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            merged = cr.apply_block(existing, block)
            if merged != existing:
                atomic_write(target, merged)
            written.append(str(target))
            _sel().log_api_access(
                caller=work_of_request(request) or "dashboard",
                operation="legibility.context_adapter.write",
                outcome="success",
                source="dashboard",
                resources=str(target),
            )
        except Exception as exc:
            logger.warning("context adapter write failed for %s: %s", target, exc)
            errors.append({"file": str(target), "error": str(exc)})
            _sel().log_api_access(
                caller=work_of_request(request) or "dashboard",
                operation="legibility.context_adapter.write",
                outcome="error",
                source="dashboard",
                resources=str(target),
                error=str(exc),
            )

    return web.json_response(
        {"ok": not errors, "written": written, "errors": errors, "workspace_dir": str(ws)}
    )
