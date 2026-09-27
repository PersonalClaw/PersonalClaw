"""The controller's side of a gate that waits on a human (WF2-R7).

When a gate parks, `ensure_continuation` mints its durable resume point once per `(path, epoch)`,
with the pending half of the typed confirmation, the escalation's outcome question (PP-9) and the
inbox row. When one is answered, `RunController.resume` applies it; the `revise` verb — "change
step 3, then carry on" — is `resume_revise`, and a human overriding a judge on the same gate is
recorded by `emit_judge_divergence`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from personalclaw.ledger import outcomes
from personalclaw.workflows import attention
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import judge_calibration, mid_flight, mutations, revision
from personalclaw.workflows.bindings import node_deps
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    TERMINAL_STATES,
    InstanceState,
    NodeKind,
    RunStatus,
    spec_path,
    walk,
)

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


#: How long an escalated gate gets to be answered before its outcome is graded (PP-9). A day,
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


def is_gate(ctl: RunController, path: str) -> bool:
    """Is this waiting instance a human-input gate (versus a `wait` deadline)?

    A `wait` is parked on the CLOCK and resolves itself, so surfacing it as needs_input
    would ask a human to answer something nobody asked them.

    `approval` and `event` are ONE case here on purpose, and #375 read that as the bug
    it is not. An event gate's wake-up arrives as a trigger-declared resume against this
    run (`triggers.loop._apply_resume` → `service.resume_run` → `resume`), which is the
    same continuation a human answering the card consumes — so an event gate is
    answerable by a human too, and `bundled/goal-pursuit-monitor`'s `park` message says
    exactly that ("answer this gate to force a check now"). Splitting them would hide a
    parked monitor from needs_input, leaving no surface for the escape hatch.
    """
    node = dict(walk(ctl.root)).get(spec_path(path))
    if node is None or node.kind != NodeKind.GATE:
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
    ask = dict(ctl.run.attention or {}) if ctl.run.attention else {}
    outstanding = [
        p for p, i in ctl.instances.items() if i.state not in TERMINAL_STATES and p != path
    ]
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
        ),
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
    # The typed CONFIRMATION record's pending half (TASKS-SOPS §4, S61i). Emitted HERE rather
    # than at a second site so it inherits this method's `(path, epoch)` idempotency for free:
    # the watchdog polls a waiting run repeatedly, and a per-poll emission would put one
    # "awaiting approval" row per poll into the ledger for a single question.
    #
    # `confirmation_id` is derived from `(run, gate, epoch)` by `confirmation.request_id`, NOT
    # from the resume token. The token is single-use and rotates on rewind; the ID has to stay
    # stable so `confirmation_pending` and `confirmation_resolved` pair up in the ledger.
    confirmation_id = stable_confirmation_id(ctl.run.id, cont.node_id or path, inst.epoch)
    ctl.publish_confirmation_pending(
        path,
        cont.node_id,
        confirmation_id=confirmation_id,
        kind=_confirmation_kind(node.config if node else {}),
    )
    _open_escalation_outcome(ctl, path, cont.node_id, confirmation_id)
    # …and DURABLY, to the inbox (WF2-R7). The SSE frame above only reaches a view that
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
    )


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
    responder: str = "",
    channel: str = "",
) -> dict[str, Any]:
    """Apply `revise{step_ref, comment}` to exactly one node, then let the run carry on.

    This is the third answer to a waiting gate, and the reason it has to exist: a reviewer who
    wants ONE step changed could previously only reject the whole plan and re-run it, which
    re-rolls the twelve stages nobody complained about (the same argument `revision.py` makes
    about regeneration).

    Mirrors `planning.session.comment_step`'s awaiting_review → running semantics (a comment
    sends the step back for a re-draft rather than accepting the artifact), with the engine's
    own state names: the gate instance goes back to PENDING at the current epoch, not DONE, so
    it re-asks against the revised step. It is deliberately NOT an approval — nothing is marked
    approved, no `always_allow` is remembered, and no `gate_resolved` is journaled, because the
    gate has not been answered yet.

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

    patch = revision.comment_patch(root, node_id, text, requested_by=responder or channel or "user")
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
    mid_flight.commit_mutation(ctl, result, responder or channel or "user")

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
        revised_by=responder or channel or "dashboard",
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

    Emitted at the same site as `confirmation_pending` so it inherits that site's
    `(path, epoch)` idempotency — one question per gate, not one per watchdog poll.
    """
    try:
        ctl.journal.open_outcome(
            producer=outcomes.PRODUCER_ESCALATION,
            subject=f"escalated gate `{node_id or path}` to the user",
            metric=journal_mod.CONFIRMATION_RESOLVED,
            metric_source=outcomes.SOURCE_LEDGER,
            match={"confirmation_id": confirmation_id},
            value_field="approved",
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


def stable_confirmation_id(run_id: str, gate_id: str, epoch: int) -> str:
    """The stable confirmation id for one (run, gate, epoch).

    Delegates to `confirmation.request_id` rather than composing a string here. Two id schemes for
    one record is the failure mode where `confirmation_pending` and `confirmation_resolved` never
    pair up in the ledger, and nobody notices until someone asks how long a gate waited.

    The EPOCH is in the key because a rewind SHOULD produce a new confirmation — the question is
    being asked about different work. Deriving from the resume token instead would break that: a
    token is single-use and rotates per poll, so pending and resolved would carry different ids for
    the same question.
    """
    from personalclaw.workflows.confirmation import request_id

    return request_id(run_id, gate_id, epoch)


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
