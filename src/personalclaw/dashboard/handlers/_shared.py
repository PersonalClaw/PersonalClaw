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
    """Check if request comes from an ephemeral (incognito) or temporary (guest) session.

    Reads X-Session-Key header (set by browser and MCP subprocesses).
    Returns True if the session should be blocked from memory operations.
    """
    sk = request.headers.get("X-Session-Key", "")
    if not sk:
        return False
    if sk == "dashboard:ui":
        return False
    if sk in state._restricted_keys:
        return True
    session_name = sk.split(":", 1)[-1] if ":" in sk else sk
    session = state._sessions.get(session_name)
    if session and session.is_restricted:
        return True
    from personalclaw import session_restrictions

    if session_restrictions.is_restricted(sk):
        return True
    return False


def _memory_refusal(state: DashboardState, request: "Any") -> str:
    """Why the work the request is made for reads none of your memory, or ``""`` when it may: a
    Temporary chat's work, or an app's that does not hold the memory permission — the app whose
    token made the request, or the one whose conversation or agent its ``X-Session-Key`` names
    (:func:`personalclaw.memory_reads.reach_of`)."""
    from personalclaw import memory_reads

    caller = request.headers.get("X-Session-Key", "")
    return memory_reads.reach_of(state, caller, app=str(request.get("app") or "")).refusal


def _session_has_persisted_history(session_name: str) -> bool:
    """Return True iff the session has a JSONL file in ~/.personalclaw/sessions/.

    This is a positive signal that the session was previously established
    as non-ephemeral: ephemeral (incognito/temporary) sessions never write
    to disk, so a persisted JSONL can only come from a real user session.

    Used by ``api_lessons_create`` to distinguish between:

    * A legitimate MCP subprocess whose in-memory session was evicted by the
      idle-sweep loop (``session.py``'s 30-minute timeout). The subprocess
      still holds the original ``PERSONALCLAW_SESSION_KEY`` env var, so it
      keeps sending the same ``X-Session-Key``, but ``state._sessions`` has
      moved on. Without this check such calls return HTTP 400 ``unknown
      session`` even though the user is actively typing in the thread.

    * A forged or stale key from a context that never had a real session
      backing it — which should continue to be rejected.

    Only checks existence, not contents. Authentication of the caller is
    still enforced by the ``X-Internal-Secret`` middleware upstream; this
    check only governs the *ephemeral vs non-ephemeral* distinction.
    """
    if (
        not session_name
        or "/" in session_name
        or "\\" in session_name
        or "\x00" in session_name
        or session_name.startswith(".")
    ):
        # Defence-in-depth against path traversal; ``PERSONALCLAW_SESSION_KEY``
        # normally has no path separators, but ``X-Session-Key`` is
        # attacker-controlled in principle even behind the secret
        # middleware. Reject forward slash (Linux/macOS) and backslash
        # (Windows) path separators, null bytes that can truncate C-level
        # path parsing, and leading dots that could target hidden
        # per-directory files outside the intended session namespace.
        return False
    sess_dir = config_loader.config_dir() / "sessions"
    if not sess_dir.exists():
        return False
    # Match the resolution order used by the channel app's interactions
    # handler when linking threads to existing sessions: bare stem first, then
    # the ``dashboard_`` prefix fallback for dashboard sessions.
    if (sess_dir / f"{session_name}.jsonl").exists():
        return True
    if (
        not session_name.startswith("dashboard_")
        and (sess_dir / f"dashboard_{session_name}.jsonl").exists()
    ):
        return True
    return False
