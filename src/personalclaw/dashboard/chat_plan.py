"""Chat plan mode — a chat bound to the EXISTING planning walkthrough.

There is deliberately **no second state machine** here. The walkthrough state is
``personalclaw.planning.session``: the same ``PlanSession``/``PlanStep`` model and the
same ``submit_artifact`` → ``edit_artifact`` → ``comment_step``/``approve_step``
transitions that the loop planning surface (``handlers/loop_routes.py``) drives. This
module only does the two things that model deliberately leaves to its owner:

  * **persists** that session for a CHAT owner instead of a loop owner
    (``PlanSession.project_id`` carries the chat session key), and
  * records the chat-side **attachment** — which task mode to restore once the plan is
    approved, and, for an activation made while a turn ran, whether the plan waits for that
    turn to end (``after_turn``) or the turn was stopped to plan (``parked``). That is turn
    bookkeeping, not plan state.

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
from personalclaw.config import loader as config_loader
from personalclaw.dashboard.chat_persistence import resolve_session
from personalclaw.dashboard.chat_utils import apply_task_mode, take_in_the_users_links
from personalclaw.dashboard.state import DashboardState, _ChatSession
from personalclaw.history import _safe_key
from personalclaw.http_errors import json_error
from personalclaw.planning import session as PS
from personalclaw.planning.session import PlanSession, PlanStep, StepStatus
from personalclaw.request_validation import bool_field, json_object_body
from personalclaw.security import MaskConflict
from personalclaw.sel import sel
from personalclaw.stale_write import stale_write_refusal
from personalclaw.task_modes import VALID_TASK_MODES
from personalclaw.turn_source import FROM_OUTSIDE, provenance


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

#: The task mode a chat leaves plan mode for when its plan recorded none it can go back to: a
#: binding that lost it, or one an older build wrote with a mode this one does not know. Ask runs
#: only read-only tools, so leaving a plan never grants more than the chat had; Agent runs all.
_NO_MODE_TO_RESTORE = "ask"


def _mode_to_restore(chat_key: str, binding: dict[str, Any]) -> str:
    """The task mode leaving plan mode goes back to: the one activation recorded, when it is a
    mode other than plan, else :data:`_NO_MODE_TO_RESTORE`."""
    recorded = binding.get("resume_task_mode")
    if isinstance(recorded, str) and recorded in VALID_TASK_MODES and recorded != "plan":
        return recorded
    logger.warning(
        "chat %s: its plan recorded no mode to go back to (%r); it leaves plan mode in %s",
        chat_key,
        recorded,
        _NO_MODE_TO_RESTORE,
    )
    return _NO_MODE_TO_RESTORE


#: The step ``kind`` a chat-owned walkthrough step carries. The planning model's kind set
#: is intentionally OPEN (it validates shape, never a taxonomy), so a chat contributes its
#: own kind rather than borrowing one of the Code feature's SDLC kinds.
PLAN_STEP_KIND = "chat_plan"


# ── persistence (chat-owned sidecar for the shared PlanSession) ──


def _path(chat_key: str) -> Path:
    # _safe_key is the single filename-sanitization rule already used for a session
    # key (history.ConversationLog._path) — reused rather than re-derived so a chat's
    # plan sidecar and its transcript agree on what a key's filename is.
    return config_dir() / PS.CHAT_PLANS_DIR / f"{_safe_key(chat_key)}.json"


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
    from personalclaw import waiting

    f = _path(session.project_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(f, json.dumps({"session": session.to_dict(), "binding": binding}, indent=2))
    waiting.changed_elsewhere()  # a step drafted for your review, or reviewed


def clear(chat_key: str) -> None:
    """Drop the chat's walkthrough entirely (the user cancelled plan mode), and the approvals
    recorded for it (`approval_seals`)."""
    from personalclaw import waiting
    from personalclaw.planning import approval_seals

    approval_seals.forget(approval_seals.chat_scope(chat_key))
    f = _path(chat_key)
    try:
        f.unlink()
    except (FileNotFoundError, OSError):
        return
    waiting.changed_elsewhere()


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


def drafts_now(chat: _ChatSession) -> bool:
    """Whether *chat*'s running turn is the plan's own: running in plan mode while the walkthrough
    is open. A turn plan mode only waits on (``after_turn``) runs in its own mode, and is not."""
    return (
        bool(chat.running) and getattr(chat, "_task_mode", "") == "plan" and in_progress(chat.key)
    )


async def stop_the_planning_turn(state: DashboardState, chat: _ChatSession) -> None:
    """Stop the plan's own turn while the chat is still in plan mode, as a Stop press stops it.

    Leaving plan mode used to hand that turn the restored mode's tools for its remaining calls:
    the gate that keeps a planning turn from changing anything is the task mode, read at each
    tool call, and under Trust or YOLO a change it asked for ran with no plan approved (#5792).
    So a door that leaves plan mode asks :func:`drafts_now` first, drops the plan, stops the turn
    here and only then changes the mode. The plan goes before the stop because the stopped turn's
    end hands its partial reply to the walkthrough as a draft, and with no plan there is none."""
    from personalclaw.dashboard.chat_handlers import stop_the_turn

    await stop_the_turn(state, chat)


def cancel_unsubmitted(chat_key: str) -> bool:
    """The chat left plan mode before its plan was drafted: the plan is cancelled, as Cancel
    cancels it, and its parked run is let go with it. Returns whether there was one.

    Only a step not yet submitted is cancelled here. One awaiting review keeps the chat in plan
    mode (the task-mode route refuses to leave it), and a finished walkthrough is a record, not an
    open plan. Left open, a drafting step outlived the mode that was to draft it: the next turn
    ran in agent mode, its answer became the plan, and approving it sent that answer back as a
    run (#5723).
    """
    sess, _ = read(chat_key)
    if sess is None:
        return False
    step = PS.current_step(sess)
    if step is None or step.status == StepStatus.AWAITING_REVIEW.value:
        return False
    clear(chat_key)
    return True


def activate(
    chat_session: _ChatSession, *, running: bool, after_turn: bool = False
) -> tuple[PlanSession, dict[str, Any]]:
    """Open — or, mid-conversation, EXTEND — the chat's walkthrough. Manual only.

    A first activation creates the session and remembers the task mode to restore. A
    later activation appends another step, which is exactly the re-planning shape the
    shared model already supports ("``steps`` may grow as the planner refines"), so
    mid-task re-planning needs no new state.

    ``running`` (a turn is in flight) records what happens to that turn. With ``after_turn`` it
    runs to its end and the plan starts then (:func:`start_after_turn`), so the next turn is the
    planning turn. Without, it is stopped to plan, which records the PARK: the transcript is
    deliberately left untouched — parking is a posture change plus a stop request, never a
    truncation.
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
    if running and after_turn:
        # Nothing is stopped or changed yet: the turn's end starts the plan.
        binding["after_turn"] = True
    elif running:
        binding["parked"] = True
        binding["parked_at"] = now
        # Diagnostic only: the transcript length at park time, kept so a park that
        # truncated the transcript is visible in the record. Nothing reads it back.
        binding["parked_messages"] = len(chat_session.messages)
    write(sess, binding)
    return sess, binding


def start_after_turn(state: DashboardState, session: _ChatSession) -> bool:
    """Turn-end hook: plan mode switched on while this turn ran starts now that it is over.

    The chat moves to the ``plan`` task mode, and the step is drafted only from what comes after
    this point, so the next turn is the planning turn. Called before anything runs next (a queued
    message included). A no-op — a single cheap sidecar read — for every other chat.
    """
    sess, binding = read(session.key)
    if sess is None or not binding.pop("after_turn", False):
        return False
    binding["draft_from"] = len(session.messages)
    write(sess, binding)
    apply_task_mode(state, session, "plan")
    state.push_sessions_update()
    return True


def maybe_submit_plan_draft(
    state: DashboardState, session: _ChatSession, *, ran_in: str = ""
) -> bool:
    """Turn-end hook: a plan-gated turn's reply IS the walkthrough artifact.

    Hands the assistant's final text to ``PS.submit_artifact`` (running →
    awaiting_review) so the gate opens on real content, in the SAME editable-markdown
    shape the loop walkthrough uses (``artifact['markdown']``). A no-op — a single
    cheap sidecar read — when the chat has no plan session, which is every quick task.

    Only a turn that ran in plan mode drafts the plan. *ran_in* is the task mode the turn
    started in (the chat's mode now when not given): an agent turn's answer is a report of what
    it did, and handing it to an open step offered it for review as the plan (#5723).
    """
    if (ran_in or getattr(session, "_task_mode", "agent")) != "plan":
        return False
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
        # A row taken in from outside drafts nothing: it is no answer of the step's turn.
        if (
            msg.get("role") == "assistant"
            and str(msg.get("content", "")).strip()
            and (provenance(msg).get("meta") or {}).get(FROM_OUTSIDE) is None
        ):
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


def _resolve(request: web.Request) -> tuple[DashboardState, _ChatSession] | web.Response:
    """The chat the request names — in memory, or loaded from its history the way the transcript
    read loads it (`resolve_session`). A chat that is only on disk (one brought over from another
    tool, one a restart did not restore) is still a chat: the page's plan gate reads it at once,
    before its own transcript read has loaded it, and a bare in-memory lookup answered that read
    404 for every such chat. A key that names no chat is still 404."""
    state: DashboardState = request.app["state"]
    chat = resolve_session(state, request.match_info["session"])
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
            # Each step carries the revision an edit of its markdown names (`PS.wire`).
            "session": PS.wire(sess) if sess else None,
            "binding": binding,
            "awaiting_step_id": awaiting_review(chat.key),
            "task_mode": getattr(chat, "_task_mode", "agent") or "agent",
        }
    )


async def api_chat_plan_activate(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/activate {stop?} — open the plan walkthrough.

    The ONLY way a chat acquires a plan session. Flips the session to task mode
    ``plan`` (so the existing gate denies mutations) and opens a walkthrough step.

    A turn in flight is stopped only when asked (#5722). By default it runs to its end and the
    mode changes then (``after_turn``), so the next turn is the planning turn. ``stop: true``
    stops it to plan now and the run is PARKED: it is stopped as a Stop press stops it (the queue
    is dropped, and the stop's card goes into the transcript and says how it ended), while
    ``session.messages`` is otherwise left exactly as it is.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    state, chat = resolved
    body = await json_object_body(request)
    stop = bool_field(body, "stop", default=False)
    was_running = bool(chat.running)
    after_turn = was_running and not stop
    parked = was_running and stop
    sess, binding = activate(chat, running=was_running, after_turn=after_turn)
    if not after_turn:
        apply_task_mode(state, chat, "plan")
    if parked:
        from personalclaw.dashboard.chat_handlers import stop_the_turn

        await stop_the_turn(state, chat)
    try:
        sel().log_api_access(
            caller="dashboard:chat-plan",
            operation="chat.plan_activate",
            outcome="enabled",
            source="dashboard",
            resources=f"session={chat.key},parked={parked},after_turn={after_turn}",
        )
    except Exception:  # noqa: BLE001
        logger.warning("SEL audit failed for chat plan activation", exc_info=True)
    state.push_sessions_update()
    return web.json_response(
        {
            "ok": True,
            "session": PS.wire(sess),
            "binding": binding,
            "parked": parked,
            "after_turn": after_turn,
            "task_mode": getattr(chat, "_task_mode", "agent") or "agent",
        }
    )


def _agent_refused(request: web.Request, chat: _ChatSession) -> web.Response | None:
    """The refusal of an agent's tool reviewing a chat's plan: the review is your answer to what
    the plan's agent drafted, never that agent's own (`approval_answer`, rule 2). An app reviews in
    a chat it started (its route row), as it approves there."""
    from personalclaw import approval_answer

    by = approval_answer.of_request(request)
    if by.kind != approval_answer.AGENT:
        return None
    why = approval_answer.check(by, what=f"plan:{chat.key}", asked_by="")
    return json_error("approval_owner_only", message=why, status=403)


async def api_chat_plan_edit(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/edit {step_id, markdown} — edit the plan.

    The user finalizes the artifact's markdown body themselves. Routes through
    ``PS.edit_artifact``; the step stays awaiting review (the user still approves).

    The markdown replaces the step's whole body, so the request names the revision of the draft
    it edited in ``If-Match`` (``PS.step_revision``, from the plan-session read): a redraft that
    landed meanwhile is refused with ``409 stale_write`` instead of being overwritten.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    _state, chat = resolved
    refused = _agent_refused(request, chat)
    if refused is not None:
        return refused
    body = await json_object_body(request)
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
    # 🔴 AN EDIT IS SAVED ONLY OVER THE DRAFT IT WAS MADE ON. Commenting sends the step back for a
    # redraft, and the redraft returns to awaiting review — the only thing this route used to
    # check — so an editor opened on the previous draft (another tab, a second window) replaced
    # the new plan with the old one plus the edit. Read, compared and written with nothing awaited
    # in between, so the turn-end redraft cannot land in the gap.
    # A step that is not at the review gate is refused below whatever the edit names.
    step = next((s for s in sess.steps if s.id == step_id), None)
    markdown = str(body["markdown"])
    if step is not None and step.status == StepStatus.AWAITING_REVIEW.value:
        stale = stale_write_refusal(
            request, PS.shown_markdown(step), what=f"the plan step {step.title!r}"
        )
        if stale is not None:
            return stale
        try:
            markdown = PS.keep_masked_markdown(step, markdown)
        except MaskConflict as exc:
            return json_error("mask_conflict", message=str(exc), status=409)
    if not PS.edit_artifact(sess, step_id, markdown):
        return json_error(
            "step_not_awaiting_review", message="That step is not awaiting review", status=409
        )
    write(sess, binding)
    return web.json_response({"ok": True, "session": PS.wire(sess)})


async def api_chat_plan_comment(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/comment {step_id, text} — comment + redraft.

    Routes through ``PS.comment_step`` (awaiting_review → running), then runs the
    re-draft as an ordinary plan-mode turn in the SAME chat.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    state, chat = resolved
    refused = _agent_refused(request, chat)
    if refused is not None:
        return refused
    body = await json_object_body(request)
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
    take_in_the_users_links(request.get("app", ""), chat.key, text)
    _dispatch(state, chat, f"Revise the plan with this feedback:\n\n{text}", own_words=text)
    return web.json_response({"ok": True, "session": PS.wire(sess)})


def _not_approvable(
    request: web.Request,
    state: DashboardState,
    sess: PlanSession,
    binding: dict[str, Any],
    step_id: str,
    scope: str,
    *,
    fresh: bool,
) -> web.Response | None:
    """The refusal of an approval of *step_id* before anything is approved, else ``None``. Read,
    compared and written with nothing awaited, so nothing lands between the check and the yes."""
    from personalclaw.planning import approval_seals

    # 🔴 A STEP IS APPROVED ONLY AS IT WAS SHOWN. A step can change between your read and your
    # press, so the request names the revision of what its review showed you (`approval_seals`).
    step = next((s for s in sess.steps if s.id == step_id), None)
    if step is not None and step.status == StepStatus.AWAITING_REVIEW.value:
        stale = approval_seals.stale_refusal(request, step)
        if stale is not None:
            return stale
    if fresh and any(s.id != step_id and s.status != StepStatus.APPROVED.value for s in sess.steps):
        return json_error(
            "plan_not_complete",
            message="A fresh chat runs the whole plan, so every other step is approved first",
            status=409,
        )
    # A fresh chat is briefed with every step, so every other step is one you approved as it is
    # now: any other waits for your review again first.
    if fresh and approval_seals.reopen_unsealed(scope, sess, but=step_id):
        write(sess, binding)
        approval_seals.forget_dropped(scope, sess)
        state.push_sessions_update()
        return json_error(
            "plan_step_reviewed_again",
            message="A step of this plan is not as you approved it, so it waits for your review",
            status=409,
        )
    return None


async def api_chat_plan_approve(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/approve {step_id, run?} — approve a step (If-Match).

    Routes through ``PS.approve_step``. When that completes the walkthrough
    (``PS.is_complete``) the chat leaves the plan posture: the remembered task mode is
    restored and, if activation had parked a run, the approved plan is dispatched as a
    continuation into the SAME session — the transcript above it is untouched.

    ``run: "fresh"`` (#3821, the gate's "Approve, and run it in a fresh chat") runs the approved
    plan in a new chat instead: same project, working folder, agent, model and privacy (its memory
    mode, never broader than the planning chat's), with the plan's
    markdown as its first and only message, and none of the conversation that produced it. The
    planning chat runs nothing (a run the plan parked there is let go) and says where the plan
    runs. Only an approval that completes the plan may take it: one with a step still waiting
    after it is refused ``plan_not_complete`` and approves nothing.

    The request names the revision of the step it was shown in ``If-Match`` (the row's
    ``review_revision``), and your yes is sealed to it (`approval_seals`). A fresh chat is briefed
    with every step, so one whose other steps are not all as you approved them is refused
    ``plan_step_reviewed_again``, and those steps wait for your review again.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    state, chat = resolved
    # Approving the plan an agent drafted is your answer to it, never that agent's own
    # (`approval_answer`, rule 2). An app approves in a chat it started (its route row); an
    # agent's tool approves in none.
    from personalclaw import approval_answer

    refused = _agent_refused(request, chat)
    if refused is not None:
        return refused
    by = approval_answer.of_request(request)
    body = await json_object_body(request)
    step_id = str(body.get("step_id", "")).strip()
    if not step_id:
        return json_error("step_id_required", message="step_id is required", status=400)
    run_in = str(body.get("run", "") or "here").strip()
    if run_in not in _RUN_IN:
        return json_error(
            "run_invalid", message=f"run must be one of {', '.join(_RUN_IN)}", status=400
        )
    # A fresh chat is a chat of yours, briefed with the plan: an app approves in a chat it
    # started (its route row), and starts none of yours.
    if run_in == _FRESH and by.kind == approval_answer.APP:
        return json_error(
            "fresh_chat_owner_only",
            message="Only you can run an approved plan in a fresh chat",
            status=403,
        )
    from personalclaw.planning import approval_seals

    sess, binding = read(chat.key)
    if sess is None:
        return json_error(
            "plan_session_missing", message="This chat has no plan session", status=404
        )
    scope = approval_seals.chat_scope(chat.key)
    approved = next((s for s in sess.steps if s.id == step_id), None)
    refused = _not_approvable(request, state, sess, binding, step_id, scope, fresh=run_in == _FRESH)
    if refused is not None:
        return refused
    if approved is None or not PS.approve_step(sess, step_id):
        return json_error(
            "step_not_awaiting_review", message="That step is not awaiting review", status=409
        )
    # Only your yes is sealed (an app's approves in its own chat, and is not yours). Sealed before
    # anything runs or is written, so a book that cannot be read (answered by the request
    # boundary) changes nothing.
    if by.kind in approval_answer.ANSWERERS:
        approval_seals.seal(scope, sess, approved)
    markdown = str((approved.artifact or {}).get("markdown", ""))
    complete = PS.is_complete(sess)
    resumed = False
    restored = ""
    fresh = None
    if complete:
        restored = _mode_to_restore(chat.key, binding)
        apply_task_mode(state, chat, restored)
        parked = bool(binding.pop("parked", False))
        if parked:
            binding.pop("parked_at", None)
            binding.pop("parked_messages", None)
        if run_in == _FRESH:
            fresh = _run_in_a_fresh_chat(
                state, chat, sess, scope, app=str(request.get("app", "") or "")
            )
            binding["runs_in"] = fresh.key
        elif parked:
            _dispatch(state, chat, _resume_prompt(markdown), own_words="")
            resumed = True
    write(sess, binding)
    if complete:
        _approvals_end(scope)
    try:
        sel().log_api_access(
            caller="dashboard:chat-plan",
            operation="chat.plan_approve",
            outcome="allowed",
            source="dashboard",
            resources=(
                f"session={chat.key},step={step_id},complete={complete}"
                + (f",runs_in={fresh.key}" if fresh is not None else "")
            ),
        )
    except Exception:  # noqa: BLE001
        logger.warning("SEL audit failed for chat plan approval", exc_info=True)
    state.push_sessions_update()
    return web.json_response(
        {
            "ok": True,
            "session": PS.wire(sess),
            "complete": complete,
            "resumed": resumed,
            "task_mode": restored or (getattr(chat, "_task_mode", "agent") or "agent"),
            "fresh_session": fresh.key if fresh is not None else None,
        }
    )


def _approvals_end(scope: str) -> None:
    """Forget the yeses a completed plan was approved by: its brief is built from them, and nothing
    reads them again (`approval_seals`). A later re-plan of the chat puts its earlier steps to you
    once more before a fresh chat is briefed with them."""
    from personalclaw.planning import approval_seals

    try:
        approval_seals.forget(scope)
    except Exception:  # noqa: BLE001 — the book only grows; the approval stands
        logger.warning("%s: the completed plan's approvals were not forgotten", scope)


#: Where an approved plan runs: here (the planning chat, the default) or in a fresh chat.
_FRESH = "fresh"
_RUN_IN: tuple[str, ...] = ("here", _FRESH)


def _fresh_prompt(sess: PlanSession, scope: str) -> str:
    """A fresh chat's brief: the approved plan, its steps' markdown in order, and nothing of the
    conversation that produced it. Only steps you approved as they are (`approval_seals`): the
    approval refuses a plan with any other, and a step that is not one is left out, never run."""
    from personalclaw.planning import approval_seals

    yours = [s for s in sess.steps if approval_seals.sealed(scope, sess, s)]
    if len(yours) != len(sess.steps):
        logger.warning(
            "chat %s: a plan step not approved as it is was left out of its brief", scope
        )
    parts = [PS.step_markdown(s).strip() for s in yours]
    return (
        "Carry out the plan below. It was reviewed and approved in another chat; "
        "do not re-plan it.\n\n" + "\n\n".join(p for p in parts if p)
    )


def _run_in_a_fresh_chat(
    state: DashboardState, chat: _ChatSession, sess: PlanSession, scope: str, *, app: str
) -> _ChatSession:
    """Start a chat that carries out the approved plan, beside the one that planned it: the same
    project, folder, agent and model, the plan as its first message, one turn dispatched. The
    planning chat gains a line saying where the plan runs (``plan_runs_in``), which its page
    links.

    Never broader than the chat that planned it: the same memory mode (a temporary or incognito
    chat's plan runs in a temporary or incognito chat, which keeps no lasting record and writes no
    memory), the same ephemeral posture and the same mode; and on an ACP provider, the same
    provider agent. What the planning chat was trusted with (its folder trust, its extra tool
    roots) is not carried: the fresh chat asks for each change, as a new chat does.
    """
    fresh = state.get_or_create_session(
        None,
        agent=str(getattr(chat, "agent", "") or ""),
        workspace_dir=str(getattr(chat, "workspace_dir", "") or ""),
        model=str(getattr(chat, "model", "") or ""),
        mode=str(getattr(chat, "mode", "") or ""),
        memory_mode=str(getattr(chat, "memory_mode", "") or "persistent"),
        ephemeral=f"dashboard:{chat.key}" in getattr(state, "_ephemeral_keys", set()),
        created_by_app=app,
        project_id=str(getattr(chat, "project_id", "") or ""),
    )
    if not fresh.workspace_dir:
        fresh.workspace_dir = str(getattr(chat, "workspace_dir", "") or "")
    fresh.acp_provider = str(getattr(chat, "acp_provider", "") or "")
    fresh.acp_provider_agent = str(getattr(chat, "acp_provider_agent", "") or "")
    _dispatch(state, fresh, _fresh_prompt(sess, scope), own_words="")
    chat.append(
        "assistant",
        "The approved plan runs in a fresh chat.",
        "msg msg-a",
        meta={"plan_runs_in": fresh.key},
    )
    return fresh


async def api_chat_plan_cancel(request: web.Request) -> web.Response:
    """POST /api/chat/sessions/{session}/plan/cancel — abandon the walkthrough.

    The counterpart to a manual-only activation: without an exit, a chat that opened a
    plan gate could never leave it (the task-mode control refuses to while a step is
    awaiting review). Restores the remembered task mode, or Ask when none was remembered
    (:func:`_mode_to_restore`), and drops the sidecar. With no plan open it changes nothing.
    """
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    state, chat = resolved
    planning = drafts_now(chat)
    sess, binding = read(chat.key)
    if sess is None and not binding:
        # No plan open: the pill already left plan mode, and a panel drawn before that (here or in
        # another tab) still offered Cancel. Restoring a mode now would overrule the pill's.
        return web.json_response(
            {"ok": True, "task_mode": getattr(chat, "_task_mode", "agent") or "agent"}
        )
    restored = _mode_to_restore(chat.key, binding)
    clear(chat.key)
    if planning:
        await stop_the_planning_turn(state, chat)
    apply_task_mode(state, chat, restored)
    state.push_sessions_update()
    return web.json_response({"ok": True, "task_mode": restored})


def _dispatch(state: DashboardState, chat: _ChatSession, prompt: str, *, own_words: str) -> None:
    """Run one more turn in this chat, appending ``prompt`` as the user message.

    Deliberately the same dispatch shape as the edit-resend path
    (``chat_regenerate.py``): append, then ``asyncio.create_task(run_chat(...))``. The
    import is local (and resolved per call) both to break the import cycle with
    ``chat_runner`` — which calls this module's turn-end hook — and so a test can
    substitute ``chat_runner.run_chat``.

    ``own_words`` are the words of ``prompt`` the user typed (her feedback, or none for the
    plan carried back once approved): the rest is this module's, and no learning path reads it
    as hers (``own_words.own_words``).
    """
    from personalclaw.dashboard.chat_runner import run_chat

    # Taken in the one way a message enters a chat, as the turn it starts now.
    chat.take_in_at_door(state, prompt, own_words=own_words, attended=True, starts=True)
    task = asyncio.create_task(run_chat(state, chat, prompt))
    chat.task = task
    state._background_tasks.add(task)
    task.add_done_callback(state._background_tasks.discard)
