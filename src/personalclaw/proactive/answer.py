"""Your answer to the Morning triage digest, whichever door it comes through.

Two doors take an answer: the digest's card in PersonalClaw (``POST /api/proactive/digest/reply``)
and a reply on the chat channel the digest reached (`proactive.channel_reply`). Both call
:func:`answer`, so a reply typed on a channel is the same answer a tap on the card is: the same
check of who is answering, the same reading of the current digest, the same grammar, the same run of
each proposal and the same record. Only what the door is (:class:`Door`) differs.

* **Only you answer** (`approval_answer`): a signed-in session of yours, or you on a paired chat
  channel. Asked before anything is read, and a refusal is audited there.
* **That digest, and no other.** An ordinal numbers ONE window, so an answer names the run it
  answers, and a run that is no longer the current digest is refused as expired rather than acted
  on best-effort against whatever is third today. A proposal you had not answered
  when the next digest ran is in that one (`proactive.carry`), under its number there, and is
  answered there like any of its own.
* **Attended.** A Yes runs its proposal through the digest's own stage
  (:func:`personalclaw.proactive.autoexec.auto_execute`) with ``answered=True`` and a synthetic
  approve rule standing for the answer — so the action denylist, ``enforce_action``'s SEL row and
  the budget floor apply to it as they apply to the digest acting on its own, and a second
  dispatch seam never exists. The two gates that are about work nobody answered do not:
  incident mode leaves attended work running, and the operator ceiling's ``ask`` is satisfied by a
  person answering, so the auto-execute grant is never asked for.
* **Idempotency is the run's own ledger, not a new store.** Every answered ordinal leaves a
  ``triage_reply`` row on the digest's run, so an answer that arrives twice — a double tap, a
  retried channel delivery, a reply typed after the gateway restarted — finds the first row and
  acks instead of acting again.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from personalclaw.approval_answer import Principal

logger = logging.getLogger(__name__)

#: The rule key recorded on a ledger row for an execution the USER authorised by answering.
#: Distinct from the `policy:trivial-tier`, because "you said yes to this one" and "the tier policy
#: allowed it" are different authorities and an audit that conflated them would lose the user's
#: own decision.
REPLY_RULE = "reply:you-approved"

#: What became of an answer as a whole (:attr:`Answered.outcome`). ``acted`` is the only one that
#: answered anything; every other one is why nothing was.
ACTED = "acted"
HELP = "help"
EXPIRED = "expired"
UNREADABLE = "unreadable"
REFUSED = "refused"

#: Why a "yes" did not reach the action stage at all, beside the stage's own reasons
#: (`autoexec.SKIP_*`).
_NOTHING_RETURNED = "nothing_returned"


@dataclass(frozen=True)
class Door:
    """Where an answer came in: who gave it, and how it is recorded and run.

    ``by`` is who answered (`approval_answer`: you, or you on a channel). ``caller`` and
    ``source`` are its SEL row's. ``session_key`` is the session its actions are screened under
    (``""`` for an answer with no session of its own, a reply on a channel). ``taught_from`` is what
    a rule an "always" teaches says it was taught from. ``memory`` returns the global memory's
    service, asked only when an "always" teaches a rule.
    """

    by: Principal
    caller: str
    source: str
    session_key: str = ""
    taught_from: str = "digest-card"
    memory: Callable[[], Any] | None = None


@dataclass(frozen=True)
class Answered:
    """What an answer did: an :data:`ACTED` with one result per ordinal it named, or why not."""

    outcome: str
    results: tuple[dict[str, Any], ...] = ()
    #: :data:`HELP`: the grammar, and why this reply was not read.
    help: str = ""
    help_reason: str = ""
    #: :data:`EXPIRED`: the run that IS the current digest (``""`` when there is none).
    current_run_id: str = ""
    #: :data:`UNREADABLE`: what the read raised. :data:`REFUSED`: why this answerer may not answer.
    error: str = ""


def pending_row(view: dict, ordinal: str) -> dict | None:
    for row in view.get("pending") or []:
        if str(row.get("ordinal", "")) == ordinal:
            return row
    return None


def write_reply_row(
    run_id: str, ordinal: str, *, verb: str, outcome: str, detail: str, answered_by: str
) -> bool:
    """Record the answer on the digest's own run. Returns False when there is no run to write to.

    The row IS the idempotency record, so a failure to write it is reported to the caller rather
    than swallowed: an answer that acted but left no row would act again on the next one.
    ``answered_by`` is who answered (`approval_answer.Principal.label`): you, or you on a channel.
    """
    from personalclaw.ledger.kinds import TRIAGE_REPLY
    from personalclaw.proactive.surface import TRIAGE_NODE_ID
    from personalclaw.workflows.journal import Journal

    try:
        Journal(run_id=run_id).write(
            TRIAGE_REPLY,
            node_id=TRIAGE_NODE_ID,
            instance_path=TRIAGE_NODE_ID,
            epoch=0,
            actor="user",
            item_ordinal=ordinal,
            verb=verb,
            outcome=outcome,
            detail=detail,
            answered_by=answered_by,
        )
    except Exception:  # noqa: BLE001
        logger.warning("proactive: reply row not written for %s/%s", run_id, ordinal, exc_info=True)
        return False
    return True


def persist_rule(door: Door, pattern: str, approve: bool) -> tuple[str, str]:
    """Teach one approval rule through the SAME guarded write the rules manager POSTs to.

    Returns ``(key, error)``. The write goes through ``MemoryService.set_semantic``, so the
    injection scanner still sees the pattern text even though the user ratified it. A
    proposal with no pattern has nothing to remember, which is said rather than raised: the card
    offers no "always" for one, and a typed "always" still answers the proposal once. A memory that
    cannot be reached is said the same way: the answer stands, and nothing was taught.
    """
    from personalclaw.proactive.approval import ApprovalRule, Verdict, rule_to_value

    if not pattern.strip():
        return "", "this proposal has no pattern to remember, so nothing was taught"
    rule = ApprovalRule(
        pattern=pattern,
        verdict=Verdict.APPROVE if approve else Verdict.DENY,
        created_from_digest=door.taught_from,
    )
    try:
        if door.memory is None:
            raise RuntimeError("no memory store is reachable from here")
        svc = door.memory()
        err = svc.set_semantic(rule.key, rule_to_value(rule), 1.0, "user_explicit")
    except Exception as exc:  # noqa: BLE001 - said on the answer; the answer itself stands
        logger.warning("proactive: the rule for %s could not be remembered", pattern, exc_info=True)
        return rule.key, f"the pattern could not be remembered ({type(exc).__name__}: {exc})"
    if err is not None:
        _code, message = err
        return rule.key, message
    return rule.key, ""


def answer_outcome(approves: bool, reason: str) -> str:
    """What became of one answer, as its ``triage_reply`` row records it.

    A "no" is declined. A "yes" is executed when its action landed (no ``reason``), failed when
    it was tried and failed, and refused when a guard or the item stopped it first — never
    declined, which would say she turned it down.
    """
    from personalclaw.proactive.autoexec import SKIP_FAILED
    from personalclaw.proactive.surface import (
        OUTCOME_ANSWER_DECLINED,
        OUTCOME_ANSWER_EXECUTED,
        OUTCOME_ANSWER_FAILED,
        OUTCOME_ANSWER_REFUSED,
    )

    if not approves:
        return OUTCOME_ANSWER_DECLINED
    if not reason:
        return OUTCOME_ANSWER_EXECUTED
    if reason == SKIP_FAILED:
        return OUTCOME_ANSWER_FAILED
    return OUTCOME_ANSWER_REFUSED


async def dispatch_approved(
    view: dict, row: dict, *, session_key: str, run_id: str
) -> tuple[str, str]:
    """Run ONE approved proposal through the auto-execute stage. Returns ``(reason, detail)``.

    ``reason`` is empty when the action landed, and otherwise says why it did not (the stage's
    `autoexec.SKIP_*`, or that it never reached the stage); ``detail`` is then the sentence the
    card and the response show, in the words the digest uses (`autoexec.not_done_note`).

    The user's answer is expressed as an in-memory approve rule for exactly this one proposal —
    never persisted, so a single "yes" does not silently become an "always" — keyed
    :data:`REPLY_RULE`, so the run's journal names her answer as what allowed it rather than a
    taught rule, and `cap=1`, so an answer can dispatch one action and no more. Its pattern is the
    proposal's own, or its kind when the run recorded none: the rule is handed this one proposal
    alone, so it authorises exactly what she saw, and a pattern is what "always" remembers, not
    what one "yes" needs. The proposal runs as its run recorded it (its arguments), and the
    capability the answer admits is its own kind's alone (`autoexec.answer_capabilities`), not
    the digest's unattended set, which holds no task list. It runs as answered work
    (``answered=True``): every guard that stage puts in front of an unattended write runs here
    too, in the order it runs there, but the two that hold only what nobody answered (incident
    mode and the operator ceiling's auto-execute grant) do not.
    """
    from datetime import datetime, timezone

    from personalclaw.proactive.approval import ApprovalRule, Verdict
    from personalclaw.proactive.autoexec import (
        SKIP_NO_PROVIDER,
        answer_capabilities,
        auto_execute,
        not_done_note,
    )
    from personalclaw.proactive.manifest import manifest_from_projection
    from personalclaw.proactive.proposals import Proposal

    action_type = str(row.get("action_type", "") or "")
    pattern = str(row.get("pattern_key", "") or "") or action_type
    if not pattern:
        # A row with no kind names nothing to do.
        return SKIP_NO_PROVIDER, not_done_note(SKIP_NO_PROVIDER, answered=True)
    manifest = manifest_from_projection(
        [
            {
                "ordinal": str(item.get("ordinal", "") or ""),
                "source": str(item.get("source", "") or ""),
                "source_id": str(item.get("source_id", "") or ""),
                "title": str(item.get("title", "") or ""),
                "permalink": str(item.get("item_permalink", "") or ""),
                "materiality": str(item.get("materiality", "") or ""),
            }
            for item in (view.get("pending") or []) + (view.get("auto_done") or [])
        ],
        window_start=str(view.get("window_start", "") or ""),
    )
    arguments = row.get("action_config")
    proposal = Proposal(
        item_id=str(row.get("ordinal", "") or ""),
        action_type=action_type,
        tier=str(row.get("tier", "") or ""),
        action_config=dict(arguments) if isinstance(arguments, dict) else {},
        pattern_key=pattern,
    )
    result = await auto_execute(
        [proposal],
        manifest=manifest,
        rules=[ApprovalRule(pattern=pattern, verdict=Verdict.APPROVE, key=REPLY_RULE)],
        now=datetime.now(timezone.utc),
        enabled=True,
        cap=1,
        capabilities=answer_capabilities(action_type),
        session_key=session_key,
        ledger=run_ledger(run_id),
        answered=True,
    )
    if result.executed:
        action = result.executed[0]
        return "", f"{proposal.action_type} on {action.source_id}"
    if result.deferred:
        deferred = result.deferred[0]
        return deferred.reason, not_done_note(deferred.reason, deferred.detail, answered=True)
    return (
        _NOTHING_RETURNED,
        "Not done: the action stage returned nothing. Open the item to do it yourself.",
    )


def run_ledger(run_id: str) -> Callable[[str, dict], None]:
    """The auto-execute stage's `LedgerFn`, bound to the digest's run so an approved action lands
    in ITS journal."""
    from personalclaw.proactive.surface import TRIAGE_NODE_ID
    from personalclaw.workflows.journal import Journal

    journal = Journal(run_id=run_id)

    def write(kind: str, fields: dict) -> None:
        journal.write(
            kind,
            node_id=TRIAGE_NODE_ID,
            instance_path=TRIAGE_NODE_ID,
            epoch=0,
            actor="user",
            **fields,
        )

    return write


def verb(parsed: Any) -> str:
    """The reply's verb as a ``triage_reply`` row and the card name it (``always yes``)."""
    from personalclaw.proactive.approval import ReplyAction

    return {
        ReplyAction.APPROVE_ONCE: "yes",
        ReplyAction.DENY_ONCE: "no",
        ReplyAction.APPROVE_ALWAYS: "always yes",
        ReplyAction.DENY_ALWAYS: "always no",
        ReplyAction.APPROVE_ALL: "yes all",
        ReplyAction.DENY_ALL: "no all",
    }.get(parsed.action, str(parsed.action))


async def answer(run_id: str, text: str, *, door: Door) -> Answered:
    """Answer the digest *run_id* with *text*, a reply in the digest's grammar, as *door* gives it.

    In order: who is answering (refused, and audited, before anything is read), the current
    digest (an unreadable one acts on nothing; a *run_id* that is not it is expired), the grammar
    (anything it cannot read gets the help line, never an interpretation), and then each ordinal
    the reply names: an answered one acks, an unknown one says so, and the rest are answered, each
    leaving its ``triage_reply`` row. One SEL row records the reply.
    """
    from personalclaw import approval_answer
    from personalclaw.proactive import digest_state
    from personalclaw.proactive.approval import HELP_TEXT, ReplyAction, parse_reply
    from personalclaw.proactive.surface import (
        ANSWERS_NOT_DONE,
        OUTCOME_ANSWER_EXECUTED,
        STATE_READY,
    )
    from personalclaw.sel import sel

    # A reply approves (or declines) what the digest's run proposed, so only you give one
    # (`approval_answer`): not an app, not an agent's tool, and not the run that proposed it.
    refused = approval_answer.check(
        door.by, what=f"digest:{run_id}", asked_by=approval_answer.run(run_id).label
    )
    if refused:
        return Answered(outcome=REFUSED, error=refused)

    try:
        view = await asyncio.to_thread(digest_state.current_view)
    except Exception as exc:  # noqa: BLE001 - a digest that cannot be read is answered by nothing
        logger.warning("proactive: reply read failed", exc_info=True)
        return Answered(outcome=UNREADABLE, error=f"{type(exc).__name__}: {exc}")

    if view.get("state") != STATE_READY or str(view.get("run_id", "")) != run_id:
        # An ordinal numbers ONE window. Acting on a stale digest's "3" would address whatever
        # happens to be third today: a wrong-target execution.
        return Answered(outcome=EXPIRED, current_run_id=str(view.get("run_id", "") or ""))

    ordinals = [str(row.get("ordinal", "")) for row in (view.get("pending") or [])]
    # Every number the digest gives, a proposal it carried from an earlier one among them: what
    # the window collected alone would refuse "4 yes" for the fourth, carried, proposal.
    parsed = parse_reply(text, max_ordinal=view.get("numbered") or None)
    if parsed.action in (ReplyAction.HELP, ReplyAction.UNPARSEABLE):
        return Answered(outcome=HELP, help=HELP_TEXT, help_reason=parsed.error or "")
    targets = ordinals if parsed.applies_to_all else [str(parsed.ordinal)]

    said = verb(parsed)
    results: list[dict[str, Any]] = []
    for ordinal in targets:
        row = pending_row(view, ordinal)
        if row is None:
            results.append(
                {"ordinal": ordinal, "outcome": "unknown", "detail": "not pending in this digest"}
            )
            continue
        # What the result is about, so a door that says it in words names the item it answered.
        about = {
            "action_type": str(row.get("action_type", "") or ""),
            "title": str(row.get("title", "") or ""),
        }
        if row.get("answered"):
            results.append(
                {
                    "ordinal": ordinal,
                    **about,
                    "outcome": "already",
                    "detail": f"already answered {row.get('answer') or 'earlier'}",
                }
            )
            continue
        rule_key, rule_error = "", ""
        if parsed.persists_rule:
            rule_key, rule_error = await asyncio.to_thread(
                persist_rule, door, str(row.get("pattern_key", "") or ""), parsed.approves
            )
        reason, detail = "", ""
        if parsed.approves:
            reason, detail = await dispatch_approved(
                view, row, session_key=door.session_key, run_id=run_id
            )
        outcome = answer_outcome(parsed.approves, reason)
        not_done = detail if outcome in ANSWERS_NOT_DONE else ""
        recorded = await asyncio.to_thread(
            write_reply_row,
            run_id,
            ordinal,
            verb=said,
            outcome=outcome,
            # The sentence, when the yes did not happen: the card shows it on the answered row.
            detail=not_done or rule_error or detail,
            answered_by=door.by.label,
        )
        results.append(
            {
                "ordinal": ordinal,
                **about,
                "outcome": "acted",
                "verb": said,
                "executed": outcome == OUTCOME_ANSWER_EXECUTED,
                # A "yes" whose action did not happen, said as the card says it. Empty otherwise.
                "not_done": not_done,
                "detail": rule_error or detail,
                "rule": rule_key,
                "rule_error": rule_error,
                # False means the answer was NOT durably recorded, so the next answer will act
                # again. Surfaced rather than hidden — the user is the only one who can retry.
                "recorded": recorded,
            }
        )
    sel().log_api_access(
        caller=door.caller,
        operation="triage_reply",
        outcome="success",
        source=door.source,
        resources=f"run:{run_id}:{said}:{','.join(targets)}",
    )
    return Answered(outcome=ACTED, results=tuple(results))


__all__ = [
    "ACTED",
    "EXPIRED",
    "HELP",
    "REFUSED",
    "REPLY_RULE",
    "UNREADABLE",
    "Answered",
    "Door",
    "answer",
    "answer_outcome",
    "dispatch_approved",
    "pending_row",
    "persist_rule",
    "run_ledger",
    "verb",
    "write_reply_row",
]
