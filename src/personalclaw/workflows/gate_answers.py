"""The controller's side of a step that waits on a human.

Two kinds of step wait on a person: a gate, and an action that stopped for one (browse at a
sign-in page, `outcome="needs_input"`) or asked a question in its output (`awaits_human`). When
one parks, `ensure_continuation` mints its durable resume point once per `(path, epoch)`, with the
pending half of the typed confirmation, the escalation's outcome question and the inbox row
— the step's own ask, under an id that is this ask's alone. When one is answered,
`RunController.resume` applies it — a parked action through `settle_parked_step`, which runs it
again; the `revise` verb — "change step 3, then carry on" — is `resume_revise`, and a human
overriding a judge on the same gate is recorded by `emit_judge_divergence`. A NO is `decline`, and a
gate is a gate: `stopping_gate` finds the step the run stops at — a decline, an approval never given
in time, a check that failed — and `end_at_gate` ends the run there. When the run
ends, `close_waits` ends every wait it still holds, and an ask that closes with nobody answering it
is `withdraw_asks`'s: its confirmation resolves `withdrawn`, saying why.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.ledger import outcomes
from personalclaw.safety_flags import yes_or_no
from personalclaw.workflows import attention, ending_sentence
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import judge_calibration, mid_flight, mutations, revision, store
from personalclaw.workflows.bindings import node_deps
from personalclaw.workflows.human_input import drop_continuations
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
    NodeKind,
    RunStatus,
    now_stamp,
    run_ending,
    spec_path,
    walk,
)
from personalclaw.workflows.step_usage import NOTHING_SENT

if TYPE_CHECKING:
    from personalclaw.approval_answer import Principal
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


#: How long an escalated gate gets to be answered before its outcome is graded. A day,
#: because a gate raised overnight is answered in the morning and grading it sooner would call a
#: sleeping user an unlanded interruption. Nothing expires at this point — the gate keeps waiting;
#: only the BET about whether interrupting was worth it closes.
ESCALATION_ANSWER_HORIZON_SECS = 24 * 3600.0


def surface_needs_input(ctl: RunController) -> None:
    """Publish the needs-input state without ending the run.

    Split from `_finish` deliberately: a gate with an unattended deadline must be
    VISIBLE now and still able to time out later. Collapsing the two would force a
    choice between surfacing promptly and honouring the timeout.
    """
    ctl.run.status = RunStatus.NEEDS_INPUT
    ctl._save_run()
    ctl._publish("workflow_run_update", {"status": RunStatus.NEEDS_INPUT.value})


def awaits_human(ctl: RunController, path: str) -> bool:
    """Is this waiting instance waiting on a PERSON (versus a `wait` deadline)?

    A `wait` is parked on the CLOCK and resolves itself, so surfacing it as needs_input
    would ask a human to answer something nobody asked them.

    `approval` and `event` gates are ONE case here on purpose, and #375 read that as the bug
    it is not. An event gate's wake-up arrives as a trigger-declared resume against this
    run (`triggers.loop._apply_resume` → `service.resume_run` → `resume`), which is the
    same continuation a human answering the card consumes — so an event gate is
    answerable by a human too, and `bundled/goal-pursuit-monitor`'s `park` message says
    exactly that ("answer this gate to force a check now"). Splitting them would hide a
    parked monitor from needs_input, leaving no surface for the escape hatch.

    A waiting ACTION is the other case, and it used to be missed: an action waits only on a
    person — it parked (`outcome="needs_input"`: browse at a sign-in page, a spent budget), or its
    output asked a question (`gate_policy.clarification_from_output`). Neither has a deadline,
    and read as "not a gate" both ended the run `needs_input` with no continuation, no Inbox row
    and nothing anywhere a person could answer.
    """
    node = dict(walk(ctl.root)).get(spec_path(path))
    if node is None:
        return False
    if node.kind == NodeKind.ACTION:
        return True
    if node.kind != NodeKind.GATE:
        return False
    raw = str((node.config or {}).get("kind", "") or "")
    return raw in ("approval", "event")


def ensure_continuation(ctl: RunController, path: str) -> None:
    """Mint a durable resume point for a waiting gate (WF2-R7), once per epoch.

    Idempotent by (path, epoch): a run can pass through `needs_input` repeatedly as the
    watchdog polls it, and a fresh token per poll would leave a pile of live approval
    links for one question — each of them individually valid.
    """
    from personalclaw.workflows.human_input import (
        create_continuation,
        handoff_bundle,
        list_continuations,
    )

    inst = ctl._instance(path)
    for existing in list_continuations(ctl.run.id):
        if existing.instance_path == path and existing.epoch == inst.epoch:
            return
    node = dict(walk(ctl.root)).get(spec_path(path))
    # THIS step's ask, kept on its instance when it began to wait. It was read off
    # `run.attention`, one slot for the whole run: two steps waiting at once — parallel gates —
    # both asked whatever the later one had written there.
    ask = dict(inst.ask)
    card = _parked_card(ctl, path, node)
    outstanding = [
        p for p, i in ctl.instances.items() if i.state not in TERMINAL_STATES and p != path
    ]
    # The typed CONFIRMATION record's id, minted with the ask and carried
    # on it. From `(run, step, epoch)` and which ask of this step it is, NOT from the resume token
    # — and not from `(run, step, epoch)` alone, which a gate asking twice in one epoch (a parked
    # step approved and stopping again, a rewind that does not force) repeated: the second ask
    # then carried the first one's id, and the first one's answer read as its answer.
    #
    # The STEP is its instance path, not its node id. A loop repeats its body's node ids, and the
    # ask's ordinal is counted per path, which starts again at zero in each cycle — so keyed on the
    # node id, every cycle's ask of one gate carried the same id. The path names the cycle.
    confirmation_id = stable_confirmation_id(
        ctl.run.id, path, inst.epoch, ask=_times_asked(ctl, path)
    )
    cont = create_continuation(
        ctl.run.id,
        node_id=node.id if node else "",
        instance_path=path,
        epoch=inst.epoch,
        resolved_inputs=_resolved_for_path(ctl, path),
        ask=ask,
        handoff=handoff_bundle(
            scope=ctl.run.workflow_name,
            status="blocked on human input",
            outstanding=outstanding,
            checks_run=[p for p, i in ctl.instances.items() if i.state in SUCCESS_STATES],
            next_steps=[f"answer the gate at {node.id if node else path}"],
            attempted=(card or {}).get("attempted") or [],
        ),
        confirmation_id=confirmation_id,
    )
    ctl._publish(
        "workflow_needs_input",
        {
            "node_id": cont.node_id,
            "instance_path": path,
            "resume_token": cont.token,
            "ask": cont.ask,
            "handoff": cont.handoff,
            "expires_at": cont.expires_at,
        },
    )
    # The confirmation's pending half. Emitted HERE rather than at a second site so it inherits
    # this method's `(path, epoch)` idempotency for free: the watchdog polls a waiting run
    # repeatedly, and a per-poll emission would put one "awaiting approval" row per poll into the
    # ledger for a single question.
    ctl.publish_confirmation_pending(
        path,
        cont.node_id,
        confirmation_id=confirmation_id,
        kind=_confirmation_kind(node.config if node else {}),
    )
    _open_escalation_outcome(ctl, path, cont.node_id, confirmation_id)
    # …and DURABLY, to the inbox. The SSE frame above only reaches a view that
    # happens to be open; a scheduled run parking at 3am would otherwise wait in silence
    # forever. Minted alongside the continuation so the two share the (path, epoch)
    # idempotency — one row per question, not one per watchdog poll.
    attention.raise_gate_item(
        ctl.services.attention_state,
        run_id=ctl.run.id,
        workflow=ctl.run.workflow_name,
        node_id=cont.node_id,
        instance_path=path,
        epoch=inst.epoch,
        resume_token=cont.token,
        ask=cont.ask,
        handoff=cont.handoff,
        card=card,
    )


def _parked_card(ctl: RunController, path: str, node: Any) -> dict[str, Any] | None:
    """The needs-input card a parked ACTION composed itself, read off the output it kept.

    Browse's sign-in handoff (`browse.handoff.request_login`) builds its `NeedsInputItem` with the
    blocker, what it tried ("opened example.com — it has never been signed in on this machine")
    and its evidence, under the output's `needs_input` key. That is the wording the user must see,
    so the continuation's handoff and the Inbox row carry it rather than a card re-derived from
    the bare ask, which would say nothing about what was tried. A card is told apart from an ASK
    under the same key (`gate_policy.clarification_from_output`) by its `blocker`; an action that
    asked a question keeps no output, so it has neither.
    """
    if node is None or node.kind != NodeKind.ACTION:
        return None
    inst = ctl.instances.get(path)
    if inst is None or not inst.output_ref:
        return None
    output = store.read_output(ctl.run.id, path)
    card = output.get("needs_input") if isinstance(output, dict) else None
    if isinstance(card, dict) and str(card.get("blocker") or "").strip():
        return card
    return None


def settle_parked_step(
    ctl: RunController, cont: Any, inst: Any, *, approved: bool, answer: Any, who: str
) -> None:
    """Apply a person's answer to a step that PARKED on them (`Ask.rerun`).

    Approving runs the step again. What the person did was lift what stopped it — sign in to the
    site, raise the budget, release the kill switch — so the step's work still has to happen, and
    recording "approved" as its output (a gate's answer) would hand the next step a yes where it
    expects a page. PENDING at the SAME epoch, the way `resume_revise` and a no-force rewind reset
    a node, so the effect ledger keys the new dispatch as the retry it is. The parked attempt's
    output is ARCHIVED rather than overwritten, because the notes a ceiling-parked browse kept are
    real work. The answer rides the dispatch it starts, and only that one (`_park_answers`,
    popped by `step_dispatch.execute`): the browse step you confirmed a sign-in for goes on to the
    run instead of re-reading a session record your sign-in never wrote. That answer lives only in
    memory: a restart between the answer and the dispatch loses it, and the step then parks on the
    same check and asks again — a repeated question, never a skipped one.

    Denying DECLINES the step, as Deny on a gate does: nothing after it runs and the run ends
    `declined`. The parked output stays on the step for a reader.
    """
    inst.degraded_reason = ""
    if approved:
        if inst.output_ref:
            store.archive_output(ctl.run.id, cont.instance_path, ctl.run.spec_version)
        inst.state = InstanceState.PENDING
        inst.output_ref = ""
        inst.failure = None
        inst.completed_at = None
        inst.attempt = 0
        ctl._park_answers[cont.instance_path] = answer
        return
    decline(ctl, cont.instance_path, inst, who=who)


def is_approval_gate(node: Any) -> bool:
    """Is `node` a gate whose passing is a person's approval?"""
    return _gate_kind(node) == "approval"


#: The gate kinds whose passing is a CHECK the engine decides rather than a person: a
#: judge, an expression, a verifier or a ladder of them. Not `event`: that gate waits for a signal,
#: and its deadline is a wake rather than a verdict — `goal-pursuit-monitor` parks on one between
#: checks and checks anyway when nothing woke it.
CHECK_GATE_KINDS = frozenset({"judge", "expression", "verify_command", "verify_script", "ladder"})


def is_check_gate(node: Any) -> bool:
    """Is `node` a gate whose passing is a check — one that stops what follows when it fails?"""
    return _gate_kind(node) in CHECK_GATE_KINDS


def tolerates_failure(node: Any) -> bool:
    """Does this gate ITSELF say that what follows may run when its check fails?

    The engine's two existing declarations, and only on the gate: `on_error: null_continue` (carry
    on, the failure still counts) and `allow_failure: true` (carry on, recorded as degraded). The
    default `on_error` is no declaration at all — it is what walked every bundled check past its
    own failure — and a fan-out's `on_item_error` is a policy for its items' failures, not the
    gate's word about its check.
    """
    cfg = (getattr(node, "config", None) or {}) if node is not None else {}
    return cfg.get("on_error") == "null_continue" or yes_or_no(cfg.get("allow_failure")) is True


def _gate_kind(node: Any) -> str:
    if node is None or getattr(node, "kind", None) != NodeKind.GATE:
        return ""
    return str((node.config or {}).get("kind", "") or "")


def decliner(by: Principal, channel: str) -> str:
    """Who declined, as the run's record names them.

    Only you answer a gate (``approval_answer``), so it names you (Settings → Account → "Your
    name"), or "you" when no name was given. A channel reply must come from the run's owner
    (`gate_policy.may_answer`) and names who replied and where.
    """
    from personalclaw.identity import operator_name

    name = (by.name if channel else "") or operator_name() or "you"
    return f"{name} in {channel}" if channel else name


def decline(
    ctl: RunController,
    path: str,
    inst: Any,
    *,
    who: str,
    record: dict[str, Any] | None = None,
    verb: str = "declined",
) -> None:
    """Record a person's NO on the step at `path` — a gate's Deny, Deny on a parked step, or Deny
    on the approval a stage's subagent asked before it started (`verb` "denied", the button she
    pressed: `stage_settlement`).

    DECLINED, not FAILED: nothing went wrong, and `on_error` is a failure policy that must not
    walk past it. The caption under the step names who declined, and the next `_step` ends the
    run there (`end_at_gate`). `record` is what a gate stores as its output — the answer, with
    `approved: false` — for the run's Inspect; a parked step keeps the output it parked with.
    Not bound downstream: DECLINED is not a success state.
    """
    inst.state = InstanceState.DECLINED
    inst.failure = None
    inst.degraded_reason = f"{verb} by {who}"
    inst.completed_at = now_stamp()
    if record is not None:
        payload = {**record, "approved": False, "declined_by": who}
        inst.output_ref = ctl.journal.store_output(path, payload)[0]


def stopping_gate(ctl: RunController) -> str | None:
    """The gate the run stops at, if any — a gate is a gate:

    * a step a person DECLINED;
    * an approval gate that ended FAILED — nobody answered in time, or it could not even ask;
    * a check gate that did not pass — FAILED, or ESCALATED (a judge that would not rule either
      way) — unless the gate itself says its failure may be walked past (`tolerates_failure`).

    `needs` means AFTER, not after-success, and `on_error: null_continue` is the default, so the
    frontier would schedule what follows behind any of them; this is read before the frontier
    (`RunController._step`) so it never gets the chance. Sorted, so which of two stopping gates ends
    the run is decided the same way every time.
    """
    nodes: dict[str, Any] | None = None
    for path in sorted(ctl.instances):
        state = ctl.instances[path].state
        if state == InstanceState.DECLINED:
            return path
        if state not in (InstanceState.FAILED, InstanceState.ESCALATED):
            continue
        nodes = nodes if nodes is not None else dict(walk(ctl.root))
        node = nodes.get(spec_path(path))
        if state == InstanceState.FAILED and is_approval_gate(node):
            return path
        if is_check_gate(node) and not tolerates_failure(node):
            return path
    return None


async def end_at_gate(ctl: RunController, path: str) -> None:
    """End the run at the gate it stopped at (`stopping_gate`).

    Every step after the gate in each sequence that holds it is marked skipped with the reason, so
    the run page shows what the gate stopped instead of those steps simply never appearing.
    Anything still in flight elsewhere is stopped, as a cancel stops it, and every other open
    question closes with the run (`close_waits`). The run ends `declined` for a person's no,
    `failed` for an approval nobody gave or a check that failed, and `escalated` for a judge that
    would not rule — and its error names the gate and why.
    """
    inst = ctl.instances[path]
    nodes = dict(walk(ctl.root))
    node = nodes.get(spec_path(path))
    label = str((getattr(node, "label", "") or getattr(node, "id", "") or "")) or path
    followers = ending_sentence.followers(ctl, path, nodes)
    cause = (
        ending_sentence.clause(inst.failure.cause_plain if inst.failure else "")
        or "it did not pass"
    )
    if inst.state == InstanceState.DECLINED:
        ending, outcome = RunStatus.DECLINED, "was declined"
        sentence = f"“{label}” was {inst.degraded_reason}"
    elif is_approval_gate(node):
        ending, outcome = RunStatus.FAILED, "was not approved"
        sentence = f"“{label}” was not approved: {cause}"
    elif inst.state == InstanceState.ESCALATED:
        ending, outcome = RunStatus.ESCALATED, "escalated"
        sentence = f"“{label}” escalated: {cause}"
    else:
        ending, outcome = RunStatus.FAILED, "failed"
        sentence = f"“{label}” failed: {cause}"
    reason = f"not run: “{label}” {outcome}"
    for later in followers:
        ctl._skip(later, reason=reason)
    if ending != RunStatus.DECLINED:
        # Its own question, if it asked one, closes saying why — "no answer within 45 seconds" —
        # rather than with the run's ending, which is all `close_waits` knows of it.
        withdraw_asks(ctl, reason=cause, instance_prefix=path)
    if followers:
        sentence += ", so nothing after it ran"
    await ctl._cancel_inflight(ending)
    await ctl._finish(ending, error=f"{sentence}.")


#: The verb an ask's confirmation closes with when NOBODY answered it: the run ended under it, or
#: a rewind dropped it. Written so a `confirmation_pending` is never left open for good
#: — "how long did this wait" has an end — and marked `answered: false`, so it grades no escalation
#: bet as the person's no. Not a verb a person can send (`confirmation.resolve`).
WITHDRAWN = "withdrawn"

#: The verb an ask closes with when the person REVISED instead of answering yes or no:
#: they sent a step back to be changed, and the gate asks again, about the changed work, under a new
#: id. A third outcome, scored as neither — `introspection.gate_stats` counts it apart, and the
#: escalation bet reads it as an answer with no number (`_open_escalation_outcome`).
REVISED = "revised"


def withdraw_asks(ctl: RunController, *, reason: str, instance_prefix: str = "") -> int:
    """Close every ask still pending under `instance_prefix` (the whole run when empty) without
    an answer: its confirmation resolves `withdrawn`, saying why, and its token is dropped. Returns
    how many were withdrawn."""
    from personalclaw.workflows.human_input import list_continuations

    # The same selection `drop_continuations` makes below, so what is closed is what is dropped.
    pending = [
        cont
        for cont in list_continuations(ctl.run.id)
        if cont.instance_path.startswith(instance_prefix)
    ]
    for cont in pending:
        ctl.publish_confirmation_resolved(
            cont.instance_path,
            cont.node_id,
            confirmation_id=cont.confirmation_id,
            verb=WITHDRAWN,
            approved=False,
            resolved_by="engine",
            reason=reason,
        )
    return drop_continuations(ctl.run.id, instance_prefix=instance_prefix) if pending else 0


def close_waits(ctl: RunController, status: RunStatus) -> None:
    """End every wait a run that has ENDED is still holding (#3620's ended-owner rule).

    `_finish` already closes the run's Inbox rows and cancels its approvals for every ending. A
    node still WAITING was the part left open: cancel a run parked at its gate and the gate kept
    reading `waiting` on the run page, its resume token stayed pending on disk, and Introspect's
    "what needs my approval" kept listing a question nothing could answer. A finished run asks
    nothing, so each wait is CANCELLED — journaled `step_cancelled` with the zero a gate that
    times out also records, because the wait itself sent nothing — published to the live view,
    and its question's reason cleared; then every pending ask is withdrawn: its confirmation
    resolves `withdrawn` with the run's ending as the reason, and its token is dropped.

    Called from the single terminal writer, for every ending: the rule is about the run being
    over, not about which verb ended it.
    """
    nodes = dict(walk(ctl.root))
    for path, inst in sorted(ctl.instances.items()):
        if inst.state != InstanceState.WAITING:
            continue
        node = nodes.get(spec_path(path))
        node_id = node.id if node else ""
        inst.state = InstanceState.CANCELLED
        inst.completed_at = now_stamp()
        inst.wake_at = 0.0
        inst.degraded_reason = ""
        ctl.journal.step_cancelled(path, node_id, epoch=inst.epoch, usage=NOTHING_SENT)
        ctl._publish(
            "workflow_node_done",
            {
                "node_id": node_id,
                "instance_path": path,
                "status": InstanceState.CANCELLED.value,
                "node_epoch": inst.epoch,
            },
        )
    withdraw_asks(ctl, reason=f"the run {run_ending(status)}")


def _resolved_for_path(ctl: RunController, path: str) -> dict[str, Any]:
    """What this node had already resolved — the field that makes a resume re-enter
    the STEP rather than re-run the enclosing subgraph."""
    node = dict(walk(ctl.root)).get(spec_path(path))
    if node is None:
        return {}
    deps = node_deps(node.config or {})
    return {dep: ctl._outputs.get(dep) for dep in sorted(deps)}


def resume_revise(
    ctl: RunController,
    cont: Any,
    token: str,
    step_ref: str,
    comment: str,
    *,
    by: Principal,
    channel: str = "",
) -> dict[str, Any]:
    """Apply `revise{step_ref, comment}` to exactly one node, then let the run carry on.

    This is the third answer to a waiting gate, and the reason it has to exist: a reviewer who
    wants ONE step changed could previously only reject the whole plan and re-run it, which
    re-rolls the twelve stages nobody complained about (the same argument `revision.py` makes
    about regeneration). It closes the ask it answered with its own outcome, `REVISED`.

    Mirrors `planning.session.comment_step`'s awaiting_review → running semantics (a comment
    sends the step back for a re-draft rather than accepting the artifact), with the engine's
    own state names: the gate instance goes back to PENDING at the current epoch, not DONE, so
    it re-asks against the revised step. It is deliberately NOT an approval — nothing is marked
    approved, no `always_allow` is remembered, and no `gate_resolved` is journaled, because the
    gate itself has not been decided yet: its ask was answered, and it asks again.

    EVERY refusal here happens before the token is consumed. The whole point of a revise is that
    the reviewer is still deciding, so a rejected revise must leave them able to answer.
    """
    from personalclaw.workflows.human_input import consume_continuation

    allowed, why = _revise_allowed(ctl.run)
    if not allowed:
        return {"ok": False, "code": "WF_REVISE_NOT_ALLOWED", "message": why}

    root = ctl.spec.get("root")
    if not isinstance(root, dict):
        return {
            "ok": False,
            "code": "WF_REVISE_NO_SPEC",
            "message": "this run has no readable spec to revise",
        }
    node_id, code, message = revision.resolve_step_ref(root, step_ref)
    if code:
        return {"ok": False, "code": code, "message": message}
    text = str(comment or "").strip()
    if not text:
        return {
            "ok": False,
            "code": "WF_REVISE_NO_COMMENT",
            "message": "a revise must say what to change (`comment`)",
        }

    # "user" is what the step's prompt calls the reviewer; a channel reply names who replied.
    patch = revision.comment_patch(
        root, node_id, text, requested_by=(by.name if channel else "") or "user"
    )
    if patch is None:
        return {
            "ok": False,
            "code": "WF_REVISE_NOT_APPLICABLE",
            "message": f"{node_id!r} cannot carry a revision comment",
        }
    merged = revision.merge_patches(ctl.spec, [patch])
    if not merged.applied:
        return {
            "ok": False,
            "code": "WF_REVISE_REJECTED",
            "message": "; ".join(merged.rejected) or "the revision did not apply",
        }

    # The token is consumed only once the revision is certain to land. A revise answers the
    # gate as surely as an approval does — leaving the token live would let the same reviewer
    # revise twice off one ask, and the second would land on an already-revised step.
    claimed = consume_continuation(ctl.run.id, token)
    if claimed is None:
        return {"ok": False, "code": "WF_RESUME_ALREADY_USED"}
    inst = ctl._instance(cont.instance_path)
    if inst.epoch != cont.epoch:
        return {"ok": False, "code": "WF_RESUME_STALE_EPOCH"}
    # The ask this revise answered closes here, as its own outcome: neither the yes nor the no an
    # approval gate is scored on. The gate asks again below about the revised step, under an id
    # of its own (`_times_asked`), so no answer to one ask can close the other.
    ctl.publish_confirmation_resolved(
        cont.instance_path,
        cont.node_id,
        confirmation_id=cont.confirmation_id,
        verb=REVISED,
        approved=False,
        resolved_by=by.label,
        reason=f"sent “{node_id}” back to be revised",
    )

    # ONE spec, written once, journaled with the ops that produced it. `mid_flight.commit_mutation`
    # is the single writer of `spec.json` + `spec_history/` + `user_edited_mid_flight`, so routing
    # the revision through it is what makes the recorded edit and the executing spec the same
    # document rather than two that agree by convention.
    result = mutations.BatchResult(
        ok=True,
        ops=[
            mutations.Op(
                kind=mutations.OpKind.UPDATE_NODE,
                node_id=node_id,
                node=patch.node,
                note=f"revise: {text}",
                raw={"op": "revise", "step_ref": node_id, "comment": text},
            )
        ],
        preview=mutations.CascadePreview(rerun=[node_id]),
        spec=merged.spec,
    )
    mid_flight.commit_mutation(ctl, result, (by.name if channel else "") or "user")

    # The revised step re-asks. PENDING at the SAME epoch, matching `mid_flight._apply_reentry`'s
    # no-force behaviour — and the cache cannot serve the old answer anyway, because the
    # cache key hashes the node's own spec region and the prompt just changed.
    inst.state = InstanceState.PENDING
    inst.output_ref = ""
    inst.failure = None
    inst.completed_at = None
    inst.wake_at = 0.0
    inst.attempt = 0
    if cont.node_id:
        ctl._outputs.pop(cont.node_id, None)
    ctl.journal.write(
        journal_mod.GATE_REVISED,
        instance_path=cont.instance_path,
        node_id=cont.node_id,
        epoch=cont.epoch,
        step_ref=node_id,
        comment=text,
        revised_by=by.label,
    )
    ctl.run.attention = None
    if ctl.run.status == RunStatus.NEEDS_INPUT:
        ctl.run.status = RunStatus.RUNNING
    ctl._persist_state()
    ctl._save_run()
    ctl._publish(
        "workflow_gate_revised",
        {
            "node_id": cont.node_id,
            "instance_path": cont.instance_path,
            "step_ref": node_id,
        },
    )
    # Same reasoning as the approval path: the gate's inbox row must not outlive the ask it
    # raised. The revised step raises its own row when it re-asks.
    attention.resolve_gate_item(ctl.services.attention_state, ctl.run.id, cont.node_id)
    ctl._publish("workflow_run_update", {"status": ctl.run.status.value})
    ctl._resume_loop()
    return {
        "ok": True,
        "revised": True,
        "approved": False,
        "node_id": cont.node_id,
        "step_ref": node_id,
        "spec_version": ctl.run.spec_version,
    }


def emit_judge_divergence(
    ctl: RunController, instance_path: str, node_id: str, human_approved: bool
) -> None:
    """Record a human overriding a judge on the same gate (LOOPS-EVOLUTION R3).

    Reads this node's last `judge_verdict` from the ledger; emits `judge_divergence` only when
    the human's decision actually contradicts it. No prior judge verdict (an ordinary approval
    gate) → nothing to diverge from, so nothing is written.
    """
    if not node_id:
        return
    verdicts = journal_mod.ledger(ctl.run.id, kinds={journal_mod.JUDGE_VERDICT})
    mine = [v for v in verdicts if v.get("node_id") == node_id]
    if not mine:
        return
    judge_verdict = str(mine[-1].get("verdict", ""))
    judged_pass = judge_verdict.upper() == "PASS"
    if judged_pass == human_approved:
        return  # judge and human agree — not a divergence
    record = judge_calibration.DivergenceRecord(
        run_id=ctl.run.id,
        node_id=node_id,
        template=str(ctl.run.workflow_name or ""),
        judge_verdict=judge_verdict,
        human_verdict="PASS" if human_approved else "REJECT",
    )
    ctl.journal.write(journal_mod.JUDGE_DIVERGENCE, **record.to_dict())


def _open_escalation_outcome(
    ctl: RunController, path: str, node_id: str, confirmation_id: str
) -> None:
    """Open the escalation's outcome question: we interrupted the user — did it land?

    The `escalation` producer of the general outcome facility (PP-9). `confirmation_pending`
    records that we ASKED; this records the bet that asking was worth it, graded from the run's
    own ledger: a `confirmation_resolved` carrying this `confirmation_id` is the measurement,
    and its `approved` boolean IS the number (approved ⇒ 1.0, rejected ⇒ 0.0 against a baseline
    of 1.0, so a rejected interruption scores −1). A gate nobody ever answers closes as
    `inconclusive` once the horizon passes — the honest reading of an interruption that went
    nowhere, and the one that decays fastest.

    Ledger-sourced on purpose: this resolves on a box with no vector store, because the ground
    truth is an event we wrote ourselves.

    Only an ANSWER grades it (`answered: true`): an ask withdrawn because the run ended under it
    (`WITHDRAWN`) is an interruption nobody answered, which the horizon closes as inconclusive —
    not the person's no that its `approved: false` would otherwise read as. A `REVISED` ask was
    answered, and with neither answer the bet is scored on, so it is graded as the answer it was.

    Emitted at the same site as `confirmation_pending` so it inherits that site's
    `(path, epoch)` idempotency — one question per gate, not one per watchdog poll.
    """
    try:
        ctl.journal.open_outcome(
            producer=outcomes.PRODUCER_ESCALATION,
            subject=f"escalated gate `{node_id or path}` to the user",
            metric=journal_mod.CONFIRMATION_RESOLVED,
            metric_source=outcomes.SOURCE_LEDGER,
            match={"confirmation_id": confirmation_id, "answered": True},
            value_field="approved",
            # WHICH answer, too: a yes and a no are the bet's scale, and a revise is neither — it
            # is graded as its own answer, with no number, rather than as the no its
            # `approved: false` would read as.
            answer_field="verb",
            unscored_answers=(REVISED,),
            horizon_secs=ESCALATION_ANSWER_HORIZON_SECS,
            # The bet is an approval: we only stop to ask when we expect a yes.
            baseline=1.0,
            instance_path=path,
            node_id=node_id,
            confirmation_id=confirmation_id,
        )
    except Exception:
        # A gate that is already waiting must not fail because its outcome record did not
        # land — the user still has a question to answer.
        logger.debug("escalation outcome open failed for run %s", ctl.run.id, exc_info=True)


def stable_confirmation_id(run_id: str, step_path: str, epoch: int, ask: int = 0) -> str:
    """The stable confirmation id for one ask: (run, step, epoch) and which ask of that step it is.

    Delegates to `confirmation.request_id` rather than composing a string here. Two id schemes for
    one record is the failure mode where `confirmation_pending` and `confirmation_resolved` never
    pair up in the ledger, and nobody notices until someone asks how long a gate waited.

    The STEP is its instance path (`root.children[1].body@2.children[0]`), which names the loop
    cycle it runs in; a node id does not, since every cycle of a loop repeats its body's ids. The
    EPOCH is in the key because a rewind SHOULD produce a new confirmation — the question is being
    asked about different work — and the ASK's ordinal because the same step can ask again within
    one epoch, and that is a new question too (ledger 249). Minted ONCE, when the ask is, and
    carried on its continuation (`Continuation.confirmation_id`): every later half — the answer,
    the withdrawal — reads it from there rather than deriving it again, so the halves pair by
    construction.
    """
    from personalclaw.workflows.confirmation import request_id

    return request_id(run_id, step_path, epoch, ask)


def _times_asked(ctl: RunController, path: str) -> int:
    """How many asks this step has made in the run so far — the ordinal of the next one. Read off
    the run's own ledger (`confirmation_pending` rows for the path), which is durable and append-
    only: a restart, a claimed token and a dropped one all leave it counting."""
    rows = journal_mod.ledger(ctl.run.id, kinds={journal_mod.CONFIRMATION_PENDING})
    return sum(1 for row in rows if row.get("instance_path") == path)


def _confirmation_kind(node_config: dict[str, Any]) -> str:
    """Which `ConfirmationType` this gate is, as its wire value.

    A destructive gate is NOT the same record as an ordinary approval: §4 gives them different
    expiry policies (auto-reject vs hold) and only the ordinary one may be muted. Reading the
    node's own declared risk keeps that classification with the author who made it, rather than
    inferring it from the prompt text at render time.
    """
    from personalclaw.workflows.confirmation import ConfirmationType

    risk = str((node_config or {}).get("risk_category", "") or "").strip().lower()
    if risk in {"destructive", "destructive_op", "irreversible"}:
        return ConfirmationType.DESTRUCTIVE_CONFIRM.value
    kind = str((node_config or {}).get("kind", "") or "").strip().lower()
    if kind in {"input", "needs_input", "question"}:
        return ConfirmationType.NEEDS_INPUT.value
    return ConfirmationType.APPROVAL.value


def parse_revise(answer: Any) -> tuple[str, str] | None:
    """Read `revise{step_ref, comment}` out of a gate answer. Returns `(step_ref, comment)` or None.

    Recognised STRUCTURALLY, by the `revise` key rather than by a free-text prefix. A gate whose
    ask is a `text` legitimately receives prose, and sniffing for the word "revise" in it would
    hijack an answer that merely mentioned revising something.

    `answer` is untyped by contract (`WORKFLOW_RESUME_SCHEMA`), so both spellings a caller
    naturally reaches for are accepted: the nested `{"revise": {...}}` a tool emits, and the flat
    `{"revise": true, "step_ref": ..., "comment": ...}` a form posts. An `answer` with no `revise`
    key is None, which is what routes every existing answer down the unchanged approval path.
    """
    if not isinstance(answer, dict) or "revise" not in answer:
        return None
    body = answer.get("revise")
    if isinstance(body, dict):
        source: dict[str, Any] = body
    else:
        # A truthy flag alongside sibling keys. A falsy flag is NOT a revise — `{"revise": false}`
        # against an approval gate is a rejection expressed clumsily, and the approval path's own
        # `validate_answer` is the right thing to tell the caller so.
        if not body:
            return None
        source = answer
    step_ref = str(source.get("step_ref", "") or source.get("step", "") or "").strip()
    comment = str(source.get("comment", "") or source.get("text", "") or "").strip()
    return step_ref, comment


def _revise_allowed(run: Any) -> tuple[bool, str]:
    """Whether this run can take a revision at all.

    A terminal run cannot: there is nothing left to re-run, so a revision would edit a spec that
    will never execute again — which would break the one promise the verb makes, that the recorded
    plan is the plan that runs.
    """
    if getattr(run, "is_terminal", False):
        return False, f"run is already {getattr(getattr(run, 'status', None), 'value', 'finished')}"
    return True, ""


def is_approved(ask: Any, answer: Any) -> bool:
    """Did the human say yes?

    Only an `approval` ask can DENY — a text or form answer is data, not a verdict, and
    treating an empty string as a denial would fail a gate the user actually answered.
    """
    from personalclaw.workflows.human_input import AskKind

    if ask.kind != AskKind.APPROVAL:
        return True
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, dict):
        return bool(answer.get("approved"))
    return False
