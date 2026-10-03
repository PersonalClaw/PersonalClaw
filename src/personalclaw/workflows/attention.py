"""Projecting a waiting run into the platform's attention surfaces.

A run that parks on `needs_input` is the one engine state whose resolution requires a human.
Until this module existed the only place that fact appeared was an SSE frame — so if nobody
happened to have the run view open, an approval gate waited silently forever. A scheduled run
firing at 3am would park and simply never be mentioned.

That is the gap this closes. A gate raises a **durable inbox item** plus **one** notification,
through `emit_attention_item` — the same seam the loop watchdog uses, for the same reason: a
caller that did `store.add(...)` and `state.notify(...)` separately drifts the two apart, and
the usual result is two notifications for one event or an inbox row nobody was told about.

Three properties this has to get right:

**Deduped per (run, node, epoch).** The watchdog re-polls a waiting run every few seconds and
`gate_answers.ensure_continuation` is idempotent per epoch — so without a dedup key a gate would
stack a row per poll, each with a valid resume token. Keyed on the EPOCH rather than the token
because a rewind legitimately re-asks the same question, and that genuinely is a new ask.

**Resolved when answered.** An inbox row that stays open after its gate is answered is worse
than no row: the user clicks it, finds nothing to do, and stops trusting the inbox. The
resolution runs off the same `gate_resolved` moment the widget uses.

**Never load-bearing.** Every function here is best-effort and swallows. A run must not fail
because the inbox could not be written — the gate is what the user is waiting on, and losing
the run to a bookkeeping error is strictly worse than losing the row.
"""

from __future__ import annotations

import logging
from typing import Any

from personalclaw.workflows import ending_sentence, needs_input, ownership

logger = logging.getLogger(__name__)

#: The registered notification pair. `loop/needs_input` already exists, carries
#: `attention=True`, and is what a user's "always interrupt me for needs_input" rule keys on —
#: so a workflow gate rides the SAME pair rather than inventing a second one a user would have
#: to configure separately to get the same behaviour.
SOURCE = "loop"
KIND = "needs_input"


def dedup_key(run_id: str, instance_path: str, epoch: int) -> str:
    """The idempotency key for one gate's attention item.

    Epoch-scoped, not token-scoped: a rewind re-asks the same question at a NEW epoch, and
    that is a genuinely new ask deserving its own row. Two polls of the same waiting gate are
    not.
    """
    return f"workflow:{run_id}:{instance_path}:{epoch}"


def ask_title(workflow: str, node_id: str, ask: dict[str, Any] | None) -> str:
    """The row's one-line summary.

    The ask's own prompt when it has one — that is the actual question, and a generic
    "workflow needs input" forces the user to open the row to learn anything. Truncated,
    because an inbox row is a glance and a model-authored prompt can be a paragraph.
    """
    prompt = str((ask or {}).get("prompt") or "").strip()
    if prompt:
        return prompt if len(prompt) <= 120 else prompt[:117] + "…"
    label = node_id or "a step"
    return f"{workflow}: {label} needs your input"


def ask_body(ask: dict[str, Any] | None, handoff: dict[str, Any] | None) -> str:
    """The row's detail: what kind of answer is wanted, and what the run was doing.

    What the asking step already tried comes next, when it says: a step that parked on a
    sign-in page names the site and why it stopped ("opened example.com — it has never been
    signed in on this machine"), which is the reason the user is being asked at all.

    The handoff's outstanding work is included because the decision often depends on it — "is
    this the last step or are eight more waiting on me?" changes how urgently a user acts.
    """
    parts: list[str] = []
    kind = str((ask or {}).get("kind") or "").strip()
    if kind:
        parts.append(
            {
                "approval": "Waiting for your approval.",
                "choice": "Waiting for you to choose an option.",
                "text": "Waiting for a written answer.",
                "form": "Waiting for you to fill in a form.",
                # Not a question: nobody has to answer it, and you can wake it early.
                "event": "Parked until something wakes it. You can wake it now.",
            }.get(kind, f"Waiting for a {kind} answer.")
        )
    attempted = [str(a) for a in ((handoff or {}).get("attempted") or []) if str(a).strip()]
    if attempted:
        parts.append(f"Tried: {'; '.join(attempted)}.")
    outstanding = (handoff or {}).get("outstanding")
    if isinstance(outstanding, list) and outstanding:
        parts.append(f"{len(outstanding)} other step(s) still pending.")
    return " ".join(parts)


def raise_gate_item(
    state: Any,
    *,
    run_id: str,
    workflow: str,
    node_id: str,
    instance_path: str,
    epoch: int,
    resume_token: str,
    ask: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
    #: The structured NeedsInputItem inputs. Optional, so a caller that has none still raises
    #: the row it raises today — a required argument here would have made the whole existing gate
    #: path a breaking change for a payload most callers cannot yet supply.
    failure: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    owner: str = "",
    project_id: str = "",
    now: float = 0.0,
    card: dict[str, Any] | None = None,
) -> str:
    """Project one waiting gate into the inbox + a notification. Returns the item id or "".

    `refs` carries the run id AND the resume token, which is what makes the row actionable
    rather than a notification with extra steps: the surface reading it can answer in place
    instead of sending the user off to find the run.

    `card` is a `NeedsInputItem` the waiting step composed itself — browse's sign-in handoff
    (`browse.handoff.request_login`) builds one with its blocker, what it tried and its evidence.
    It is carried as its producer wrote it rather than re-derived from the ask, re-bound to THIS
    run's node and token, and its choices are the ask's: an approval continuation takes yes or no,
    so a labelled choice ("I have signed in") offered as a button would be an answer the resume
    path refuses.
    """
    if state is None:
        return ""
    try:
        from personalclaw.inbox import ItemKind, emit_attention_item

        if card:
            from dataclasses import replace

            parsed = needs_input.NeedsInputItem.from_dict(card)
            item = replace(
                parsed,
                run_id=run_id,
                node_id=node_id,
                resume_token=resume_token,
                choices=[str(c) for c in ((ask or {}).get("choices") or [])],
                owner=owner or parsed.owner,
                project_id=project_id or parsed.project_id,
                created_at=now or parsed.created_at,
            )
        else:
            item = needs_input.build_item(
                run_id=run_id,
                node_id=node_id,
                ask=ask,
                failure=failure,
                attempts=attempts,
                evidence=evidence,
                resume_token=resume_token,
                owner=owner,
                project_id=project_id,
                now=now,
            )
        return emit_attention_item(
            state,
            source=SOURCE,
            kind=KIND,
            item_kind=ItemKind.NEEDS_INPUT.value,
            title=ask_title(workflow, node_id, ask),
            body=ask_body(ask, handoff),
            # The structured card rides the EXISTING free-form `refs` dict. The inbox is a
            # general attention store shared with channel messages, so widening `InboxItem`'s schema
            # for one item kind would make every other kind carry empty workflow fields. Today's
            # keys are preserved verbatim so a surface written against them keeps working.
            refs={
                "workflow": run_id,
                "workflow_name": workflow,
                "workflow_node": node_id,
                "resume_token": resume_token,
                **needs_input.card_refs(item),
            },
            dedup_key=dedup_key(run_id, instance_path, epoch),
        )
    except Exception:
        # Best-effort by contract: the gate is what the user is waiting on, and losing the run
        # to a bookkeeping failure is strictly worse than losing the row.
        logger.debug("workflow %s: could not raise the gate attention item", run_id, exc_info=True)
        return ""


def resolve_gate_item(state: Any, run_id: str, node_id: str = "") -> int:
    """Close the open inbox row(s) for an answered (or expired) gate. Returns the count.

    Called on the same `gate_resolved` moment the widget folds. An inbox row that outlives its
    gate is worse than no row at all — the user opens it, finds nothing to do, and learns to
    distrust the surface.

    Scoped by node when one is named: a run with two concurrent gates has two rows, and
    answering one must not close the other. A run ENDING closes what it left open without an
    answer (:func:`expire_run_items`).

    The WORKFLOW ref vocabulary; the resolve itself is `inbox.resolve_attention_items`, beside
    the emitter. It was a private copy of that loop here, which is why the loop watchdog — the
    other emitter, and one this module's own docstring names — had no resolver at all: the
    mechanism was reachable only through a function that hard-coded `refs["workflow"]`, so
    calling it for a loop closed nothing (measured: exactly 0, #335). One implementation, one
    open-status vocabulary, and each caller supplies the refs it stamped.
    """
    from personalclaw.inbox import resolve_attention_items

    refs = {"workflow": run_id}
    if node_id:
        refs["workflow_node"] = node_id
    return resolve_attention_items(state, refs)


def raise_declined_wait(state: Any, run: Any, *, cycle: int, said: str) -> str:
    """One Inbox item, and its one notification, for a run that waits for its owner because she
    declined a call cycle *cycle* of its loop made (`declines.end_cycle`): titled as a loops-table
    loop's wait after her Deny is, its body the run's name and the sentence *said*, deep-linked to
    the run (and, for a run started as a loop, to the loop). Closed as handled when she resumes it
    (:func:`resolve_declined_wait`), and with the run's other rows when it ends
    (:func:`expire_run_items`). Best-effort like everything here. Returns the item id or ""."""
    if state is None:
        return ""
    try:
        from personalclaw.inbox import ItemKind, emit_attention_item

        name = str(getattr(run, "title", "") or getattr(run, "workflow_name", "") or run.id)
        loop_kind = str(getattr(run, "loop_kind", "") or "")
        refs = {"workflow": run.id, "declined_wait": str(cycle)}
        if loop_kind:
            refs.update({"loop": run.id, "loop_kind": loop_kind})
        return emit_attention_item(
            state,
            source=SOURCE,
            kind=KIND,
            item_kind=ItemKind.NEEDS_INPUT.value,
            title=f"{'Loop' if loop_kind else 'Run'} waiting — you declined one of its steps",
            body=f"{name} — {said}",
            refs=refs,
            dedup_key=f"workflow-run:{run.id}:declined:{cycle}",
        )
    except Exception:
        logger.debug("workflow %s: could not raise its wait after a Deny", run.id, exc_info=True)
    return ""


def resolve_declined_wait(state: Any, run_id: str, *, cycle: int) -> int:
    """Close the Inbox item a run's wait after its owner's Deny raised, now that she resumed it."""
    if state is None:
        return 0
    from personalclaw.inbox import resolve_attention_items

    return resolve_attention_items(state, {"workflow": run_id, "declined_wait": str(cycle)})


def expire_run_items(state: Any, run_id: str, *, ended: str) -> int:
    """Close every open attention row for a run that has ended. Returns how many were closed.

    A run that failed, was cancelled or completed ends its own outstanding questions: nothing
    about it is actionable any more. Without this, cancelling a run mid-gate would leave a
    permanently unanswerable row in the inbox — the exact dead-row problem this module exists to
    avoid, arrived by a different path. Nobody answered them, so they EXPIRE, saying why
    (*ended*: "the workflow run was cancelled"): an answered gate closed its own row as handled
    when it was answered (:func:`resolve_gate_item`), and a row the run's end closed read
    "Handled" for a question nobody had answered.
    """
    from personalclaw.inbox import expire_attention_items

    return expire_attention_items(state, {"workflow": run_id}, ended=ended)


def announce_run_end(state: Any, run: Any, status: Any, *, refused: bool = False) -> str:
    """Tell the user a run has ended, when the ending is one they need to hear.

    A run started AS A LOOP announces its end as a loops-table loop always has
    (:func:`_announce_loop_end`). Any other workflow run tells the user when it ended needing them
    — it failed, or it escalated and stopped before it finished (:func:`_announce_workflow_end`) —
    and says nothing when it completed, was cancelled or was declined. ``refused`` says a spend
    ceiling refused the model call a failed step needed: then the ending needs its owner (lift the
    cap or wait for it to reset), so a loop's raises an Inbox item too. Returns the item id or "".
    """
    if state is None:
        return ""
    if getattr(run, "loop_kind", ""):
        return _announce_loop_end(state, run, status, refused=refused)
    return _announce_workflow_end(state, run, status)


#: How a workflow run's ending that needs the user reads as its Inbox row's title.
_WORKFLOW_ENDINGS: dict[str, str] = {
    "failed": "Workflow run failed",
    "escalated": "Workflow run stopped before it finished",
}


def _announce_workflow_end(state: Any, run: Any, status: Any) -> str:
    """A workflow run that ended failed or escalated: one Inbox row and its one notification.

    🔴 A run started from the Workflows page ended ``escalated`` — "The run continued past
    “investigate”, which escalated." — and no bell entry, Inbox item or channel message followed:
    only a run started as a loop, and a run a trigger started (on the trigger's own route), ever
    said how they ended. The two endings are the ones the run page offers Retry for, so each is a
    decision that is the user's now, and it is raised the way a loop's escalation is: a durable
    row that deep-links to the run (``refs.workflow``), with its one notification through the
    user's rule for "a run needs you" (``loop/needs_input``, the pair a workflow gate rides).

    Said once, by whoever ends up telling the user: a run a TRIGGER started is reported on that
    trigger's route (``run_finish.report_to_its_trigger``), a subagent batch in the chat that
    started it (``run_finish.report_to_its_chat``), and a sub-run's ending is its parent's step, so
    none of them raises a second note here. Deduped per run and ending.
    """
    from personalclaw.workflows.batch_start import reports_to_a_chat
    from personalclaw.workflows.models import OriginKind

    ending = str(getattr(status, "value", status))
    title = _WORKFLOW_ENDINGS.get(ending, "")
    if not title:
        return ""
    origin = getattr(run, "origin", None)
    if getattr(origin, "kind", None) == OriginKind.HOOK and getattr(origin, "trigger_id", ""):
        return ""
    if getattr(run, "parent_run_id", None) or reports_to_a_chat(run):
        return ""
    try:
        from personalclaw.inbox import ItemKind, emit_attention_item

        name = str(getattr(run, "title", "") or getattr(run, "workflow_name", "") or run.id)
        attention = getattr(run, "attention", None) or {}
        reason = str(
            getattr(run, "error_message", "")
            or attention.get("detail")
            or attention.get("reason")
            or ""
        ).strip()
        return emit_attention_item(
            state,
            source=SOURCE,
            kind=KIND,
            item_kind=ItemKind.NEEDS_INPUT.value,
            title=title,
            body=f"{name} — {reason}" if reason else name,
            refs={"workflow": run.id},
            dedup_key=f"workflow-run:{run.id}:{ending}",
        )
    except Exception:
        logger.debug("workflow %s: could not announce the run's end", run.id, exc_info=True)
    return ""


#: How an escalated loop's Inbox row is titled, by its escalation's cause. A cause with no title
#: of its own — a step that failed at its work, refused tools, a start nobody approved — is a
#: loop that stopped before it finished, and its body says why.
_LOOP_STOP_TITLE = {
    ending_sentence.BUDGET: "Loop stopped at its budget",
    ending_sentence.JUDGE: "Loop stopped: the judge could not decide",
    ending_sentence.APPROVAL_TIMEOUT: "Loop stopped: an ask ran out of time",
    ending_sentence.SPEND_CAP: "Loop stopped: a spend cap refused its next call",
}


def _announce_loop_end(state: Any, run: Any, status: Any, *, refused: bool = False) -> str:
    """Tell the user a run started AS A LOOP has ended, as a loops-table loop always has.

    🔴 A General loop is a workflow run (PP-16), and the workflow engine told nobody when one
    ended. Measured 2026-09-25 on a live drive: an unattended General loop finished, another was
    cancelled, a third stopped at its cycle ceiling with "This run stopped and needs a decision" —
    and there was not one notification or inbox row for any of them. A loop you start unattended
    is one you are not watching; the end of it is the thing you need to hear. The loops-table
    watchdog announces the same moments (`loop/watchdog.py:_NOTIFY_EVENTS`), so this is parity:

    * ``complete`` → one "Loop complete" notification;
    * ``failed`` → one "Loop failed" notification, carrying the engine's reason; when a spend
      ceiling refused the call a step needed (``refused``), a durable inbox row plus its one
      notification instead, titled as a loops-table loop's spend-cap pause is, since lifting the
      cap is its owner's to do;
    * ``escalated`` → a durable inbox row plus its one notification: the loop stopped before its
      done condition, and a human decides what happens next on the run page (Retry, Fork). Its
      title says what happened, by the escalation's own cause (`ending_sentence.CAUSES`) — "Loop
      stopped at its budget", "Loop stopped: the judge could not decide", "Loop stopped: an ask
      ran out of time", "Loop stopped: a spend cap refused its next call", or "Loop stopped
      before it finished" — and not that it waits on an
      answer: nothing answers an escalation where the row is. Its body is the loop's name, the
      escalation's sentence and what to do about it;
    * ``cancelled`` and ``declined`` → nothing: the user did it.

    Its refs carry ``loop`` (every loop surface deep-links by it, and ``#/loops/<id>`` lands on
    the run page) AND ``workflow``, so deleting the run closes the row with the run's others.
    Deduped per run: a run ends once. Best-effort like everything here. Returns the item id or "".
    """
    from personalclaw import notification_kinds
    from personalclaw.workflows.models import RunStatus

    title = str(getattr(run, "title", "") or getattr(run, "workflow_name", "") or run.id)
    meta = {"loop_id": run.id, "loop_kind": run.loop_kind, "run_id": run.id}
    try:
        if status == RunStatus.COMPLETE:
            state.notify(notification_kinds.LOOP_COMPLETE, "Loop complete", title, meta=meta)
        elif status == RunStatus.FAILED and refused:
            from personalclaw.inbox import ItemKind, emit_attention_item
            from personalclaw.loop import spend_cap

            reason = str(getattr(run, "error_message", "") or "").strip()
            return emit_attention_item(
                state,
                source=SOURCE,
                kind=KIND,
                item_kind=ItemKind.NEEDS_INPUT.value,
                title=spend_cap.TITLE,
                body=f"{title} — {reason}" if reason else title,
                refs={"loop": run.id, "loop_kind": run.loop_kind, "workflow": run.id},
                dedup_key=f"loop-run:{run.id}:spend_cap",
            )
        elif status == RunStatus.FAILED:
            reason = str(getattr(run, "error_message", "") or "").strip()
            body = f"{title} — {reason}" if reason else title
            state.notify(notification_kinds.LOOP_FAILED, "Loop failed", body[:300], meta=meta)
        elif status == RunStatus.ESCALATED:
            from personalclaw.inbox import ItemKind, emit_attention_item

            attention = getattr(run, "attention", None) or {}
            detail = str(attention.get("detail") or "").strip()
            remedy = str(attention.get("remedy") or "").strip()
            said = " ".join(part for part in (detail, remedy) if part)
            return emit_attention_item(
                state,
                source=SOURCE,
                kind=KIND,
                item_kind=ItemKind.NEEDS_INPUT.value,
                title=_LOOP_STOP_TITLE.get(
                    str(attention.get("cause") or ""), "Loop stopped before it finished"
                ),
                body=f"{title} — {said}" if said else title,
                refs={"loop": run.id, "loop_kind": run.loop_kind, "workflow": run.id},
                dedup_key=f"loop-run:{run.id}:escalated",
            )
    except Exception:
        logger.debug("workflow %s: could not announce the loop's end", run.id, exc_info=True)
    return ""


def cancel_run_approvals(state: Any, run: Any, ending: str, *, because: str = "") -> int:
    """End every approval still listed for an ended run's stages. Returns how many ended.

    The same promise as :func:`expire_run_items`, for the other thing a run leaves asking: an
    approval under the run's key space (``workflow:<run>:<node>``) belongs to work that is over,
    and approving it would start that work anyway. *ending* is the run's own phrase ("was
    cancelled"), worded as the decision path's owner check words it, so the audit row and the
    expired ask read the same whichever of the two ends an approval first — and, as that check
    does, the loop that started the run is named first when it is over (`loop.children`), else
    what the run's cancel said ended it (*because*: "its chat turn was stopped").
    """
    cancel = getattr(state, "cancel_approvals", None)
    if cancel is None:
        return 0
    from personalclaw.loop.children import ended_by
    from personalclaw.workflows.models import ended_because

    try:
        return int(
            cancel(
                session_prefix=f"{ownership.OWNED_PREFIX}{run.id}:",
                reason=ended_by(run)
                or ended_because(f"the workflow run that asked for it {ending}", because),
            )
        )
    except Exception:
        logger.warning("run %s: ending its approvals failed", run.id, exc_info=True)
        return 0
