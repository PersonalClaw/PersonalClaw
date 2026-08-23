"""The inbound tool table + the fencing wrapper (MCP-READONLY-INBOUND §C1/§C3).

Session 1 ships the TABLE MACHINERY with an empty table; Session 2 adds the five
curated read-only tools. That split is deliberate: the transport, auth, caps and
audit are verifiable on their own (an MCP client can connect and see zero tools),
and the tools then land against a substrate already proven.

The load-bearing piece here is :func:`wrap_result`. Everything an inbound tool
returns came from the user's own stores, but it flows to a MODEL — and content in
those stores can carry prompt injection (a scraped page saved to knowledge, an
inbox message). So every textual result passes through one wrapper that fences it
as data. A new tool cannot skip that, because returning anything else isn't
representable: handlers return text, and the dispatcher does the wrapping.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from personalclaw.inbound import caps as caps_mod

logger = logging.getLogger(__name__)

ToolHandler = Callable[[dict, Any], Awaitable[str]]


@dataclass(frozen=True)
class ToolSpec:
    """One inbound tool: its schema for `tools/list` and its read-only handler."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    handler: ToolHandler | None = None


def wrap_result(text: str, tool: str, client_id: str = "") -> dict:
    """The ONLY way a tool result reaches a caller.

    Delegates to `framing.mcp_tool_result` — the ONE wrapper shared by all five
    surfaces (§1.4). It used to cap and fence inline here, which made this the MCP
    dialect's private fencing path; a second dialect would have grown a second one,
    and the rule "a new dialect cannot forget to fence" would have been untrue on the
    day it mattered.
    """
    from personalclaw.inbound.framing import mcp_tool_result

    return mcp_tool_result(text, tool=tool, client_id=client_id)


# ── Argument validation ────────────────────────────────────────────────────────
#
# Every handler validates through these, so a bad argument is a JSON-RPC
# invalid-params error with a readable reason rather than a traceback or a silently
# wrong answer.


def _require_text(arguments: dict, field: str, *, max_len: int = 500) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field!r} is required and must be a non-empty string")
    return value.strip()[:max_len]


def _clamp_limit(arguments: dict, *, default: int, ceiling: int) -> int:
    """A limit within bounds. Out-of-range CLAMPS rather than erroring.

    An over-large limit is a caller being optimistic, not a caller being wrong — and
    the cap is ours to enforce either way (§C3: "out-of-range limit clamped").
    """
    raw = arguments.get("limit", default)
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("'limit' must be a number")
    return max(1, min(int(raw), ceiling))


def _enum_value(value: Any) -> str:
    """An enum's wire value, never its Python repr.

    MRI-5's client drive got `[TaskStatus.OPEN]` back from `tasks_list` — the repr of a
    Python enum, handed to a model as if it were the status vocabulary. Anything crossing
    this boundary is read by a machine that has never seen our class names.
    """
    return str(getattr(value, "value", value))


def _reject_unknown(arguments: dict, allowed: "tuple[str, ...]") -> None:
    """Refuse arguments we don't recognize (§C3: unknown args → invalid-params).

    Naming them beats ignoring them: a typo'd `quesry` that silently returns
    everything looks like a bug in the answer rather than a bug in the call.
    """
    unknown = sorted(set(arguments or {}) - set(allowed))
    if unknown:
        raise ValueError(
            f"unknown argument(s): {', '.join(unknown)}. Accepted: {', '.join(allowed)}"
        )


# ── The handlers ───────────────────────────────────────────────────────────────


async def _memory_recall(arguments: dict, state: Any) -> str:
    """Recall memories.

    **T2.1's gate location, verified — and it does NOT apply here.** The
    temporary/incognito restriction is not inside `recall_with_provenance`; it lives at
    the dashboard endpoint as `_blocks_reads_session`, which reads an `X-Session-Key`
    header and asks whether *that session* may read memory. It is strictly per-session:
    a temporary session is denied its own context so the thread starts blank. It is not
    an instance-wide memory lock.

    An inbound MCP call is a separate caller with no session, so there is no session
    whose restriction could apply. Blocking inbound recall whenever some unrelated
    temporary chat happens to be open would be a misreading of the mechanism — it would
    make an IDE's memory lookups fail for reasons the user cannot see and did not ask
    for. What DOES gate this surface is its own switch: the whole endpoint is
    unmounted unless the owner enabled it and minted a token.

    Recorded rather than silently skipped, because "the restriction gate is honored"
    and "the restriction gate does not apply" look identical in a diff.
    """
    _reject_unknown(arguments, ("query", "limit"))
    query = _require_text(arguments, "query")
    limit = _clamp_limit(arguments, default=8, ceiling=20)

    def _run() -> list:
        from personalclaw.memory_service import MemoryService
        from personalclaw.vector_memory import VectorMemoryStore

        store = VectorMemoryStore()
        store.init()
        return MemoryService.over_vector_store(store).recall_with_provenance(
            query_text=query, limit=limit
        )

    hits = await asyncio.get_event_loop().run_in_executor(None, _run)
    if not hits:
        return f"No memories matched {query!r}."
    lines = [f"{len(hits)} memory hit(s) for {query!r}:"]
    for hit in hits:
        source = str(hit.get("source") or "unknown")
        when = str(hit.get("created_at") or hit.get("ts") or "")[:19]
        text = str(hit.get("text") or hit.get("value") or "").strip()
        lines.append(f"- [{source}{f' · {when}' if when else ''}] {text}")
    return "\n".join(lines)


async def _knowledge_search(arguments: dict, state: Any) -> str:
    _reject_unknown(arguments, ("query", "limit"))
    query = _require_text(arguments, "query")
    limit = _clamp_limit(arguments, default=10, ceiling=20)

    def _run() -> list:
        from personalclaw.knowledge import get_knowledge_store
        from personalclaw.knowledge.retrieval import HybridRetriever

        return HybridRetriever(get_knowledge_store()).search(query, limit=limit)

    items = await asyncio.get_event_loop().run_in_executor(None, _run)
    items = caps_mod.clamp_items(items)
    if not items:
        return f"No knowledge items matched {query!r}."
    lines = [f"{len(items)} knowledge item(s) for {query!r}:"]
    for item in items:
        title = str(item.get("title") or item.get("id") or "untitled")
        summary = str(item.get("summary") or item.get("content") or "").strip()
        lines.append(f"- {title}: {summary[:300]}")
    return "\n".join(lines)


async def _tasks_list(arguments: dict, state: Any) -> str:
    _reject_unknown(arguments, ("status", "project", "limit"))
    status = arguments.get("status")
    project = arguments.get("project")
    for name, value in (("status", status), ("project", project)):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name!r} must be a string")
    limit = _clamp_limit(arguments, default=25, ceiling=100)

    from personalclaw.tasks import registry

    tasks, total = await registry.list_all_tasks(
        status=status or None, project=project or None, limit=limit
    )
    if not tasks:
        return "No tasks matched."
    lines = [f"{len(tasks)} of {total} task(s):"]
    for task in tasks:
        assignee = f" · @{task.assignee}" if task.assignee else ""
        lines.append(f"- [{_enum_value(task.status)}] {task.id}: {task.title}{assignee}")
    return "\n".join(lines)


async def _task_get(arguments: dict, state: Any) -> str:
    _reject_unknown(arguments, ("id",))
    task_id = _require_text(arguments, "id", max_len=120)

    from personalclaw.tasks import registry

    task = await registry.get_task(task_id)
    if task is None:
        return f"No task with id {task_id!r}."
    lines = [
        f"{task.id}: {task.title}",
        f"status: {_enum_value(task.status)} · priority: {_enum_value(task.priority)}",
    ]
    if task.assignee:
        lines.append(f"assignee: {task.assignee}")
    if task.author:
        lines.append(f"author: {task.author}")
    if task.due:
        lines.append(f"due: {task.due}")
    if task.labels:
        lines.append(f"labels: {', '.join(task.labels)}")
    if task.description:
        lines.append(f"\n{task.description}")
    if task.exit_criteria:
        lines.append("\nexit criteria:")
        for criterion in task.exit_criteria[:20]:
            if isinstance(criterion, dict):
                lines.append(
                    f"- [{criterion.get('status', '?')}] {criterion.get('description', '')}"
                )
    return "\n".join(lines)


async def _sessions_search(arguments: dict, state: Any) -> str:
    _reject_unknown(arguments, ("query", "limit"))
    query = _require_text(arguments, "query")
    limit = _clamp_limit(arguments, default=5, ceiling=10)

    def _run() -> list:
        from personalclaw.history import ConversationLog

        log = ConversationLog()
        # The FTS index first (it also excludes restricted sessions), then the linear
        # scan — which applies the SAME exclusion. Either way, temporary/incognito
        # transcripts never reach an inbound caller.
        try:
            from personalclaw import session_search

            hits = session_search.search_sessions(query, limit=limit)
            if hits:
                return hits
        except Exception:  # noqa: BLE001
            logger.debug("inbound: session index unavailable", exc_info=True)
        return log.search_sessions(query, limit)

    hits = await asyncio.get_event_loop().run_in_executor(None, _run)
    hits = caps_mod.clamp_items(hits)
    if not hits:
        return f"No conversations matched {query!r}."
    from personalclaw.security import redact_credentials, redact_exfiltration_urls

    lines = [f"{len(hits)} conversation(s) matching {query!r}:"]
    for hit in hits:
        title = str(hit.get("title") or hit.get("key") or "untitled")
        snippet = str(hit.get("snippet") or "")
        # Redaction is MANDATORY here (§C3): a transcript can contain a pasted key,
        # and this text is leaving the machine to another process.
        title, _ = redact_exfiltration_urls(title)
        title, _ = redact_credentials(title)
        if snippet:
            snippet, _ = redact_exfiltration_urls(snippet)
            snippet, _ = redact_credentials(snippet)
        lines.append(f"- {hit.get('key', '?')}: {title}")
        if snippet:
            lines.append(f"    {snippet[:200]}")
    return "\n".join(lines)


async def _status(arguments: dict, state: Any) -> str:
    _reject_unknown(arguments, ())

    def _run() -> str:
        from personalclaw import __version__

        lines = [f"PersonalClaw {__version__}"]
        # Deliberately NO config values (§C3): this reports liveness and counts, not
        # settings — a status tool that leaks configuration is a reconnaissance tool.
        try:
            from personalclaw.tasks import registry  # noqa: F401

            lines.append("tasks: available")
        except Exception:  # noqa: BLE001
            lines.append("tasks: unavailable")
        try:
            from personalclaw.memory_service import MemoryService
            from personalclaw.vector_memory import VectorMemoryStore

            store = VectorMemoryStore()
            store.init()
            caps = MemoryService.over_vector_store(store).capabilities()
            lines.append(
                "memory: "
                + ("vector search" if caps.vector else "keyword search")
                + (" + entity graph" if getattr(caps, "entity_graph", False) else "")
            )
        except Exception:  # noqa: BLE001
            lines.append("memory: unavailable")
        try:
            from personalclaw.knowledge import get_knowledge_store

            lines.append(f"knowledge items: {get_knowledge_store().get_stats().get('items', 0)}")
        except Exception:  # noqa: BLE001
            lines.append("knowledge: unavailable")
        return "\n".join(lines)

    return await asyncio.get_event_loop().run_in_executor(None, _run)


# ── The curated table ──────────────────────────────────────────────────────────
#
# Read-only by CONSTRUCTION: every handler above calls a read path and returns text.
# There is no dispatcher to a generic tool surface, so no inbound request can reach a
# write, an install, or a config change — that property comes from this table being
# hand-written and short, and it is why it must stay that way.

TOOLS: dict[str, ToolSpec] = {
    "memory_recall": ToolSpec(
        name="memory_recall",
        description=(
            "Search what the assistant remembers about the user and their work — "
            "facts, preferences, and episodes it recorded itself. This is the "
            "assistant's own internal memory, NOT the user's documents; use "
            "knowledge_search for those. Returns each hit with where and when it "
            "came from."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall."},
                "limit": {
                    "type": "integer",
                    "description": "Max hits (1-20, default 8).",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
        handler=_memory_recall,
    ),
    "knowledge_search": ToolSpec(
        name="knowledge_search",
        description=(
            "Search the user's own saved items — documents, files, notes, pages they "
            "kept. This is their personal library, NOT the assistant's internal "
            "memory; use memory_recall for that."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for."},
                "limit": {
                    "type": "integer",
                    "description": "Max items (1-20, default 10).",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
        handler=_knowledge_search,
    ),
    "tasks_list": ToolSpec(
        name="tasks_list",
        description="List the user's tasks, optionally filtered by status or project.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "e.g. open, in_progress, done."},
                "project": {"type": "string", "description": "Project name to scope to."},
                "limit": {
                    "type": "integer",
                    "description": "Max tasks (1-100, default 25).",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
        },
        handler=_tasks_list,
    ),
    "task_get": ToolSpec(
        name="task_get",
        description=("Read one task in full — description, exit criteria, assignee, due date."),
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string", "description": "The task id."}},
            "required": ["id"],
        },
        handler=_task_get,
    ),
    "sessions_search": ToolSpec(
        name="sessions_search",
        description=(
            "Search past conversations by what was said in them. Temporary and "
            "incognito conversations are never searchable, and results are "
            "credential-redacted."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words said in the conversation."},
                "limit": {
                    "type": "integer",
                    "description": "Max conversations (1-10, default 5).",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
        handler=_sessions_search,
    ),
    "status": ToolSpec(
        name="status",
        description=(
            "What this PersonalClaw instance can currently do — version and which "
            "subsystems are available. Reports no configuration values."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_status,
    ),
}


def list_tools() -> list[dict]:
    """The `tools/list` payload — schema only, never handlers."""
    return [
        {"name": spec.name, "description": spec.description, "inputSchema": spec.input_schema}
        for spec in sorted(TOOLS.values(), key=lambda s: s.name)
    ]


async def call_tool(name: str, arguments: dict, state: Any, client_id: str = "") -> dict:
    """Dispatch one tool call.

    Raises ``KeyError`` for an unknown tool and ``ValueError`` for bad arguments,
    which the transport maps to the corresponding JSON-RPC errors. The result is
    wrapped HERE rather than in each handler, so fencing cannot be forgotten.

    ``client_id`` rides through to the fence attribution so the provenance names the
    integration, not just the surface.
    """
    spec = TOOLS.get(name)
    if spec is None or spec.handler is None:
        raise KeyError(name)
    text = await spec.handler(arguments, state)
    return wrap_result(text, name, client_id)
