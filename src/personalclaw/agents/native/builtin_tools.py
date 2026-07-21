"""Built-in file/code/shell tools for the native runtime.

In the ACP architecture the file/edit/shell tools were the external CLI's own
built-ins (claude-code provides Read/Write/Bash); ``personalclaw-core`` only
layered orchestration (spawn/memory/artifact) on top. The native in-process
runtime has no such CLI, so this provider supplies the essential workspace
tools — read, write, edit, ls, glob, grep, and bash — scoped to the session's
``cwd`` and gated by the same :mod:`personalclaw.security` checks (deny-list,
sensitive-path) plus :func:`personalclaw.sandbox.wrap_argv` for shell.

All paths are resolved relative to ``cwd`` and confined to it (no escaping the
workspace via ``..`` or absolute paths outside it). Tool execution itself is
also gated by the runtime's approval gate (``requires_approval`` per tool).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from pathlib import Path
from typing import Any

from personalclaw.tool_providers import result_store
from personalclaw.tool_providers.base import (
    RiskLevel,
    ToolDefinition,
    ToolProvider,
    ToolResult,
)
from personalclaw.tool_providers.projection import project_and_retain, project_output

logger = logging.getLogger(__name__)

# Per-invoke workspace context for the native tools. The category providers
# (Filesystem/Shell/Knowledge/…) are now REGISTRY SINGLETONS (one instance,
# shared across sessions) rather than per-session constructions, so the cwd /
# extra-roots / inbox-sender that used to come from the constructor now flow via
# these contextvars — the native runtime binds them in ``_invoke`` (alongside the
# session key in mcp_core) just before dispatching a tool, so each turn's tool
# call resolves THIS turn's workspace. Resolution is contextvar-first with an
# instance fallback (so a directly-constructed provider — e.g. in a test or the
# /api/tools catalog probe — still works). The path-confinement resolve runs in
# the async context where the var is bound (before any executor hop), so the
# binding is always visible to the security check.
_CURRENT_CWD: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_native_cwd", default=""
)
_CURRENT_EXTRA_ROOTS: contextvars.ContextVar[tuple] = contextvars.ContextVar(
    "personalclaw_native_extra_roots", default=()
)
_CURRENT_AGENT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_native_agent", default=""
)
# The Project this turn's work scopes under ("" = none). Bound alongside cwd/agent so
# a tool that produces a durable artifact (artifact_save) can stamp its project_id —
# tying work created during a project's session/loop back to that Project (S5).
_CURRENT_PROJECT_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "personalclaw_native_project_id", default=""
)


def bind_tool_context(
    *,
    cwd: Path | str | None,
    agent: str = "",
    extra_roots: list | None = None,
    project_id: str = "",
):
    """Bind the per-turn workspace context for native tool dispatch; returns a
    list of reset tokens the caller restores after the call. Called by the native
    runtime in ``_invoke`` so the registry-singleton category providers resolve
    this turn's cwd/agent/extra-roots/project."""
    tokens = [
        _CURRENT_CWD.set(str(cwd) if cwd else ""),
        _CURRENT_AGENT.set(agent or ""),
        _CURRENT_EXTRA_ROOTS.set(tuple(str(r) for r in (extra_roots or []))),
        _CURRENT_PROJECT_ID.set(project_id or ""),
    ]
    return tokens


def reset_tool_context(tokens) -> None:
    _vars: tuple[contextvars.ContextVar, ...] = (
        _CURRENT_CWD,
        _CURRENT_AGENT,
        _CURRENT_EXTRA_ROOTS,
        _CURRENT_PROJECT_ID,
    )
    for var, tok in zip(_vars, tokens or []):
        try:
            var.reset(tok)
        except (ValueError, LookupError):
            pass


def current_project_id() -> str:
    """The Project id bound for this turn's tool dispatch ("" if none) — used by
    artifact_save to tie a created artifact to the active Project (S5)."""
    return _CURRENT_PROJECT_ID.get()


# Tool name → conceptual CATEGORY. Each category is surfaced by its own provider
# (UT1 split): the platform bundle owns filesystem/shell/core (always-on, cwd-
# coupled); the rest are pre-installed-but-uninstallable app providers, one per
# entity. A provider built with categories={...} surfaces only its slice; the
# default (categories=None) surfaces everything (tests, direct construction).
_CATEGORY_OF: dict[str, str] = {
    # filesystem (platform, always-on, cwd-coupled)
    "read_file": "filesystem",
    "write_file": "filesystem",
    "edit_file": "filesystem",
    "list_dir": "filesystem",
    "glob": "filesystem",
    "grep": "filesystem",
    "repo_map": "filesystem",
    # shell (platform, always-on, cwd-coupled)
    "bash": "shell",
    # core/runtime affordance (platform, always-on) — the projection retrieval tool
    "tool_result_get": "core",
    # knowledge (installable app)
    "knowledge_search": "knowledge",
    "knowledge_create": "knowledge",
    "knowledge_get": "knowledge",
    "knowledge_update": "knowledge",
    "knowledge_stats": "knowledge",
    # tasks (installable app) — the Project→TaskList→Task CONTAINER hierarchy.
    "task_create": "tasks",
    "task_list": "tasks",
    "task_get": "tasks",
    "task_update": "tasks",
    "task_ready": "tasks",
    "task_search": "tasks",
    "project_create": "tasks",
    "project_list": "tasks",
    "task_list_create": "tasks",
    # projects (installable app) — autonomous project RUNS (loops: code/goal/general/
    # design/research). Loops were absorbed into the uber Project feature; the agent
    # operates them through one cohesive project_run_* set.
    "project_run_create": "projects",
    "project_run_start": "projects",
    "project_run_status": "projects",
    "project_run_list": "projects",
    # inbox (installable app)
    "post_to_inbox": "inbox",
}

# The platform (always-on) categories — surfaced by the core platform provider,
# never user-removable. The rest are installable app providers.
PLATFORM_CATEGORIES: frozenset[str] = frozenset({"filesystem", "shell", "core"})
# category → (provider_name, display) for the installable app providers.
APP_CATEGORY_PROVIDERS: dict[str, tuple[str, str]] = {
    "knowledge": ("personalclaw-knowledge-tools", "Knowledge Tools"),
    "tasks": ("personalclaw-tasks-tools", "Tasks Tools"),
    "projects": ("personalclaw-project-tools", "Projects Tools"),
    "inbox": ("personalclaw-inbox-tools", "Inbox Tools"),
}

_MAX_READ_BYTES = 256 * 1024  # cap a single read to keep context bounded
# Shared with the MCP tool adapter (the app) via projection.DEFAULT_TOOL_OUTPUT_CAP so
# every tool surface projects at the same threshold — one source of truth.
from personalclaw.tool_providers.projection import (  # noqa: E402
    DEFAULT_TOOL_OUTPUT_CAP as _MAX_OUTPUT_CHARS,
)

_BASH_TIMEOUT = 120.0  # default bash timeout when the agent doesn't set one
# Agent-settable bash timeout ceiling. The agent runs its own tests/builds via bash
# (no dedicated run_tests/diagnostics tools), so a slow suite needs headroom beyond
# the 120s default — but a hard cap keeps a background turn from wedging forever.
# Matches the supervisor's verify budget (watchdog._run_check) so a worker can
# reproduce a green gate on a slow suite.
_BASH_TIMEOUT_MAX = 600.0

# Background ingestion tasks for agent-authored knowledge — held so they aren't GC'd
# mid-flight, mirroring the create-fast/enrich-async lifecycle (the tool returns the
# item id immediately; enrichment runs in the background, not blocking the agent turn).
_bg_ingest_tasks: set = set()


def _kn_redact(text: str | None) -> str:
    """Scrub credentials + exfiltration URLs from knowledge text before it reaches the
    model — the same guard the HTTP context-injection path applies. The library can
    hold scraped pages or pasted notes carrying secrets; an agent reading it via tools
    must not see what the chat-injection card would have redacted."""
    if not text:
        return text or ""
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    cleaned, _ = redact_exfiltration_urls(text)
    cleaned, _ = redact_credentials(cleaned)
    return cleaned


def _kn_title(item: dict) -> str:
    """Best display title for a knowledge item. Items are searchable before
    enrichment (create-fast/enrich-async), so the raw ``title`` may still be empty
    (unscraped bookmark) — fall back through the AI/link/url titles."""
    for key in ("title", "ai_title", "url_title", "url"):
        val = (item.get(key) or "").strip()
        if val:
            return val
    return "(untitled)"


def _kn_snippet(item: dict, limit: int = 160) -> str:
    """Best one-line snippet: prefer a summary, then the link/description, then body."""
    insights = item.get("insights") or {}
    for cand in (
        item.get("summary"),
        item.get("ai_summary"),
        insights.get("summary") if isinstance(insights, dict) else None,
        item.get("url_description"),
        item.get("content"),
    ):
        text = (cand or "").strip()
        if text:
            return text[:limit].replace("\n", " ")
    return ""


def _enrich_in_background(item_id: str) -> None:
    """Fire-and-forget the ingestion node-graph for an agent-created/updated item."""
    from personalclaw.knowledge import (
        get_knowledge_embedder,
        get_knowledge_llm_pool,
        get_knowledge_store,
    )
    from personalclaw.knowledge.pipeline.runner import ingest_item

    async def _run() -> None:
        try:
            # Pass the embedder so an agent-created item is vector-searchable like one
            # created via the UI (the gateway's ingest queue embeds; this direct path
            # must too, else agent-authored knowledge is keyword-only forever).
            await ingest_item(
                get_knowledge_store(),
                item_id,
                embedder=get_knowledge_embedder(),
                insights_pool=get_knowledge_llm_pool(),
            )
        except Exception:
            logger.debug("background knowledge enrich failed for %s", item_id, exc_info=True)

    task = asyncio.create_task(_run())
    _bg_ingest_tasks.add(task)
    task.add_done_callback(_bg_ingest_tasks.discard)


def _ok_capped(
    text: str,
    limit: int = _MAX_OUTPUT_CHARS,
    *,
    content_type: str | None = None,
    session_key: str = "",
) -> ToolResult:
    """Success result whose ``output`` is a type-aware **projection** within ``limit``.

    Routes large output through :func:`project_output` (OP1) — keeping the salient
    slice for its content type (log error lines, diff hunks, json shape, test
    failures, csv head/tail) instead of a blind middle-cut — and, when projected,
    retains the **full raw** in the per-session tool-result store (OP2) so the
    model can pull the dropped part via ``tool_result_get``.

    Stays fail-soft + backward-compatible: a small result, or one of an
    unknown/declared-generic type, passes through exactly as the old head/tail
    :func:`maybe_truncate` did. ``content_type`` is the tool's declared type (wins
    over inference); ``session_key`` enables the raw store (omitted → no raw_ref,
    projection still applies). The result's ``metadata`` carries ``content_type``
    + ``raw_ref`` so the event/render layers can consume them.
    """
    # One shared dispatch-time discipline (project + retain raw + name the recovery
    # affordance) — the same helper the MCP adapter uses, so no surface diverges.
    # project_and_retain returns the projection outcome (truncated/original_length)
    # in meta, so we project exactly once.
    proj_text, meta = project_and_retain(
        text,
        session_key=session_key,
        content_type=content_type,
        cap=limit,
    )
    return ToolResult(
        success=True,
        output=proj_text,
        truncated=bool(meta.get("truncated")),
        original_length=meta.get("original_length"),
        metadata=meta,
    )


def _denied_bash_reason(command: str) -> str | None:
    """Return the denied pattern a command matches, or None.

    Delegates to :func:`personalclaw.security.denied_command_reason` — the single
    source of truth for the credential-exfiltration / destructive-command denylist
    (always-on built-in patterns + any user additions from ``AppConfig.security``).
    """
    from personalclaw import security

    return security.denied_command_reason(command)


class NativeBuiltinToolProvider(ToolProvider):
    """Workspace file/code/shell tools for the native agent loop."""

    # VCS / vendored / build dirs that grep + repo_map skip — searching them is
    # slow and floods results with deps, lockfiles, and build output.
    _SKIP_DIRS = frozenset(
        {
            ".git",
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            "env",
            "dist",
            "build",
            ".next",
            "target",
            ".mypy_cache",
            ".pytest_cache",
            "vendor",
            ".idea",
            ".tox",
            ".cache",
            "coverage",
            ".gradle",
        }
    )

    def __init__(
        self,
        cwd: Path | None = None,
        *,
        sandbox_mode: str = "auto",
        agent: str = "",
        session_key: str = "",
        extra_roots: list[Path] | None = None,
        categories: "frozenset[str] | set[str] | None" = None,
        provider_name: str = "builtin",
        display: str = "Workspace Tools",
    ) -> None:
        # Instance-held workspace state. For a REGISTRY-SINGLETON category provider
        # these are the construction-time fallbacks; the live per-turn values come
        # from the contextvars (bound by the runtime in _invoke). For a directly-
        # constructed provider (tests, the /api/tools catalog probe) these ARE the
        # values. The properties below resolve contextvar-first, instance-fallback.
        self._cwd_inst = Path(cwd) if cwd else Path.cwd()
        # Additional directories the file tools may read/write outside cwd. Needed by
        # the Code/Goal-Loop workers: a BROWNFIELD project's worker cwd is the user's
        # workspace, but its engine files (status.json/brief.md/guidance.txt/findings/
        # questions.json) live in the project files dir UNDER ~/.personalclaw — outside
        # the workspace. Without this, _resolve would reject every engine-file path as
        # "escapes the workspace root", so the worker couldn't read its brief or write
        # findings. Empty for normal chat sessions (workspace-only confinement holds).
        self._extra_roots_inst = [Path(r).resolve() for r in (extra_roots or [])]
        self._sandbox_mode = sandbox_mode
        # Identity for post_to_inbox: the agent role (sender) + its session
        # (reply_target so a needs_reply item routes back to this agent).
        self._agent_inst = agent or ""
        self._session_key_inst = session_key or ""
        # Which tool CATEGORIES this provider surfaces. None = ALL (the monolithic
        # form, kept for tests + direct construction). A category provider passes
        # its own subset (e.g. {"filesystem","shell","core"} for the platform
        # bundle, {"knowledge"} for the Knowledge app) so each conceptual entity is
        # its own provider while all handler bodies stay shared here.
        self._categories = frozenset(categories) if categories is not None else None
        self._provider_name = provider_name
        self._display = display

    # ── per-turn workspace state (contextvar-first, instance fallback) ──
    @property
    def _cwd(self) -> Path:
        v = _CURRENT_CWD.get()
        return Path(v) if v else self._cwd_inst

    @property
    def _extra_roots(self) -> list[Path]:
        v = _CURRENT_EXTRA_ROOTS.get()
        return [Path(r).resolve() for r in v] if v else self._extra_roots_inst

    @property
    def _agent(self) -> str:
        return _CURRENT_AGENT.get() or self._agent_inst

    @property
    def _session_key(self) -> str:
        from personalclaw import mcp_core

        return mcp_core.get_current_session_key() or self._session_key_inst

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def display_name(self) -> str:
        return self._display

    # ── path confinement ──
    def _resolve(self, rel: str) -> Path:
        """Resolve ``rel`` under cwd (or an extra allowed root); raise on escape.

        A relative path resolves under cwd. An absolute path is accepted only if it
        lands inside cwd OR one of ``extra_roots`` (the project files dir for a
        brownfield worker). This keeps the default workspace-only confinement for
        chat sessions while letting a worker reach its engine files."""
        base = self._cwd.resolve()
        p = (base / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
        allowed = [base, *self._extra_roots]
        if not any(root == p or root in p.parents for root in allowed):
            raise ValueError(f"path {rel!r} escapes the workspace root")
        return p

    async def list_tools(self) -> list[ToolDefinition]:
        s = {"type": "object"}
        _all = self._all_tool_defs(s)
        if self._categories is None:
            return _all
        # A category provider surfaces only its slice (+ stamps its own provider
        # name so /api/tools + the registry group it correctly).
        out = [t for t in _all if _CATEGORY_OF.get(t.name) in self._categories]
        for t in out:
            t.provider = self._provider_name
        return out

    def _all_tool_defs(self, s: dict) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="read_file",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Read a UTF-8 text file from the workspace. Args: path (str), optional max_bytes (int).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"path": {"type": "string"}, "max_bytes": {"type": "integer"}},
                    "required": ["path"],
                },
            ),
            ToolDefinition(
                name="write_file",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description="Create or overwrite a text file in the workspace. Args: path (str), content (str).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            ),
            ToolDefinition(
                name="edit_file",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description="Replace old_str with new_str in a file. old_str must match EXACTLY ONCE (include surrounding context to make it unique) — if it matches multiple times the edit is rejected unless replace_all is true. Args: path, old_str, new_str, replace_all (optional, default false).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {
                        "path": {"type": "string"},
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["path", "old_str", "new_str"],
                },
            ),
            ToolDefinition(
                name="list_dir",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="List entries in a workspace directory. Args: path (str, default '.').",
                parameters={**s, "properties": {"path": {"type": "string"}}},
            ),
            ToolDefinition(
                name="glob",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Find files matching a glob pattern under the workspace. Args: pattern (str, e.g. '**/*.py').",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
            ),
            ToolDefinition(
                name="grep",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Search file contents (substring by default, or a Python regex with regex=true). Skips .git/node_modules/venv/build dirs. Args: query (str), optional glob (str), optional regex (bool), optional max_results (int).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {
                        "query": {"type": "string"},
                        "glob": {"type": "string"},
                        "regex": {"type": "boolean"},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="repo_map",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Structural map of the workspace codebase — the directory tree plus the "
                    "top-level definitions (functions, classes, exports) of each source file, "
                    "so you can orient WITHOUT reading every file. Args: optional path (str, "
                    "subdir to map, default the whole workspace), optional max_files (int)."
                ),
                parameters={
                    **s,
                    "properties": {"path": {"type": "string"}, "max_files": {"type": "integer"}},
                },
            ),
            ToolDefinition(
                name="bash",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.DESTRUCTIVE,
                description=(
                    "Run a shell command in the workspace — your PRIMARY way to interact with the "
                    "environment. Use it for git (status/diff/branch/commit — push is blocked), "
                    "running tests (pytest, npm test, go test, cargo test, make test), linters/"
                    "type-checkers (ruff, eslint, tsc, go vet), builds, package managers, and any "
                    "standard CLI. Prefer real commands over asking for a dedicated tool. Runs in a "  # noqa: E501
                    "login shell at the workspace root; stdout+stderr are merged and the exit code "
                    "is reported. Sandboxed + credential/exfiltration deny-list enforced. Args: "
                    "command (str), optional timeout (int seconds, default 120, max 600 — raise it "
                    "for a slow test suite or build)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {
                            "type": "integer",
                            "description": "Seconds before the command is killed (default 120, max 600).",  # noqa: E501
                        },
                    },
                    "required": ["command"],
                },
            ),
            ToolDefinition(
                name="post_to_inbox",
                provider=self.name,
                requires_approval=False,
                # CAUTION: surfaces an outward message to the user (a bounded
                # side-effect — a store write + WS broadcast), not a read.
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Surface a message to the user in their Inbox triage queue — use when "
                    "you finish something worth reporting, need a decision, or have a heads-up, "
                    "and no one is watching the chat live. Args: message (str), kind "
                    "('notification'|'question'|'fyi', default 'notification'; 'question' asks "
                    "for a reply), optional context (str — why/what you used)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "message": {"type": "string"},
                        "kind": {"type": "string", "enum": ["notification", "question", "fyi"]},
                        "context": {"type": "string"},
                    },
                    "required": ["message"],
                },
            ),
            ToolDefinition(
                name="knowledge_search",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Search the user's knowledge library (notes, bookmarks, docs). Args: query (str), optional limit (int, default 8).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="knowledge_create",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Add an item to the user's knowledge library. Args: type "
                    "('note'|'fleeting'|'journal'|'gist'|'bookmark', default 'note'), "
                    "title (str), content (str — the note/gist body), url (str — for bookmark), "
                    "optional tags (list of str), optional gist_language (str — the "
                    "code language for a gist, e.g. 'python')."
                ),
                parameters={
                    **s,
                    "properties": {
                        "type": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "url": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "gist_language": {"type": "string"},
                    },
                },
            ),
            ToolDefinition(
                name="knowledge_get",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Fetch one knowledge item by id (title, type, content, tags, summary). Args: id (str).",  # noqa: E501
                parameters={**s, "properties": {"id": {"type": "string"}}, "required": ["id"]},
            ),
            ToolDefinition(
                name="knowledge_update",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Update an existing knowledge item and re-enrich it. Args: id (str, required), "
                    "and any of title (str), content (str), tags (list of str), url (str), "
                    "gist_language (str — only for gist items; sets the code language for syntax "
                    "highlighting), is_pinned (bool), is_archived (bool). Editing content/url re-runs extraction."  # noqa: E501
                ),
                parameters={
                    **s,
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "url": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "gist_language": {"type": "string"},
                        "is_pinned": {"type": "boolean"},
                        "is_archived": {"type": "boolean"},
                    },
                    "required": ["id"],
                },
            ),
            ToolDefinition(
                name="knowledge_stats",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Get an overview of the knowledge library for gap detection: total item "
                    "count, a by-type breakdown, and the most common tags. No args."
                ),
                parameters={**s, "properties": {}},
            ),
            # ── Tasks (Project → TaskList → Task) ──
            ToolDefinition(
                name="task_create",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Create a task in the user's task system. Args: title (str, required), "
                    "optional description (str), priority ('critical'|'high'|'medium'|'low'|"
                    "'trivial', default medium), task_list_id (str — place it in a task list; "
                    "the task's project label is derived from the list), labels (list of str), "
                    "due (str ISO date), exit_criteria (list of {description, met?}), "
                    "action_plan (list of {content} ordered), depends_on (list of task ids "
                    "that must finish first). Cycles are rejected."
                ),
                parameters={
                    **s,
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "priority": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low", "trivial"],
                        },
                        "task_list_id": {"type": "string"},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "due": {"type": "string"},
                        "exit_criteria": {"type": "array", "items": {"type": "object"}},
                        "action_plan": {"type": "array", "items": {"type": "object"}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title"],
                },
            ),
            ToolDefinition(
                name="task_list",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "List tasks, most-recent first. Args: optional status "
                    "('open'|'in_progress'|'blocked'|'done'|'cancelled'), project (str label), "
                    "task_list_id (str), limit (int, default 25)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "status": {"type": "string"},
                        "project": {"type": "string"},
                        "task_list_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            ToolDefinition(
                name="task_get",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="Fetch one task by id (full detail incl. exit criteria, plan, deps). Args: id (str).",  # noqa: E501
                parameters={**s, "properties": {"id": {"type": "string"}}, "required": ["id"]},
            ),
            ToolDefinition(
                name="task_update",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Update a task. Args: id (str, required), and any of title, description, "
                    "status ('open'|'in_progress'|'blocked'|'done'|'cancelled' — 'done' is "
                    "rejected while exit criteria are incomplete), priority, task_list_id, "
                    "labels, due, exit_criteria, action_plan, depends_on. The 'project' label "
                    "is derived from the task list and cannot be set directly."
                ),
                parameters={
                    **s,
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {"type": "string"},
                        "priority": {"type": "string"},
                        "task_list_id": {"type": "string"},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "due": {"type": "string"},
                        "exit_criteria": {"type": "array", "items": {"type": "object"}},
                        "action_plan": {"type": "array", "items": {"type": "object"}},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id"],
                },
            ),
            ToolDefinition(
                name="task_ready",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "List tasks that can be started now (no unfinished prerequisites), "
                    "optionally scoped. Args: optional project (str), task_list_id (str)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "project": {"type": "string"},
                        "task_list_id": {"type": "string"},
                    },
                },
            ),
            ToolDefinition(
                name="task_search",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Search tasks by text + filters. Args: optional query (str over title+"
                    "description), status (list), priority (list), tags (list), project (str), "
                    "sort_by ('relevance'|'created_at'|'updated_at'|'priority'), limit (int)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "query": {"type": "string"},
                        "status": {"type": "array", "items": {"type": "string"}},
                        "priority": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "project": {"type": "string"},
                        "sort_by": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            ToolDefinition(
                name="project_create",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Create a project (a scoping container for task lists). Args: name (str, "
                    "required, unique), optional agent_instructions_template (str)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "name": {"type": "string"},
                        "agent_instructions_template": {"type": "string"},
                    },
                    "required": ["name"],
                },
            ),
            ToolDefinition(
                name="project_list",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description="List projects (with their task lists). No args.",
                parameters={**s, "properties": {}},
            ),
            ToolDefinition(
                name="task_list_create",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Create a task list inside a project. Args: name (str, required), optional "
                    "project_id (str) or project_name (str, find-or-create); repeatable (bool — "
                    "place under the Repeatable project). With no project it lands in 'Chore'."
                ),
                parameters={
                    **s,
                    "properties": {
                        "name": {"type": "string"},
                        "project_id": {"type": "string"},
                        "project_name": {"type": "string"},
                        "repeatable": {"type": "boolean"},
                    },
                    "required": ["name"],
                },
            ),
            # ── Projects: create + launch + watch an autonomous run (any kind) ──
            # A Project is the uber work-unit; a loop is one KIND of project-scoped run.
            # ONE cohesive project_* set operates them all (kind selects code SDLC vs
            # goal/general/design/research), matching the unified Project+Loop arch.
            ToolDefinition(
                name="project_run_create",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description=(
                    "Create a project RUN — an autonomous, multi-cycle execution (a 'loop') — from a "  # noqa: E501
                    "plan you shaped with the user. USE WHEN the user wants substantial over-many-cycles "  # noqa: E501
                    "work rather than a one-shot chat answer. The `kind` selects the engine: 'code' "  # noqa: E501
                    "(SDLC plan→execute in a codebase — feature/refactor/bugfix, gated stages, its own "  # noqa: E501
                    "workspace + tasks), 'goal' (open-ended research-or-action toward an outcome — "
                    "investigate/monitor/drive to done), 'research' (deep web research → a synthesized "  # noqa: E501
                    "report), 'design' (a design system — tokens/components/exports), or 'general' (a "  # noqa: E501
                    "generic iterative task). Offer it, then create on the user's go. Does NOT start it "  # noqa: E501
                    "— call project_run_start on their go. (To create a plain task CONTAINER instead, "  # noqa: E501
                    "use project_create.) Args: kind (required), task (str, required, 12+ chars — the "  # noqa: E501
                    "goal/work), name?, project_id? (bind under an existing Project container), attended?, "  # noqa: E501
                    "max_cycles?, success_criteria?. kind 'code': project_kind? (greenfield|brownfield), "  # noqa: E501
                    "entry_stage?, workspace_dir? (brownfield needs one to start), stage_plan? "
                    "([{stage,title,objective,exit_criteria?,tasks?}]), verify_command?, test_command?. "  # noqa: E501
                    "kind goal/research/design/general: sub_goals? ([str]), deliverables? ([str]), "
                    "scope? ([str]), goal_type? (goal only), rubric? ([str])."
                ),
                parameters={
                    **s,
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["code", "goal", "general", "design", "research"],
                        },
                        "task": {"type": "string"},
                        "name": {"type": "string"},
                        "project_id": {"type": "string"},
                        "attended": {"type": "boolean"},
                        "max_cycles": {"type": "integer"},
                        "success_criteria": {"type": "string"},
                        # code-kind
                        "project_kind": {"type": "string"},
                        "entry_stage": {"type": "string"},
                        "workspace_dir": {"type": "string"},
                        "stage_plan": {"type": "array"},
                        "verify_command": {"type": "string"},
                        "test_command": {"type": "string"},
                        # goal/research/design/general
                        "sub_goals": {"type": "array"},
                        "deliverables": {"type": "array"},
                        "scope": {"type": "array"},
                        "goal_type": {"type": "string"},
                        "rubric": {"type": "array"},
                    },
                    "required": ["kind", "task"],
                },
            ),
            ToolDefinition(
                name="project_run_start",
                provider=self.name,
                requires_approval=True,
                risk_level=RiskLevel.CAUTION,
                description="Launch a created project run (any kind), or resume a paused/failed one. Args: project_id (str, required — the run id).",  # noqa: E501
                parameters={
                    **s,
                    "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"],
                },
            ),
            ToolDefinition(
                name="project_run_status",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Read live progress of any project run — status, stage/phase progress, cycles, latest "  # noqa: E501
                    "finding, and any blocker / needs-input — to report to the user. Args: project_id (str, required — the run id)."  # noqa: E501
                ),
                parameters={
                    **s,
                    "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"],
                },
            ),
            ToolDefinition(
                name="project_run_list",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "List the user's project runs (autonomous executions) with kind + live status, to find "  # noqa: E501
                    "one to report on or resume. Args: optional kind (filter: code|goal|general|design|research), limit (int)."  # noqa: E501
                ),
                parameters={
                    **s,
                    "properties": {
                        "kind": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            ToolDefinition(
                name="tool_result_get",
                provider=self.name,
                requires_approval=False,
                risk_level=RiskLevel.SAFE,
                description=(
                    "Retrieve the FULL raw output of an earlier tool call that was projected/"
                    "truncated. When a tool result shows '[projected … full result: "
                    'tool_result_get(result_id="r_…")]\', call this with that result_id to pull the '  # noqa: E501
                    "part the preview dropped. Args: result_id (str, required); optional grep (str, "  # noqa: E501
                    "return only matching lines), start/end (int, char range), max_chars (int)."
                ),
                parameters={
                    **s,
                    "properties": {
                        "result_id": {"type": "string"},
                        "grep": {"type": "string"},
                        "start": {"type": "integer"},
                        "end": {"type": "integer"},
                        "max_chars": {"type": "integer"},
                    },
                    "required": ["result_id"],
                },
            ),
        ]

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        try:
            handler = getattr(self, f"_t_{tool_name}", None)
            if handler is None:
                return ToolResult(success=False, error=f"unknown builtin tool {tool_name!r}")
            return await handler(arguments)
        except ValueError as exc:  # confinement / arg errors → surface to model
            msg = str(exc)
            hints: list[str] = []
            if "escapes the workspace" in msg:
                hints = [
                    "Use a path relative to the workspace root; '..' and absolute paths outside it are not allowed."  # noqa: E501
                ]
            return ToolResult(success=False, error=msg, recovery_hints=hints)
        except KeyError as exc:  # a required argument was omitted
            return ToolResult(
                success=False,
                error=f"missing required argument: {exc}",
                recovery_hints=[
                    f"Provide the {exc} argument; see the tool's parameter schema for required fields."  # noqa: E501
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("builtin tool %s failed", tool_name, exc_info=True)
            return ToolResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                recovery_hints=[
                    "Check the arguments against the tool's parameter schema and retry."
                ],
            )

    # ── tool-output retrieval (OP2): pull the full raw of a projected result ──
    async def _t_tool_result_get(self, a: dict) -> ToolResult:
        rid = str(a.get("result_id", "")).strip()
        if not rid:
            return ToolResult(
                success=False,
                error="result_id is required",
                recovery_hints=["Pass the result_id named in the projection note, e.g. r_001ab."],
            )
        if not self._session_key:
            return ToolResult(success=False, error="no session context for tool-result retrieval")
        grep = a.get("grep")
        start = int(a.get("start") or 0)
        end = a.get("end")
        end = int(end) if end is not None else None
        max_chars = int(a.get("max_chars") or _MAX_OUTPUT_CHARS)
        res = result_store.fetch_slice(
            self._session_key,
            rid,
            start=start,
            end=end,
            grep=str(grep) if grep else None,
            max_chars=max_chars,
        )
        if not res.get("ok"):
            return ToolResult(
                success=False,
                error=res.get("error", "not found"),
                recovery_hints=[
                    "The raw result may have been evicted (bounded store) — re-run the original tool."  # noqa: E501
                ],
            )
        note = (
            f"[{res['mode']}: showing {res['shown']} of {res['length']} chars"
            + (f", {res.get('matches', 0)} match(es)" if res["mode"] == "grep" else "")
            + (f"; more from start_index={res['next_index']}" if res.get("next_index") else "")
            + "]"
        )
        # Carry the ORIGINAL stored content_type back so a retrieved diff/log/json
        # renders rich (not flattened to generic). A grep slice loses structure, so
        # only the full range keeps the type; grep falls back to generic.
        ctype = res.get("content_type", "generic") if res.get("mode") == "range" else "generic"
        return ToolResult(
            success=True, output=f"{note}\n{res['content']}", metadata={"content_type": ctype}
        )

    # ── SDLC: create/launch a Code project or Goal Loop from chat (sdlc_tools.py) ──
    async def _t_project_run_create(self, a: dict) -> ToolResult:
        from personalclaw.agents.native import sdlc_tools

        return await sdlc_tools.project_create(a)

    async def _t_project_run_start(self, a: dict) -> ToolResult:
        from personalclaw.agents.native import sdlc_tools

        return await sdlc_tools.project_start(a)

    async def _t_project_run_status(self, a: dict) -> ToolResult:
        from personalclaw.agents.native import sdlc_tools

        return await sdlc_tools.project_status(a)

    async def _t_project_run_list(self, a: dict) -> ToolResult:
        from personalclaw.agents.native import sdlc_tools

        return await sdlc_tools.project_list(a)

    # ── tool impls (run blocking fs/proc work off the loop) ──
    async def _t_read_file(self, a: dict) -> ToolResult:
        path = self._resolve(str(a["path"]))
        cap = int(a.get("max_bytes") or _MAX_READ_BYTES)

        # Sentinel distinguishes "not a file" (None) from "binary file" (a marker the
        # _read closure returns) so the caller maps each to its own message.
        _BINARY = object()

        def _read() -> object:
            if not path.is_file():
                return None
            raw = path.read_bytes()[:cap]
            # A NUL byte in the head means binary (image/compiled artifact/etc.) —
            # decoding it with errors='replace' would hand the model a wall of mojibake
            # it can't use and might act on as if it were source. Flag it honestly.
            # Check only the first 8KB — git's own binary heuristic, and matches the
            # FE file-read handler (api_file_read) so both read paths agree.
            if b"\x00" in raw[:8192]:
                return _BINARY
            return raw.decode("utf-8", "replace")

        data = await asyncio.get_event_loop().run_in_executor(None, _read)
        if data is None:
            return ToolResult(
                success=False,
                error=f"not a file: {a['path']}",
                recovery_hints=[
                    "Use glob to locate the file, or list_dir on its parent directory."
                ],
            )
        if data is _BINARY:
            return ToolResult(
                success=False,
                error=f"binary file (not UTF-8 text): {a['path']}",
                recovery_hints=[
                    "This is a binary file — read_file only handles text. Use list_dir/glob to inspect it, or a bash tool if you need its bytes."  # noqa: E501
                ],
            )
        return _ok_capped(data, session_key=self._session_key)  # type: ignore[arg-type]

    async def _t_write_file(self, a: dict) -> ToolResult:
        path = self._resolve(str(a["path"]))
        content = str(a.get("content", ""))

        def _write() -> str | None:
            # Returns an error string on a known-failure, else None on success. Guard
            # the cases that would otherwise raise a raw OSError caught by the generic
            # invoke() handler — which leaks the absolute server path + gives a
            # misleading "check the arguments" hint (the args are fine; the target is).
            if path.is_dir():
                return "is a directory"
            # A parent segment that's a FILE (not a dir) makes mkdir raise NotADirectory
            # — report it cleanly instead of leaking the OSError.
            parent = path.parent
            if parent.exists() and not parent.is_dir():
                return "parent is not a directory"
            parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return None

        err = await asyncio.get_event_loop().run_in_executor(None, _write)
        if err == "is a directory":
            return ToolResult(
                success=False,
                error=f"path is a directory, not a file: {a['path']}",
                recovery_hints=[
                    "Pass a file path, not a directory. Add a filename segment (e.g. dir/file.py)."
                ],
            )
        if err == "parent is not a directory":
            return ToolResult(
                success=False,
                error=f"a parent path segment is a file, not a directory: {a['path']}",
                recovery_hints=[
                    "A directory in the path is actually a file — pick a different location or remove the conflicting file first."  # noqa: E501
                ],
            )
        return ToolResult(success=True, output=f"Wrote {len(content)} chars to {a['path']}")

    async def _t_edit_file(self, a: dict) -> ToolResult:
        path = self._resolve(str(a["path"]))
        old, new = str(a["old_str"]), str(a["new_str"])
        replace_all = bool(a.get("replace_all"))

        def _edit() -> tuple[bool, str]:
            # Returns (ok, message). ok=False carries a stable error string the
            # caller maps to a recovery hint without re-deriving the failure mode.
            if not path.is_file():
                return False, f"not a file: {a['path']}"
            # An EMPTY old_str is meaningless and dangerous: str.count("") = len+1 and
            # replace("", new) inserts `new` between every char (file corruption). And a
            # no-op old==new would report "Edited" success while changing nothing, so the
            # worker would believe it made an edit it didn't. Reject both up front.
            if old == "":
                return False, "old_str is empty"
            if old == new:
                return False, "old_str and new_str are identical (no change)"
            text = path.read_text(encoding="utf-8")
            count = text.count(old)
            if count == 0:
                return False, "old_str not found in file"
            # Ambiguous edit guard (matches the Claude Code Edit contract): a
            # non-unique old_str would silently patch the wrong occurrence, so
            # reject it unless the caller explicitly opts into replace_all.
            if count > 1 and not replace_all:
                return False, f"old_str matched {count} times (not unique)"
            n = count if replace_all else 1
            path.write_text(text.replace(old, new, n), encoding="utf-8")
            return True, f"Edited {a['path']} ({n} replacement{'s' if n != 1 else ''})"

        ok, msg = await asyncio.get_event_loop().run_in_executor(None, _edit)
        if ok:
            return ToolResult(success=True, output=msg)
        if msg.startswith("not a file"):
            hint = "Use glob or list_dir to confirm the path, or write_file to create it first."
        elif "not unique" in msg:
            hint = "Add surrounding lines to old_str so it matches exactly once, or pass replace_all=true to change every occurrence."  # noqa: E501
        elif msg == "old_str is empty":
            hint = "old_str must be the exact existing text to replace. To create a file or append, use write_file."  # noqa: E501
        elif "identical" in msg:
            hint = "The file already contains new_str — no edit needed. If you meant a different change, set old_str to the current text."  # noqa: E501
        else:  # old_str not found
            hint = "Read the file first to copy the exact text (including whitespace) you want to replace."  # noqa: E501
        return ToolResult(success=False, error=msg, recovery_hints=[hint])

    async def _t_list_dir(self, a: dict) -> ToolResult:
        path = self._resolve(str(a.get("path") or "."))

        def _ls() -> str | None:
            if not path.is_dir():
                return None
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
            return "\n".join(entries) or "(empty)"

        listing = await asyncio.get_event_loop().run_in_executor(None, _ls)
        if listing is None:
            return ToolResult(
                success=False,
                error=f"not a directory: {a.get('path', '.')}",
                recovery_hints=[
                    "Use list_dir on the parent directory, or read_file if this path is a file."
                ],
            )
        return _ok_capped(listing, session_key=self._session_key)

    async def _t_glob(self, a: dict) -> ToolResult:
        base = self._cwd.resolve()
        pattern = str(a["pattern"])

        def _glob() -> str:
            matches = sorted(str(p.relative_to(base)) for p in base.glob(pattern) if p.is_file())
            if len(matches) > 500:
                # Signal the cap rather than silently showing 500 of N (no-silent-truncation).
                shown = matches[:500]
                shown.append(
                    f"…[showing 500 of {len(matches)} matches — narrow the pattern to see the rest]"
                )
                return "\n".join(shown)
            return "\n".join(matches) or "(no matches)"

        return _ok_capped(
            await asyncio.get_event_loop().run_in_executor(None, _glob),
            session_key=self._session_key,
        )

    async def _t_grep(self, a: dict) -> ToolResult:
        import re as _re

        base = self._cwd.resolve()
        query = str(a["query"])
        glob_pat = str(a.get("glob") or "**/*")
        max_results = int(a.get("max_results") or 200)
        use_regex = bool(a.get("regex"))
        # Compile once when in regex mode; a bad pattern is a usable error, not a crash.
        matcher = None
        if use_regex:
            try:
                matcher = _re.compile(query)
            except _re.error as e:
                return ToolResult(
                    success=False,
                    error=f"invalid regex: {e}",
                    recovery_hints=[
                        "Fix the pattern, or drop regex=true to search for the literal text."
                    ],
                )

        def _grep() -> str:
            hits: list[str] = []
            for p in base.glob(glob_pat):
                if not p.is_file():
                    continue
                # Skip VCS/vendored/build dirs — searching them is slow + noisy.
                if any(part in self._SKIP_DIRS for part in p.relative_to(base).parts):
                    continue
                try:
                    for i, line in enumerate(
                        p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                    ):
                        if matcher.search(line) if matcher else query in line:
                            hits.append(f"{p.relative_to(base)}:{i}: {line.strip()[:200]}")
                            if len(hits) >= max_results:
                                # Signal the cap: the worker must know more matches may
                                # exist (no-silent-truncation) so it can narrow `glob`
                                # or raise `max_results` rather than assume these are all.
                                hits.append(
                                    f"…[stopped at max_results={max_results} — more matches may exist; narrow `glob` or raise `max_results`]"  # noqa: E501
                                )
                                return "\n".join(hits)
                except (OSError, UnicodeDecodeError):
                    continue
            return "\n".join(hits) or "(no matches)"

        return _ok_capped(
            await asyncio.get_event_loop().run_in_executor(None, _grep),
            session_key=self._session_key,
        )

    async def _t_repo_map(self, a: dict) -> ToolResult:
        """A structural overview of the workspace: the source tree + each file's
        top-level definitions, so the agent orients without reading everything.
        Dependency-free: Python via the stdlib ``ast``, other languages via a
        couple of cheap top-level regexes (def/class/func/export/type)."""
        base = (self._resolve(str(a["path"])) if a.get("path") else self._cwd).resolve()
        max_files = int(a.get("max_files") or 200)

        def _build() -> str:
            import ast
            import re

            # Source extensions worth mapping (skip data/asset files).
            exts = {
                ".py",
                ".js",
                ".jsx",
                ".ts",
                ".tsx",
                ".go",
                ".rs",
                ".rb",
                ".java",
                ".kt",
                ".c",
                ".cc",
                ".cpp",
                ".h",
                ".hpp",
                ".cs",
                ".php",
                ".swift",
                ".scala",
                ".sh",
            }
            skip_dirs = self._SKIP_DIRS
            # cheap top-level symbol regexes for non-Python files
            sym_re = re.compile(
                r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
                r"(?:(?:function|class|interface|type|enum|struct|trait|impl|func|fn|def)\s+([A-Za-z_][\w]*)"  # noqa: E501
                r"|(?:const|let|var)\s+([A-Za-z_][\w]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][\w]*)\s*=>)"  # noqa: E501
            )
            # Path.walk (py3.12+) with in-place dir pruning so the traversal never
            # DESCENDS into node_modules/.git/etc — rglob('*') would walk every ignored
            # file (slow on a real brownfield repo with a big node_modules) only to skip
            # it after. Sort dirs + files for deterministic, stable output.
            files: list[Path] = []
            truncated = False
            for root_path, dirnames, filenames in base.walk():
                dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
                for fn in sorted(filenames):
                    if Path(fn).suffix in exts:
                        files.append(root_path / fn)
                        if len(files) >= max_files:
                            truncated = True
                            break
                if truncated:
                    break
            if not files:
                return "(no source files found under this path)"
            # Be honest when the cap truncated the map — otherwise the worker reads
            # the capped count as the WHOLE repo and may miss files (no-silent-caps).
            header = (
                f"# Repo map — {base.name}/  ({len(files)} source files"
                + (
                    f", capped at max_files={max_files} — map is PARTIAL; pass a "
                    f"subdir `path` or a higher `max_files` to see the rest"
                    if truncated
                    else ""
                )
                + ")"
            )
            lines: list[str] = [header, ""]
            for p in files:
                rel = p.relative_to(base)
                try:
                    src = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                syms: list[str] = []
                if p.suffix == ".py":
                    try:
                        tree = ast.parse(src)
                        for node in tree.body:
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                syms.append(f"def {node.name}()")
                            elif isinstance(node, ast.ClassDef):
                                methods = [
                                    n.name
                                    for n in node.body
                                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                ][:8]
                                syms.append(
                                    f"class {node.name}"
                                    + (f" ({', '.join(methods)})" if methods else "")
                                )
                    except SyntaxError:
                        pass
                else:
                    for line in src.splitlines():
                        m = sym_re.match(line)
                        if m:
                            name = m.group(1) or m.group(2)
                            if name:
                                syms.append(name)
                        if len(syms) >= 30:
                            break
                lines.append(f"{rel}" + (f"\n    {' · '.join(syms[:30])}" if syms else ""))
            return "\n".join(lines)

        return _ok_capped(
            await asyncio.get_event_loop().run_in_executor(None, _build),
            session_key=self._session_key,
        )

    async def _t_bash(self, a: dict, *, timeout: float | None = None) -> ToolResult:
        from personalclaw import security
        from personalclaw.sandbox import wrap_argv

        # Read the module global at CALL time (not as a default arg, which binds at
        # def-time) so a test monkeypatching _BASH_TIMEOUT still takes effect.
        if timeout is None:
            # Agent-settable timeout (bash is the primary env interface — a slow test
            # suite/build needs more than the default). Capped so it can't wedge a
            # background turn indefinitely. Invalid/:absent → the default.
            try:
                requested = float(a.get("timeout") or _BASH_TIMEOUT)
            except (TypeError, ValueError):
                requested = _BASH_TIMEOUT
            timeout = max(1.0, min(requested, _BASH_TIMEOUT_MAX))
        command = str(a["command"])
        # App-level guards before any execution:
        # 1. sensitive credential-path access (is_sensitive_bash_command);
        # 2. the configured execute_bash denied-command regexes (credential
        #    exfiltration — aws s3 cp, echo $AWS_SECRET, IMDS 169.254.169.254, …).
        sens = security.is_sensitive_bash_command(command)
        if sens:
            return ToolResult(
                success=False,
                error=sens,
                recovery_hints=[
                    "This command touches a sensitive credential path. Use a non-credential path or a different approach."  # noqa: E501
                ],
            )
        deny = _denied_bash_reason(command)
        if deny:
            return ToolResult(
                success=False,
                error=f"Blocked: command matches denied pattern {deny!r}",
                recovery_hints=[
                    "This command matches a credential-exfiltration denylist. Use a read-only alternative or a different approach."  # noqa: E501
                ],
            )

        argv = ["bash", "-lc", command]
        wrapped, cleanup = wrap_argv(argv, mode=self._sandbox_mode)
        try:
            proc = await asyncio.create_subprocess_exec(
                *wrapped,
                cwd=str(self._cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                # Reap the killed child so it doesn't linger as a zombie (and asyncio
                # doesn't warn about a still-pending transport) — matches the watchdog's
                # _run_check / _commit_stage timeout handling.
                try:
                    await proc.wait()
                except Exception:
                    pass
                return ToolResult(
                    success=False,
                    error=f"command timed out after {timeout:.0f}s",
                    recovery_hints=[
                        "The command exceeded the time limit. Narrow its scope or run it in the background."  # noqa: E501
                    ],
                )
        finally:
            if cleanup:
                try:
                    Path(cleanup).unlink(missing_ok=True)
                except OSError:
                    pass
        raw = (out or b"").decode("utf-8", "replace")
        rc = proc.returncode
        if rc == 0:
            # Shell output is log-shaped → project (keep error/warn lines + tail)
            # and retain the raw for tool_result_get.
            return _ok_capped(raw, content_type="log", session_key=self._session_key)
        # Failure: project the error body the same way (the failing lines are the
        # signal), keep the raw retrievable, and carry recovery hints.
        proj = project_output(raw, cap=_MAX_OUTPUT_CHARS, content_type="log")
        meta: dict[str, Any] = {"content_type": proj.content_type}
        if proj.truncated and self._session_key:
            rid = result_store.store_result(self._session_key, raw, content_type="log")
            if rid:
                meta["raw_ref"] = rid
        return ToolResult(
            success=False,
            error=f"exit {rc}:\n{proj.text}",
            recovery_hints=[
                f"The command exited non-zero ({rc}). Read the error output above, "
                "fix the cause, and retry — or try a different approach."
            ],
            truncated=proj.truncated,
            original_length=proj.original_length,
            metadata=meta,
        )

    async def _t_post_to_inbox(self, a: dict) -> ToolResult:
        message = str(a.get("message", "")).strip()
        if not message:
            return ToolResult(success=False, error="post_to_inbox requires a non-empty 'message'")
        kind = str(a.get("kind", "notification")).strip().lower()
        if kind not in ("notification", "question", "fyi"):
            kind = "notification"
        from personalclaw.inbox_providers.native_source import post_to_inbox

        # Push runs synchronously (store write + WS broadcast); off the loop to be safe.
        item = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: post_to_inbox(
                message,
                kind=kind,
                sender_name=self._agent or "agent",
                context=str(a.get("context", "")) or None,
                reply_target=self._session_key,
            ),
        )
        if item is None:
            return ToolResult(success=False, error="inbox sink unavailable")
        return ToolResult(success=True, output=f"posted to inbox ({kind}) — item {item.id}")

    async def _t_knowledge_search(self, a: dict) -> ToolResult:
        query = str(a.get("query", "")).strip()
        if not query:
            return ToolResult(success=False, error="knowledge_search requires 'query'")
        try:
            limit = int(a.get("limit", 8) or 8)
        except (ValueError, TypeError):
            limit = 8

        def _search() -> str:
            from personalclaw.knowledge import get_knowledge_embedder, get_knowledge_store
            from personalclaw.knowledge.retrieval import HybridRetriever

            store = get_knowledge_store()
            # Use the same embedder the gateway uses → full hybrid (keyword+graph+vector)
            # retrieval, not a keyword-only degrade. None (embeddings off) falls back cleanly.
            emb = get_knowledge_embedder()
            embed_fn = emb.embed if emb and emb.is_available() else None
            results = HybridRetriever(store, embedder=embed_fn).search(query, limit=limit)
            if not results:
                return "(no matching knowledge items)"
            lines = []
            for r in results[:limit]:
                item = store.get_item(r["id"])
                if not item:
                    continue
                snippet = _kn_redact(_kn_snippet(item))
                tail = f" — {snippet}" if snippet else ""
                # P12: surface the per-item citation locator so the agent can cite WHERE in
                # the source the match sits (section / line range), not just name the item.
                loc_bits = []
                if r.get("section"):
                    loc_bits.append(str(r["section"]))
                if r.get("line_range"):
                    lr = r["line_range"]
                    loc_bits.append(f"lines {lr[0]}-{lr[1]}")
                loc = f" [{' · '.join(loc_bits)}]" if loc_bits else ""
                lines.append(
                    f"- [{item.get('type', 'note')}] {_kn_redact(_kn_title(item))} (id={r['id']}){loc}{tail}"  # noqa: E501
                )
            return "\n".join(lines) or "(no matching knowledge items)"

        # Output is markdown-ish bullet lines ("- [type] title (id=…)"), NOT JSON —
        # leave the type to inference (the native search-results override parses the
        # rare JSON case; otherwise it renders as clean text, never a failed JSON tree).
        return _ok_capped(
            await asyncio.get_event_loop().run_in_executor(None, _search),
            session_key=self._session_key,
        )

    async def _t_knowledge_create(self, a: dict) -> ToolResult:
        item_type = str(a.get("type", "note")).strip() or "note"
        if item_type not in ("note", "fleeting", "journal", "gist", "bookmark"):
            return ToolResult(
                success=False,
                error=f"knowledge_create: unsupported type {item_type!r} (agents author text/bookmark types)",  # noqa: E501
                recovery_hints=["Use one of: note, fleeting, journal, gist, bookmark."],
            )
        title = str(a.get("title", "")).strip()
        content = str(a.get("content", ""))
        url = str(a.get("url", "")).strip()
        if item_type == "bookmark":
            if not url:
                return ToolResult(success=False, error="knowledge_create: bookmark requires a url")
            from urllib.parse import urlsplit

            try:
                scheme = urlsplit(url).scheme.lower()
            except ValueError:
                scheme = ""
            if scheme not in ("http", "https"):
                return ToolResult(
                    success=False, error="knowledge_create: bookmark url must be http(s)"
                )
        if not title and not content.strip() and not url:
            return ToolResult(
                success=False, error="knowledge_create: title, content, or url required"
            )
        if not title and item_type == "journal":
            # Journals are date-driven records — enrichment never AI-titles them, so a
            # blank title becomes the entry's date (mirrors the HTTP create handler);
            # otherwise an agent's untitled journal would keep a content-slug title forever.
            from datetime import datetime

            title = datetime.now().strftime("%B %-d, %Y")
        tags = _t if isinstance((_t := a.get("tags")), list) else []
        extra: dict[str, Any] = {"processing_status": "queued"}
        if item_type == "gist":
            lang = str(a.get("gist_language", "")).strip()
            if lang:
                extra["gist_language"] = lang

        from personalclaw.knowledge import get_knowledge_store

        store = get_knowledge_store()
        # Bookmark dedup: re-saving a URL already in the library (modulo trailing slash /
        # tracking params) returns the existing item rather than a duplicate — the same
        # guard the HTTP create path applies.
        if item_type == "bookmark" and url:
            existing = store.find_active_by_url(url)
            if existing:
                return ToolResult(
                    success=True, output=f"knowledge bookmark already saved — id {existing['id']}"
                )
        item_id = store.create_typed_item(
            item_type=item_type,
            title=title or (url or content[:60]) or "Untitled",
            content=content,
            tags=[str(t) for t in tags],
            url=url,
            provider="native",
            extra=extra,
        )
        # Enrich in the background (node-graph: extraction → insights → entities →
        # embed) so the tool returns the id immediately — create-fast/enrich-async,
        # not a 30-60s block on the agent's turn.
        _enrich_in_background(item_id)
        return ToolResult(
            success=True,
            output=f"created knowledge {item_type} (enriching in background) — id {item_id}",
        )

    async def _t_knowledge_get(self, a: dict) -> ToolResult:
        item_id = str(a.get("id", "")).strip()
        if not item_id:
            return ToolResult(success=False, error="knowledge_get requires 'id'")

        def _get() -> str:
            from personalclaw.knowledge import get_knowledge_store

            item = get_knowledge_store().get_item(item_id)
            if not item:
                return ""
            tags = ", ".join(item.get("tags", []) or [])
            insights = item.get("insights") or {}
            summary = (
                item.get("summary")
                or item.get("ai_summary")
                or insights.get("summary")
                or item.get("url_description")
                or ""
            ).strip()
            itype = item.get("type", "note")
            # A gist's language is a meaningful attribute — surface it in the type tag
            # (e.g. "[gist · python]") so the agent knows what language the code is.
            type_tag = itype
            if itype == "gist" and (item.get("gist_language") or "").strip():
                type_tag = f"{itype} · {item['gist_language'].strip()}"
            lines = [f"# {_kn_title(item)} [{type_tag}]"]
            # Signal when enrichment is still pending so the agent doesn't mistake
            # not-yet-ready insights/tags for an item that has none (create-fast).
            pstatus = item.get("processing_status") or ""
            if pstatus in ("queued", "processing"):
                lines.append("(still enriching — summary, tags, and insights may not be ready yet)")
            # Archived items are hidden from search/lists — an agent reaching one by id
            # should know it was deliberately put away, so it can caveat rather than
            # present stale/retired content as current.
            if item.get("is_archived"):
                lines.append(
                    "(archived — the user has put this item away; treat as retired unless they ask about it)"  # noqa: E501
                )
            # An unreachable bookmark's page couldn't be fetched, so the body is just the
            # URL — tell the agent why the content is missing (vs. a not-yet-scraped item),
            # and that a re-fetch may succeed later. A hard 'failed' is flagged likewise.
            if pstatus == "unreachable":
                lines.append(
                    "(unreachable — the page couldn't be fetched, so only the URL is saved; the content may be retrievable on a later retry)"  # noqa: E501
                )
            elif pstatus == "failed":
                reason = (item.get("processing_error") or "").strip()
                lines.append(
                    f"(processing failed{': ' + reason if reason else ''} — content may be incomplete)"  # noqa: E501
                )
            if tags:
                lines.append(f"tags: {tags}")
            if item.get("url"):
                lines.append(f"url: {item.get('url')}")
            # File-backed items: give the agent the file's shape (it can't open the
            # bytes inline) — dimensions/pages/size — so it knows what it's dealing with.
            if item.get("file_path"):
                meta = item.get("file_metadata") or {}
                bits = []
                if meta.get("width") and meta.get("height"):
                    bits.append(f"{meta['width']}x{meta['height']}")
                if meta.get("page_count"):
                    bits.append(f"{meta['page_count']} pages")
                if meta.get("sheet_count"):
                    bits.append(f"{meta['sheet_count']} sheets")
                if meta.get("slide_count"):
                    bits.append(f"{meta['slide_count']} slides")
                if meta.get("row_count"):
                    bits.append(f"{meta['row_count']} rows")
                if item.get("file_size"):
                    kb = item["file_size"] / 1024
                    bits.append(f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB")
                if item.get("mime_type"):
                    bits.append(item["mime_type"])
                if bits:
                    lines.append("file: " + ", ".join(bits))
            if summary:
                lines.append(f"\nsummary: {summary}")
            # Surface the distilled enrichment so the agent can use it without
            # re-reading the whole document.
            kps = insights.get("key_points")
            if isinstance(kps, list) and kps:
                lines.append("key points:\n" + "\n".join(f"- {p}" for p in kps))
            acts = insights.get("action_items")
            if isinstance(acts, list) and acts:
                lines.append("action items:\n" + "\n".join(f"- {a}" for a in acts))
            if item.get("content"):
                lines.append(f"\n{item.get('content')}")
            # Scrub credentials + exfiltration URLs before the item reaches the model,
            # matching the HTTP context-injection guard.
            return _kn_redact("\n".join(lines))

        out = await asyncio.get_event_loop().run_in_executor(None, _get)
        if not out:
            return ToolResult(
                success=False,
                error=f"knowledge item {item_id!r} not found",
                recovery_hints=[
                    "Use knowledge_search to find the correct item id, then retry knowledge_get."
                ],
            )
        return _ok_capped(out, session_key=self._session_key)

    async def _t_knowledge_update(self, a: dict) -> ToolResult:
        item_id = str(a.get("id", "")).strip()
        if not item_id:
            return ToolResult(success=False, error="knowledge_update requires 'id'")
        # Only the agent-editable fields; ignore anything else the model passes.
        fields: dict[str, Any] = {}
        for key in ("title", "content", "url"):
            if key in a:
                fields[key] = str(a[key])
        # A url edit must stay http(s) (same guard as create) — no javascript:/data:.
        if str(fields.get("url", "")).strip():
            from urllib.parse import urlsplit

            try:
                scheme = urlsplit(fields["url"].strip()).scheme.lower()
            except ValueError:
                scheme = ""
            if scheme not in ("http", "https"):
                return ToolResult(success=False, error="knowledge_update: url must be http(s)")
        if "tags" in a and isinstance(a["tags"], list):
            fields["tags"] = [str(t) for t in a["tags"]]
        for key in ("is_pinned", "is_archived"):
            if key in a:
                fields[key] = 1 if a[key] else 0
        # gist_language only applies to gist items — applied conditionally in _update
        # once the item type is known (mirrors the create-tool guard).
        want_lang = str(a["gist_language"]).strip() if "gist_language" in a else None
        if not fields and want_lang is None:
            return ToolResult(
                success=False,
                error="knowledge_update: no updatable fields given",
                recovery_hints=[
                    "Pass at least one of: title, content, tags, url, gist_language, is_pinned, is_archived."  # noqa: E501
                ],
            )
        # Editing the text re-runs extraction/insights/entities so enrichment stays
        # consistent with the new content (the create/update → enrich contract).
        reenrich = "content" in fields or "url" in fields

        applied = dict(fields)

        def _update() -> str:
            from datetime import datetime

            from personalclaw.knowledge import get_knowledge_store

            store = get_knowledge_store()
            item = store.get_item(item_id)
            if not item:
                return "not_found"
            # Journal immutability (same rule the HTTP PATCH enforces): a journal's body is
            # append-only — editable on its creation day, frozen after. Block a content/
            # title edit to a past-day journal so an agent can't mutate a record the UI +
            # HTTP path forbid. Curation (tags/pin/archive) stays allowed.
            if (item.get("item_type") or item.get("type")) == "journal" and (
                "content" in applied or "title" in applied
            ):
                created = str(item.get("created_at") or "")[:10]
                if created and created != datetime.now().isoformat()[:10]:
                    return "journal_locked"
            # A language only makes sense on a gist; silently ignore it on other types
            # rather than stamping a meaningless column (the FE only reads it for gists).
            if want_lang is not None and (item.get("type") or item.get("item_type")) == "gist":
                applied["gist_language"] = want_lang
            if not applied:
                return "noop"  # nothing to change (e.g. gist_language given for a non-gist)
            store.update_item(item_id, **applied)
            store.db.commit()
            return "ok"

        result = await asyncio.get_event_loop().run_in_executor(None, _update)
        if result == "not_found":
            return ToolResult(
                success=False,
                error=f"knowledge item {item_id!r} not found",
                recovery_hints=["Use knowledge_search to find the correct item id."],
            )
        if result == "journal_locked":
            return ToolResult(
                success=False,
                error="knowledge_update: this journal entry is immutable — its creation day has passed",  # noqa: E501
                recovery_hints=[
                    "A journal's body can't be edited after its creation day. You can still update tags, is_pinned, or is_archived."  # noqa: E501
                ],
            )
        if reenrich:
            _enrich_in_background(item_id)
        if not applied:
            return ToolResult(
                success=True,
                output=f"no change to {item_id} — gist_language only applies to gist items",
            )
        return ToolResult(
            success=True, output=f"updated knowledge item {item_id} ({', '.join(applied)})"
        )

    async def _t_knowledge_stats(self, a: dict) -> ToolResult:
        def _overview() -> dict:
            from personalclaw.knowledge import get_knowledge_store

            return get_knowledge_store().corpus_overview()

        ov = await asyncio.get_event_loop().run_in_executor(None, _overview)
        if not ov.get("total"):
            return ToolResult(success=True, output="Knowledge library is empty.")
        by_type = ", ".join(f"{t}: {c}" for t, c in ov["by_type"].items())
        top_tags = ", ".join(f"{r['tag']} ({r['count']})" for r in ov["top_tags"]) or "(none)"
        return ToolResult(
            success=True,
            output=(
                f"Knowledge library: {ov['total']} items, {ov['entities']} entities.\n"
                f"By type: {by_type}\n"
                f"Top tags: {top_tags}"
            ),
        )

    # ── Tasks (Project → TaskList → Task) ──

    @staticmethod
    def _task_line(t) -> str:
        bits = [f"[{t.status.value}]", t.title, f"(id={t.id})"]
        if t.priority.value != "medium":
            bits.append(f"!{t.priority.value}")
        if t.project:
            bits.append(f"@{t.project}")
        ec = t.exit_criteria or []
        if ec:
            met = sum(1 for e in ec if (e.get("status") == "complete" or e.get("met")))
            bits.append(f"{met}/{len(ec)} criteria")
        return " ".join(bits)

    async def _t_task_create(self, a: dict) -> ToolResult:
        from personalclaw.tasks import reconcile, registry

        title = str(a.get("title", "")).strip()
        if not title:
            return ToolResult(success=False, error="task_create requires 'title'")
        fields = {
            k: a[k]
            for k in (
                "description",
                "priority",
                "task_list_id",
                "labels",
                "due",
                "exit_criteria",
                "action_plan",
                "depends_on",
            )
            if k in a
        }
        try:
            task = await registry.create_task(title=title, **fields)
        except reconcile.DependencyCycleError as e:
            return ToolResult(
                success=False,
                error=str(e),
                recovery_hints=["Remove the dependency that closes the loop."],
            )
        return ToolResult(success=True, output=f"created task {task.id}: {self._task_line(task)}")

    async def _t_task_list(self, a: dict) -> ToolResult:
        from personalclaw.tasks import registry

        try:
            limit = int(a.get("limit", 25) or 25)
        except (ValueError, TypeError):
            limit = 25
        tasks, total = await registry.list_all_tasks(
            status=a.get("status") or None,
            project=a.get("project") or None,
            task_list_id=a.get("task_list_id") or None,
            limit=limit,
        )
        if not tasks:
            return ToolResult(success=True, output="(no matching tasks)")
        body = "\n".join(f"- {self._task_line(t)}" for t in tasks)
        head = f"{total} task(s)" + (f", showing {len(tasks)}" if total > len(tasks) else "")
        return _ok_capped(f"{head}:\n{body}", session_key=self._session_key)

    async def _t_task_get(self, a: dict) -> ToolResult:
        from personalclaw.tasks import registry

        item_id = str(a.get("id", "")).strip()
        if not item_id:
            return ToolResult(success=False, error="task_get requires 'id'")
        task = await registry.get_task(item_id)
        if not task:
            return ToolResult(success=False, error=f"no task with id {item_id!r}")
        d = task.to_dict()
        lines = [self._task_line(task)]
        if task.description:
            lines.append(f"\n{task.description}")
        ec = d.get("exit_criteria") or []
        if ec:
            lines.append("\nExit criteria:")
            lines += [f"  [{'x' if e.get('met') else ' '}] {e.get('description', '')}" for e in ec]
        ap = d.get("action_plan") or []
        if ap:
            lines.append("\nAction plan:")
            lines += [f"  {e.get('sequence', i)}. {e.get('content', '')}" for i, e in enumerate(ap)]
        prereqs = task.prerequisite_ids()
        if prereqs:
            lines.append("\nDepends on: " + ", ".join(prereqs))
        return _ok_capped("\n".join(lines), session_key=self._session_key)

    async def _t_task_update(self, a: dict) -> ToolResult:
        from personalclaw.tasks import reconcile, registry

        item_id = str(a.get("id", "")).strip()
        if not item_id:
            return ToolResult(success=False, error="task_update requires 'id'")
        fields = {
            k: a[k]
            for k in (
                "title",
                "description",
                "status",
                "priority",
                "task_list_id",
                "labels",
                "due",
                "exit_criteria",
                "action_plan",
                "depends_on",
            )
            if k in a
        }
        if not fields:
            return ToolResult(success=False, error="task_update: nothing to change")
        # Normalize the status BEFORE update_task — an LLM commonly emits a synonym
        # (complete/completed/finished/todo/…) which TaskStatus() rejects with a bare
        # ValueError that the catch below then mis-labels as an exit-criteria failure,
        # confusing the worker into a loop that never marks the task done (the cockpit
        # rail then lies). Coerce the obvious synonyms; reject anything else with the
        # valid set named explicitly.
        if "status" in fields:
            _VALID = {"open", "in_progress", "done", "cancelled", "blocked"}
            _SYNONYM = {
                "complete": "done",
                "completed": "done",
                "finished": "done",
                "todo": "open",
                "to_do": "open",
                "pending": "open",
                "in-progress": "in_progress",
                "inprogress": "in_progress",
                "doing": "in_progress",
                "wip": "in_progress",
                "canceled": "cancelled",
                "won't_do": "cancelled",
            }
            raw = str(fields["status"]).strip().lower().replace(" ", "_")
            norm = raw if raw in _VALID else _SYNONYM.get(raw)
            if norm is None:
                return ToolResult(
                    success=False,
                    error=f"task_update: {fields['status']!r} is not a valid status",
                    recovery_hints=["Use one of: open, in_progress, done, cancelled, blocked."],
                )
            fields["status"] = norm
        try:
            task = await registry.update_task(item_id, **fields)
        except reconcile.DependencyCycleError as e:
            return ToolResult(
                success=False,
                error=str(e),
                recovery_hints=["Remove the dependency that closes the loop."],
            )
        except ValueError as e:
            return ToolResult(
                success=False,
                error=str(e),
                recovery_hints=["Complete the exit criteria before marking the task done."],
            )
        if not task:
            return ToolResult(success=False, error=f"no task with id {item_id!r}")
        return ToolResult(success=True, output=f"updated {task.id}: {self._task_line(task)}")

    async def _t_task_ready(self, a: dict) -> ToolResult:
        from personalclaw.tasks import registry

        tasks = await registry.ready_tasks(
            project=a.get("project") or None, task_list_id=a.get("task_list_id") or None
        )
        if not tasks:
            return ToolResult(success=True, output="(no ready tasks — all blocked or done)")
        return _ok_capped(
            "Ready to start:\n" + "\n".join(f"- {self._task_line(t)}" for t in tasks),
            session_key=self._session_key,
        )

    async def _t_task_search(self, a: dict) -> ToolResult:
        from personalclaw.tasks import registry

        try:
            limit = int(a.get("limit", 25) or 25)
        except (ValueError, TypeError):
            limit = 25
        tasks, total = await registry.search_tasks(
            query=str(a.get("query", "")),
            statuses=a.get("status") if isinstance(a.get("status"), list) else None,
            priorities=a.get("priority") if isinstance(a.get("priority"), list) else None,
            tags=a.get("tags") if isinstance(a.get("tags"), list) else None,
            project=a.get("project") or None,
            sort_by=str(a.get("sort_by", "relevance")),
            limit=limit,
        )
        if not tasks:
            return ToolResult(success=True, output="(no matching tasks)")
        body = "\n".join(f"- {self._task_line(t)}" for t in tasks)
        return _ok_capped(f"{total} match(es):\n{body}", session_key=self._session_key)

    async def _t_project_create(self, a: dict) -> ToolResult:
        from personalclaw.tasks.hierarchy import HierarchyStore

        name = str(a.get("name", "")).strip()
        if not name:
            return ToolResult(success=False, error="project_create requires 'name'")

        def _create():
            return HierarchyStore().create_project(
                name=name, agent_instructions_template=str(a.get("agent_instructions_template", ""))
            )

        try:
            project = await asyncio.get_event_loop().run_in_executor(None, _create)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(
            success=True, output=f"created project '{project.name}' (id={project.id})"
        )

    async def _t_project_list(self, a: dict) -> ToolResult:
        from personalclaw.tasks.hierarchy import HierarchyStore

        def _list() -> str:
            store = HierarchyStore()
            projects = store.list_projects()
            lines = []
            for p in projects:
                lists = store.list_task_lists(project_id=p.id)
                tail = f" — lists: {', '.join(tl.name for tl in lists)}" if lists else ""
                lines.append(
                    f"- {p.name} (id={p.id}){' [default]' if p.is_default_project() else ''}{tail}"
                )
            return "\n".join(lines)

        return _ok_capped(
            await asyncio.get_event_loop().run_in_executor(None, _list),
            session_key=self._session_key,
        )

    async def _t_task_list_create(self, a: dict) -> ToolResult:
        from personalclaw.tasks.hierarchy import HierarchyStore

        name = str(a.get("name", "")).strip()
        if not name:
            return ToolResult(success=False, error="task_list_create requires 'name'")

        def _create():
            return HierarchyStore().create_task_list(
                name=name,
                project_id=str(a.get("project_id", "")),
                project_name=str(a.get("project_name", "")),
                repeatable=bool(a.get("repeatable", False)),
            )

        try:
            tl = await asyncio.get_event_loop().run_in_executor(None, _create)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(
            success=True,
            output=f"created task list '{tl.name}' (id={tl.id}, project_id={tl.project_id})",
        )


# ── Category providers (UT1 split) ──────────────────────────────────────────
# One provider per conceptual entity. All share the handler bodies above; each
# surfaces only its category's tools (list_tools filters by self._categories).
# These are registry singletons — they resolve the per-turn workspace via the
# contextvars the runtime binds, NOT a per-session constructor. The platform
# bundle (filesystem/shell/core) is always-on; the rest are installable apps.


def create_platform_tools_provider(config: dict | None = None) -> "NativeBuiltinToolProvider":
    """The always-on platform tool surface: filesystem + shell + the tool_result_get
    affordance. The native agent's foundation — never user-removable."""
    return NativeBuiltinToolProvider(
        categories=PLATFORM_CATEGORIES,
        provider_name="personalclaw-filesystem",
        display="Filesystem & Shell Tools",
    )


def _make_app_category_provider(category: str):
    name, display = APP_CATEGORY_PROVIDERS[category]

    def _factory(config: dict | None = None) -> "NativeBuiltinToolProvider":
        return NativeBuiltinToolProvider(
            categories={category},
            provider_name=name,
            display=display,
        )

    return _factory


create_knowledge_tools_provider = _make_app_category_provider("knowledge")
create_tasks_tools_provider = _make_app_category_provider("tasks")
create_project_tools_provider = _make_app_category_provider("projects")
create_inbox_tools_provider = _make_app_category_provider("inbox")
