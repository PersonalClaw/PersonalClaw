"""§7 context-provider endpoints — the live-store side of ``context_router``.

Two surfaces:

- ``GET /api/context`` — the routed-context manifest for a project, backing the
  in-process ``get_context`` MCP tool. Resolves the project from an explicit
  ``?project_id=`` or, failing that, the calling session's bound project (the
  ``X-Session-Key`` header), or the Personal default. Read-only.
- ``POST /api/projects/{project_id}/context-adapters/regenerate`` — renders the
  marker-fenced PClaw block into the project's ``workspace_dir`` adapter files
  (``CLAUDE.md`` / ``AGENTS.md`` / ``.cursorrules``), replace-in-place. Gated on
  ``legibility.context_adapters`` (default off — writing into a user's project dir
  is consent-gated) AND a bound ``workspace_dir``. Every write is SEL-audited.

The wiring here is the ONLY place the router touches live stores; the assembly
itself lives in :mod:`personalclaw.legibility.context_router` (pure, testable).
"""

import logging
from pathlib import Path

from aiohttp import web

from personalclaw.atomic_write import atomic_write
from personalclaw.http_errors import json_error
from personalclaw.legibility import context_router as cr
from personalclaw.tasks.hierarchy import HierarchyStore

logger = logging.getLogger(__name__)

# The canonical per-tool adapter files rendered into a project's workspace_dir.
# Each is replace-in-place inside the PCLAW fence; content outside is never touched.
ADAPTER_FILES = ("CLAUDE.md", "AGENTS.md", ".cursorrules")


def _sel():
    from personalclaw.sel import sel

    return sel()


def _skills_index() -> list[dict]:
    """The surfaced skills index (key + description), best-effort."""
    try:
        from personalclaw.skills.loader import SkillsLoader

        rows = SkillsLoader().list_skills(with_usage=True)
        # Retired/inactive skills don't belong in a context handoff.
        return [r for r in rows if (r.get("status") or "active") == "active"]
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


def _memory_service(state):
    """The MemoryService for recall, or None. Reuses the memory handler's resolver
    so the context read can never drift from the agent's own memory view."""
    try:
        from personalclaw.dashboard.handlers.memory import _get_service

        return _get_service(state)
    except Exception:
        logger.debug("context: memory service unavailable", exc_info=True)
        return None


def _route_for_project(state, project, query: str) -> cr.RoutedContext:
    """Assemble the routed context for one project against the live stores."""
    return cr.route_context(
        project,
        query=query,
        memory_svc=_memory_service(state),
        knowledge_retriever=_knowledge_retriever(),
        skills=_skills_index(),
    )


def _session_project_id(state, request: web.Request) -> str:
    """The bound project of the session named by ``X-Session-Key`` (or "")."""
    if state is None:
        return ""
    sk = request.headers.get("X-Session-Key", "")
    if not sk or sk == "dashboard:ui":
        return ""
    session_name = sk.split(":", 1)[-1] if ":" in sk else sk
    session = (getattr(state, "_sessions", {}) or {}).get(session_name)
    return str(getattr(session, "project_id", "") or "") if session else ""


async def api_context_get(request: web.Request) -> web.Response:
    """GET /api/context?query=…&project_id=… — the routed-context manifest.

    Project resolution precedence: explicit ``project_id`` → the calling session's
    bound project → the Personal default. Rules top, scored memory/knowledge/skills
    middle (distinct headings), L0 unloaded-catalog bottom. Never writes.
    """
    state = request.app.get("state")
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

    routed = _route_for_project(state, project, query)
    return web.json_response(routed.to_dict())


async def api_project_context_regenerate(request: web.Request) -> web.Response:
    """POST /api/projects/{project_id}/context-adapters/regenerate.

    Renders the marker-fenced PClaw block into the project's workspace_dir adapter
    files, replace-in-place. Refuses (403) when ``legibility.context_adapters`` is
    off, and (400) when the project binds no workspace_dir. Every write is SEL-audited.
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

    query = ""
    try:
        body = await request.json()
        query = str((body or {}).get("query", ""))[:500]
    except Exception:
        query = ""

    routed = _route_for_project(state, project, query)
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
                caller=request.headers.get("X-Session-Key", "dashboard"),
                operation="legibility.context_adapter.write",
                outcome="success",
                source="dashboard",
                resources=str(target),
            )
        except Exception as exc:
            logger.warning("context adapter write failed for %s: %s", target, exc)
            errors.append({"file": str(target), "error": str(exc)})
            _sel().log_api_access(
                caller=request.headers.get("X-Session-Key", "dashboard"),
                operation="legibility.context_adapter.write",
                outcome="error",
                source="dashboard",
                resources=str(target),
                error=str(exc),
            )

    return web.json_response(
        {"ok": not errors, "written": written, "errors": errors, "workspace_dir": str(ws)}
    )
