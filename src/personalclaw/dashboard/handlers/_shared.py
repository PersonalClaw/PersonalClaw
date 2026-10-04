"""Shared helpers used across handler submodules."""

import logging
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.config import loader as config_loader
from personalclaw.config.loader import ConfigPreserveError, ConfigWriteError
from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)


class RefusedInConfigTransaction(Exception):
    """A config write refused INSIDE the config transaction (``config.transactions``).

    Raised, not returned, so the transaction writes nothing. It carries the answer — and the
    audit row, when the refusal has one — for the handler to give once the lock is released.
    """

    def __init__(self, response: web.Response, *, audit: dict[str, Any] | None = None) -> None:
        super().__init__(response.status)
        self.response = response
        self.audit = audit

    def answer(self) -> web.Response:
        if self.audit is not None:
            # Resolved per call: tests replace the package's `sel`.
            import personalclaw.dashboard.handlers as _pkg  # noqa: F811 — circular import

            _pkg.sel().log_api_access(**self.audit)
        return self.response


def config_write_refusal(exc: ConfigWriteError) -> web.Response:
    """The answer to a config write the transaction refused, which wrote nothing: 500 for a
    file that cannot be read (it needs repairing), 503 for a write that could not get its turn
    (another writer held the lock; try again)."""
    status = 500 if isinstance(exc, ConfigPreserveError) else 503
    return web.json_response({"error": str(exc)}, status=status)


def _get_memory(state: DashboardState):
    """Get MemoryStore from context_builder, or create standalone.

    Its vector store embeds with the model bound in Settings → Models at each use
    (``VectorMemoryStore.embed_fn``), so nothing is wired here: a function wired on first access
    was the model bound then, which every later rebind and clear left in place."""
    if state.context_builder:
        mem = state.context_builder.memory
    else:
        # Fallback: create standalone MemoryStore with an attached record store.
        # The vector store is attached UNCONDITIONALLY (even with no embedder) so
        # lessons + semantic memory persist to memory.db in API-only mode — a store
        # with no embed_fn still does key/value CRUD. Without this, the record store
        # (the sole lesson backing after the JSONL store's retirement) would be
        # absent here and lesson writes on this path would be silently dropped.
        if not hasattr(state, "_standalone_memory"):
            from personalclaw.memory import MemoryStore
            from personalclaw.vector_memory import VectorMemoryStore

            mem = MemoryStore()
            mem.init()
            vs = VectorMemoryStore()
            vs.init()
            mem.vector_store = vs
            state._standalone_memory = mem  # type: ignore[attr-defined]
        mem = state._standalone_memory  # type: ignore[attr-defined]
    return mem


def _get_skills(state: DashboardState):
    """Get SkillsLoader from context_builder, or create standalone."""
    if state.context_builder:
        return state.context_builder.skills
    if not hasattr(state, "_standalone_skills"):
        from personalclaw.skills import SkillsLoader

        skills = SkillsLoader(install_builtins=False)
        state._standalone_skills = skills  # type: ignore[attr-defined]
    return state._standalone_skills  # type: ignore[attr-defined]


def _resolve_skill_path(name: str) -> Path | None:
    """Find SKILL.md for a marketplace skill by name (supports nested paths)."""
    skills_dir = config_loader.config_dir() / "skills"
    for pattern in (f"*/{name}/SKILL.md", f"packages/*/skills/*/{name}/SKILL.md"):
        for p in skills_dir.glob(pattern):
            return p
    return None


async def _list_marketplace_skills() -> list[dict[str, Any]]:
    """List skills from the marketplace using ``personalclaw skills list`` CLI."""
    import asyncio
    import re

    try:
        proc = await asyncio.create_subprocess_exec(
            "personalclaw",
            "skills",
            "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            return []
    except FileNotFoundError:
        return []
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.communicate()
        return []

    result: list[dict[str, Any]] = []
    pkg = ""
    for line in stdout.decode(errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Package header: non-indented line (no leading spaces)
        if not line.startswith(" ") and not line.startswith("Installed"):
            pkg = stripped
            continue
        # Description line (check BEFORE skill line — both match `word: text`)
        dm = re.match(r"^\s+Description:\s+(.+)", line)
        if dm and result and result[-1]["package"] == pkg:
            result[-1]["description"] = dm.group(1)
            continue
        # Skill line: "  name [vX.Y.Z]: display-name" or "  name: display-name"
        m = re.match(r"^\s+(\S+)(?:\s+\[v[\d.]+\])?:\s+(.+)", line)
        if m:
            name = m.group(1)
            resolved = _resolve_skill_path(name)
            result.append(
                {
                    "key": f"marketplace/{name}",
                    "name": name,
                    "description": "",
                    "path": str(resolved) if resolved else "",
                    "dir": str(resolved.parent) if resolved else "",
                    "always": False,
                    "source": "marketplace",
                    "package": pkg,
                }
            )

    return result


def _is_restricted_session(state: DashboardState, request: "Any") -> bool:
    """Check if request comes from work that keeps nothing: an Incognito or Temporary chat's, the
    work such a chat started, or work for a chat whose mode nothing can say. Judged as the work it
    does for the chat at the top (``memory_reads.keeps_nothing``, the check the agent's artifact
    and workflow tools make too): a subagent's or a workflow step's request keeps nothing when
    anything it works for keeps nothing, whatever its own key is marked. Each session's mode is
    read by the one reader of a session's mode, over the gateway's live chats: the live chat first,
    then the registry, the transcript and a step's run.

    Reads X-Session-Key header (set by browser and MCP subprocesses).
    Returns True if the session should be blocked from memory operations.
    """
    sk = request.headers.get("X-Session-Key", "")
    if not sk or sk == "dashboard:ui":
        return False
    from personalclaw import memory_reads

    return bool(memory_reads.keeps_nothing(state, sk))


def _memory_refusal(state: DashboardState, request: "Any") -> str:
    """Why the work the request is made for reads none of your memory, or ``""`` when it may: a
    Temporary chat's work, or an app's that does not hold the memory permission — the app whose
    token made the request, or the one whose conversation or agent its ``X-Session-Key`` names
    (:func:`personalclaw.memory_reads.reach_of`)."""
    from personalclaw import memory_reads

    caller = request.headers.get("X-Session-Key", "")
    return memory_reads.reach_of(state, caller, app=str(request.get("app") or "")).refusal


def _change_refused_for_the_app(
    state: DashboardState, request: "Any", operation: str
) -> web.Response | None:
    """The 403 for a change to your memory made for an app's work that may change none of it,
    said in the app's words (``memory_reads.app_refusal``) with its security-log row; ``None`` when
    the work may. The app is found as :func:`_memory_refusal` finds it: the one whose token made
    the request, or whose conversation, agent or scheduled job its ``X-Session-Key`` names. Asked
    before anything else the change does, so nothing of it happens. A Temporary or Incognito
    chat's change is :func:`_is_restricted_session`'s to answer."""
    from personalclaw import memory_reads

    caller = request.headers.get("X-Session-Key", "")
    token_app = str(request.get("app") or "")
    app = memory_reads.reach_of(state, caller, app=token_app).app
    refused = memory_reads.app_refusal(app, changing=True)
    if not refused:
        return None
    # Resolved per call: tests replace the package's `sel`.
    import personalclaw.dashboard.handlers as _pkg  # noqa: F811 — circular import

    _pkg.sel().log_api_access(
        caller=caller or f"app:{token_app}",
        operation=operation,
        outcome="denied",
        source="dashboard",
        resources="app_memory_not_granted",
        error=refused,
    )
    return web.json_response({"error": refused}, status=403)
