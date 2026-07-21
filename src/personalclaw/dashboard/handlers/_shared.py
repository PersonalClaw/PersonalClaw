"""Shared helpers used across handler submodules."""

import logging
from pathlib import Path
from typing import Any

from personalclaw.dashboard.state import DashboardState

logger = logging.getLogger(__name__)


def _get_memory(state: DashboardState):
    """Get MemoryStore from context_builder, or create standalone.

    Ensures the vector store's embed_fn is wired from the active embedding
    model on first access — the same deferred resolution used by knowledge.
    Without this, memory writes skip embedding when the gateway boots before
    the model provider entry is registered."""
    if state.context_builder:
        mem = state.context_builder.memory
    else:
        # Fallback: create standalone MemoryStore
        if not hasattr(state, "_standalone_memory"):
            from personalclaw.memory import MemoryStore

            mem = MemoryStore()
            mem.init()
            state._standalone_memory = mem  # type: ignore[attr-defined]
        mem = state._standalone_memory  # type: ignore[attr-defined]
    # Deferred embed_fn wiring: if the vector store exists but has no embed_fn,
    # try to resolve it now (the provider entry may have been registered after boot).
    if hasattr(mem, "vector_store") and mem.vector_store and not mem.vector_store.embed_fn:
        try:
            from personalclaw.embedding_providers.registry import get_active_embed_fn

            embed_fn = get_active_embed_fn()
            if embed_fn:
                mem.vector_store.embed_fn = embed_fn
        except Exception:
            pass
    return mem


def _get_active_workspace(state: DashboardState) -> str:
    """Return the working directory of the most recently active chat session."""
    sessions = getattr(state, "_sessions", {})
    if sessions:
        # Pick the session with the most messages (most active)
        best = max(sessions.values(), key=lambda s: s.total_messages, default=None)
        if best and best.workspace_dir:
            return best.workspace_dir
    return ""


def _get_lessons(state: DashboardState, cwd: str | None = None):
    """Get LessonStore for a working directory. Falls back to global."""
    ws = cwd or _get_active_workspace(state)
    if ws and state.context_builder:
        return state.context_builder.get_lessons_for(ws)
    return state.lessons


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
    skills_dir = _path_home_pclaw() / "skills"
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


def _blocks_reads_session(state: DashboardState, request: "Any") -> bool:
    """Check if request comes from a temporary session that blocks memory reads."""
    sk = request.headers.get("X-Session-Key", "")
    if not sk or sk == "dashboard:ui":
        return False
    session_name = sk.split(":", 1)[-1] if ":" in sk else sk
    session = state._sessions.get(session_name)
    if session and session.blocks_reads:
        return True
    from personalclaw import session_restrictions

    if session_restrictions.is_temporary(sk):
        return True
    return False


def _path_home_pclaw():
    """Resolve PersonalClaw home dir, honoring PERSONALCLAW_HOME."""
    try:
        from personalclaw.config.loader import config_dir as _cd

        return _cd()
    except Exception:
        from pathlib import Path as _P

        return _P.home() / ".personalclaw"


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
    sess_dir = _path_home_pclaw() / "sessions"
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
