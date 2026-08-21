"""Chat plan mode — a chat bound to the EXISTING planning walkthrough (CC-8).

There is deliberately **no second state machine** here. The walkthrough state is
``personalclaw.planning.session``: the same ``PlanSession``/``PlanStep`` model and the
same ``submit_artifact`` → ``edit_artifact`` → ``comment_step``/``approve_step``
transitions that the loop planning surface (``handlers/loop_routes.py``) drives. This
module only does the two things that model deliberately leaves to its owner:

  * **persists** that session for a CHAT owner instead of a loop owner
    (``PlanSession.project_id`` carries the chat session key), and
  * records the chat-side **attachment** — which task mode to restore once the plan is
    approved, and whether activation happened mid-turn and therefore parked a run.
    That is turn bookkeeping, not plan state.

Two properties are load-bearing:

**Activation is manual only.** Nothing on the send path calls into this module to
*create* a session; ``activate`` is reachable only from the composer affordance's
endpoint. The turn-end hook (``maybe_submit_plan_draft``) is a no-op for a chat with no
session, so a quick task is untouched — no heuristic, no auto-detection.

**The no-execute guarantee is the task-mode gate, not a prompt.** Activation flips the
session to task mode ``plan``; the canonical gate in ``personalclaw.task_modes`` (which
the native runtime consults in ``_guard_and_invoke`` *before* approval) is what denies a
mutating tool. This module never writes a "don't execute" instruction anywhere — it only
sets the posture the existing gate reads, and refuses to *leave* that posture while a
step is still awaiting review.

Storage: ``config_dir()/chat_plans/<safe key>.json``, atomic writes, reads tolerate a
missing/corrupt file. This is a user-facing availability surface (the walkthrough panel),
so a corrupt file **fails open** to "no plan session" + a warning rather than wedging the
chat behind an unreadable gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir
from personalclaw.dashboard.chat_utils import _history_key_for, apply_task_mode
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import _safe_key
from personalclaw.http_errors import json_error
from personalclaw.planning import session as PS
from personalclaw.planning.session import PlanSession, PlanStep, StepStatus
from personalclaw.sel import sel

logger = logging.getLogger(__name__)

#: The step ``kind`` a chat-owned walkthrough step carries. The planning model's kind set
#: is intentionally OPEN (it validates shape, never a taxonomy), so a chat contributes its
#: own kind rather than borrowing one of the Code feature's SDLC kinds.
PLAN_STEP_KIND = "chat_plan"

_DIR_NAME = "chat_plans"


# ── persistence (chat-owned sidecar for the shared PlanSession) ──


def _path(chat_key: str) -> Path:
    # _safe_key is the single filename-sanitization rule already used for a session
    # key (history.ConversationLog._path) — reused rather than re-derived so a chat's
    # plan sidecar and its transcript agree on what a key's filename is.
    return config_dir() / _DIR_NAME / f"{_safe_key(chat_key)}.json"


def read(chat_key: str) -> tuple[PlanSession | None, dict[str, Any]]:
    """The chat's walkthrough + its attachment record, or ``(None, {})``.

    Fails OPEN: an unreadable/corrupt sidecar reads as "no plan session" (warned) so a
    bad file cannot wedge the chat inside a gate it can no longer approve out of.
    """
    f = _path(chat_key)
    try:
        raw = json.loads(f.read_text())
    except FileNotFoundError:
        return None, {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        logger.warning("chat plan sidecar unreadable for %s — treating as absent", chat_key)
        return None, {}
    if not isinstance(raw, dict) or not isinstance(raw.get("session"), dict):
        logger.warning("chat plan sidecar malformed for %s — treating as absent", chat_key)
        return None, {}
    binding = raw.get("binding")
    return PlanSession.from_dict(raw["session"]), dict(binding) if isinstance(binding, dict) else {}


def write(session: PlanSession, binding: dict[str, Any]) -> None:
    """Persist the walkthrough + attachment for the chat named by ``project_id``."""
    f = _path(session.project_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(f, json.dumps({"session": session.to_dict(), "binding": binding}, indent=2))


def clear(chat_key: str) -> None:
    """Drop the chat's walkthrough entirely (the user cancelled plan mode)."""
    f = _path(chat_key)
    try:
        f.unlink()
    except (FileNotFoundError, OSError):
        pass


# ── the chat-side binding over the shared state machine ──


def awaiting_review(chat_key: str) -> str:
    """The id of the step whose review gate is OPEN, or ``""``.

    This is the single predicate for "this chat is awaiting plan approval" — derived
    from the shared model (``current_step`` + its status), never from a flag of our own.
    """
    sess, _ = read(chat_key)
    if sess is None:
        return ""
    step = PS.current_step(sess)
    if step is not None and step.status == StepStatus.AWAITING_REVIEW.value:
        return step.id
    return ""


def in_progress(chat_key: str) -> bool:
    """True while the chat's walkthrough is unfinished (any step not yet approved)."""
    sess, _ = read(chat_key)
    return sess is not None and not PS.is_complete(sess)


def activate(chat_session: _ChatSession, *, running: bool) -> tuple[PlanSession, dict[str, Any]]:
    """Open — or, mid-conversation, EXTEND — the chat's walkthrough. Manual only.

    A first activation creates the session and remembers the task mode to restore. A
    later activation appends another step, which is exactly the re-planning shape the
    shared model already supports ("``steps`` may grow as the planner refines"), so
    mid-task re-planning needs no new state.

    ``running`` (a turn is in flight) records the PARK: the transcript is deliberately
    left untouched — parking is a posture change plus a stop request, never a truncation.
    """
    now = time.time()
    sess, binding = read(chat_session.key)
    if sess is None:
        sess = PlanSession(project_id=chat_session.key, created_at=now)
        binding = {}
    # Only the FIRST activation records the mode to restore: re-planning from inside
    # plan mode must not overwrite it with "plan" and strand the chat there.
    binding.setdefault("resume_task_mode", getattr(chat_session, "_task_mode", "agent") or "agent")
    replan = bool(sess.steps)
    step = PlanStep(
        id=f"chat-plan-{len(sess.steps) + 1}",
        kind=PLAN_STEP_KIND,
        title="Re-plan" if replan else "Plan",
        objective=(
            "Revise the plan for the rest of this conversation."
            if replan
            else "Plan the work before anything runs."
        ),
        # RUNNING, not PENDING: the plan-mode turn IS the planner pass, so the step is
        # already being worked. submit_artifact only accepts a RUNNING step.
        status=StepStatus.RUNNING.value,
    )
    sess.steps.append(step)
    # A draft may only come from a turn that ran AFTER this step opened. Without this
    # boundary the turn-end hook scans the whole transcript backwards and can hand the
    # step a PREVIOUS turn's reply: measured live on a mid-task re-plan, where
    # `chat-plan-2` was handed the (pre-edit) `chat-plan-1` draft while the re-plan
    # turn's actual reply — "I will answer your question directly in one step." — was
    # dropped. The user then reviews and approves a plan they did not ask for, and
    # `_resume_prompt` carries that stale text into the run.
    binding["draft_from"] = len(chat_session.messages)
    if running:
        binding["parked"] = True
        binding["parked_at"] = now
        # Diagnostic only: the transcript length at park time, kept so a park that
        # truncated the transcript is visible in the record. Nothing reads it back.
        binding["parked_messages"] = len(chat_session.messages)
    write(sess, binding)
    return sess, binding


def maybe_submit_plan_draft(state: DashboardState, session: _ChatSession) -> bool:
    """Turn-end hook: a plan-gated turn's reply IS the walkthrough artifact.

    Hands the assistant's final text to ``PS.submit_artifact`` (running →
    awaiting_review) so the gate opens on real content, in the SAME editable-markdown
    shape the loop walkthrough uses (``artifact['markdown']``). A no-op — a single
    cheap sidecar read — when the chat has no plan session, which is every quick task.
    """
    sess, binding = read(session.key)
    if sess is None:
        return False
    step = PS.current_step(sess)
    if step is None or step.status != StepStatus.RUNNING.value:
        return False
    # Only this step's own turn can draft it — see ``draft_from`` in ``activate``. An
    # older assistant message is NOT a fallback: handing the step a previous turn's
    # reply is the exact defect this boundary exists to prevent, and doing nothing
    # leaves the gate honestly saying "Drafting…" until a real reply lands.
    try:
        start = int(binding.get("draft_from", 0))
    except (TypeError, ValueError):
        start = 0
    markdown = ""
    for msg in reversed(session.messages[start:]):
        if msg.get("role") == "assistant" and str(msg.get("content", "")).strip():
            markdown = str(msg["content"])
            break
    if not markdown:
        return False
    if not PS.submit_artifact(sess, step.id, {"markdown": markdown}):
        return False
    write(sess, binding)
    try:
        state.broadcast_ws("chat_plan_step", {"session": session.key, "step_id": step.id})
    except Exception:  # noqa: BLE001 — a broadcast failure must not lose the artifact
        logger.debug("chat plan step broadcast failed", exc_info=True)
    return True


def _resume_prompt(markdown: str) -> str:
    """The continuation that carries an approved plan back into the SAME conversation.

    A continuation, not a restart: the transcript is intact above it, so the agent is
    resuming work it already has context for.
    """
    return (
        "The plan below was reviewed and approved. Continue this conversation by "
        "carrying it out — do not re-plan it and do not restate it.\n\n" + markdown
    )


# ── HTTP surface (mirrors the loop walkthrough's read / mutate / write discipline) ──


async def _body(request: web.Request) -> dict | web.Response:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return json_error("invalid_json", message="Request body must be JSON", status=400)
    if not isinstance(data, dict):
        return json_error("invalid_json", message="JSON body must be an object", status=400)
    return data


def _resolve(request: web.Request) -> tuple[DashboardState, _ChatSession] | web.Response:
    state: DashboardState = request.app["state"]
    name = request.match_info["session"]
    chat = state._sessions.get(name)
    if chat is None:
        return json_error("session_not_found", message="No such chat session", status=404)
    return state, chat


async def api_chat_plan_session(request: web.Request) -> web.Response:
    """GET /api/chat/sessions/{session}/plan-session — the walkthrough state.

    Mirrors ``api_loop_plan_session``: ``{session: null}`` means "no plan session",
    which is the state of every chat that never used the composer affordance.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    _state, chat = resolved
    sess, binding = read(chat.key)
    return web.json_response(
        {
            "session": sess.to_dict() if sess else None,
            "binding": binding,
            "awaiting_step_id": awaiting_review(chat.key),
            "task_mode": getattr(chat, "_task_mode", "agent") or "agent",
        }
    )


async def api_chat_plan_activate(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/activate — open the plan walkthrough.

    The ONLY way a chat acquires a plan session. Flips the session to task mode
    ``plan`` (so the existing gate denies mutations) and opens a walkthrough step. When
    a turn is in flight the run is PARKED: the queue is dropped and a cooperative stop
    is requested, while ``session.messages`` is left exactly as it is.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    state, chat = resolved
    was_running = bool(chat.running)
    sess, binding = activate(chat, running=was_running)
    apply_task_mode(state, chat, "plan")
    if was_running:
        chat._queue.clear()
        chat._stop_state = "soft_pending"

        async def _parked() -> None:
            # The park is complete once the turn actually stops. Resetting here (rather
            # than on approval) keeps the posture honest if the user cancels instead.
            chat._stop_state = "idle"
            state.push_sessions_update()

        await state.sessions.stop_turn(
            _history_key_for(chat.key), force=False, on_soft=_parked, on_hard=_parked
        )
    try:
        sel().log_api_access(
            caller="dashboard:chat-plan",
            operation="chat.plan_activate",
            outcome="enabled",
            source="dashboard",
            resources=f"session={chat.key},parked={was_running}",
        )
    except Exception:  # noqa: BLE001
        logger.warning("SEL audit failed for chat plan activation", exc_info=True)
    state.push_sessions_update()
    return web.json_response(
        {"ok": True, "session": sess.to_dict(), "binding": binding, "parked": was_running}
    )


async def api_chat_plan_edit(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/edit {step_id, markdown} — edit the plan.

    The user finalizes the artifact's markdown body themselves. Routes through
    ``PS.edit_artifact``; the step stays awaiting review (the user still approves).
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    _state, chat = resolved
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    step_id = str(body.get("step_id", "")).strip()
    if not step_id:
        return json_error("step_id_required", message="step_id is required", status=400)
    if "markdown" not in body:
        return json_error("markdown_required", message="markdown is required", status=400)
    sess, binding = read(chat.key)
    if sess is None:
        return json_error(
            "plan_session_missing", message="This chat has no plan session", status=404
        )
    if not PS.edit_artifact(sess, step_id, str(body["markdown"])):
        return json_error(
            "step_not_awaiting_review", message="That step is not awaiting review", status=409
        )
    write(sess, binding)
    return web.json_response({"ok": True, "session": sess.to_dict()})


async def api_chat_plan_comment(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/comment {step_id, text} — comment + redraft.

    Routes through ``PS.comment_step`` (awaiting_review → running), then runs the
    re-draft as an ordinary plan-mode turn in the SAME chat.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    state, chat = resolved
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    step_id = str(body.get("step_id", "")).strip()
    if not step_id:
        return json_error("step_id_required", message="step_id is required", status=400)
    text = str(body.get("text", "")).strip()
    if not text:
        return json_error("comment_text_required", message="Comment text is required", status=400)
    sess, binding = read(chat.key)
    if sess is None:
        return json_error(
            "plan_session_missing", message="This chat has no plan session", status=404
        )
    if not PS.comment_step(sess, step_id, text, at=time.time()):
        return json_error(
            "step_not_awaiting_review", message="That step is not awaiting review", status=409
        )
    write(sess, binding)
    _dispatch(state, chat, f"Revise the plan with this feedback:\n\n{text}")
    return web.json_response({"ok": True, "session": sess.to_dict()})


async def api_chat_plan_approve(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/approve {step_id} — approve a step.

    Routes through ``PS.approve_step``. When that completes the walkthrough
    (``PS.is_complete``) the chat leaves the plan posture: the remembered task mode is
    restored and, if activation had parked a run, the approved plan is dispatched as a
    continuation into the SAME session — the transcript above it is untouched.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    state, chat = resolved
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    step_id = str(body.get("step_id", "")).strip()
    if not step_id:
        return json_error("step_id_required", message="step_id is required", status=400)
    sess, binding = read(chat.key)
    if sess is None:
        return json_error(
            "plan_session_missing", message="This chat has no plan session", status=404
        )
    if not PS.approve_step(sess, step_id):
        return json_error(
            "step_not_awaiting_review", message="That step is not awaiting review", status=409
        )
    approved = next((s for s in sess.steps if s.id == step_id), None)
    markdown = str((approved.artifact or {}).get("markdown", "")) if approved else ""
    complete = PS.is_complete(sess)
    resumed = False
    restored = ""
    if complete:
        restored = str(binding.get("resume_task_mode", "agent")) or "agent"
        apply_task_mode(state, chat, restored)
        if binding.pop("parked", False):
            binding.pop("parked_at", None)
            binding.pop("parked_messages", None)
            _dispatch(state, chat, _resume_prompt(markdown))
            resumed = True
    write(sess, binding)
    try:
        sel().log_api_access(
            caller="dashboard:chat-plan",
            operation="chat.plan_approve",
            outcome="allowed",
            source="dashboard",
            resources=f"session={chat.key},step={step_id},complete={complete}",
        )
    except Exception:  # noqa: BLE001
        logger.warning("SEL audit failed for chat plan approval", exc_info=True)
    state.push_sessions_update()
    return web.json_response(
        {
            "ok": True,
            "session": sess.to_dict(),
            "complete": complete,
            "resumed": resumed,
            "task_mode": restored or (getattr(chat, "_task_mode", "agent") or "agent"),
        }
    )


async def api_chat_plan_cancel(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/cancel — abandon the walkthrough.

    The counterpart to a manual-only activation: without an exit, a chat that opened a
    plan gate could never leave it (the task-mode control refuses to while a step is
    awaiting review). Restores the remembered task mode and drops the sidecar.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    state, chat = resolved
    _sess, binding = read(chat.key)
    restored = str(binding.get("resume_task_mode", "agent")) or "agent"
    clear(chat.key)
    apply_task_mode(state, chat, restored)
    state.push_sessions_update()
    return web.json_response({"ok": True, "task_mode": restored})


def _dispatch(state: DashboardState, chat: _ChatSession, prompt: str) -> None:
    """Run one more turn in this chat, appending ``prompt`` as the user message.

    Deliberately the same dispatch shape as the edit-resend path
    (``chat_regenerate.py``): append, then ``asyncio.create_task(_run_chat(...))``. The
    import is local (and resolved per call) both to break the import cycle with
    ``chat_runner`` — which calls this module's turn-end hook — and so a test can
    substitute ``chat_runner._run_chat``.
    """
    from personalclaw.dashboard.chat_runner import _run_chat

    chat.append("user", prompt, "msg msg-u")
    task = asyncio.create_task(_run_chat(state, chat, prompt))
    chat.task = task
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
