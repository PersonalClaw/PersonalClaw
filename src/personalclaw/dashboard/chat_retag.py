"""Magic re-tag — batch AI re-evaluation of every chat session's tags.

Triggered from the chat-history board's sparkle button. One background job at
a time walks EVERY non-restricted chat session (in-memory + persisted-on-disk),
reads its transcript, and asks the background LLM for the session's COMPLETE
new tag set: keeping tags that still fit, updating transient status tags
('in-progress' → 'done'), adding fitting existing tags, creating at most a
couple of new ones when warranted, and dropping tags that no longer apply.

Progress is pushed over the shared /api/ws socket (``retag_progress`` /
``retag_done`` events — the same broadcast idiom chat uses), so the board can
show a live running state and refresh as sessions land. The job is idempotent
(same content → same proposed set; the whole set is replaced per session) and
cancellable (POST .../cancel).

LLM calls go through :func:`personalclaw.llm_helpers.one_shot_completion`
(background/reasoning tier — NOT the chat model), one session per call, with a
small concurrency cap.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

# One session per LLM call; a couple in flight at once.
_CONCURRENCY = 2
# Per-session LLM budget — a stuck provider must not wedge the whole batch.
_CALL_TIMEOUT_SECS = 120
# Transcript excerpt cap per session (chars) — enough signal, bounded cost.
_TRANSCRIPT_CAP = 6000
# Bounds mirrored from title-time auto-tagging (chat_title).
_MAX_TAGS_PER_SESSION = 4
_MAX_NEW_TAGS_PER_SESSION = 2


@dataclass
class RetagJob:
    """One batch re-tag run — identity, lifecycle, progress counters."""

    id: str
    status: str = "running"  # running | done | error | cancelled
    done: int = 0
    total: int = 0
    updated: int = 0  # sessions whose tag set actually changed
    skipped: int = 0  # empty transcript / unparseable reply / unchanged
    errors: int = 0
    current: str = ""  # session key being evaluated (best-effort display)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "done": self.done,
            "total": self.total, "updated": self.updated, "skipped": self.skipped,
            "errors": self.errors, "current": self.current, "error": self.error,
        }


@dataclass
class _Candidate:
    """One session to evaluate: where it lives + what it currently carries."""

    key: str  # display/session key (dashboard-local name for live sessions)
    history_key: str  # raw persisted key ('' when the session was never saved)
    in_memory: bool
    tags: list[str] = field(default_factory=list)  # current tag ids


def _get_job(state: DashboardState) -> RetagJob | None:
    return getattr(state, "_retag_job", None)


def _get_task(state: DashboardState) -> "asyncio.Task | None":
    return getattr(state, "_retag_task", None)


def _collect_candidates(state: DashboardState) -> list[_Candidate]:
    """Every non-restricted chat session: live ones first, then disk-only.

    Mirrors the enumeration of GET /api/chat/sessions — restricted
    (incognito/temporary) and closed sessions are excluded; worker sessions
    (loops/code) are included like the history list includes them.
    """
    out: list[_Candidate] = []
    seen: set[str] = set()
    for s in state._sessions.values():
        seen.add(s.key)
        if getattr(s, "memory_mode", "persistent") != "persistent":
            continue
        out.append(_Candidate(key=s.key, history_key="", in_memory=True, tags=list(s.tags)))
    if state.conversation_log:
        try:
            disk = state.conversation_log.list_sessions()
        except Exception:
            logger.warning("retag: list_sessions failed", exc_info=True)
            disk = []
        for d in disk:
            raw_key = d.get("key", "")
            if raw_key.startswith("dashboard:"):
                name = raw_key.removeprefix("dashboard:")
            elif raw_key.startswith("dashboard_"):
                name = raw_key.removeprefix("dashboard_")
            else:
                continue  # non-dashboard namespaces (channel/worker internals)
            if name in seen:
                continue
            meta = state.conversation_log.get_metadata(raw_key)
            if meta.get("closed"):
                continue
            if meta.get("memory_mode") in ("incognito", "temporary"):
                continue
            seen.add(name)
            tags = [t for t in meta.get("tags", []) if isinstance(t, str)]
            out.append(_Candidate(key=name, history_key=raw_key, in_memory=False, tags=tags))
    return out


def _transcript_for(state: DashboardState, cand: _Candidate) -> str:
    """A bounded transcript excerpt for the LLM (most recent content last)."""
    if cand.in_memory:
        session = state._sessions.get(cand.key)
        lines = []
        for m in (session.messages if session else []):
            role = m.get("role", "")
            content = m.get("content", "")
            if role in ("user", "assistant") and content:
                lines.append(f"{role}: {content}")
        text = "\n".join(lines)
    else:
        try:
            text = state.conversation_log.load_transcript(cand.history_key)
        except Exception:
            text = ""
    text = text.strip()
    if len(text) > _TRANSCRIPT_CAP:
        text = text[-_TRANSCRIPT_CAP:]
    return text


def _build_retag_prompt(state: DashboardState, cand: _Candidate, transcript: str) -> str:
    """The per-session re-tag prompt: vocabulary + current tags + transcript."""
    tag_index = {t["id"]: t for t in state._tags if t.get("id")}
    vocab = ", ".join(
        f"{t.get('name', '')}{' (status)' if t.get('status') else ''}"
        for t in sorted(state._tags, key=lambda t: t.get("order", 0))
        if t.get("name")
    )
    current = ", ".join(
        tag_index[t]["name"] for t in cand.tags if t in tag_index
    ) or "(none)"
    return (
        "You are re-evaluating the tags on a saved chat conversation.\n"
        f"Tag vocabulary: {vocab or '(none)'}\n"
        f"Tags currently on this chat: {current}\n\n"
        "Conversation transcript (may be truncated):\n"
        f"{transcript}\n\n"
        "Reply with EXACTLY one line and nothing else, in the form:\n"
        "TAGS: tag1, tag2\n"
        "— the COMPLETE new set of tags for this chat. Rules:\n"
        "- Strongly prefer existing vocabulary tags that genuinely fit.\n"
        "- Status-like tags (in-progress, done, blocked, review, ...) must reflect the "
        "conversation's CURRENT reality — update them if stale.\n"
        "- Remove tags that clearly no longer fit.\n"
        f"- Propose at most {_MAX_NEW_TAGS_PER_SESSION} NEW tags (short, 1-2 words) only "
        "when nothing existing fits.\n"
        f"- At most {_MAX_TAGS_PER_SESSION} tags total.\n"
        "- Reply 'TAGS: unchanged' to keep the current tags as-is.\n"
        "- Reply 'TAGS: none' to remove all tags."
    )


def _parse_retag_reply(text: str) -> list[str] | None:
    """Parse the ``TAGS:`` line. None = keep as-is (unchanged/unparseable);
    [] = remove all; otherwise the complete proposed tag-name set."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("tags:"):
            continue
        raw = stripped[5:].strip()
        low = raw.lower()
        if not raw or low in ("unchanged", "keep", "same"):
            return None
        if low in ("none", "n/a", "-"):
            return []
        names: list[str] = []
        seen: set[str] = set()
        for part in raw.split(","):
            name = part.strip().strip('"').strip("'").strip(".")
            if name.lower().startswith("new:"):
                name = name[4:].strip()
            if not name or len(name) > 40 or name.lower() in seen:
                continue
            seen.add(name.lower())
            names.append(name)
        return names[:_MAX_TAGS_PER_SESSION]
    return None


def _resolve_tag_ids(state: DashboardState, names: list[str]) -> list[str]:
    """Map proposed names to tag ids, creating at most 2 new tags per session
    via the SAME create path the UI uses (proper ids/colors/order)."""
    from personalclaw.dashboard.chat_tags import _auto_color, create_tag, find_tag_by_name

    ids: list[str] = []
    created = 0
    for name in names:
        if len(ids) >= _MAX_TAGS_PER_SESSION:
            break
        tag = find_tag_by_name(state, name)
        if tag is None:
            if created >= _MAX_NEW_TAGS_PER_SESSION:
                continue
            tag = create_tag(state, name, color=_auto_color(name))
            if tag is None:
                continue
            created += 1
        if tag["id"] not in ids:
            ids.append(tag["id"])
    return ids


def _apply_tags(state: DashboardState, cand: _Candidate, new_ids: list[str]) -> bool:
    """Persist the new tag set on a session. Returns True when it changed."""
    if sorted(new_ids) == sorted(cand.tags):
        return False
    if cand.in_memory:
        from personalclaw.dashboard.chat_persistence import _save_session_to_history

        session = state._sessions.get(cand.key)
        if session is None:
            return False
        session.tags = list(new_ids)
        _save_session_to_history(state, session, force=True)
    else:
        if not state.conversation_log:
            return False
        state.conversation_log.update_metadata(cand.history_key, {"tags": list(new_ids)})
    return True


async def _retag_one(state: DashboardState, job: RetagJob, cand: _Candidate,
                     sem: asyncio.Semaphore) -> None:
    """Evaluate + apply one session, then publish a progress frame."""
    from personalclaw.llm_helpers import one_shot_completion

    changed = False
    try:
        async with sem:
            job.current = cand.key
            transcript = _transcript_for(state, cand)
            if transcript:
                prompt = _build_retag_prompt(state, cand, transcript)
                reply = await asyncio.wait_for(
                    one_shot_completion(prompt, use_case="background"),
                    timeout=_CALL_TIMEOUT_SECS,
                )
                names = _parse_retag_reply(reply)
                if names is not None:
                    new_ids = _resolve_tag_ids(state, names)
                    changed = _apply_tags(state, cand, new_ids)
        if changed:
            job.updated += 1
        else:
            job.skipped += 1
    except asyncio.CancelledError:
        raise  # don't count a cancelled session as done
    except Exception:
        logger.warning("retag: session %s failed", cand.key, exc_info=True)
        job.errors += 1
    job.done += 1
    state.broadcast_ws("retag_progress", job.to_dict())
    if changed:
        state.push_sessions_update()


async def _drive(state: DashboardState, job: RetagJob) -> None:
    """The batch: enumerate, fan out with a small semaphore, publish terminal."""
    try:
        candidates = _collect_candidates(state)
        job.total = len(candidates)
        state.broadcast_ws("retag_progress", job.to_dict())
        sem = asyncio.Semaphore(_CONCURRENCY)
        await asyncio.gather(*(_retag_one(state, job, c, sem) for c in candidates))
        job.status = "done"
        job.current = ""
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.current = ""
        state.broadcast_ws("retag_done", job.to_dict())
        raise
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        logger.warning("retag: batch failed: %s", exc, exc_info=True)
        job.status = "error"
        job.error = str(exc)[:300]
    state.broadcast_ws("retag_done", job.to_dict())
    state.push_sessions_update()


# ── HTTP API ────────────────────────────────────────────────────────────────

async def api_retag_all(request: web.Request) -> web.Response:
    """POST /api/sessions/retag-all — start (or return) the batch re-tag job."""
    state: DashboardState = request.app["state"]
    existing = _get_job(state)
    if existing is not None and existing.status == "running":
        return web.json_response(existing.to_dict(), status=200)
    job = RetagJob(id=uuid.uuid4().hex[:12])
    state._retag_job = job
    task = asyncio.create_task(_drive(state, job))
    state._retag_task = task
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
    sel().log_api_access(
        caller="dashboard", operation="chat.retag_all",
        outcome="allowed", source="dashboard", resources=job.id,
    )
    return web.json_response(job.to_dict(), status=202)


async def api_retag_status(request: web.Request) -> web.Response:
    """GET /api/sessions/retag-all — the current/last job (for UI hydration)."""
    state: DashboardState = request.app["state"]
    job = _get_job(state)
    if job is None:
        return web.json_response({"status": "idle"})
    return web.json_response(job.to_dict())


async def api_retag_cancel(request: web.Request) -> web.Response:
    """POST /api/sessions/retag-all/cancel — cancel the in-flight job."""
    state: DashboardState = request.app["state"]
    job = _get_job(state)
    task = _get_task(state)
    if job is None or job.status != "running" or task is None or task.done():
        return web.json_response({"error": "no running job"}, status=404)
    task.cancel()
    sel().log_api_access(
        caller="dashboard", operation="chat.retag_cancel",
        outcome="allowed", source="dashboard", resources=job.id,
    )
    return web.json_response({"ok": True})
