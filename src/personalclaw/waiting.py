"""Waiting on you: one list of everything the owner must answer, from every source (#3888).

Each source kept its own registry, its own shape and its own frame, so every surface read some of
them and missed the rest: the tray counted approvals only, the phone listed approvals only, and
Mission Control made six reads and folded them. This is the one list, served as
``GET /api/waiting``. It adds no authority: every item names the route its own source answers it
through, with that route's guards unchanged, and the list has no answer route of its own.

**One item per thing waiting on you**, ``{id, kind, work, session, brief, consequence, risk,
asked_at, age_secs, expires_at, current, interrupt, answer, route}``:

- ``kind`` is the source: ``approval`` (a tool call, a helper's start and a tool server's question
  among them), ``question`` (an agent's question in a chat), ``loop_question`` (a loop's, a Code
  loop's merge and conflict among them), ``gate`` (a workflow run's), ``park`` (a trigger's parked
  action), ``browse_grant``, ``plan_review`` (a loop's plan step or a plan ready to launch, a
  chat's plan step) and ``bridge_confirmation``.
- ``work`` is the work it belongs to (``chat:``, ``loop:``, ``workflow:``, ``subagent:``,
  ``trigger:``), ``""`` for none; ``session`` the chat it is shown in, ``""`` for work no chat
  started.
- ``brief`` is the source's own words, exactly as its own card shows them: an approval's registry
  row fields, a question's cards, a loop's question and why, a gate's ask and handoff, a park's
  card. Security copy is never reworded here.
- ``consequence`` is what answering it lets happen (:data:`CONSEQUENCES`): ``destructive`` (an
  approval of a call that deletes or runs anything), ``changes`` (an approval, a gate's
  confirmation, a parked action, a merge or conflict, a browser grant, a bridge confirmation), or
  ``asks`` (a question, a loop's question, a gate's input, a plan review). ``risk`` is the source's
  own risk word when it has one (an approval's ``safe``/``caution``/``destructive``), else ``""``.
- ``asked_at`` (ISO, ``None`` when the source keeps no time), ``age_secs``, and ``expires_at``
  (ISO, ``None`` for an ask that waits until it is answered). ``current`` is whether it can still
  be answered: an item past answering is never listed, so it is true when read, and a reader that
  holds an item past its ``expires_at`` disables its action itself.
- ``interrupt`` is whether it may interrupt you now (:func:`interrupt_for`), decided once here from
  mute, quiet hours and its kind's notification rule, so every surface applies one policy: an
  approval's push, its toast and its cue follow it, and so does every reader that would ping.
- ``answer`` is the owner's exact action: ``{method, path, takes, body}``, the source's own route
  and what it takes. ``route`` is the dashboard address that shows it with its card.

**One order:** by consequence, then age, oldest first (#3888's open question 7, settled):
destructive first, then what changes something, then the asks.

**A source that cannot be read is said** in ``unreadable`` (its kind), never as nothing waiting.

**Who reads what** (:class:`Reach`): you read everything. An app reads the list only when its
manifest declares ``/api/waiting`` (the permission middleware refuses it otherwise, as it does
``/api/approvals``), and then reads all of it, as the approval relay reads every approval; an agent
reads its own chat's items. Neither ever answers one (``approval_answer``), so neither is handed
a route to answer it by, nor what answers one: a gate's or a park's resume token and a bridge
confirmation's token are yours alone (:func:`for_reader`).

The gateway's memory is read on its event loop (:func:`taken`), and the stores and files off it
(:func:`read`), so nothing the loop changes is read from another thread.

**The list says it moved** in one ``waiting_changed`` frame per window (:data:`WINDOW_SECS`), a
doorbell carrying nothing (``{}``): a reader reads the list again (:func:`changed`). It rings from
each place an item is asked or ends: an approval asked or answered, a question asked or ended, a
loop's status, question or plan written, a gate's continuation written, claimed or dropped, a
run's status, a park raised, withdrawn or claimed, a browser grant asked or answered, a chat's
plan written or cleared, a bridge confirmation minted or taken. An app's socket hears it only when
its manifest declares ``/api/waiting`` (``ws_state.WAITING_LIST_FRAMES``).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from personalclaw import session_keys
from personalclaw.instants import utc_iso

logger = logging.getLogger(__name__)

#: The clock ages and expiries are read on: the gateway's own, which a test replaces.
_now = time.time

#: The frame that says the list moved, and how long a burst of changes is gathered for: the
#: running list's window (``running_now.WINDOW_SECS``), short enough that an ask reads at once,
#: long enough that a burst is one read of the list.
CHANGED = "waiting_changed"
WINDOW_SECS = 0.15

#: Whether an item may interrupt you now (:func:`interrupt_for`): ``deliver`` it may (a ping, a
#: push, a toast, a cue, the buddy's alert); ``quiet`` and ``hush`` not now, the gate's two answers
#: inside quiet hours (``quiet`` for a kind it records as one you must answer, ``needs_input``;
#: ``hush`` for the rest, an approval among them), and ``quiet`` too under a rule that keeps its
#: kind quiet (a badge, the digest); ``drop`` not at all (mute, the severity floor, a rule that
#: says never). It decides what interrupts, never what is listed: every item is in the list and
#: every count whatever it says. The words are the notification gate's postures
#: (``entity_routes.notification_posture``), carried as they are so a reader can tell not now
#: from not at all.
INTERRUPTS = ("deliver", "quiet", "hush", "drop")

#: The notification kind an item's kind is decided for: an approval's own, ``approval/requested``;
#: every other item asks you as a ``needs_input`` row (``loop/needs_input``).
_NOTIFIED_AS = {"approval": "approval"}
_NEEDS_INPUT = "needs_input"

#: What answering an item lets happen, most consequential first: the list's first order.
CONSEQUENCES = ("destructive", "changes", "asks")

#: Every field an item carries, whatever its kind.
ITEM_FIELDS = (
    "id",
    "kind",
    "work",
    "session",
    "brief",
    "consequence",
    "risk",
    "asked_at",
    "age_secs",
    "expires_at",
    "current",
    "interrupt",
    "answer",
    "route",
)

#: The fields of an approval's registry row its card shows (``ApprovalPrompt``), carried as its
#: brief: the same words the chat card, the nudge and the phone show.
APPROVAL_BRIEF = (
    "tool",
    "tool_input",
    "tool_purpose",
    "source_label",
    "session_title",
    "whose_work",
    "asked_for",
    "agent",
    "reach",
    "blast_radius",
    "deny_effect",
    "is_read_only",
    "confirms",
    "asked_by",
    "trigger_name",
)


def _iso(epoch: float | None) -> str | None:
    return utc_iso(epoch) if epoch else None


def _path(*parts: str) -> str:
    return "/".join(quote(str(p), safe="") if i else str(p) for i, p in enumerate(parts))


def _chat_route(key: str) -> str:
    return f"#/chat/{quote(key, safe='')}" if key else ""


def _work_route(work: str) -> str:
    kind, _, ident = work.partition(":")
    if kind == "loop":
        return f"#/loops/{ident}"
    if kind == "workflow":
        return f"#/workflows/runs/{ident}"
    if kind == "chat":
        return _chat_route(ident)
    if kind == "trigger":
        return f"#/triggers?open={quote(ident, safe='')}"
    return ""


def _answer(method: str, path: str, takes: Iterable[Any], body: Mapping[str, Any] | None = None):
    return {"method": method, "path": path, "takes": list(takes), "body": dict(body or {})}


def _item(
    *,
    kind: str,
    ident: str,
    work: str,
    session: str,
    brief: Mapping[str, Any],
    consequence: str,
    answer: Mapping[str, Any],
    route: str,
    asked_at: float | None,
    expires_at: float | None = None,
    risk: str = "",
    now: float,
) -> dict[str, Any]:
    return {
        "id": f"{kind}:{ident}",
        "kind": kind,
        "work": work,
        "session": session,
        "brief": dict(brief),
        "consequence": consequence,
        "risk": risk,
        "asked_at": _iso(asked_at),
        "age_secs": max(0, int(now - asked_at)) if asked_at else None,
        "expires_at": _iso(expires_at),
        "current": not expires_at or now < expires_at,
        "interrupt": "deliver",
        "answer": dict(answer),
        "route": route,
        "_ts": float(asked_at or 0.0),
    }


# ── may it interrupt you now ─────────────────────────────────────────────────────────────────


def interrupt_for(kind: str) -> str:
    """Whether an item of *kind* may interrupt you now (:data:`INTERRUPTS`): the notification
    gate's posture for the kind it is notified as (mute, the severity floor, quiet hours), then
    that kind's rule, on the gateway's clock (:data:`_now`). A rule that says never drops it, and
    one that delivers quietly (a badge, the digest) keeps it from pinging. Never raises: a gate
    that cannot be read delivers, as ``DashboardState.notify`` does, since a ping too many is
    recoverable and an ask nobody heard is not."""
    from datetime import datetime

    from personalclaw import notification_rules
    from personalclaw.providers import entity_routes

    notified = _NOTIFIED_AS.get(kind, _NEEDS_INPUT)
    try:
        posture = entity_routes.notification_posture(notified, now=datetime.fromtimestamp(_now()))
        if posture == entity_routes.POSTURE_DROP:
            return posture
        mode = notification_rules.resolve_rule_for_legacy(notified).mode
    except Exception:  # noqa: BLE001 — undecidable reads as may, as the gate itself reads it
        logger.debug("waiting list: could not decide whether %s may interrupt", kind, exc_info=True)
        return entity_routes.POSTURE_DELIVER
    if mode == "never":
        return entity_routes.POSTURE_DROP
    if mode != "immediate" and posture == entity_routes.POSTURE_DELIVER:
        return entity_routes.POSTURE_QUIET
    return posture


# ── 1. approvals ─────────────────────────────────────────────────────────────────────────────


def _approvals(entries: list[dict[str, Any]], window: float, now: float) -> list[dict[str, Any]]:
    from personalclaw import running_now, work_phases

    out = []
    for entry in entries:
        approval_id = str(entry.get("id") or "")
        if not approval_id:
            continue
        raw = str(entry.get("session") or "")
        works = running_now.approval_work(entry)
        work = works[0] if works else str(entry.get("work") or "")
        risk = str(entry.get("risk") or "")
        ts = float(entry.get("ts") or 0.0) or None
        out.append(
            _item(
                kind="approval",
                ident=approval_id,
                work=work,
                session=work_phases.chat_of(raw),
                brief={k: entry.get(k) for k in APPROVAL_BRIEF if k in entry},
                consequence="destructive" if risk == "destructive" else "changes",
                risk=risk,
                answer=_answer(
                    "POST",
                    _path("/api/approvals", approval_id) + "/{answer}",
                    ("approve", "reject"),
                ),
                route=_work_route(str(entry.get("work") or "")) or _chat_route(raw),
                asked_at=ts,
                expires_at=(ts + window) if ts and window > 0 else None,
                now=now,
            )
        )
    return out


# ── 2. an agent's questions ──────────────────────────────────────────────────────────────────


def _questions(cards: list[dict[str, Any]], window: float, now: float) -> list[dict[str, Any]]:
    from personalclaw.approval_place import place_of

    out = []
    for card in cards:
        qid, chat = str(card.get("id") or ""), str(card.get("session") or "")
        if not qid or not chat:
            continue
        ts = float(card.get("ts") or 0.0) or None
        out.append(
            _item(
                kind="question",
                ident=qid,
                work=place_of(chat).get("work") or f"chat:{chat}",
                session=chat,
                brief={"questions": card.get("questions") or [], "asked_by": card.get("asked_by")},
                consequence="asks",
                answer=_answer(
                    "POST",
                    _path("/api/chat/sessions", chat, "questions", qid, "answer"),
                    ("answers", "skip"),
                ),
                route=_chat_route(chat),
                asked_at=ts,
                expires_at=(ts + window) if ts and window > 0 else None,
                now=now,
            )
        )
    return out


# ── 3. a loop's question, and 7. a loop's plan ───────────────────────────────────────────────


def _loop_route(loop: Any) -> str:
    return f"#/code/{loop.id}" if getattr(loop, "kind", "") == "code" else f"#/loops/{loop.id}"


def _loop_questions(loops: list[Any], now: float) -> list[dict[str, Any]]:
    from personalclaw.loop import files as loop_files

    out = []
    for loop in loops:
        if str(getattr(loop.status, "value", loop.status)) != "needs_input":
            continue
        q = loop_files.pending_question(loop.id) or {}
        brief: dict[str, Any] = {"name": loop.name, "question": q.get("question") or ""}
        for key in ("why", "asked_by", "merge", "conflict", "spend_cap", "declined", "no_tools"):
            if key in q:
                brief[key] = q[key]
        base = _path("/api/loops", loop.id)
        if "merge" in q:
            consequence, answer = "changes", _answer("POST", f"{base}/merge", ("confirm",))
        elif "conflict" in q:
            consequence, answer = "changes", _answer("POST", f"{base}/conflict", ("redo", "drop"))
        else:
            consequence, answer = "asks", _answer("POST", f"{base}/nudge", ("text",))
        out.append(
            _item(
                kind="loop_question",
                ident=loop.id,
                work=f"loop:{loop.id}",
                session="",
                brief=brief,
                consequence=consequence,
                answer=answer,
                route=_loop_route(loop),
                asked_at=float(q.get("ts") or 0.0) or None,
                now=now,
            )
        )
    return out


def _loop_plans(loops: list[Any], now: float) -> list[dict[str, Any]]:
    from personalclaw.loop import files as loop_files
    from personalclaw.planning import session as plans

    out = []
    for loop in loops:
        status = str(getattr(loop.status, "value", loop.status))
        base = _path("/api/loops", loop.id)
        if status == "review":
            # The plan is ready and waits for her launch.
            out.append(
                _item(
                    kind="plan_review",
                    ident=f"loop:{loop.id}",
                    work=f"loop:{loop.id}",
                    session="",
                    brief={"name": loop.name, "task": getattr(loop, "task", "")},
                    consequence="asks",
                    answer=_answer("PATCH", base, ("start",), {"action": "start"}),
                    route=_loop_route(loop),
                    asked_at=None,
                    now=now,
                )
            )
            continue
        if status != "planning":
            continue
        sess = loop_files.read_plan_session(loop.id)
        step = plans.current_step(sess) if sess is not None else None
        if step is None or step.status != plans.StepStatus.AWAITING_REVIEW.value:
            continue
        out.append(
            _item(
                kind="plan_review",
                ident=f"loop:{loop.id}:{step.id}",
                work=f"loop:{loop.id}",
                session="",
                brief={
                    "name": loop.name,
                    "step": {
                        "id": step.id,
                        "kind": step.kind,
                        "title": step.title,
                        "objective": step.objective,
                    },
                },
                consequence="asks",
                answer=_answer(
                    "POST", f"{base}/plan/approve", ("approve", "comment"), {"step_id": step.id}
                ),
                route=_loop_route(loop),
                asked_at=float(getattr(sess, "updated_at", 0.0) or 0.0) or None,
                now=now,
            )
        )
    return out


def _chat_plans(now: float) -> list[dict[str, Any]]:
    import json

    from personalclaw.config.loader import config_dir
    from personalclaw.planning import session as plans

    out = []
    folder = config_dir() / plans.CHAT_PLANS_DIR
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            raw = json.loads(f.read_text())
            sess = plans.PlanSession.from_dict(raw["session"])
        except Exception:  # noqa: BLE001 — a damaged sidecar reads as no plan, as chat_plan.read
            continue
        chat = str(sess.project_id or "")
        step = plans.current_step(sess)
        if not chat or step is None or step.status != plans.StepStatus.AWAITING_REVIEW.value:
            continue
        out.append(
            _item(
                kind="plan_review",
                ident=f"chat:{chat}:{step.id}",
                work=f"chat:{chat}",
                session=chat,
                brief={"step": {"id": step.id, "title": step.title, "objective": step.objective}},
                consequence="asks",
                answer=_answer(
                    "POST",
                    _path("/api/chat/sessions", chat, "plan", "approve"),
                    ("approve", "comment"),
                    {"step_id": step.id},
                ),
                route=_chat_route(chat),
                asked_at=float(sess.updated_at or 0.0) or None,
                now=now,
            )
        )
    return out


# ── 4. a workflow run's gate ─────────────────────────────────────────────────────────────────


_RUNS_PAGE = 200


def _gates(now: float) -> list[dict[str, Any]]:
    from personalclaw.workflows import human_input
    from personalclaw.workflows import store as runs
    from personalclaw.workflows.models import RunStatus

    out = []
    offset = 0
    while True:
        page, total = runs.list_runs(status=RunStatus.NEEDS_INPUT, limit=_RUNS_PAGE, offset=offset)
        for run in page:
            conts = [c for c in human_input.list_continuations(run.id) if not c.expired]
            if not conts:
                continue
            try:
                instances = runs.read_state(run.id)
            except Exception:  # noqa: BLE001 — the gate's own deadline is unknown, not absent
                instances = {}
            for cont in conts:
                inst = instances.get(cont.instance_path)
                wake = float(getattr(inst, "wake_at", 0.0) or 0.0)
                deadline = (
                    min(x for x in (wake, cont.expires_at) if x)
                    if (wake or cont.expires_at)
                    else None
                )
                kind = str(cont.ask.get("kind") or "approval")
                base = _path("/api/workflows/runs", run.id)
                token = {"resume_token": cont.token}
                if cont.confirmation_id:
                    answer = _answer(
                        "POST", f"{base}/confirm", ("approve", "reject", "skip", "quit"), token
                    )
                else:
                    answer = _answer("POST", f"{base}/resume", (kind,), token)
                root = str(getattr(run, "root_run_id", "") or run.id)
                out.append(
                    _item(
                        kind="gate",
                        ident=f"{run.id}:{cont.confirmation_id or cont.node_id}:{cont.epoch}",
                        work=f"workflow:{root}",
                        session="",
                        brief={
                            "workflow": run.workflow_name,
                            "node_id": cont.node_id,
                            "ask": dict(cont.ask),
                            "handoff": dict(cont.handoff),
                        },
                        consequence="changes" if kind == "approval" else "asks",
                        answer=answer,
                        route=f"#/workflows/runs/{run.id}",
                        asked_at=cont.created_at or None,
                        expires_at=deadline,
                        now=now,
                    )
                )
        offset += len(page)
        if not page or offset >= total:
            return out


# ── 5. a trigger's parked action ─────────────────────────────────────────────────────────────


def _parks(now: float) -> list[dict[str, Any]]:
    import json

    from personalclaw.triggers import parks
    from personalclaw.triggers.store import trigger_name

    out = []
    folder = parks.parks_dir()
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            park = parks.TriggerPark.from_dict(json.loads(f.read_text()))
        except Exception:  # noqa: BLE001 — a damaged park reads as none, as parks.load does
            continue
        if not park.trigger_id or parks.expired(park, now=now):
            continue
        out.append(
            _item(
                kind="park",
                ident=park.trigger_id,
                work=f"trigger:{park.trigger_id}",
                session="",
                brief={
                    "trigger_name": trigger_name(park.trigger_id),
                    "question": park.question,
                    "card": dict(park.card),
                },
                consequence="changes",
                answer=_answer(
                    "POST",
                    _path("/api/triggers", park.trigger_id, "answer"),
                    (True, False),
                    {"resume_token": park.token},
                ),
                route=_work_route(f"trigger:{park.trigger_id}"),
                asked_at=park.created_at or None,
                expires_at=park.created_at + parks.answerable_secs() if park.created_at else None,
                now=now,
            )
        )
    return out


# ── 6. a browser grant, and 8. a bridge confirmation ─────────────────────────────────────────


def _browse_grants(grants: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    out = []
    for grant in grants:
        rid = str(grant.get("request_id") or "")
        if not rid:
            continue
        asked = float(grant.get("requested_at") or 0.0) or None
        timeout = float(grant.get("timeout") or 0.0)
        out.append(
            _item(
                kind="browse_grant",
                ident=rid,
                work="",
                session="",
                brief={k: grant.get(k) for k in ("task", "scope", "group")},
                consequence="changes",
                answer=_answer(
                    "POST", _path("/api/browse/grants", rid) + "/{answer}", ("approve", "reject")
                ),
                route="#/dashboard",
                asked_at=asked,
                expires_at=(asked + timeout) if asked and timeout > 0 else None,
                now=now,
            )
        )
    return out


def _confirmation_ref(token: str) -> str:
    """A bridge confirmation's id in the list: its token answers it, so the list names it by a
    digest of it instead."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _bridge_confirmations(pending: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    out = []
    for row in pending:
        token = str(row.get("token") or "")
        if not token:
            continue
        out.append(
            _item(
                kind="bridge_confirmation",
                ident=_confirmation_ref(token),
                work="",
                session="",
                brief={k: row.get(k) for k in ("action", "body", "asked_by")},
                consequence="changes",
                answer=_answer(
                    "POST",
                    _path("/api/external-access/bridge/confirmations", token),
                    (True, False),
                ),
                route="#/inbox",
                asked_at=row.get("asked_at") or None,
                expires_at=row.get("expires_at") or None,
                now=now,
            )
        )
    return out


# ── the list ─────────────────────────────────────────────────────────────────────────────────


@dataclass
class Snapshot:
    """What the gateway's memory held when the list was read: taken on its event loop."""

    approvals: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    grants: list[dict[str, Any]] = field(default_factory=list)
    confirmations: list[dict[str, Any]] = field(default_factory=list)
    window: float = 0.0
    unreadable: list[str] = field(default_factory=list)


def _taken(kind: str, read: Callable[[], Any], unreadable: list[str], default: Any) -> Any:
    try:
        return read()
    except Exception:  # noqa: BLE001 — a source that cannot be read is said, never empty
        logger.warning("waiting list: %s could not be read", kind, exc_info=True)
        unreadable.append(kind)
        return default


def taken(state: Any) -> Snapshot:
    """The gateway's memory as the list reads it. Call on its event loop."""
    from personalclaw.browse import grant
    from personalclaw.inbound import bridge

    snap = Snapshot()
    snap.window = _taken(
        "approval", lambda: float(state.approval_window_secs()), snap.unreadable, 0.0
    )
    snap.approvals = _taken(
        "approval",
        lambda: [dict(e) for e in getattr(state, "_pending_approvals", {}).values()],
        snap.unreadable,
        [],
    )
    snap.questions = _taken(
        "question", lambda: list(state.owner_questions.pending()), snap.unreadable, []
    )
    snap.grants = _taken("browse_grant", grant.pending_grants, snap.unreadable, [])
    snap.confirmations = _taken(
        "bridge_confirmation", bridge.pending_confirmations, snap.unreadable, []
    )
    return snap


def _order(item: Mapping[str, Any]) -> tuple[int, int, float]:
    rank = CONSEQUENCES.index(item["consequence"]) if item["consequence"] in CONSEQUENCES else 1
    ts = float(item.get("_ts") or 0.0)
    return (rank, 0 if ts else 1, ts)


def read(snap: Snapshot) -> tuple[list[dict[str, Any]], list[str]]:
    """Every item waiting on you, in the list's order, and the kinds that could not be read. Call
    off the event loop: it reads the loop and run stores and their files."""
    from personalclaw.loop import store as loops

    now = _now()
    unreadable = list(dict.fromkeys(snap.unreadable))
    items: list[dict[str, Any]] = []
    items += _approvals(snap.approvals, snap.window, now)
    items += _questions(snap.questions, snap.window, now)
    all_loops = _taken("loop_question", loops.list_all, unreadable, None)
    if all_loops is not None:
        items += _taken("loop_question", lambda: _loop_questions(all_loops, now), unreadable, [])
        items += _taken("plan_review", lambda: _loop_plans(all_loops, now), unreadable, [])
    else:
        unreadable.append("plan_review")
    items += _taken("plan_review", lambda: _chat_plans(now), unreadable, [])
    items += _taken("gate", lambda: _gates(now), unreadable, [])
    items += _taken("park", lambda: _parks(now), unreadable, [])
    items += _browse_grants(snap.grants, now)
    items += _bridge_confirmations(snap.confirmations, now)
    live = [i for i in items if i["current"]]
    live.sort(key=_order)
    decided: dict[str, str] = {}
    for i in live:
        i.pop("_ts", None)
        notified = _NOTIFIED_AS.get(i["kind"], _NEEDS_INPUT)
        if notified not in decided:
            decided[notified] = interrupt_for(i["kind"])
        i["interrupt"] = decided[notified]
    return live, list(dict.fromkeys(unreadable))


def chats_waiting(snap: Snapshot) -> set[str]:
    """The chats something waiting on you is shown in (each item's ``session``): an approval, a
    helper's among them (shown in the chat that started it), a question and a chat's plan step.
    The other kinds belong to work no chat started. Call off the event loop: a chat's plan is a
    file."""
    now = _now()
    items = _approvals(snap.approvals, snap.window, now) + _questions(
        snap.questions, snap.window, now
    )
    items += _taken("plan_review", lambda: _chat_plans(now), [], [])
    return {str(i["session"]) for i in items if i["session"] and i["current"]}


# ── who reads what ───────────────────────────────────────────────────────────────────────────


@dataclass
class Reach:
    """Who reads the list: you, all of it with what answers each item (:attr:`everyone`); an app
    whose manifest declares the route, all of it with nothing that answers one (:attr:`app`); an
    agent, its own chat's items (:attr:`chat`); anyone else, nothing."""

    everyone: bool = False
    app: str = ""
    chat: str = ""

    def sees(self, item: Mapping[str, Any]) -> bool:
        if self.everyone or self.app:
            return True
        chat = self.chat.removeprefix(session_keys.DASHBOARD.prefix)
        return bool(chat) and str(item.get("session") or "") == chat


def for_reader(reach: Reach, item: dict[str, Any]) -> dict[str, Any]:
    """*item* as *reach* reads it: yours whole; anyone else's with its choices and no route to send
    them to, nor what answers it (a resume token, a bridge confirmation's token), since nobody
    else ever answers one: the list hands a reader nothing its source's door would refuse it."""
    if reach.everyone:
        return item
    answer = {"method": "", "path": "", "takes": list(item["answer"]["takes"]), "body": {}}
    return {**item, "answer": answer}


def reach_of(request: Any, state: Any) -> Reach:
    """Who *request* reads as, from the principal it proved (``approval_answer.of_request``). Call
    off the event loop: an agent's chat is walked (``subagent_reach``)."""
    from personalclaw import approval_answer
    from personalclaw.subagent_reach import reader_of

    by = approval_answer.of_request(request)
    if by.kind == approval_answer.APP:
        return Reach(app=by.name)
    if by.kind not in (approval_answer.OWNER, approval_answer.AGENT):
        return Reach()
    reader = reader_of(request, state)
    if reader.everyone:
        return Reach(everyone=True)
    return Reach(chat=reader.chat)


# ── the list moved ───────────────────────────────────────────────────────────────────────────


@dataclass
class _Bell:
    """The ring due at the end of this window, and the event loop it is due on."""

    handle: asyncio.TimerHandle | None = None
    loop: asyncio.AbstractEventLoop | None = None


def changed(state: Any) -> None:
    """Something waiting on you was asked or ended: the list rings once its window closes. Called
    from any thread: off the event loop, the change is handed to it. Never raises: an ask or an
    answer must not fail for its ring."""
    try:
        _changed(state)
    except Exception:  # noqa: BLE001 — a lost ring leaves the readers' slow poll, never a failure
        logger.debug("waiting list: could not ring", exc_info=True)


def _changed(state: Any) -> None:
    if state is None:
        return
    home = getattr(state, "_ws_loop", None)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if home is not None and loop is not home:
        # Off the gateway's loop (a thread, or a loop of its own): the change is handed to it.
        if not home.is_closed():
            home.call_soon_threadsafe(changed, state)
        return
    if loop is None:
        return
    bell = getattr(state, "_waiting_bell", None)
    if not isinstance(bell, _Bell):
        bell = _Bell()
        state._waiting_bell = bell
    if bell.handle is not None and (bell.loop is None or bell.loop.is_closed()):
        bell.handle = None  # due on a loop that is gone: it will never ring
    if bell.handle is None:
        bell.loop = loop
        bell.handle = _call_later(loop, WINDOW_SECS, _ring, state)


def _call_later(loop: asyncio.AbstractEventLoop, delay: float, callback: Any, *args: Any) -> Any:
    """The window's timer: the event loop's, which a test replaces to close a window by hand."""
    return loop.call_later(delay, callback, *args)


def _ring(state: Any) -> None:
    state._waiting_bell.handle = None
    state.broadcast_ws(CHANGED, {})


def changed_elsewhere() -> None:
    """Something waiting on you was asked or ended where no dashboard state is to hand (a loop's
    files, a run's gate, a park, a browser grant, a bridge confirmation): the gateway's list
    rings, when this process is the gateway's."""
    try:
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        state = get_dashboard_state()
    except Exception:  # noqa: BLE001 — as changed(): never a failure for a ring
        return
    if state is not None:
        changed(state)
